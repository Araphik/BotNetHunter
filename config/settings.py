import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

APP_VERSION = os.getenv("APP_VERSION")
if not APP_VERSION:
    version_file = BASE_DIR / "VERSION"
    if version_file.exists():
        APP_VERSION = version_file.read_text().strip()
    else:
        APP_VERSION = "1.0.0"


VK_API_VERSION = os.getenv('VK_API_VERSION', '5.131')
VK_API_URL = 'https://api.vk.com/method'
REQUEST_DELAY = float(os.getenv('REQUEST_DELAY', '0.35'))
FLOOD_WAIT = int(os.getenv('FLOOD_WAIT', '3'))
MAX_FRIENDS_ANALYZE = int(os.getenv('MAX_FRIENDS_ANALYZE', '100'))

# VK токены: VK_TOKEN_1, VK_TOKEN_2, ... из .env
VK_TOKENS = []
i = 1
while True:
    token = os.getenv(f'VK_TOKEN_{i}')
    if not token:
        break
    VK_TOKENS.append(token)
    i += 1

if not VK_TOKENS:
    raise RuntimeError("Не задан ни один VK токен. Добавьте VK_TOKEN_1 (и т.д.) в .env!")

# Auth & DB
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY не задан в .env!")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 часа
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'postgresql+psycopg://botnethunter:botnethunter@localhost:5432/botnethunter'
)

# Admin credentials
ADMIN_EMAIL = os.getenv('ADMIN_EMAIL')
ADMIN_PASSWORD = os.getenv('ADMIN_PASSWORD')
if not ADMIN_EMAIL or not ADMIN_PASSWORD:
    raise RuntimeError("ADMIN_EMAIL и ADMIN_PASSWORD должны быть заданы в .env!")

def get_app_version() -> str:
    """Динамически получает версию из окружения или файла"""
    return os.getenv("APP_VERSION") or (
        (BASE_DIR / "VERSION").read_text().strip() 
        if (BASE_DIR / "VERSION").exists() 
        else "0.0.0"
    )
