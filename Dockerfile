FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Обновляем системные пакеты для устранения debian-уязвимостей
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

# Сначала явно апгрейдим pip и setuptools>=82.0.0,
# чтобы вендорные копии wheel и jaraco.context внутри setuptools
# были обновлены до установки остальных зависимостей (CVE-2026-24049, CVE-2026-23949)
RUN pip install --no-cache-dir --upgrade pip "setuptools>=82.0.0" \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system appgroup \
    && adduser --system --ingroup appgroup --no-create-home appuser \
    && chown -R appuser:appgroup /app

USER appuser

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]