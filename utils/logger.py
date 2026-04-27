import logging
import sys
from contextvars import ContextVar

# Контекстная переменная для хранения request_id
request_id_var = ContextVar('request_id', default=None)


class RequestContextFilter(logging.Filter):
    """Фильтр для добавления request_id в логи"""
    def filter(self, record):
        record.request_id = request_id_var.get() or '-'
        return True


def setup_logger(name='botnethunter', level=logging.INFO):
    logger = logging.getLogger(name)
    logger.setLevel(level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        # Добавляем request_id в формат логов
        formatter = logging.Formatter(
            '%(asctime)s — %(levelname)s — [%(request_id)s] — %(message)s'
        )
        handler.setFormatter(formatter)
        handler.addFilter(RequestContextFilter())
        logger.addHandler(handler)

    return logger


logger = setup_logger()