"""
In-memory rate limiter: не более MAX_REQUESTS запросов за WINDOW секунд
с одного IP-адреса или Bearer-токена.
"""
import time
from collections import defaultdict
from threading import Lock


class RateLimiter:
    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window = window_seconds
        self._store: dict[str, list[float]] = defaultdict(list)
        self._lock = Lock()

    def is_allowed(self, key: str) -> tuple[bool, int]:
        """
        Возвращает (разрешено, количество_оставшихся_запросов).
        Если не разрешено — remaining = 0.
        """
        now = time.time()
        with self._lock:
            # Чистим устаревшие отметки времени
            self._store[key] = [t for t in self._store[key] if now - t < self.window]
            count = len(self._store[key])
            if count >= self.max_requests:
                return False, 0
            self._store[key].append(now)
            return True, self.max_requests - count - 1


# Глобальный экземпляр — один на всё приложение
rate_limiter = RateLimiter(max_requests=60, window_seconds=60)