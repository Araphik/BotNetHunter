"""
CSRF-защита для HTML-форм.

Схема: Double Submit Cookie
  1. При GET-запросе страницы генерируется случайный csrf_secret и
     сохраняется в HttpOnly-куке `csrf_secret`. Одновременно
     генерируется csrf_token = HMAC(SECRET_KEY, csrf_secret),
     который передаётся в шаблон как скрытое поле формы.
  2. При POST-запросе сервер читает csrf_secret из куки, заново
     вычисляет ожидаемый токен и сравнивает с пришедшим из формы.

Почему старый подход (IP как session key) не работал:
  - Запросы проходят через reverse proxy (nginx), и request.client.host
    на GET и POST может отличаться (реальный IP vs 127.0.0.1).
  - Токен, сгенерированный при GET, не совпадал с ожидаемым при POST.
"""
import hmac
import hashlib
import secrets

from config.settings import SECRET_KEY

CSRF_COOKIE_NAME = "csrf_secret"
CSRF_COOKIE_MAX_AGE = 3600  # 1 час


def _compute_token(csrf_secret: str) -> str:
    """Вычисляет CSRF-токен как HMAC от csrf_secret."""
    return hmac.new(
        SECRET_KEY.encode(),
        csrf_secret.encode(),
        hashlib.sha256,
    ).hexdigest()


def generate_csrf_token(request) -> tuple[str, str | None]:
    """
    Возвращает (csrf_token, csrf_secret_to_set_in_cookie).

    Если кука csrf_secret уже есть — переиспользуем её (csrf_secret_to_set_in_cookie=None).
    Если куки нет — генерируем новый секрет (нужно установить куку в ответе).
    """
    existing_secret = request.cookies.get(CSRF_COOKIE_NAME)
    if existing_secret:
        return _compute_token(existing_secret), None

    new_secret = secrets.token_hex(32)
    return _compute_token(new_secret), new_secret


def validate_csrf_token(request, form_token: str) -> bool:
    """
    Проверяет CSRF-токен из формы против куки csrf_secret.
    Использует secrets.compare_digest для защиты от тайминг-атак.
    """
    if not form_token:
        return False
    csrf_secret = request.cookies.get(CSRF_COOKIE_NAME)
    if not csrf_secret:
        return False
    expected = _compute_token(csrf_secret)
    return secrets.compare_digest(expected, form_token)