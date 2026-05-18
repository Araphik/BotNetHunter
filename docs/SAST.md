# SAST через SonarQube

Проект настроен для статического анализа кода в SonarQube.

## Что добавлено

- `sonar-project.properties` в корне репозитория.
- GitLab CI jobs `sast_sonarqube_scan` и `sast_sonarqube_report`.
- GitHub Actions job `sast-sonarqube-job`, если проект запускается в GitHub.
- Отдельная проверка SonarQube API на отсутствие открытых уязвимостей уровня `CRITICAL` и `BLOCKER`.

## Настройка SonarQube Server

При production-запуске проекта SonarQube поднимается в Docker Compose вместе с приложением:

```bash
make docker-up-prod
```

Веб-интерфейс доступен через reverse proxy:

```text
https://botnethunter.duckdns.org/sonar/
```

При первом входе используйте стандартную учетную запись SonarQube `admin` / `admin` и сразу смените пароль.

1. Создайте проект в SonarQube с ключом `botnethunter`.
2. Создайте токен анализа проекта.
3. В GitLab добавьте CI/CD variables:
   - `SONAR_TOKEN` - токен анализа SonarQube.
   - `SONAR_HOST_URL` - `https://botnethunter.duckdns.org/sonar`.

Для GitHub Actions используются те же значения: `SONAR_TOKEN` как secret и `SONAR_HOST_URL` как repository variable.

## Локальный запуск

Требуется установленный `sonar-scanner`.

```bash
export SONAR_TOKEN=...
export SONAR_HOST_URL=https://sonarqube.example.com
make sonar-scan
```

## Подтверждение отсутствия критических уязвимостей

После выполнения SAST job в pipeline:

- отчет анализа доступен в SonarQube в проекте `botnethunter`;
- шаг проверки критических уязвимостей должен показать `0 critical/blocker vulnerabilities found`;
- артефакты pipeline содержат JSON-ответ SonarQube и краткий Markdown-отчет.
