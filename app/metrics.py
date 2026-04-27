import psutil
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from app.models import SystemMetrics

def collect_system_metrics(db: Session) -> dict:
    try:
        metrics = {
            "cpu_percent": psutil.cpu_percent(interval=1),
            "memory_percent": psutil.virtual_memory().percent,
            "disk_percent": psutil.disk_usage('/').percent,
            "network_sent": psutil.net_io_counters().bytes_sent,
            "network_recv": psutil.net_io_counters().bytes_recv,
        }
        for name, value in metrics.items():
            db.add(SystemMetrics(
                metric_name=name,
                metric_value=value,
                recorded_at=datetime.utcnow()
            ))
        db.commit()
        return metrics
    except Exception as e:
        print(f"[Metrics] Ошибка сбора данных: {e}")
        return {"cpu_percent": 0, "memory_percent": 0, "disk_percent": 0}

def get_recent_metrics(db: Session, hours: int = 24) -> dict:
    try:
        cutoff = datetime.utcnow() - timedelta(hours=hours)
        results = db.query(
            SystemMetrics.metric_name,
            SystemMetrics.metric_value
        ).filter(
            SystemMetrics.recorded_at >= cutoff
        ).order_by(SystemMetrics.recorded_at.desc()).all()

        latest = {}
        for row in results:
            if row.metric_name not in latest:
                latest[row.metric_name] = round(row.metric_value, 2)
        return latest
    except Exception as e:
        print(f"[Metrics] Ошибка чтения истории: {e}")
        return {}

# удаляем данные старше 7 дней
def cleanup_old_metrics(db: Session, days: int = 7) -> int:
    try:
        cutoff = datetime.utcnow() - timedelta(days=days)
        deleted = db.query(SystemMetrics).filter(
            SystemMetrics.recorded_at < cutoff
        ).delete(synchronize_session=False)
        db.commit()
        return deleted
    except Exception as e:
        print(f"[Metrics] Ошибка очистки: {e}")
        return 0