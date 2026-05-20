import os
import json
import asyncio
import uuid
from contextvars import ContextVar
from fastapi import FastAPI, Request, Depends, Form, HTTPException, status, Query, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from sqlalchemy import func, inspect, text
from pathlib import Path
from datetime import datetime, timezone, timedelta
import hashlib
import shutil

from app.database import get_db, engine, Base, SessionLocal
from app.models import (
    User, AnalysisHistory, AdminSettings, ModuleParameter, VKToken,
    AnalyzeRequest, AnalyzeResponse, HistoryItemResponse, HistoryListResponse, APIError, Session
)
from app.auth import (
    get_password_hash, verify_password, create_access_token, decode_token, is_admin_login, generate_totp_secret,
    get_totp_uri, verify_totp, create_temp_2fa_token, decode_temp_2fa_token,
    create_admin_token, is_admin_token
)
from config.settings import BASE_DIR, APP_VERSION
from config.weights import DEFAULT_REQUESTS_LIMIT
from config.settings import ADMIN_EMAIL, ADMIN_PASSWORD
from core.token_manager import TokenManager
from api.endpoints import analyze_user, analyze_group, _normalize_target
from config.settings import get_app_version
from utils.logger import logger, request_id_var
from app.rate_limiter import rate_limiter
from app.csrf import generate_csrf_token, validate_csrf_token, CSRF_COOKIE_NAME, CSRF_COOKIE_MAX_AGE
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter
import logging

rate_limit_counter = Counter(
    'http_requests_rate_limited_total',
    'Total number of rate limited requests',
    ['path']
)

# Часовой пояс Москвы (UTC+3)
MSK_TZ = timezone(timedelta(hours=3))


def apply_schema_migrations():
    """Минимальные idempotent-миграции для существующих установок без Alembic."""
    inspector = inspect(engine)
    if not inspector.has_table("users"):
        return

    existing_columns = {column["name"] for column in inspector.get_columns("users")}
    statements = []

    if "totp_secret" not in existing_columns:
        statements.append("ALTER TABLE users ADD COLUMN totp_secret VARCHAR(32)")

    if "is_2fa_enabled" not in existing_columns:
        if engine.dialect.name == "postgresql":
            statements.append("ALTER TABLE users ADD COLUMN is_2fa_enabled BOOLEAN NOT NULL DEFAULT false")
        else:
            statements.append("ALTER TABLE users ADD COLUMN is_2fa_enabled BOOLEAN NOT NULL DEFAULT 0")

    if "avatar_path" not in existing_columns:
        statements.append("ALTER TABLE users ADD COLUMN avatar_path VARCHAR(255)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))


def to_msk_time(dt: datetime) -> datetime:
    """Конвертирует datetime в московское время (UTC+3)"""
    if dt.tzinfo is None:
        # Если время без часового пояса, считаем что это UTC
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(MSK_TZ)


def _set_csrf_cookie(response, new_secret: str | None):
    """Устанавливает куку csrf_secret в ответ, если был сгенерирован новый секрет."""
    if new_secret:
        response.set_cookie(
            CSRF_COOKIE_NAME,
            new_secret,
            httponly=True,
            max_age=CSRF_COOKIE_MAX_AGE,
            secure=True,
            samesite="lax",
        )
    return response


def csrf_template_response(request, template_name: str, context: dict):
    """
    Рендерит шаблон с csrf_token и при необходимости устанавливает куку csrf_secret.
    Использовать вместо прямого templates.TemplateResponse + generate_csrf_token.
    """
    token, new_secret = generate_csrf_token(request)
    context["csrf_token"] = token
    response = templates.TemplateResponse(request, template_name, context)
    return _set_csrf_cookie(response, new_secret)


def wants_json_response(request: Request) -> bool:
    accept = request.headers.get("accept", "")
    requested_with = request.headers.get("x-requested-with", "")
    return "application/json" in accept or requested_with == "fetch"


def _mark_analysis_failed(db: Session, record_id: int, message: str):
    record = db.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first()
    if not record:
        return
    record.status = "failed"
    record.details = json.dumps({"error": message}, ensure_ascii=False)
    record.completed_at = datetime.now(MSK_TZ)
    db.commit()


def run_analysis_job(record_id: int):
    db = SessionLocal()
    try:
        record = db.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first()
        if not record or record.status != "pending":
            return

        tm = TokenManager(db)
        if not tm.get_tokens_count():
            _mark_analysis_failed(db, record_id, "Сервис временно недоступен: нет активных VK-токенов")
            return

        user = db.query(User).filter(User.id == record.user_id).first()

        if record.target_type == "group":
            group_result = analyze_group(record.target, tm)
            if not group_result or group_result["posts_analyzed"] == 0:
                _mark_analysis_failed(db, record_id, "Не удалось получить посты группы или стена закрыта.")
                return

            record.score = None
            record.risk_level = "HIGH" if group_result["average_score"] > 60 else "MEDIUM" if group_result["average_score"] > 30 else "NORMAL"
            record.details = json.dumps(group_result.get("details", {}), ensure_ascii=False)
            record.average_score = group_result["average_score"]
            record.score_distribution = json.dumps(group_result["distribution"], ensure_ascii=False)
            record.members_analyzed = group_result["members_analyzed"]

            if user:
                user.requests_today += group_result["members_analyzed"]
                user.last_request_date = datetime.now(MSK_TZ)
        else:
            result = analyze_user(record.target, tm)
            if not result:
                _mark_analysis_failed(db, record_id, "Не удалось получить данные профиля.")
                return

            record.score = result.total_score
            record.risk_level = result.risk_level
            record.details = json.dumps({
                "reasons": result.reasons,
                "anomalies": result.anomalies,
                "profile": {
                    "id": result.user_id,
                    "screen_name": result.profile_data.screen_name if result.profile_data else "",
                },
            }, ensure_ascii=False)
            record.average_score = None
            record.score_distribution = None
            record.members_analyzed = 1

            if user:
                user.requests_today += 1
                user.last_request_date = datetime.now(MSK_TZ)

        record.status = "completed"
        record.completed_at = datetime.now(MSK_TZ)
        db.commit()
    except Exception as exc:
        logger.exception(f"Ошибка фонового анализа {record_id}: {exc}")
        db.rollback()
        _mark_analysis_failed(db, record_id, "Внутренняя ошибка анализа.")
    finally:
        db.close()


# uuid7 — если пакет не установлен, fallback на uuid4
try:
    from uuid_extensions import uuid7 as _uuid7
    def new_request_id() -> str:
        return str(_uuid7())
except ImportError:
    def new_request_id() -> str:
        return str(uuid.uuid4())

# Доверенные origin для CORS
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "https://botnethunter.duckdns.org").split(",")
    if o.strip()
]

