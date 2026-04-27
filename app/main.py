import os
import json
import asyncio
from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from pathlib import Path
from datetime import datetime, timezone, timedelta


from app.database import get_db, engine, Base, SessionLocal
from app.models import User, AnalysisHistory, AdminSettings, ModuleParameter
from app.auth import (
    get_password_hash, verify_password, create_access_token, decode_token, is_admin_login
)
from config.settings import BASE_DIR, APP_VERSION
from config.weights import DEFAULT_REQUESTS_LIMIT, DEFAULT_MODULE_WEIGHTS
from config.settings import ADMIN_EMAIL, ADMIN_PASSWORD
from core.token_manager import TokenManager
from api.endpoints import analyze_user, analyze_group
from config.settings import get_app_version

# ИНИЦИАЛИЗАЦИЯ
app = FastAPI(title="BotNetHunter")
Base.metadata.create_all(bind=engine)
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")




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
    return (
        admin_email == ADMIN_EMAIL and 
        admin_token == "admin_session"
    )


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


@app.middleware("http")
async def add_version_to_templates(request: Request, call_next):
    """Добавляет версию в каждый запрос"""
    request.state.app_version = get_app_version()
    response = await call_next(request)
    return response

# АВТОРИЗАЦИЯ 
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.post("/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if is_admin_login(email, password):
        response = RedirectResponse(url="/admin", status_code=303)
        response.set_cookie("admin_email", ADMIN_EMAIL, httponly=True, max_age=86400, secure=True, samesite="lax")
        response.set_cookie("admin_token", "admin_session", httponly=True, max_age=86400, secure=True, samesite="lax")
        return response
    
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Неверный email или пароль"})
    if not user.is_active:
        return templates.TemplateResponse(request, "login.html", {"request": request, "error": "Аккаунт заблокирован"})
    
    token = create_access_token(data={"sub": user.email})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="token", value=token, httponly=True, max_age=86400, secure=True, samesite="lax")
    return response


@app.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse(request, "register.html")


