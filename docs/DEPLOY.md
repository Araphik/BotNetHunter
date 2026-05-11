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
VK_TOKEN_1=your_vk_token_here
SECRET_KEY=your_generated_secret_key_here
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=secure_admin_password
DATABASE_URL=postgresql+psycopg://botnethunter:botnethunter@postgres:5432/botnethunter
POSTGRES_DB=botnethunter
POSTGRES_USER=botnethunter
POSTGRES_PASSWORD=botnethunter
SECURITY_ALLOWED_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
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

---

## 5. Проверка работоспособности

После успешного запуска веб-интерфейс доступен по адресу:

**http://127.0.0.1:8000**

---

## Переменные окружения

| Переменная | Описание | Допустимые значения | По умолчанию |
|---|---|---|---|
| `VK_TOKEN_1` | Токен доступа (Service token или User token) к VK API | Строка | **Уникально - указывается пользователем самостоятельно** |
| `SECRET_KEY` | Криптографический ключ для подписи сессий и токенов безопасности | Строка (hex, мин. 32 символа) | **Уникально - указывается пользователем самостоятельно** |
| `ADMIN_EMAIL` | Email для учётной записи системного администратора | Строка (формат email) | `admin@example.com` |
| `ADMIN_PASSWORD` | Пароль первичной учётной записи администратора | Строка | `admin_password` |
| `DATABASE_URL` | Строка подключения (DSN) для SQLAlchemy | URL подключения PostgreSQL | `postgresql+psycopg://botnethunter:botnethunter@postgres:5432/botnethunter` |
| `POSTGRES_DB` | Имя создаваемой базы данных в контейнере PostgreSQL | Строка | `botnethunter` |
| `POSTGRES_USER` | Имя пользователя-владельца базы данных PostgreSQL | Строка | `botnethunter` |
| `POSTGRES_PASSWORD` | Пароль пользователя базы данных PostgreSQL | Строка | `botnethunter` |
| `SECURITY_ALLOWED_ORIGINS` | Список разрешённых источников для политики CORS (без wildcard `*`) | URL через запятую | `http://127.0.0.1:8000,http://localhost:8000` |