# ИНИЦИАЛИЗАЦИЯ
app = FastAPI(
    title="BotNetHunter",
    description="Система анализа профилей ВКонтакте на наличие ботов",
    version=APP_VERSION,
    docs_url="/docs",
    redoc_url="/redoc",
)
Instrumentator(should_group_status_codes=False).instrument(app).expose(app)
Base.metadata.create_all(bind=engine)
apply_schema_migrations()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

AVATAR_DIR = os.getenv("AVATAR_STORAGE_DIR") or "/tmp/avatars"
Path(AVATAR_DIR).mkdir(parents=True, exist_ok=True)
app.mount("/avatars", StaticFiles(directory=AVATAR_DIR), name="avatars")



# Отключаем дублирующие логи от uvicorn
logging.getLogger("uvicorn.access").disabled = True

# Security scheme для API
security = HTTPBearer(auto_error=False)


@app.get("/health", tags=["Service"])
async def health():
    return {"status": "ok", "service": "botnethunter", "version": APP_VERSION}


@app.get("/api-docs", response_class=HTMLResponse, tags=["API Docs"], include_in_schema=False)
async def api_docs():
    return get_swagger_ui_html(
        openapi_url="/api-docs/openapi.yaml",
        title="BotNetHunter API Docs",
    )


@app.get("/api-docs/openapi.yaml", tags=["API Docs"], include_in_schema=False)
async def api_docs_openapi_yaml():
    return FileResponse(
        BASE_DIR / "docs" / "api" / "swagger.yaml",
        media_type="application/yaml",
        filename="swagger.yaml",
    )


# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ 
def get_user_from_cookie(request: Request, db: Session):
    token = request.cookies.get("token")
    if not token:
        return None
    email = decode_token(token)
    if not email:
        return None
    return db.query(User).filter(User.email == email).first()


def get_admin_from_cookie(request: Request) -> bool:
    admin_email = request.cookies.get("admin_email")
    admin_token = request.cookies.get("admin_token")
    return admin_email == ADMIN_EMAIL and is_admin_token(admin_token)


def get_param_value(module_name: str, param_key: str, default: int) -> int:
    try:
        db = SessionLocal()
        param = db.query(ModuleParameter).filter(
            ModuleParameter.module_name == module_name,
            ModuleParameter.param_key == param_key
        ).first()
        db.close()
        return param.param_value if param else default
    except Exception:
        return default


