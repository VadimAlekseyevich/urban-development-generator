# API

Базовый префикс: `/api/v1`.

## Реализовано

- `GET /health/live` — liveness;
- `GET /health/ready` — готовность PostgreSQL;
- `GET /projects` — список проектов;
- `POST /projects` — создание проекта;
- `GET /projects/{project_id}` — получение проекта.

## Далее

- `/projects/{id}/datasets` — загрузка, импорт и валидация;
- `/projects/{id}/layers` — нормализованные слои;
- `/projects/{id}/runs` — создание вариантов;
- `/runs/{id}` и `/runs/{id}/stages` — статус и прогресс;
- `/runs/{id}/metrics` — показатели;
- `/runs/{id}/export` — экспорт;
- `/projects/{id}/compare` — сравнение вариантов.

Тяжёлые GIS-операции выполняются в worker: API только создаёт job и возвращает идентификатор запуска.
