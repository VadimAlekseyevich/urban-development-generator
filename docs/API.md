# API

Базовый префикс: `/api/v1`.

## Реализовано

- `GET /health/live` — liveness;
- `GET /health/ready` — готовность PostgreSQL;
- `GET /projects?limit=50&offset=0` — список проектов;
- `POST /projects` — создание проекта;
- `GET /projects/{project_id}` — получение проекта;
- `PATCH /projects/{project_id}` — частичное обновление проекта;
- `DELETE /projects/{project_id}` — удаление проекта.

## Project contract

Проект хранит явный `working_srid` — положительный EPSG SRID проектной метрической CRS.
`AUTO` и географические CRS вроде EPSG:4326 не являются допустимым persisted working CRS.

Минимальный create payload:

```json
{
  "name": "Demo project",
  "description": null,
  "working_srid": 32637,
  "boundary_metadata": {
    "source_srid": 4326,
    "geometry_type": "MULTIPOLYGON",
    "feature_count": 1
  }
}
```

`boundary_metadata` описывает источник/форму project boundary отдельно от PostGIS geometry.
Само поле boundary в persistence остаётся nullable `MULTIPOLYGON`; его ingest/normalization относится к следующим data tasks.

## Далее

- `/projects/{id}/datasets` — загрузка, импорт и валидация;
- `/projects/{id}/layers` — нормализованные слои;
- `/projects/{id}/runs` — создание вариантов;
- `/runs/{id}` и `/runs/{id}/stages` — статус и прогресс;
- `/runs/{id}/metrics` — показатели;
- `/runs/{id}/export` — экспорт;
- `/projects/{id}/compare` — сравнение вариантов.

Тяжёлые GIS-операции выполняются в worker: API только создаёт job и возвращает идентификатор запуска.
