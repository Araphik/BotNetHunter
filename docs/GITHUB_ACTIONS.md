# GitHub Actions pipeline

GitHub Actions workflow находится в `.github/workflows/ci.yml`.

Pipeline запускается для:

- push в `main`;
- push в `master`;
- создания Git-тега;
- pull request в `main` или `master` для базовых проверок.

## Что делает pipeline

1. `build-job` - установка зависимостей.
2. `syntax-check-job` - Ruff lint и `compileall`.
3. `import-check-job` - импорт FastAPI-приложения.
4. `sast-sonarqube-job` - SonarQube SAST и проверка отсутствия открытых `CRITICAL`/`BLOCKER` vulnerabilities.
5. `package-job` - сборка Docker-образа, публикация в GitHub Container Registry и формирование дистрибутивов.
6. `container-scan-trivy-job` - сканирование Docker-образа через Aqua Trivy.
7. `release-job` - создание или обновление GitHub Release для тегов.

## Настройки GitHub

### Actions permissions

Откройте:

```text
Settings -> Actions -> General -> Workflow permissions
```

Выберите:

```text
Read and write permissions
```

Workflow дополнительно задает permissions:

- `contents: write` - создание Releases и публикация HTML-отчета Trivy в ветку `gh-pages`;
- `packages: write` - публикация Docker-образа в `ghcr.io`;

### SonarQube

В SonarQube создайте проект:

```text
Project key: botnethunter
Project name: BotNetHunter
```

В GitHub добавьте:

```text
Settings -> Secrets and variables -> Actions
```

Secret:

```text
SONAR_TOKEN
```

Repository variable:

```text
SONAR_HOST_URL
```

Для текущего production docker-compose используйте:

```text
SONAR_HOST_URL=https://botnethunter.duckdns.org/sonar
```

Важно: `SONAR_HOST_URL` должен быть доступен GitHub-hosted runner. Локальный `http://localhost:9000` из вашего ноутбука для GitHub Actions не подойдет.

## Где будут артефакты

Docker image:

```text
ghcr.io/<owner>/<repo>:<branch-or-tag>
ghcr.io/<owner>/<repo>:sha-<short-sha>
```

Дистрибутивы сохраняются как workflow artifacts:

- `botnethunter-<version>-source.zip`;
- `botnethunter-<version>-docker-image.tar.gz`;
- `SHA256SUMS`.

Trivy в GitHub Actions не загружает JSON/SARIF как workflow artifact, потому что GitHub artifact storage может закончиться и заблокировать upload. Code scanning alerts тоже не используются: в личном репозитории GitHub может держать эту функцию выключенной без GitHub Advanced Security. Вместо этого отчеты можно смотреть без artifact storage и без Code Scanning:

- в `Actions -> CI -> container-scan-trivy-job -> Summary` - краткая Markdown-сводка с количеством уязвимостей и top findings;
- на GitHub Pages из ветки `gh-pages`: `https://<owner>.github.io/<repo>/trivy/` - HTML-отчет с поиском, фильтрацией по severity и ссылками на CVE.

Внутри job все еще создаются файлы:

- `trivy-image-report.json`;
- `trivy-image-report.html`;

но они используются только для summary и публикации HTML-страницы в `gh-pages`.

Чтобы HTML-отчет открылся как сайт, в настройках репозитория включите:

```text
Settings -> Pages -> Build and deployment -> Deploy from a branch
Branch: gh-pages
Folder: / (root)
```

SonarQube сохраняет:

- `sonarqube-critical-vulnerabilities.json`;
- `sonarqube-critical-vulnerabilities.md`.

## GitHub Release

Release создается при push Git-тега:

```bash
git tag v1.0.0
git push origin v1.0.0
```

В Release будут загружены файлы из `dist/`.
