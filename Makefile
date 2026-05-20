PYTHON     ?= $(shell if [ -x .venv/bin/python ]; then echo .venv/bin/python; elif command -v python3 >/dev/null 2>&1; then echo python3; else echo python; fi)
PIP        ?= $(PYTHON) -m pip
UVICORN    ?= $(PYTHON) -m uvicorn
APP_MODULE ?= app.main:app
HOST       ?= 127.0.0.1
PORT       ?= 8000
IMAGE      ?= botnethunter

.PHONY: install check run db-up wait-db db-down clean init \
docker-build docker-up docker-up-prod docker-down docker-down-prod \
docker-clean-prod rebuild docker-logs docker-shell \
sonar-scan version version-bump-patch version-bump-minor version-bump-major version-set

# — Локальная разработка —

init:
	@test -f .env && echo ".env уже существует, пропускаю" || cp .env.example .env

install:
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

check: db-up wait-db
	$(PYTHON) -m compileall analyzers api app config core models utils
	$(PYTHON) -c "from app.main import app; print(app.title)"

db-up:
	docker compose up -d postgres

wait-db:
	@echo "Waiting for PostgreSQL..."
	@for i in $$(seq 1 30); do \
		if docker compose exec -T postgres pg_isready -U botnethunter -d botnethunter >/dev/null 2>&1; then \
			echo "PostgreSQL is ready"; \
			exit 0; \
		fi; \
		sleep 1; \
	done; \
	docker compose logs postgres; \
	exit 1

db-down:
	docker compose stop postgres

run: db-up wait-db
	$(UVICORN) $(APP_MODULE) --host $(HOST) --port $(PORT) --reload

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

sonar-scan:
	@test -n "$$SONAR_TOKEN" || (echo "SONAR_TOKEN is required" && exit 1)
	@test -n "$$SONAR_HOST_URL" || (echo "SONAR_HOST_URL is required" && exit 1)
	@command -v sonar-scanner >/dev/null 2>&1 || (echo "sonar-scanner is not installed" && exit 1)
	sonar-scanner

# — Docker —

docker-build:
	@APP_VERSION=$$(cat VERSION) docker compose build

docker-up:
	@APP_VERSION=$$(cat VERSION) docker compose up -d

docker-up-prod:
	@APP_VERSION=$$(cat VERSION) docker compose --profile prod up -d

docker-down:
	docker compose down

docker-down-prod:
	docker compose --profile prod down --remove-orphans

docker-clean-prod:
	docker compose --profile prod down -v --remove-orphans --rmi local

rebuild: docker-down docker-build docker-up
	@echo "Перезапуск завершен"

docker-logs:
	docker compose logs -f

docker-shell:
	docker compose exec botnethunter sh

version:
	@cat VERSION

version-bump-patch:
	@current=$$(cat VERSION); \
	major=$$(echo $$current | cut -d. -f1); \
	minor=$$(echo $$current | cut -d. -f2); \
	patch=$$(echo $$current | cut -d. -f3); \
	new_patch=$$((patch + 1)); \
	new_version="$$major.$$minor.$$new_patch"; \
	echo "$$new_version" > VERSION; \
	echo "Updated: $$current -> $$new_version"

version-bump-minor:
	@current=$$(cat VERSION); \
	major=$$(echo $$current | cut -d. -f1); \
	minor=$$(echo $$current | cut -d. -f2); \
	new_version="$$major.$$((minor + 1)).0"; \
	echo "$$new_version" > VERSION; \
	echo "Updated: $$current -> $$new_version"

version-bump-major:
	@current=$$(cat VERSION); \
	major=$$(echo $$current | cut -d. -f1); \
	new_version="$$(($$major + 1)).0.0"; \
	echo "$$new_version" > VERSION; \
	echo "Updated: $$current -> $$new_version"

version-set:
	@if [ -z "$(word 2,$(MAKECMDGOALS))" ]; then \
		echo "Usage: make version-set 1.0.0"; \
		exit 1; \
	fi
	@echo "$(word 2,$(MAKECMDGOALS))" > VERSION
	@echo "Version set to $(word 2,$(MAKECMDGOALS))"

%:
	@true
