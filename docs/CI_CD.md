# CI/CD pipeline

Pipeline настроен в `.gitlab-ci.yml` и запускается для:

- push в ветку `main`;
- push в ветку `master`;
- создания Git-тега.

## Этапы

1. `install_dependencies` - установка Python-зависимостей и `pip check`.
2. `lint_code` - запуск `ruff check --select E9,F63,F7,F82` и `compileall`.
3. `sast_sonarqube_scan` - статический анализ безопасности кода через SonarQube.
4. `sast_sonarqube_report` - проверка отчета SonarQube на отсутствие открытых `CRITICAL` и `BLOCKER` vulnerabilities.
5. `build_distribution` - сборка Docker-образа, публикация в GitLab Container Registry и формирование zip/tar.gz-дистрибутивов.
6. `container_scan_trivy` - сканирование Docker-образа через Aqua Trivy.
7. `release_distribution` - создание GitLab Release для тегов.

## Переменные GitLab CI/CD

В настройках проекта GitLab добавьте:

| Переменная | Тип | Назначение |
| ---------- | --- | ---------- |
| `SONAR_HOST_URL` | Variable | URL SonarQube Server, например `https://sonarqube.example.com` |
| `SONAR_TOKEN` | Masked variable | Токен анализа проекта SonarQube |

Стандартные переменные для GitLab Container Registry (`CI_REGISTRY`, `CI_REGISTRY_IMAGE`, `CI_REGISTRY_USER`, `CI_REGISTRY_PASSWORD`) предоставляет GitLab.

## SonarQube

В SonarQube должен существовать проект с ключом:

```text
botnethunter
```

Quality Gate ожидается прямо в job `sast_sonarqube_scan` за счет настройки:

```properties
sonar.qualitygate.wait=true
```

Если SonarQube находит открытые vulnerability уровня `CRITICAL` или `BLOCKER`, job `sast_sonarqube_report` завершится ошибкой.

## Trivy

Job `container_scan_trivy` сканирует опубликованный Docker-образ из GitLab Container Registry и сохраняет:

- `reports/trivy-image-report.txt`;
- `reports/trivy-image-report.json`;
- `reports/gl-container-scanning-report.json`.

Последний файл подключен как GitLab `container_scanning` report.

## Дистрибутивы и Releases

На `main/master` pipeline собирает дистрибутивы и сохраняет их в artifacts job `build_distribution`.

GitLab Releases привязаны к тегам, поэтому публикация в Releases выполняется при создании тега:

```bash
git tag v1.0.0
git push origin v1.0.0
```

Release будет содержать ссылки на:

- исходный zip-дистрибутив;
- архив Docker-образа;
- Docker-образ в GitLab Container Registry.
