from collections import defaultdict
from typing import Any

from fastapi import WebSocket


class UploadProgressManager:
    def __init__(self):
        self._connections: dict[str, set[WebSocket]] = defaultdict(set)

    async def connect(self, upload_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections[upload_id].add(websocket)

    def disconnect(self, upload_id: str, websocket: WebSocket) -> None:
        sockets = self._connections.get(upload_id)
        if not sockets:
            return
        sockets.discard(websocket)
        if not sockets:
            self._connections.pop(upload_id, None)

    async def publish(self, upload_id: str | None, payload: dict[str, Any]) -> None:
        if not upload_id:
            return
        sockets = list(self._connections.get(upload_id, set()))
        for websocket in sockets:
            try:
                await websocket.send_json(payload)
            except Exception:
                self.disconnect(upload_id, websocket)


upload_progress_manager = UploadProgressManager()
