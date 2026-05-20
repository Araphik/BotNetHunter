# Руководство по установке и запуску системы

> **Поддерживаемые ОС:** Linux, macOS, Windows (WSL2 или нативный Docker)
>
> **Требования:** Docker и Docker Compose v2+

---

## 1. Подготовка среды

Перейдите в директорию с проектом:

```bash
cd project_docker
```

---

## 2. Настройка конфигурации

Создайте файл `.env` в корневой директории:

```bash
touch .env
```

Заполните `.env` необходимыми значениями. Секретный ключ можно сгенерировать командой:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

**Пример минимального содержимого `.env`:**

```env
SECRET_KEY=your_generated_secret_key_here
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=secure_admin_password
DATABASE_URL=postgresql+psycopg://botnethunter:botnethunter@postgres:5432/botnethunter
POSTGRES_DB=botnethunter
POSTGRES_USER=botnethunter
POSTGRES_PASSWORD=botnethunter
SECURITY_ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
SONAR_POSTGRES_DB=sonar
SONAR_POSTGRES_USER=sonar
SONAR_POSTGRES_PASSWORD=replace_with_random_sonar_db_password
SONAR_WEB_CONTEXT=/sonar
```

> **Примечание:** При запуске всех компонентов в Docker хост в `DATABASE_URL` должен указывать на имя сервиса базы данных в `docker-compose.yml` (обычно `postgres` или `db`, но не `localhost`).

---

## 3. Сборка Docker-образов

```bash
make docker-build
```

---

## 4. Запуск системы

Запустите все сервисы (веб-приложение и PostgreSQL) в фоновом режиме:

```bash
make docker-up
```

Для production-запуска вместе с nginx reverse proxy и SonarQube:

```bash
make docker-up-prod
```

SonarQube будет доступен по адресу:

```text
https://botnethunter.duckdns.org/sonar/
```

Trivy HTML-отчет за reverse proxy будет доступен по адресу:

```text
https://botnethunter.duckdns.org/trivy/trivy-image-report.html
```

nginx отдает этот файл из локальной папки `reports/`, которая монтируется в контейнер как `/usr/share/nginx/html/trivy`. Чтобы обновить опубликованный отчет на сервере, положите актуальный `trivy-image-report.html` в `reports/` и перезапустите nginx или перечитайте конфигурацию.

При первом входе используйте `admin` / `admin`, затем смените пароль и создайте project token для CI/CD.

> **Примечание для Linux-хоста:** SonarQube использует Elasticsearch. Если контейнер `sonarqube` не стартует, задайте на хосте `sudo sysctl -w vm.max_map_count=524288` и добавьте `vm.max_map_count=524288` в `/etc/sysctl.conf`.

---

## 5. Проверка работоспособности

После успешного запуска веб-интерфейс доступен по адресу:

**http://127.0.0.1:8000**

---

## Переменные окружения

| Переменная | Описание | Допустимые значения | По умолчанию |
|---|---|---|---|
| `SECRET_KEY` | Криптографический ключ для подписи сессий и токенов безопасности | Строка (hex, мин. 32 символа) | **Уникально - указывается пользователем самостоятельно** |
| `ADMIN_EMAIL` | Email для учётной записи системного администратора | Строка (формат email) | `admin@example.com` |
| `ADMIN_PASSWORD` | Пароль первичной учётной записи администратора | Строка | `admin_password` |
| `DATABASE_URL` | Строка подключения (DSN) для SQLAlchemy | URL подключения PostgreSQL | `postgresql+psycopg://botnethunter:botnethunter@postgres:5432/botnethunter` |
| `POSTGRES_DB` | Имя создаваемой базы данных в контейнере PostgreSQL | Строка | `botnethunter` |
| `POSTGRES_USER` | Имя пользователя-владельца базы данных PostgreSQL | Строка | `botnethunter` |
| `POSTGRES_PASSWORD` | Пароль пользователя базы данных PostgreSQL | Строка | `botnethunter` |
| `SECURITY_ALLOWED_ORIGINS` | Список разрешённых источников для политики CORS (без wildcard `*`) | URL через запятую | `http://127.0.0.1:8000,http://localhost:8000` |
| `SONAR_POSTGRES_DB` | Имя базы данных SonarQube | Строка | `sonar` |
| `SONAR_POSTGRES_USER` | Пользователь базы данных SonarQube | Строка | `sonar` |
| `SONAR_POSTGRES_PASSWORD` | Пароль базы данных SonarQube | Строка | **Уникально - указывается пользователем самостоятельно** |
| `SONAR_WEB_CONTEXT` | URL-префикс SonarQube за reverse proxy | Строка | `/sonar` |
