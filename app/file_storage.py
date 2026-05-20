import os
import shutil
from pathlib import Path
from typing import BinaryIO

from config.settings import BASE_DIR


class StoredFile:
    def __init__(self, path: Path | None = None, stream: BinaryIO | None = None, content_length: int | None = None):
        self.path = path
        self.stream = stream
        self.content_length = content_length


class AvatarStorage:
    def save_file(self, source_path: Path, object_name: str, content_type: str) -> None:
        raise NotImplementedError

    def open_file(self, object_name: str) -> StoredFile | None:
        raise NotImplementedError

    def delete_file(self, object_name: str) -> None:
        raise NotImplementedError


class FilesystemAvatarStorage(AvatarStorage):
    def __init__(self, root: str | None = None):
        default_root = BASE_DIR / "data" / "avatars"
        self.root = Path(root or os.getenv("AVATAR_STORAGE_DIR") or default_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, object_name: str) -> Path:
        path = (self.root / Path(object_name).name).resolve()
        if self.root not in path.parents and path != self.root:
            raise ValueError("Invalid object name")
        return path

    def save_file(self, source_path: Path, object_name: str, content_type: str) -> None:
        destination = self._safe_path(object_name)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source_path), str(destination))

    def open_file(self, object_name: str) -> StoredFile | None:
        path = self._safe_path(object_name)
        if not path.exists() or not path.is_file():
            return None
        return StoredFile(path=path, content_length=path.stat().st_size)

    def delete_file(self, object_name: str) -> None:
        self._safe_path(object_name).unlink(missing_ok=True)


class S3AvatarStorage(AvatarStorage):
    def __init__(self):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("Для FILE_STORAGE_BACKEND=s3 установите зависимость boto3") from exc

        self.bucket = os.getenv("S3_BUCKET")
        if not self.bucket:
            raise RuntimeError("S3_BUCKET должен быть задан для S3-хранилища")

        self.prefix = os.getenv("S3_PREFIX", "avatars").strip("/")
        self.client = boto3.client(
            "s3",
            endpoint_url=os.getenv("S3_ENDPOINT_URL"),
            aws_access_key_id=os.getenv("S3_ACCESS_KEY_ID"),
            aws_secret_access_key=os.getenv("S3_SECRET_ACCESS_KEY"),
            region_name=os.getenv("S3_REGION", "us-east-1"),
        )
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def _key(self, object_name: str) -> str:
        safe_name = Path(object_name).name
        return f"{self.prefix}/{safe_name}" if self.prefix else safe_name

    def save_file(self, source_path: Path, object_name: str, content_type: str) -> None:
        self.client.upload_file(
            str(source_path),
            self.bucket,
            self._key(object_name),
            ExtraArgs={"ContentType": content_type},
        )
        source_path.unlink(missing_ok=True)

    def open_file(self, object_name: str) -> StoredFile | None:
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(object_name))
        except Exception:
            return None
        return StoredFile(
            stream=response["Body"],
            content_length=response.get("ContentLength"),
        )

    def delete_file(self, object_name: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=self._key(object_name))


def get_avatar_storage() -> AvatarStorage:
    backend = os.getenv("FILE_STORAGE_BACKEND", "filesystem").strip().lower()
    if backend in {"filesystem", "fs", "local"}:
        return FilesystemAvatarStorage()
    if backend in {"s3", "minio"}:
        return S3AvatarStorage()
    raise RuntimeError(f"Неизвестный FILE_STORAGE_BACKEND: {backend}")
