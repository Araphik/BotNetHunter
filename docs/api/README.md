# BotNetHunter API

**BotNetHunter** — это веб-сервис для анализа пользователей и сообществ социальной сети ВКонтакте с целью выявления бот-активности.

Система принимает идентификатор пользователя или группы, выполняет анализ по нескольким критериям и возвращает итоговую оценку риска.



## API документация

Полное описание API доступно в формате OpenAPI:  
[swagger.yaml](swagger.yaml)
---

## Основные endpoints

**POST `/analyze`**

Запуск анализа пользователя или группы

**Параметры:**

* `target` — ID или screen_name
* `target_type` — `user` или `group`

**Ответ:**

1. Итоговый score.
2. Уровень риска.
3. Причины.



**GET `/history`**

Выдача истории анализов пользователя



##  Обработка ошибок

API использует формат ошибок в соответствии с RFC 7807 (`application/problem+json`):

```json
{
  "title": "Ошибка",
  "detail": "Описание проблемы",
  "requestId": "uuid",
  "timestamp": "UTC time"
}
```


## Структура

```text
docs/api/swagger.yaml   # OpenAPI описание API
app/                    # серверное приложение
analyzers/              # модули анализа
core/                   # VK client и token manager
```
