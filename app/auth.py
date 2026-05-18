from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from datetime import datetime, timedelta
from jose import jwt, JWTError
from config.settings import SECRET_KEY, ALGORITHM, ACCESS_TOKEN_EXPIRE_MINUTES, ADMIN_EMAIL, ADMIN_PASSWORD
import pyotp


ph = PasswordHasher()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        ph.verify(hashed_password, plain_password)
        return True
    except VerifyMismatchError:
        return False


def get_password_hash(password: str) -> str:
    return ph.hash(password)


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def is_admin_login(email: str, password: str) -> bool:
    return email == ADMIN_EMAIL and password == ADMIN_PASSWORD


def generate_totp_secret() -> str:
    """Генерирует криптографически стойкий Base32 секрет для TOTP"""
    return pyotp.random_base32()

def get_totp_uri(email: str, secret: str) -> str:
    """Формирует URI для привязки в Google Authenticator / Authy"""
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="BotNetHunter")

def verify_totp(secret: str, token: str) -> bool:
    """Проверяет OTP код с допустимым окном ±1 период (защита от рассинхрона)"""
    try:
        return pyotp.TOTP(secret).verify(token, valid_window=1)
    except Exception:
        return False

def create_temp_2fa_token(data: dict) -> str:
    """Создаёт временный токен для этапа 'пароль пройден, ждём OTP' (живёт 5 минут)"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=5)
    to_encode.update({"exp": expire, "step": "2fa_pending"})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def decode_temp_2fa_token(token: str) -> dict | None:
    """Декодирует временный токен 2FA"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("step") == "2fa_pending":
            return payload
        return None
    except JWTError:
        return None
    