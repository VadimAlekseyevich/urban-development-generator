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

## Generated buildings viewport API

S08-T13 добавляет read-only delivery persisted зданий конкретного run:

- `GET /projects/{project_id}/building-runs` — generation runs проекта с количеством generated buildings;
- `GET /projects/{project_id}/building-runs/{run_id}/buildings/geojson?bbox=west,south,east,north&limit=1500` — bounded viewport GeoJSON.

`bbox` задаётся в EPSG:4326, а spatial filtering выполняется в `GenerationRun.working_srid`.
Ответ возвращается в EPSG:4326 и содержит typed T12 properties: building/source refs,
zone, archetype, use, floors, footprint area и GFA. `truncated=true` означает, что
viewport достиг limit и клиенту следует приблизить карту или повторить запрос меньшей областью.


## Infrastructure run read API

S10-T12 exposes only persisted, run-scoped infrastructure presentation data:

- `GET /projects/{project_id}/infrastructure-runs`;
- `GET /projects/{project_id}/infrastructure-runs/{run_id}/facilities/geojson?bbox=...&limit=1500&origin=existing|generated`;
- `GET /projects/{project_id}/infrastructure-runs/{run_id}/metrics`;
- `GET /projects/{project_id}/infrastructure-runs/{run_id}/demand/geojson?bbox=...&limit=1500`.

Facility and demand GeoJSON are bounded viewport reads in EPSG:4326 with filtering in the run
working CRS. `metrics` and `demand/geojson` return HTTP 409 when the run exists but the
authoritative S10 UI read-model has not been materialized yet. Reads never rerun snapping,
routing, placement or metric computation.


## Далее

- `/projects/{id}/datasets` — загрузка, импорт и валидация;
- `/projects/{id}/layers` — нормализованные слои;
- `/projects/{id}/runs` — создание вариантов;
- `/runs/{id}` и `/runs/{id}/stages` — статус и прогресс;
- `/runs/{id}/metrics` — показатели;
- `/runs/{id}/export` — экспорт;
- `/projects/{id}/compare` — сравнение вариантов.

Тяжёлые GIS-операции выполняются в worker: API только создаёт job и возвращает идентификатор запуска.
