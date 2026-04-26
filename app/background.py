import asyncio
from app.database import SessionLocal
from app.metrics import collect_system_metrics, cleanup_old_metrics


async def run_background_tasks():

    cycle_count = 0
    cleanup_every_n_cycles = 10  # 10 минут

    while True:
        db = SessionLocal()
        try:
            # Сбор текущих метрик
            collect_system_metrics(db)
            
            cycle_count += 1
            
            # Очистка старых данных (каждые 10 циклов)
            if cycle_count % cleanup_every_n_cycles == 0:
                deleted = cleanup_old_metrics(db, days=7)
                if deleted > 0:
                    print(f"[Metrics Cleanup] Удалено устаревших записей: {deleted}")
                else:
                    print("[Metrics Cleanup] Старых записей не найдено")

        except Exception as e:
            print(f"[Background Task] Ошибка: {e}")
        finally:
            db.close()

        await asyncio.sleep(60)