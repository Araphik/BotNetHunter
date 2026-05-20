import base64
import hashlib
import hmac

from config.settings import SECRET_KEY


def create_avatar_share_token(user_id: int) -> str:
    payload = str(user_id).encode("utf-8")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).digest()
    raw_token = payload + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
    return base64.urlsafe_b64encode(raw_token).decode("ascii").rstrip("=")


def decode_avatar_share_token(token: str) -> int | None:
    try:
        padded = token + "=" * (-len(token) % 4)
        raw_token = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload, signature = raw_token.split(b".", 1)
        expected = hmac.new(SECRET_KEY.encode("utf-8"), payload, hashlib.sha256).digest()
        signature_bytes = base64.urlsafe_b64decode(signature + b"=" * (-len(signature) % 4))
    except Exception:
        return None
    if not hmac.compare_digest(signature_bytes, expected):
        return None
    try:
        return int(payload.decode("utf-8"))
    except ValueError:
        return None
