import os
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, declarative_base
from config.settings import DATABASE_URL

# Создаём папку для БД если её нет (нужно при первом запуске в Docker)
if DATABASE_URL.startswith("sqlite:///"):
    db_path = Path(DATABASE_URL.replace("sqlite:///", ""))
    db_path.parent.mkdir(parents=True, exist_ok=True)

# Включаем WAL-режим для конкурентного доступа
engine = create_engine(
    DATABASE_URL,
    connect_args={
        "check_same_thread": False,  # Разрешаем многопоточный доступ
        "timeout": 30  # Ждём освобождения блокировки до 30 сек
    },
    pool_pre_ping=True,  # Проверяем соединение перед использованием
    pool_recycle=3600  # Переподключаем раз в час
)

@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, connection_record):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")  # Write-Ahead Logging
    cursor.execute("PRAGMA busy_timeout=30000")  # 30 сек ожидания
    cursor.execute("PRAGMA synchronous=NORMAL")  # Баланс скорости/надёжности
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