async def get_current_api_user(request: Request, db: Session = Depends(get_db), credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Зависимость для авторизации в API (по токену из заголовка или куки)"""
    
    # 1. Пробуем получить токен из заголовка Authorization: Bearer
    if credentials and credentials.credentials:
        email = decode_token(credentials.credentials)
        if email:
            user = db.query(User).filter(User.email == email).first()
            if user and user.is_active:
                return user
    
    # 2. Пробуем получить из куки (для совместимости с веб-интерфейсом)
    token = request.cookies.get("token")
    if token:
        email = decode_token(token)
        if email:
            user = db.query(User).filter(User.email == email).first()
            if user and user.is_active:
                return user
    
    # 3. Проверяем админа по куки
    if get_admin_from_cookie(request):
        # Для админа возвращаем "виртуального" пользователя
        class AdminUser:
            id = 0
            email = ADMIN_EMAIL
            is_admin = True
            requests_limit = 10000
            requests_today = 0
        return AdminUser()
    
    return None


# MIDDLEWARE

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """Rate limiting: не более 60 запросов в минуту с одного IP или токена."""
    path = request.url.path
    # Пропускаем health-check и статические файлы (CSS, JS, шрифты)
    if path == "/health" or path.startswith("/static/"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        rate_key = "token:" + auth_header[7:]
    else:
        forwarded_for = request.headers.get("X-Forwarded-For", "")
        rate_key = "ip:" + (forwarded_for.split(",")[0].strip() if forwarded_for else (request.client.host if request.client else "unknown"))

    allowed, remaining = rate_limiter.is_allowed(rate_key)
    if not allowed:
        rate_limit_counter.labels(path=path).inc()
        
        return JSONResponse(
            status_code=429,
            content={"detail": "Too Many Requests. Попробуйте через минуту."},
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(rate_limiter.max_requests),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Window": str(rate_limiter.window),
            },
        )
    return await call_next(request)

@app.middleware("http")
async def session_validation_middleware(request: Request, call_next):
    """Проверяет валидность сессии при каждом запросе (для деактивации сессий)"""
    if request.url.path.startswith("/static/") or request.url.path == "/health":
        return await call_next(request)
    
    token = request.cookies.get("token")
    if token:
        db = SessionLocal()
        try:
            # Проверяем, существует ли сессия в БД и активна ли она
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            session = db.query(Session).filter(
                Session.token == token_hash
            ).first()
            
            # Если сессия найдена в БД и она НЕактивна - удаляем cookie
            if session and not session.is_active:
                response = await call_next(request)
                response.delete_cookie("token")
                return response
            
        finally:
            db.close()
    
    return await call_next(request)


@app.middleware("http")
async def add_request_id_middleware(request: Request, call_next):
    """Middleware для генерации UUIDv7 request ID и логирования запросов."""
    request_id = request.headers.get("X-Request-ID") or new_request_id()

    request_id_var.set(request_id)
    logger.info(f"{request.method} {request.url.path} - started")

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(f"{request.method} {request.url.path} - completed with status {response.status_code}")

    return response


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """CSP и CORS-заголовки."""
    origin = request.headers.get("origin", "")
    if request.method == "OPTIONS" and origin and origin not in ALLOWED_ORIGINS:
        return JSONResponse(
            status_code=403,
            content={"detail": "CORS: origin not allowed"},
        )
    response = await call_next(request)
    # Content-Security-Policy
    script_src = "'self' https://cdn.jsdelivr.net"
    img_src = "'self' data: https://api.qrserver.com"
    if request.url.path.startswith("/api-docs"):
        script_src += " 'unsafe-inline'"
        img_src += " https://fastapi.tiangolo.com"

    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        f"script-src {script_src}; "
        "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
        f"img-src {img_src}; "
        "font-src 'self' https://cdn.jsdelivr.net; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    # CORS — только для явно разрешённых origin
    if origin in ALLOWED_ORIGINS:
        response.headers["Access-Control-Allow-Origin"] = origin
        response.headers["Access-Control-Allow-Credentials"] = "true"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Authorization, Content-Type, X-Request-ID"
        response.headers["Vary"] = "Origin"
    return response

@app.middleware("http")
async def add_version_to_templates(request: Request, call_next):
    request.state.app_version = get_app_version()
    response = await call_next(request)
    return response



def create_session(db: Session, user_id: int, token: str, user_agent: str = None, ip_address: str = None):
    """Создает новую сессию для пользователя"""
    session = Session(
        user_id=user_id,
        token=hashlib.sha256(token.encode()).hexdigest(),  # Храним хеш токена
        user_agent=user_agent,
        ip_address=ip_address,
        is_active=True
    )
    db.add(session)
    db.commit()
    return session

def validate_session(db: Session, token: str) -> bool:
    """Проверяет, активна ли сессия"""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    session = db.query(Session).filter(
        Session.token == token_hash,
        Session.is_active == True
    ).first()
    return session is not None

def deactivate_user_sessions(db: Session, user_id: int):
    """Деактивирует все сессии пользователя"""
    db.query(Session).filter(
        Session.user_id == user_id,
        Session.is_active == True
    ).update({"is_active": False})
    db.commit()

def deactivate_session(db: Session, session_id: int, user_id: int):
    """Деактивирует конкретную сессию"""
    session = db.query(Session).filter(
        Session.id == session_id,
        Session.user_id == user_id
    ).first()
    if session:
        session.is_active = False
        db.commit()
        return True
    return False

def get_user_sessions(db: Session, user_id: int):
    """Получает все сессии пользователя"""
    return db.query(Session).filter(
        Session.user_id == user_id
    ).order_by(Session.last_activity.desc()).all()


# HTML-ИНТЕРФЕЙС 
@app.get("/", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def home(request: Request):
    return csrf_template_response(request, "login.html", {})


@app.post("/login", tags=["Web UI"], include_in_schema=False)
async def login(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    if is_admin_login(email, password):
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie("admin_email", ADMIN_EMAIL, httponly=True, max_age=86400, secure=True, samesite="lax")
        response.set_cookie("admin_token", create_admin_token(), httponly=True, max_age=86400, secure=True, samesite="lax")
        
        return response
    
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        return csrf_template_response(request, "login.html", {"error": "Неверный email или пароль"})
    
    if not user.is_active:
        return csrf_template_response(request, "login.html", {"error": "Аккаунт заблокирован"})
    
    if user.is_2fa_enabled and user.totp_secret:
        temp_token = create_temp_2fa_token({"sub": user.email})
        response = RedirectResponse(url="/login/2fa", status_code=303)
        response.set_cookie("temp_2fa", temp_token, httponly=True, max_age=300, secure=True, samesite="lax")
        return response
    
    token = create_access_token(data={"sub": user.email})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="token", value=token, httponly=True, max_age=86400, secure=True, samesite="lax")
    create_session(db, user.id, token, request.headers.get("user-agent"), request.client.host if request.client else None)
    return response




@app.get("/register", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def register_page(request: Request):
    return csrf_template_response(request, "register.html", {})


@app.post("/register", tags=["Web UI"], include_in_schema=False)
async def register(request: Request, email: str = Form(...), password: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if db.query(User).filter(User.email == email).first():
        return csrf_template_response(request, "register.html", {"error": "Email уже зарегистрирован"})
    
    new_user = User(email=email, hashed_password=get_password_hash(password), requests_limit=DEFAULT_REQUESTS_LIMIT)
    db.add(new_user)
    db.commit()
    
    token = create_access_token(data={"sub": new_user.email})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="token", value=token, httponly=True, max_age=86400, secure=True, samesite="lax")
    create_session(db, new_user.id, token, request.headers.get("user-agent"), request.client.host if request.client else None)
    
    return response


@app.get("/logout", tags=["Web UI"], include_in_schema=False)
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("token")
    response.delete_cookie("admin_email")
    response.delete_cookie("admin_token")
    return response


@app.get("/dashboard", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    if get_admin_from_cookie(request):
        return RedirectResponse(url="/admin", status_code=303)
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")
    
    # Получаем историю и конвертируем время в московское
    history_raw = db.query(AnalysisHistory).filter(AnalysisHistory.user_id == user.id).order_by(AnalysisHistory.created_at.desc()).limit(50).all()
    history = []
    for h in history_raw:
        h.created_at_msk = to_msk_time(h.created_at) if h.created_at else None
        history.append(h)
    
    pending_analysis = next((h for h in history if h.status == "pending"), None)
    progress_percent = min(user.requests_today / max(user.requests_limit, 1) * 100, 100)
    requests_left = max(user.requests_limit - user.requests_today, 0)
    
    # Конвертируем время пользователя в московское
    user.created_at_msk = to_msk_time(user.created_at) if user.created_at else None
    
    # Формируем URL аватарки
    avatar_url = f"/avatars/{user.avatar_path}" if user.avatar_path else None
    
    token, new_secret = generate_csrf_token(request)
    response = templates.TemplateResponse(request, "dashboard.html", {
        "request": request, 
        "user": user, 
        "history": history, 
        "error": None,
        "progress_percent": progress_percent, 
        "requests_left": requests_left,
        "csrf_token": token,
        "pending_analysis": pending_analysis,
        "avatar_url": avatar_url,  # <-- Добавляем avatar_url
    })
    _set_csrf_cookie(response, new_secret)
    response.headers["X-RateLimit-Limit"] = str(user.requests_limit)
    response.headers["X-RateLimit-Remaining"] = str(requests_left)
    reset_time = to_msk_time(datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))
    response.headers["X-RateLimit-Reset"] = str(int(reset_time.timestamp()))
    return response
    

@app.post("/analyze", tags=["Web UI"], include_in_schema=False)
async def analyze_web(
    request: Request,
    background_tasks: BackgroundTasks,
    target: str = Form(...),
    target_type: str = Form("user"),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    """HTML-интерфейс: быстро ставит анализ в очередь и запускает его в фоне."""
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if get_admin_from_cookie(request):
        user = None
    else:
        user = get_user_from_cookie(request, db)
        if not user:
            if wants_json_response(request):
                return JSONResponse({"detail": "Требуется авторизация"}, status_code=401)
            return RedirectResponse(url="/")
        
        # Конвертируем текущее время в московское для сравнения дат
        today = to_msk_time(datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
        if user.last_request_date is None or to_msk_time(user.last_request_date).date() < today.date():
            user.requests_today = 0
            user.last_request_date = datetime.now(MSK_TZ)
        
        if user.requests_today >= user.requests_limit:
            if wants_json_response(request):
                return JSONResponse(
                    {"detail": f"Лимит исчерпан ({user.requests_today}/{user.requests_limit})."},
                    status_code=429,
                    headers={"Retry-After": "86400"},
                )
            history = db.query(AnalysisHistory).filter(AnalysisHistory.user_id == user.id).order_by(AnalysisHistory.created_at.desc()).limit(50).all()
            token, new_secret = generate_csrf_token(request)
            response = templates.TemplateResponse(request, "dashboard.html", {
                "request": request, "user": user, "history": history,
                "error": f"Лимит исчерпан ({user.requests_today}/{user.requests_limit}).",
                "progress_percent": 100, "requests_left": 0,
                "csrf_token": token,
            })
            _set_csrf_cookie(response, new_secret)
            response.status_code = 429
            response.headers["Retry-After"] = "86400"
            response.headers["X-RateLimit-Limit"] = str(user.requests_limit)
            response.headers["X-RateLimit-Remaining"] = "0"
            reset_time = to_msk_time(today + timedelta(days=1))
            response.headers["X-RateLimit-Reset"] = str(int(reset_time.timestamp()))
            return response

    tm = TokenManager(db)
    if not tm.get_tokens_count():
        if wants_json_response(request):
            return JSONResponse({"detail": "Ошибка: Сервис временно недоступен"}, status_code=503)
        return csrf_template_response(request, "dashboard.html", {
            "user": user, "history": [],
            "error": "Ошибка: Сервис временно недоступен",
            "progress_percent": 0, "requests_left": max(user.requests_limit - user.requests_today, 0) if user else 0,
        })

    pending = None
    if user:
        pending = db.query(AnalysisHistory).filter(
            AnalysisHistory.user_id == user.id,
            AnalysisHistory.status == "pending",
        ).order_by(AnalysisHistory.created_at.desc()).first()
    if pending:
        if wants_json_response(request):
            return JSONResponse({"id": pending.id, "status": pending.status}, status_code=202)
        return RedirectResponse(url="/dashboard", status_code=303)

    target = target.strip()
    target_type = target_type if target_type in {"user", "group"} else "user"
    record = AnalysisHistory(
        user_id=user.id if user else 0,
        target=_normalize_target(target),
        target_type=target_type,
        status="pending",
        details=json.dumps({"message": "Анализ выполняется"}, ensure_ascii=False),
        created_at=datetime.now(MSK_TZ),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    background_tasks.add_task(run_analysis_job, record.id)

    if wants_json_response(request):
        return JSONResponse({"id": record.id, "status": record.status}, status_code=202)
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/analysis/status/{record_id}", tags=["Web UI"], include_in_schema=False)
async def analysis_status(record_id: int, request: Request, db: Session = Depends(get_db)):
    if get_admin_from_cookie(request):
        record = db.query(AnalysisHistory).filter(AnalysisHistory.id == record_id).first()
    else:
        user = get_user_from_cookie(request, db)
        if not user:
            return JSONResponse({"detail": "Требуется авторизация"}, status_code=401)
        record = db.query(AnalysisHistory).filter(
            AnalysisHistory.id == record_id,
            AnalysisHistory.user_id == user.id,
        ).first()
    if not record:
        return JSONResponse({"detail": "Запись не найдена"}, status_code=404)

    payload = {
        "id": record.id,
        "status": record.status,
        "result_url": f"/history/{record.id}" if record.status == "completed" else None,
    }
    return JSONResponse(
        payload,
        headers={
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.get("/history/{history_id}", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def history_detail(request: Request, history_id: int, db: Session = Depends(get_db)):
    if get_admin_from_cookie(request):
        record = db.query(AnalysisHistory).filter(AnalysisHistory.id == history_id).first()
    else:
        user = get_user_from_cookie(request, db)
        if not user:
            return RedirectResponse(url="/")
        record = db.query(AnalysisHistory).filter(AnalysisHistory.id == history_id, AnalysisHistory.user_id == user.id).first()
    if not record:
        return RedirectResponse(url="/dashboard")
    
    # Конвертируем время записи в московское для отображения
    record.created_at_msk = to_msk_time(record.created_at) if record.created_at else None
    
    return templates.TemplateResponse(request, "history_detail.html", {
        "request": request,
        "user": None if get_admin_from_cookie(request) else get_user_from_cookie(request, db),
        "record": record, 
        "details": json.loads(record.details) if record.details else {}
    })


@app.get("/account/change-password", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")
    return csrf_template_response(request, "change_password.html", {
        "request": request,
        "user": user,
        "error": None,
    })


@app.post("/account/change-password", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def change_password(
    request: Request,
    current_password: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db),
):
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")

    def render_error(message: str):
        return csrf_template_response(request, "change_password.html", {
            "user": user,
            "error": message,
        })

    if not verify_password(current_password, user.hashed_password):
        return render_error("Текущий пароль указан неверно")
    if new_password != confirm_password:
        return render_error("Новый пароль и повтор пароля не совпадают")
    if len(new_password) < 8:
        return render_error("Новый пароль должен быть не короче 8 символов")
    if current_password == new_password:
        return render_error("Новый пароль должен отличаться от текущего")

    user.hashed_password = get_password_hash(new_password)
    db.commit()

    return RedirectResponse(url="/dashboard", status_code=303)


@app.post("/account/delete", tags=["Web UI"], include_in_schema=False)
async def delete_account(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")
    db.query(AnalysisHistory).filter(AnalysisHistory.user_id == user.id).delete()
    db.delete(user)
    db.commit()
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("token")
    return response

@app.post("/account/avatar/upload", tags=["Web UI"], include_in_schema=False)
async def upload_avatar(
    request: Request,
    file: UploadFile = File(...),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
        
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")

    MAX_AVATAR_SIZE = 100 * 1024 * 1024  # 100MB
    
    # Проверяем размер файла ЧЕРЕЗ Content-Length header ДО чтения
    content_length = request.headers.get("content-length")
    if content_length:
        # Вычитаем размер других полей формы (примерно 1-2KB)
        estimated_file_size = int(content_length) - 2048
        if estimated_file_size > MAX_AVATAR_SIZE:
            logger.warning(
                f"Пользователь {user.email} попытался загрузить файл "
                f"размером {estimated_file_size / 1024 / 1024:.2f}MB (лимит {MAX_AVATAR_SIZE / 1024 / 1024}MB)"
            )
            return RedirectResponse(
                url="/dashboard?error=file_too_large",
                status_code=303
            )

    # Базовая проверка типа файла
    if not file.content_type or not file.content_type.startswith("image/"):
        return RedirectResponse(url="/dashboard?error=invalid_file_type", status_code=303)

    # Используем директорию для аватарок
    AVATAR_DIR = os.getenv("AVATAR_STORAGE_DIR") or "/tmp/avatars"
    avatar_path = Path(AVATAR_DIR)
    avatar_path.mkdir(parents=True, exist_ok=True)

    # Генерируем безопасное уникальное имя файла
    _, ext = os.path.splitext(file.filename or ".jpg")
    ext = ext.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".gif", ".webp"):
        ext = ".jpg"
    unique_name = f"avatar_{user.id}_{uuid.uuid4().hex}{ext}"
    file_path = avatar_path / unique_name

    try:
        # Сохраняем файл потоково с проверкой размера
        total_size = 0
        with file_path.open("wb") as buffer:
            while True:
                chunk = await file.read(1024 * 1024)  # Читаем по 1MB
                if not chunk:
                    break
                total_size += len(chunk)
                
                # Проверяем лимит во время загрузки
                if total_size > MAX_AVATAR_SIZE:
                    buffer.close()
                    file_path.unlink(missing_ok=True)
                    logger.warning(
                        f"Файл {unique_name} превысил лимит: {total_size / 1024 / 1024:.2f}MB"
                    )
                    return RedirectResponse(
                        url="/dashboard?error=file_too_large",
                        status_code=303
                    )
                
                buffer.write(chunk)
                
    except Exception as e:
        logger.error(f"Ошибка сохранения аватара: {e}")
        if file_path.exists():
            file_path.unlink(missing_ok=True)
        return RedirectResponse(url="/dashboard?error=upload_failed", status_code=303)

    # Удаляем старую аватарку, если она была
    old_avatar = user.avatar_path
    if old_avatar:
        old_path = avatar_path / old_avatar
        if old_path.exists():
            try:
                os.remove(old_path)
            except OSError:
                pass

    # Обновляем путь в БД (храним только имя файла)
    user.avatar_path = unique_name
    db.commit()

    logger.info(
        f"Пользователь {user.email} загрузил аватар {unique_name} "
        f"({total_size / 1024 / 1024:.2f}MB)"
    )
    return RedirectResponse(url="/dashboard?avatar_updated=1", status_code=303)

# REST API 
@app.post("/api/analyze", response_model=AnalyzeResponse, tags=["API"], status_code=status.HTTP_201_CREATED)
async def analyze_api(
    request_data: AnalyzeRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user)
):
    """ Анализ профиля или группы ВКонтакте """
    if not current_user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    # Проверка лимитов для обычных пользователей
    if current_user and not hasattr(current_user, 'is_admin'):
        today = to_msk_time(datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
        if current_user.last_request_date is None or to_msk_time(current_user.last_request_date).date() < today.date():
            current_user.requests_today = 0
            current_user.last_request_date = datetime.now(MSK_TZ)
        
        if current_user.requests_today >= current_user.requests_limit:
            raise HTTPException(
                status_code=429,
                detail=f"Лимит исчерпан ({current_user.requests_today}/{current_user.requests_limit}). Попробуйте завтра.",
                headers={"Retry-After": "86400"}
            )
    
    # Проверка токенов
    tm = TokenManager(db)
    if not tm.get_tokens_count():
        raise HTTPException(status_code=503, detail="Сервис временно недоступен: нет активных токенов")
    
    try:
        if request_data.target_type == "group":
            remaining_limit = getattr(current_user, 'requests_limit', 10000) - getattr(current_user, 'requests_today', 0) if current_user and not hasattr(current_user, 'is_admin') else 10000
            
            group_result = analyze_group(request_data.target, tm)
            
            if not group_result or group_result["posts_analyzed"] == 0:
                raise HTTPException(status_code=400, detail="Не удалось получить посты группы или стена закрыта")
            
            members_analyzed = group_result["members_analyzed"]
            if current_user and not hasattr(current_user, 'is_admin'):
                current_user.requests_today += members_analyzed
                db.commit()
            
            details_json = json.dumps(group_result.get("details", {}), ensure_ascii=False)
            
            record = AnalysisHistory(
                user_id=getattr(current_user, 'id', 0) if current_user else 0,
                target=_normalize_target(request_data.target),
                target_type="group",
                score=None,
                risk_level="HIGH" if group_result["average_score"] > 60 else "MEDIUM" if group_result["average_score"] > 30 else "NORMAL",
                details=details_json,
                average_score=group_result["average_score"],
                score_distribution=json.dumps(group_result["distribution"], ensure_ascii=False),
                members_analyzed=members_analyzed,
                created_at=datetime.now(MSK_TZ)
            )
            
            db.add(record)
            db.commit()
            db.refresh(record)
            
            return AnalyzeResponse(
                id=record.id,
                target=record.target,
                target_type=record.target_type,
                score=None,
                risk_level=record.risk_level,
                average_score=record.average_score,
                members_analyzed=record.members_analyzed,
                details=json.loads(record.details),
                created_at=record.created_at
            )
            
        else:
            result = analyze_user(request_data.target, tm)
            if not result:
                raise HTTPException(status_code=400, detail="Не удалось получить данные профиля")
            
            details = {"reasons": result.reasons, "anomalies": result.anomalies, "profile": {"id": result.user_id, "screen_name": result.profile_data.screen_name if result.profile_data else ""}}
            record = AnalysisHistory(
                user_id=getattr(current_user, 'id', 0) if current_user else 0,
                target=_normalize_target(request_data.target),
                target_type="user",
                score=result.total_score,
                risk_level=result.risk_level,
                details=json.dumps(details, ensure_ascii=False),
                average_score=None,
                score_distribution=None,
                members_analyzed=1,
                created_at=datetime.now(MSK_TZ)
            )
            
            if current_user and not hasattr(current_user, 'is_admin'):
                current_user.requests_today += 1
                db.commit()
            
            db.add(record)
            db.commit()
            db.refresh(record)
            
            return AnalyzeResponse(
                id=record.id,
                target=record.target,
                target_type=record.target_type,
                score=record.score,
                risk_level=record.risk_level,
                average_score=None,
                members_analyzed=1,
                details=json.loads(record.details),
                created_at=record.created_at
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API analyze error: {e}")
        raise HTTPException(status_code=500, detail="Внутренняя ошибка сервера")


@app.get("/api/history", response_model=HistoryListResponse, tags=["API"])
async def history_api(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user)
):
    """Получение истории анализов текущего пользователя """

    if not current_user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    if hasattr(current_user, 'is_admin'):
        return HistoryListResponse(items=[], total=0)
    
    total = db.query(AnalysisHistory).filter(AnalysisHistory.user_id == current_user.id).count()
    items = db.query(AnalysisHistory).filter(
        AnalysisHistory.user_id == current_user.id
    ).order_by(
        AnalysisHistory.created_at.desc()
    ).offset(offset).limit(limit).all()
    
    return HistoryListResponse(
        items=[
            HistoryItemResponse(
                id=item.id,
                target=item.target,
                target_type=item.target_type,
                score=item.score,
                risk_level=item.risk_level,
                created_at=to_msk_time(item.created_at) if item.created_at else item.created_at
            ) for item in items
        ],
        total=total
    )


@app.get("/api/history/{history_id}", response_model=AnalyzeResponse, tags=["API"])
async def history_detail_api(
    history_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_api_user)
):
    """Получение детальной информации по конкретной записи истории"""
    if not current_user:
        raise HTTPException(status_code=401, detail="Требуется авторизация")
    
    if hasattr(current_user, 'is_admin'):
        record = db.query(AnalysisHistory).filter(AnalysisHistory.id == history_id).first()
    else:
        record = db.query(AnalysisHistory).filter(
            AnalysisHistory.id == history_id,
            AnalysisHistory.user_id == current_user.id
        ).first()
    
    if not record:
        raise HTTPException(status_code=404, detail="Запись не найдена")
    
    return AnalyzeResponse(
        id=record.id,
        target=record.target,
        target_type=record.target_type,
        score=record.score,
        risk_level=record.risk_level,
        average_score=record.average_score,
        members_analyzed=record.members_analyzed,
        details=json.loads(record.details),
        created_at=to_msk_time(record.created_at) if record.created_at else record.created_at
    )


@app.get("/api/version", response_model=dict[str, str], tags=["API"])
async def get_version_api():
    return {
        "version": APP_VERSION,
        "api_version": f"v{os.getenv('VK_API_VERSION', '5.131')}",
        "build": os.getenv("BUILD_NUMBER", "local")
    }


# АДМИН-ПАНЕЛЬ 
@app.get("/admin", response_class=HTMLResponse, tags=["Admin UI"], include_in_schema=False)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    # Конвертируем время для фильтров в московское
    today_msk = to_msk_time(datetime.utcnow()).replace(hour=0, minute=0, second=0, microsecond=0)
    
    stats = {
        "total_users": db.query(User).count(),
        "new_users_today": db.query(User).filter(User.created_at >= today_msk).count(),
        "requests_today": db.query(AnalysisHistory).filter(AnalysisHistory.created_at >= today_msk).count(),
        "total_requests": db.query(AnalysisHistory).count(),
        "avg_risk_score": int(db.query(func.avg(AnalysisHistory.score)).scalar() or 0),
        "api_load": 0,
        "active_tokens": TokenManager(db).get_tokens_count(),
    }
    recent = db.query(AnalysisHistory, User.email).join(User).order_by(AnalysisHistory.created_at.desc()).limit(10).all()
    recent_activities = [{"created_at": to_msk_time(h.created_at), "user_email": email, "action": "analyze", "target": h.target, "risk_level": h.risk_level} for h, email in recent]
    return templates.TemplateResponse(request, "admin_dashboard.html", {"request": request, "stats": stats, "recent_activities": recent_activities})


DEFAULT_SETTINGS = {
    # Основные параметры анализа
    'risk_coefficient': 5,
    'posts_limit': 100,
    
    # Параметры анализа и выявления нарушений
    'similarity_threshold': 0.85,
    'count_repetitive': 3,
    'rapid_comment_window_min': 3,
    'comments_per_time_window': 5,
    'regular_interval_tolerance_sec': 10,
    'min_interval_for_regular_check': 30,
    'percent_liked': 80,
    'cross_user_min_group_size': 3,
    'new_acc_activity': 15,
    'new_acc_id_threshold': 850_000_000,
    
    # Штрафы за активность под публикациями
    'penalty_mass_likes': 10, 'penalty_repetitive': 12, 'penalty_generic': 8, 'penalty_rapid': 15,
    'penalty_regular': 10, 'penalty_night': 8, 'penalty_new_acc': 10, 'penalty_coordination': 15, 'penalty_global_spam': 20,
    
    # Штрафы за публикации группы
    'penalty_high_freq': 12, 'penalty_repetitive_content': 18, 'penalty_link_spam': 10,
    'penalty_night_posting': 10, 'penalty_caps': 8,
    
    # Штрафы за профиль нарушителя
    'penalty_prof_new_2024': 20, 'penalty_prof_new_2022': 12, 'penalty_prof_no_photo': 18,
    'penalty_prof_empty_0': 25, 'penalty_prof_empty_1': 15, 'penalty_prof_0_friends': 10,
    'penalty_prof_low_friends': 5, 'penalty_prof_geo_anomaly': 20, 'penalty_prof_bot_name': 15
}

def _get_admin_settings(db: Session):
    """Получает текущие настройки из БД для отображения в админ-панели"""
    current = dict(DEFAULT_SETTINGS)
    try:
        db_settings = db.query(AdminSettings).filter(AdminSettings.key.like('setting_%')).all()
        logger.debug(f"Найдено {len(db_settings)} записей настроек в БД")
        for s in db_settings:
            # Убираем префикс для маппинга на DEFAULT_SETTINGS
            key = s.key.replace('setting_', '')
            if key in current:
                try:
                    val = float(s.value)
                    # Если это порог схожести, умножаем на 100 для UI
                    if key == 'similarity_threshold':
                        val = val * 100
                    current[key] = val
                    logger.debug(f"Настройка {key} = {val} (из БД: '{s.value}')")
                except ValueError as e:
                    logger.warning(f"Не удалось преобразовать '{s.value}' для настройки {key}: {e}")
                except Exception as e:
                    logger.warning(f"Ошибка обработки настройки {key}: {e}")
            else:
                logger.debug(f"Настройка {key} не в DEFAULT_SETTINGS, пропущена")
    except Exception as e:
        logger.error(f"Ошибка получения настроек из БД: {e}")
    return current

@app.get("/admin/settings", response_class=HTMLResponse, tags=["Admin UI"], include_in_schema=False)
async def admin_settings_page(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    settings = _get_admin_settings(db)
    return csrf_template_response(request, "admin_settings.html", {"request": request, "settings": settings})

@app.post("/admin/settings/save", tags=["Admin UI"], include_in_schema=False)
async def admin_settings_save(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    form_data = await request.form()
    csrf_token = form_data.get("csrf_token", "")
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    saved_count = 0
    for key, default_val in DEFAULT_SETTINGS.items():
        val = form_data.get(key)
        # Проверяем, что значение не None и не пустая строка
        if val is not None and val.strip() != "":
            try:
                val_float = float(val.strip())
                # Если это порог схожести, делим на 100 для хранения (0.85)
                if key == 'similarity_threshold':
                    val_float = val_float / 100.0
                db_key = f"setting_{key}"
                setting = db.query(AdminSettings).filter(AdminSettings.key == db_key).first()
                if setting:
                    setting.value = str(val_float)
                    logger.debug(f"Обновлена настройка {db_key} = {val_float}")
                else:
                    db.add(AdminSettings(key=db_key, value=str(val_float)))
                    logger.debug(f"Добавлена настройка {db_key} = {val_float}")
                saved_count += 1
            except ValueError as e:
                logger.warning(f"Не удалось преобразовать значение для {key}: '{val}', ошибка: {e}")
            except Exception as e:
                logger.error(f"Ошибка сохранения настройки {key}: {e}")
    
    try:
        db.commit()
        logger.info(f"Сохранено {saved_count} настроек в БД")
    except Exception as e:
        logger.error(f"Ошибка настроек: {e}")
        db.rollback()
        return RedirectResponse(url="/admin/settings?error=1", status_code=303)
    
    return RedirectResponse(url="/admin/settings?saved=1", status_code=303)




@app.get("/admin/users", response_class=HTMLResponse, tags=["Admin UI"], include_in_schema=False)
async def admin_users(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin_users.html", {"request": request, "users": users, "global_limit": DEFAULT_REQUESTS_LIMIT})


@app.get("/admin/sessions", response_class=HTMLResponse, tags=["Admin UI"], include_in_schema=False)
async def admin_sessions(request: Request, db: Session = Depends(get_db)):
    """Показывает все активные сессии"""
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    sessions = db.query(Session).join(User).filter(
        Session.is_active == True
    ).order_by(Session.last_activity.desc()).all()
    
    return templates.TemplateResponse(request, "admin_sessions.html", {
        "request": request,
        "sessions": sessions
    })

@app.post("/admin/sessions/{session_id}/deactivate", tags=["Admin UI"], include_in_schema=False)
async def admin_deactivate_session(request: Request, session_id: int, db: Session = Depends(get_db)):
    """Деактивирует сессию"""
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    session = db.query(Session).filter(Session.id == session_id).first()
    if session:
        session.is_active = False
        db.commit()
    
    return RedirectResponse(url="/admin/sessions", status_code=303)

@app.post("/admin/users/{user_id}/deactivate-sessions", tags=["Admin UI"], include_in_schema=False)
async def admin_deactivate_user_sessions(request: Request, user_id: int, db: Session = Depends(get_db)):
    """Деактивирует все сессии пользователя"""
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    deactivate_user_sessions(db, user_id)
    return RedirectResponse(url="/admin/users", status_code=303)

@app.post("/admin/users/{user_id}/block", tags=["Admin UI"], include_in_schema=False)
async def admin_user_block(request: Request, user_id: int, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    user = db.query(User).filter(User.id == user_id).first()
    if user and not user.is_admin:
        user.is_active = False
        # Деактивируем все сессии пользователя
        deactivate_user_sessions(db, user_id)
        db.commit()
    
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/unblock", tags=["Admin UI"], include_in_schema=False)
async def admin_user_unblock(request: Request, user_id: int, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = True
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/limit", tags=["Admin UI"], include_in_schema=False)
async def admin_user_limit(request: Request, user_id: int, new_limit: int = Form(...), db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if user and not user.is_admin:
        user.requests_limit = max(1, min(10000, new_limit))
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/settings/global_limit", tags=["Admin UI"], include_in_schema=False)
async def admin_global_limit(request: Request, global_limit: int = Form(...), db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    setting = db.query(AdminSettings).filter(AdminSettings.key == "global_requests_limit").first()
    if setting:
        setting.value = str(global_limit)
    else:
        db.add(AdminSettings(key="global_requests_limit", value=str(global_limit)))
    db.commit()
    return RedirectResponse(url="/admin/users?limit_updated=1", status_code=303)


@app.get("/admin/metrics", response_class=HTMLResponse, tags=["Admin UI"], include_in_schema=False)
async def admin_metrics(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    from app.metrics import collect_system_metrics, get_recent_metrics
    
    current = collect_system_metrics(db)
    history = get_recent_metrics(db, hours=24)
    
    from sqlalchemy import func, text
    
    # Конвертируем время в московское для группировки по часам
    hourly_stats = db.query(
        func.strftime('%H', func.datetime(AnalysisHistory.created_at, '+3 hours')).label('hour'),
        func.count(AnalysisHistory.id).label('count')
    ).filter(
        AnalysisHistory.created_at >= datetime.now(MSK_TZ) - timedelta(hours=24)
    ).group_by('hour').order_by('hour').all()
    
    activity_hours = [f"{h:02d}:00" for h in range(24)]
    activity_map = {row.hour.zfill(2): row.count for row in hourly_stats if row.hour}
    activity_values = [activity_map.get(f"{h:02d}", 0) for h in range(24)]

    history_records = db.query(AnalysisHistory).order_by(
        AnalysisHistory.created_at.desc()
    ).limit(10).all()
    
    recent_errors = []
    
    metrics_display = {
        "cpu_percent": round(current.get("cpu_percent", 0), 1),
        "memory_percent": round(current.get("memory_percent", 0), 1),
        "disk_percent": round(current.get("disk_percent", 0), 1),
        "active_tokens": TokenManager(db).get_tokens_count(),
        "api_requests_per_min": history.get("api_requests_per_min", 0),
        "api_errors": history.get("api_errors", 0),
        "api_avg_response_ms": 0,
        "users_online": 0,
        "requests_today": db.query(AnalysisHistory).filter(
            AnalysisHistory.created_at >= datetime.now(MSK_TZ).replace(hour=0)
        ).count(),
        "new_users_24h": db.query(User).filter(
            User.created_at >= datetime.now(MSK_TZ) - timedelta(days=1)
        ).count(),
        "blocked_users": db.query(User).filter(User.is_active == False).count(),
    }
    
    return templates.TemplateResponse(
        request, "admin_metrics.html",
        {
            "request": request,
            "metrics": metrics_display,
            "activity_hours": activity_hours,
            "activity_values": activity_values,
            "recent_errors": recent_errors,
            "history_records": history_records,
        }
    )


# УПРАВЛЕНИЕ VK ТОКЕНАМИ (через панель админа)
@app.get("/admin/tokens", response_class=HTMLResponse, tags=["Admin UI"], include_in_schema=False)
async def admin_tokens(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    tokens = db.query(VKToken).order_by(VKToken.created_at.desc()).all()
    return csrf_template_response(request, "admin_tokens.html", {
        "request": request,
        "tokens": tokens,
        "active_count": sum(1 for t in tokens if t.is_active),
    })


@app.post("/admin/tokens/add", tags=["Admin UI"], include_in_schema=False)
async def admin_tokens_add(
    request: Request, 
    token: str = Form(...), 
    description: str = Form(None),
    csrf_token: str = Form(...),
    db: Session = Depends(get_db)
):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    
    existing = db.query(VKToken).filter(VKToken.token == token).first()
    if existing:
        return RedirectResponse(url="/admin/tokens?error=duplicate", status_code=303)
    
    new_token = VKToken(token=token, description=description, is_active=True)
    db.add(new_token)
    db.commit()
    
    TokenManager._instance = None
    
    return RedirectResponse(url="/admin/tokens?success=1", status_code=303)


@app.post("/admin/tokens/{token_id}/delete", tags=["Admin UI"], include_in_schema=False)
async def admin_tokens_delete(request: Request, token_id: int, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    token = db.query(VKToken).filter(VKToken.id == token_id).first()
    if token:
        db.delete(token)
        db.commit()
        TokenManager._instance = None
    
    return RedirectResponse(url="/admin/tokens", status_code=303)


@app.post("/admin/tokens/{token_id}/toggle", tags=["Admin UI"], include_in_schema=False)
async def admin_tokens_toggle(request: Request, token_id: int, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    token = db.query(VKToken).filter(VKToken.id == token_id).first()
    if token:
        token.is_active = not token.is_active
        db.commit()
        TokenManager._instance = None
    
    return RedirectResponse(url="/admin/tokens", status_code=303)


@app.get("/version", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def version_page(request: Request):
    from config.settings import APP_VERSION, VK_API_VERSION, BASE_DIR
    
    version_file = BASE_DIR / "VERSION"
    build_date = "—"
    if version_file.exists():
        mtime = os.path.getmtime(version_file)
        build_date = datetime.fromtimestamp(mtime, tz=MSK_TZ).strftime("%d.%m.%Y %H:%M MSK")
    
    return templates.TemplateResponse(
        request, "version.html",
        {
            "request": request,
            "version": APP_VERSION,
            "api_version": f"v{VK_API_VERSION}",
            "build_date": build_date
        }
    )
    

@app.get("/login/2fa", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def login_2fa_page(request: Request):
    temp = request.cookies.get("temp_2fa")
    if not temp or not decode_temp_2fa_token(temp):
        return RedirectResponse(url="/", status_code=303)
    return csrf_template_response(request, "login_2fa.html", {"request": request})

@app.post("/login/2fa", tags=["Web UI"], include_in_schema=False)
async def login_2fa_verify(request: Request, otp_code: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    temp = request.cookies.get("temp_2fa")
    payload = decode_temp_2fa_token(temp) if temp else None
    if not payload:
        return RedirectResponse(url="/", status_code=303)
    user = db.query(User).filter(User.email == payload["sub"]).first()
    if not user or not verify_totp(user.totp_secret, otp_code):
        return csrf_template_response(request, "login_2fa.html", {"error": "Неверный код подтверждения"})
    
    token = create_access_token(data={"sub": user.email})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="token", value=token, httponly=True, max_age=86400, secure=True, samesite="lax")
    response.delete_cookie("temp_2fa")
    
    create_session(db, user.id, token, request.headers.get("user-agent"), request.client.host if request.client else None)
    
    return response

@app.get("/account/2fa/setup", response_class=HTMLResponse, tags=["Web UI"], include_in_schema=False)
async def setup_2fa_page(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")
    if user.is_2fa_enabled:
        return RedirectResponse(url="/dashboard")
        
    secret = generate_totp_secret()
    # Используем только имя пользователя (без @domain) для лучшего совместимости
    account_name = user.email.split('@')[0] if '@' in user.email else user.email
    uri = get_totp_uri(account_name, secret)
    # QR через публичный API - правильно кодируем URL
    import urllib.parse
    qr_data = urllib.parse.quote(uri, safe='')
    qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={qr_data}"
    
    return csrf_template_response(request, "setup_2fa.html", {
        "request": request, 
        "user": user, 
        "qr_url": qr_url, 
        "uri": uri, 
        "temp_secret": secret,
        "secret_key": secret  # Добавляем чистый секрет для отображения
    })

@app.post("/account/2fa/enable", tags=["Web UI"], include_in_schema=False)
async def enable_2fa(request: Request, otp_code: str = Form(...), temp_secret: str = Form(...), csrf_token: str = Form(...), db: Session = Depends(get_db)):
    if not validate_csrf_token(request, csrf_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
        
    user = get_user_from_cookie(request, db)
    if not user or user.is_2fa_enabled:
        return RedirectResponse(url="/dashboard")
        
    if not verify_totp(temp_secret, otp_code):
        import urllib.parse
        account_name = user.email.split('@')[0] if '@' in user.email else user.email
        uri = get_totp_uri(account_name, temp_secret)
        qr_data = urllib.parse.quote(uri, safe='')
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={qr_data}"
        
        return csrf_template_response(request, "setup_2fa.html", {
            "request": request, 
            "user": user, 
            "error": "Неверный код. Попробуйте снова.", 
            "qr_url": qr_url, 
            "uri": uri, 
            "temp_secret": temp_secret,
            "secret_key": temp_secret
        })
        
    user.totp_secret = temp_secret
    user.is_2fa_enabled = True
    db.commit()
    return RedirectResponse(url="/dashboard?2fa_enabled=1", status_code=303)

@app.post("/account/2fa/disable", tags=["Web UI"], include_in_schema=False)
async def disable_2fa(request: Request, db: Session = Depends(get_db)):
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")
    user.totp_secret = None
    user.is_2fa_enabled = False
    db.commit()
    return RedirectResponse(url="/dashboard?2fa_disabled=1", status_code=303)

@app.on_event("startup")
async def startup_event():
    import asyncio
    from app.background import run_background_tasks

    asyncio.create_task(run_background_tasks())
    print("Фоновый сбор метрик запущен")