@app.post("/register")
async def register(request: Request, email: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(request, "register.html", {"request": request, "error": "Email уже зарегистрирован"})
    new_user = User(email=email, hashed_password=get_password_hash(password), requests_limit=DEFAULT_REQUESTS_LIMIT)
    db.add(new_user)
    db.commit()
    token = create_access_token(data={"sub": new_user.email})
    response = RedirectResponse(url="/dashboard", status_code=303)
    response.set_cookie(key="token", value=token, httponly=True, max_age=86400, secure=True, samesite="lax")
    return response


@app.get("/logout")
async def logout():
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie("token")
    response.delete_cookie("admin_email")
    response.delete_cookie("admin_token")
    return response


# ПОЛЬЗОВАТЕЛЬСКИЙ ИНТЕРФЕЙС 
@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    if get_admin_from_cookie(request):
        return RedirectResponse(url="/admin", status_code=303)
    user = get_user_from_cookie(request, db)
    if not user:
        return RedirectResponse(url="/")
    history = db.query(AnalysisHistory).filter(AnalysisHistory.user_id == user.id).order_by(AnalysisHistory.created_at.desc()).limit(50).all()
    progress_percent = min(user.requests_today / max(user.requests_limit, 1) * 100, 100)
    requests_left = max(user.requests_limit - user.requests_today, 0)
    
    response = templates.TemplateResponse(request, "dashboard.html", {
        "request": request, "user": user, "history": history, "error": None,
        "progress_percent": progress_percent, "requests_left": requests_left,
    })
    
    # Добавляем заголовки rate limit
    response.headers["X-RateLimit-Limit"] = str(user.requests_limit)
    response.headers["X-RateLimit-Remaining"] = str(requests_left)
    response.headers["X-RateLimit-Reset"] = str(int((datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp()))
    
    return response
    

@app.post("/analyze")
async def analyze(request: Request, target: str = Form(...), target_type: str = Form("user"), db: Session = Depends(get_db)):
    # Проверка админа
    if get_admin_from_cookie(request):
        user = None
    else:
        user = get_user_from_cookie(request, db)
        if not user:
            return RedirectResponse(url="/")
        
        today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        if user.last_request_date is None or user.last_request_date.date() < today.date():
            user.requests_today = 0
            user.last_request_date = datetime.utcnow()
        
        if user.requests_today >= user.requests_limit:
            history = db.query(AnalysisHistory).filter(AnalysisHistory.user_id == user.id).order_by(AnalysisHistory.created_at.desc()).limit(50).all()
            response = templates.TemplateResponse(request, "dashboard.html", {
                "request": request, "user": user, "history": history,
                "error": f"Лимит исчерпан ({user.requests_today}/{user.requests_limit}).",
                "progress_percent": 100, "requests_left": 0,
            })
            response.status_code = 429
            response.headers["Retry-After"] = "86400"
            response.headers["X-RateLimit-Limit"] = str(user.requests_limit)
            response.headers["X-RateLimit-Remaining"] = "0"
            return response

    tm = TokenManager()
    if not tm.tokens:
        return templates.TemplateResponse(request, "dashboard.html", {
            "request": request, "user": user, "history": [],
            "error": "Ошибка: VK токены не заданы. Добавьте VK_TOKEN_1 (и т.д.) в .env.",
            "progress_percent": 0, "requests_left": max(user.requests_limit - user.requests_today, 0) if user else 0,
        })

    if target_type == "group":
        # АНАЛИЗ ГРУППЫ
        if user:
            remaining_limit = user.requests_limit - user.requests_today
        else:
            remaining_limit = 10000
            
        group_result = analyze_group(target, tm, max_members=remaining_limit)
        
        if not group_result or group_result["members_analyzed"] == 0:
            return templates.TemplateResponse(request, "dashboard.html", {
                "request": request, "user": user, "history": [],
                "error": "Не удалось получить участников группы или список закрыт.",
                "progress_percent": 0, "requests_left": max(user.requests_limit - user.requests_today, 0) if user else 0,
            })
            
        members_analyzed = group_result["members_analyzed"]
        if user:
            user.requests_today += members_analyzed
            db.commit()
        
        record = AnalysisHistory(
            user_id=user.id if user else 0,
            target=target,
            target_type="group",
            score=None,
            risk_level="HIGH" if group_result["average_score"] > 60 else "MEDIUM" if group_result["average_score"] > 30 else "NORMAL",
            details=json.dumps({"type": "group", "message": f"Проанализировано {members_analyzed} участников"}, ensure_ascii=False),
            average_score=group_result["average_score"],
            score_distribution=json.dumps(group_result["distribution"], ensure_ascii=False),
            members_analyzed=members_analyzed
        )

    else:
        # АНАЛИЗ ПРОФИЛЯ
        result = analyze_user(target, tm)
        if not result:
            return templates.TemplateResponse(request, "dashboard.html", {
                "request": request, "user": user, "history": [],
                "error": "Не удалось получить данные профиля.",
                "progress_percent": 0, "requests_left": max(user.requests_limit - user.requests_today, 0) if user else 0,
            })
            
        details = {"reasons": result.reasons, "anomalies": result.anomalies, "profile": {"id": result.user_id, "screen_name": result.profile_data.screen_name if result.profile_data else ""}}
        record = AnalysisHistory(
            user_id=user.id if user else 0,
            target=target,
            target_type="user",
            score=result.total_score,
            risk_level=result.risk_level,
            details=json.dumps(details, ensure_ascii=False),
            average_score=None,
            score_distribution=None,
            members_analyzed=1
        )
        
        if user:
            user.requests_today += 1
            db.commit()

    db.add(record)
    db.commit()
    db.refresh(record)
    return RedirectResponse(url=f"/history/{record.id}", status_code=303)


@app.get("/history/{history_id}", response_class=HTMLResponse)
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
    return templates.TemplateResponse(request, "history_detail.html", {
        "request": request,
        "user": None if get_admin_from_cookie(request) else get_user_from_cookie(request, db),
        "record": record, "details": json.loads(record.details)
    })


@app.post("/account/delete")
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


# === АДМИН-ПАНЕЛЬ ===
@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    stats = {
        "total_users": db.query(User).count(),
        "new_users_today": db.query(User).filter(User.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)).count(),
        "requests_today": db.query(AnalysisHistory).filter(AnalysisHistory.created_at >= datetime.utcnow().replace(hour=0, minute=0, second=0)).count(),
        "total_requests": db.query(AnalysisHistory).count(),
        "avg_risk_score": int(db.query(func.avg(AnalysisHistory.score)).scalar() or 0),
        "api_load": 0,
        "active_tokens": len(TokenManager().tokens),
    }
    recent = db.query(AnalysisHistory, User.email).join(User).order_by(AnalysisHistory.created_at.desc()).limit(10).all()
    recent_activities = [{"created_at": h.created_at, "user_email": email, "action": "analyze", "target": h.target, "risk_level": h.risk_level} for h, email in recent]
    return templates.TemplateResponse(request, "admin_dashboard.html", {"request": request, "stats": stats, "recent_activities": recent_activities})


@app.get("/admin/weights", response_class=HTMLResponse)
async def admin_weights(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    weights = {k: v.copy() for k, v in DEFAULT_MODULE_WEIGHTS.items()}
    db_weights = db.query(AdminSettings).filter(AdminSettings.key.like("weight_%")).all()
    for w in db_weights:
        key = w.key.replace("weight_", "")
        if key in weights:
            try:
                weights[key]["global_weight"] = json.loads(w.value).get("global_weight", weights[key]["global_weight"])
            except Exception:
                pass
    return templates.TemplateResponse(request, "admin_weights.html", {
        "request": request, "weights": weights,
        "weights_json": json.dumps(weights, indent=2, ensure_ascii=False),
    })


@app.post("/admin/weights/save")
async def admin_weights_save(request: Request, profile_analyzer: float = Form(1.0), social_graph_analyzer: float = Form(1.2), behavior_analyzer: float = Form(0.9), cross_check_analyzer: float = Form(1.1), db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    weights = {"profile_analyzer": profile_analyzer, "social_graph_analyzer": social_graph_analyzer, "behavior_analyzer": behavior_analyzer, "cross_check_analyzer": cross_check_analyzer}
    for key, value in weights.items():
        setting = db.query(AdminSettings).filter(AdminSettings.key == f"weight_{key}").first()
        if setting:
            setting.value = json.dumps({"global_weight": value})
        else:
            db.add(AdminSettings(key=f"weight_{key}", value=json.dumps({"global_weight": value})))
    db.commit()
    return RedirectResponse(url="/admin/weights?saved=1", status_code=303)


@app.get("/admin/weights/reset")
async def admin_weights_reset(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    db.query(AdminSettings).filter(AdminSettings.key.like("weight_%")).delete()
    db.commit()
    return RedirectResponse(url="/admin/weights", status_code=303)


@app.get("/admin/weights/parameters", response_class=HTMLResponse)
async def admin_parameters(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    modules = {k: v.copy() for k, v in DEFAULT_MODULE_WEIGHTS.items()}
    db_params = db.query(ModuleParameter).all()
    for p in db_params:
        if p.module_name in modules and p.param_key in modules[p.module_name]["parameters"]:
            modules[p.module_name]["parameters"][p.param_key]["value"] = p.param_value
    db_weights = db.query(AdminSettings).filter(AdminSettings.key.like("weight_%")).all()
    for w in db_weights:
        key = w.key.replace("weight_", "")
        if key in modules:
            try:
                modules[key]["global_weight"] = json.loads(w.value).get("global_weight", modules[key]["global_weight"])
            except Exception:
                pass
    return templates.TemplateResponse(request, "admin_parameters.html", {
        "request": request, "modules": modules,
        "modules_json": json.dumps(modules, indent=2, ensure_ascii=False),
    })


@app.post("/admin/weights/parameters/save")
async def admin_parameters_save(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)

    form_data = await request.form()
    
    for key, value in form_data.items():
        if key.startswith("param_") and key.endswith("_value"):
            cleaned = key.replace("param_", "").replace("_value", "")
            parts = cleaned.rsplit("_", 1)
            if len(parts) != 2:
                continue
            module_name, param_key = parts
            if module_name not in DEFAULT_MODULE_WEIGHTS or param_key not in DEFAULT_MODULE_WEIGHTS[module_name]["parameters"]:
                continue
            try:
                int_value = int(float(value))
            except ValueError:
                continue

            param = db.query(ModuleParameter).filter(ModuleParameter.module_name == module_name, ModuleParameter.param_key == param_key).first()
            if param:
                param.param_value = int_value
            else:
                db.add(ModuleParameter(module_name=module_name, param_key=param_key, param_value=int_value))

    for module_name in DEFAULT_MODULE_WEIGHTS.keys():
        gw_key = f"global_weight_{module_name}"
        if gw_key in form_data:
            try:
                gw_value = float(form_data[gw_key])
                setting = db.query(AdminSettings).filter(AdminSettings.key == f"weight_{module_name}").first()
                if setting:
                    setting.value = json.dumps({"global_weight": gw_value})
                else:
                    db.add(AdminSettings(key=f"weight_{module_name}", value=json.dumps({"global_weight": gw_value})))
            except ValueError:
                pass

    db.commit()
    return RedirectResponse(url="/admin/weights/parameters?saved=1", status_code=303)


@app.get("/admin/weights/parameters/reset")
async def admin_parameters_reset(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    db.query(ModuleParameter).delete()
    db.query(AdminSettings).filter(AdminSettings.key.like("weight_%")).delete()
    db.commit()
    return RedirectResponse(url="/admin/weights/parameters", status_code=303)


@app.get("/admin/users", response_class=HTMLResponse)
async def admin_users(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    users = db.query(User).order_by(User.created_at.desc()).all()
    return templates.TemplateResponse(request, "admin_users.html", {"request": request, "users": users, "global_limit": DEFAULT_REQUESTS_LIMIT})


@app.post("/admin/users/{user_id}/block")
async def admin_user_block(request: Request, user_id: int, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if user and not user.is_admin:
        user.is_active = False
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/unblock")
async def admin_user_unblock(request: Request, user_id: int, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if user:
        user.is_active = True
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/limit")
async def admin_user_limit(request: Request, user_id: int, new_limit: int = Form(...), db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    user = db.query(User).filter(User.id == user_id).first()
    if user and not user.is_admin:
        user.requests_limit = max(1, min(10000, new_limit))
        db.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/settings/global_limit")
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


@app.get("/admin/metrics", response_class=HTMLResponse)
async def admin_metrics(request: Request, db: Session = Depends(get_db)):
    if not get_admin_from_cookie(request):
        return RedirectResponse(url="/", status_code=303)
    
    from app.metrics import collect_system_metrics, get_recent_metrics
    
    current = collect_system_metrics(db)
    history = get_recent_metrics(db, hours=24)
    
    from sqlalchemy import func, text
    
    # конвертируем UTC в локальное время UTC+3
    hourly_stats = db.query(
        func.strftime('%H', func.datetime(AnalysisHistory.created_at, '+3 hours')).label('hour'),
        func.count(AnalysisHistory.id).label('count')
    ).filter(
        AnalysisHistory.created_at >= datetime.utcnow() - timedelta(hours=24)
    ).group_by('hour').order_by('hour').all()
    
    # Заполняем все 24 часа
    activity_hours = [f"{h:02d}:00" for h in range(24)]
    activity_map = {row.hour.zfill(2): row.count for row in hourly_stats if row.hour}
    activity_values = [activity_map.get(f"{h:02d}", 0) for h in range(24)]

    
    # Получение последних записей истории
    history_records = db.query(AnalysisHistory).order_by(
        AnalysisHistory.created_at.desc()
    ).limit(10).all()
    
    recent_errors = []
    
    metrics_display = {
        "cpu_percent": round(current.get("cpu_percent", 0), 1),
        "memory_percent": round(current.get("memory_percent", 0), 1),
        "disk_percent": round(current.get("disk_percent", 0), 1),
        "active_tokens": len(TokenManager().tokens),
        "api_requests_per_min": history.get("api_requests_per_min", 0),
        "api_errors": history.get("api_errors", 0),
        "api_avg_response_ms": 0,
        "users_online": 0,
        "requests_today": db.query(AnalysisHistory).filter(
            AnalysisHistory.created_at >= datetime.utcnow().replace(hour=0)
        ).count(),
        "new_users_24h": db.query(User).filter(
            User.created_at >= datetime.utcnow() - timedelta(days=1)
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
    
@app.get("/version", response_class=HTMLResponse, include_in_schema=False)
async def version_page(request: Request):
    from config.settings import APP_VERSION, VK_API_VERSION, BASE_DIR
    
    version_file = BASE_DIR / "VERSION"
    build_date = "—"
    if version_file.exists():
        msk = timezone(timedelta(hours=3))
        mtime = os.path.getmtime(version_file)
        build_date = datetime.fromtimestamp(mtime, tz=msk).strftime("%d.%m.%Y %H:%M MSK")
    
    return templates.TemplateResponse(
        request, "version.html",
        {
            "request": request,
            "version": APP_VERSION,
            "api_version": f"v{VK_API_VERSION}",
            "build_date": build_date
        }
    )


@app.get("/api/version", response_class=JSONResponse, include_in_schema=False)
async def get_version_api():
    """JSON API для получения версии (для скриптов и CI/CD)"""
    from config.settings import APP_VERSION, VK_API_VERSION
    return {
        "version": APP_VERSION,
        "api_version": f"v{VK_API_VERSION}",
        "build": os.getenv("BUILD_NUMBER", "local")
    }
    
@app.on_event("startup")
async def startup_event():
    import asyncio
    from app.background import run_background_tasks

    asyncio.create_task(run_background_tasks())
    print("Фоновый сбор метрик запущен")