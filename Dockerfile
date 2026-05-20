FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Обновляем базовые Alpine-пакеты и убираем неиспользуемые Python-модули.
RUN apk upgrade --no-cache \
    && rm -f /usr/local/lib/python3.11/lib-dynload/_sqlite3*.so \
    && rm -f /usr/local/lib/python3.11/lib-dynload/_curses*.so

COPY requirements.txt ./

# Сначала явно апгрейдим pip, setuptools и wheel,
# чтобы Trivy не видел уязвимые Python build tools в базовом образе.
RUN pip install --no-cache-dir --upgrade pip "setuptools>=82.0.0" "wheel>=0.46.2" \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup -S appgroup \
    && adduser -S -D -H -G appgroup appuser \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
