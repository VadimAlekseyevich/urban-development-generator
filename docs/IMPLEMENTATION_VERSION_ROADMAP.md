# Urban Development Generator — атомарный план реализации по версиям

> Этот документ является детализацией `docs/DEVELOPMENT_PLAN.md`. Он переводит каждый спринт в небольшие версии, каждая из которых должна быть реализуема **одним самостоятельным запросом к нейросети** без необходимости одновременно переписывать несколько подсистем.

## 1. Правило версионирования

До первого полного релиза используется pre-1.0 SemVer:

- один спринт = один minor-релиз: `v0.N.x`;
- одна атомарная задача = один patch-релиз: `v0.N.M`;
- `v1.0.0` = интегрированный продукт, готовый к демонстрации и экспериментам ВКР.

Версия считается атомарной, если её можно описать одной задачей, она имеет ограниченную область изменений, независимые критерии приёмки и не требует «заодно» реализовать следующий алгоритмический этап.

## 2. Definition of Done для каждой атомарной версии

Каждая версия должна удовлетворять общему DoD:

1. изменение укладывается в существующие архитектурные границы;
2. нет бизнес-логики в HTTP-контроллерах и React-компонентах;
3. публичные контракты типизированы;
4. добавлены или обновлены unit/integration tests;
5. миграция БД создаётся только при реальном изменении схемы;
6. повторный запуск миграций/worker-job не должен портить данные;
7. ошибки имеют диагностический код и человекочитаемое сообщение;
8. логирование содержит `project_id`, `run_id`, `job_id` там, где они известны;
9. CI не ухудшается;
10. документация обновляется, если меняется контракт/API/схема.

Для запроса нейросети достаточно использовать шаблон:

```text
Реализуй только версию <VERSION> из docs/IMPLEMENTATION_VERSION_ROADMAP.md.
Сначала прочитай DEVELOPMENT_PLAN.md, ARCHITECTURE.md и текущий код.
Не делай несвязанных рефакторингов и не реализуй последующие версии.
Соблюдай архитектурные инварианты документа.
Добавь тесты, миграции только при необходимости и обнови документацию контракта.
В конце перечисли изменённые файлы, тесты и известные ограничения.
```

---

# 3. Целевая архитектура

## 3.1. Стиль системы

Основной стиль — **модульный монолит + отдельные worker-процессы**.

Это сознательно предпочтительнее ранних микросервисов:

- алгоритмические модули пока развиваются вместе;
- транзакции и воспроизводимость проще контролировать в одной кодовой базе;
- меньше сетевых контрактов и DevOps-нагрузки;
- при росте нагрузки границы уже должны быть готовы к физическому разделению.

Физически система состоит из:

```text
Browser
  |
  v
Frontend (React + TypeScript + MapLibre)
  |
  v
FastAPI -- PostgreSQL/PostGIS
  |              |
  |              +-- metadata / normalized vector data / run results
  |
  +-- Redis/Queue -- Worker pools
                      |
                      +-- ingestion
                      +-- generation
                      +-- analysis
                      +-- export
                      |
                      v
                Algorithmic Core

Object Storage adapter
  +-- local filesystem in dev
  +-- S3/MinIO-compatible backend in scalable deployment
```

## 3.2. Жёсткие границы модулей

### `core/urban_generator`

Алгоритмическое ядро не импортирует FastAPI, SQLAlchemy, Redis, ARQ и frontend-типы. Оно работает через Python-модели/протоколы и получает подготовленные данные.

Допустимые зависимости ядра: Shapely, GeoPandas/Pyogrio, Rasterio/GDAL, NumPy, SciPy, NetworkX и собственные domain-типы.

### `backend/app`

Отвечает за:

- HTTP API;
- auth-ready границу;
- Pydantic schemas;
- транзакции;
- DB repositories;
- управление uploads/artifacts;
- постановку jobs;
- API-level validation;
- сериализацию результатов.

Контроллер не выполняет GIS-алгоритм напрямую.

### `worker`

Worker является orchestration/adapters-слоем для длительных операций. Он:

- получает `job_id`;
- загружает immutable job payload;
- открывает нужные datasets;
- вызывает algorithmic core;
- пишет stage/result artifacts;
- обновляет progress/state;
- корректно переживает retry.

### `frontend`

Frontend не вычисляет authoritative GIS-результаты. Его задача — управление проектом, запуск операций, отображение слоёв, параметров, прогресса и сравнений.

## 3.3. Data architecture

Данные разделяются на три уровня.

### Raw data

Оригинальные файлы не кладутся в JSON/BYTEA-колонки PostgreSQL.

- dev: `storage/`;
- production-like: S3/MinIO-compatible object storage;
- БД хранит URI, hash, размер, media type, source metadata.

Большие растр-файлы хранятся как GeoTIFF/COG в object storage. Rasterio читает окна, а не грузит весь raster в память.

### Normalized source data

Нормализованные векторные сущности находятся в PostGIS и связаны с `project_id`/`dataset_id`/version.

Обязательные индексы:

- GiST на geometry;
- B-tree на `project_id`, `dataset_id`, `run_id`, `status`;
- composite indexes на частые выборки `(project_id, dataset_id)` и `(run_id, feature_type)`.

Canonical exchange CRS — EPSG:4326. Для вычислений проект имеет `working_srid` в метрической CRS. Производные вычислительные слои обязаны явно хранить SRID и проверяться перед distance/area операциями.

### Generated/run data

Результаты принадлежат конкретному immutable `GenerationRun`.

Нельзя «перезаписывать текущий город» поверх предыдущего результата. Новый запуск создаёт новую версию результатов.

Если объём generated features вырастет до миллионов строк, таблицы должны быть готовы к partitioning по `run_id` или `project_id`; до этого преждевременное partitioning не требуется.

## 3.4. Масштабирование

### API

FastAPI должен быть stateless. Любое состояние находится в PostgreSQL, Redis или object storage. Поэтому API можно горизонтально масштабировать несколькими replicas за reverse proxy.

### Workers

Разные типы нагрузки должны иметь логические очереди:

- `ingest` — импорт/нормализация;
- `generation` — roads/blocks/buildings;
- `analysis` — accessibility/metrics;
- `export` — GeoPackage/CSV/GeoJSON/COG preparation.

Это позволяет отдельно увеличивать число worker-процессов тяжёлого класса, не блокируя короткие jobs.

### PostgreSQL/PostGIS

Необходимы:

- connection pool;
- GiST indexes;
- bounded bbox queries;
- batch inserts вместо row-by-row;
- `EXPLAIN ANALYZE` для тяжёлых запросов;
- `VACUUM/ANALYZE` в production-like окружении;
- серверная пагинация;
- запрет выгрузки огромных FeatureCollection целиком.

### Map rendering

GeoJSON допустим для небольших слоёв и разработки. Для больших слоёв архитектура должна предусматривать MVT (`ST_AsMVT`) или предварительную генерацию tiles. Frontend не должен получать сотни тысяч features одним JSON-ответом.

### Reproducibility

Каждый run хранит:

- `seed`;
- `config_version`;
- полную нормализованную config;
- версии datasets;
- commit SHA;
- working SRID;
- timestamps;
- stage diagnostics;
- metrics.

## 3.5. Job state machine

Длительные операции используют состояния:

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

Stage-level состояние:

```text
pending -> running -> completed | failed | skipped
```

Job handler должен быть идемпотентным: retry одного и того же `job_id` либо продолжает незавершённую стадию, либо безопасно возвращает уже сохранённый результат.

## 3.6. Наблюдаемость

Минимальный production-like уровень:

- structured JSON logs;
- request/job correlation ID;
- duration каждого stage;
- feature counts;
- memory-sensitive diagnostics для raster/graph операций;
- health/readiness;
- метрики количества queued/running/failed jobs;
- сохранение stack trace только в server logs, безопасное сообщение — в API.

---

# 4. Sprint 0 — инженерный baseline (`v0.1.x`)

Цель: воспроизводимая среда разработки, где сервис можно запускать и расширять без архитектурного долга.

| Версия | Один запрос к нейросети | Результат / DoD | Архитектура и масштабируемость |
|---|---|---|---|
| `v0.1.1` | Нормализовать Python workspace, `pyproject.toml`, uv, Ruff, pytest, mypy | Одна команда установки; lint/test команды документированы | Один dependency graph для backend/core/worker, без дублирования версий |
| `v0.1.2` | Довести Docker Compose для API, worker, PostGIS, Redis, frontend | `docker compose up --build` поднимает стек | Все сервисы конфигурируются env-переменными; нет localhost-зависимостей внутри контейнеров |
| `v0.1.3` | Довести FastAPI lifecycle и health endpoints | `/health/live`, `/health/ready`; readiness реально проверяет DB/Redis | API stateless; readiness не выполняет тяжёлых запросов |
| `v0.1.4` | Настроить Alembic lifecycle и базовую миграционную дисциплину | upgrade/downgrade на чистой БД | Никакого `create_all` в production path |
| `v0.1.5` | Завершить frontend shell React/TS/MapLibre | Пустая карта, app shell, API client | API URL конфигурируемый; компоненты отделены от data-fetch layer |
| `v0.1.6` | Настроить CI backend/frontend | lint, typecheck, unit tests, build | CI кэширует зависимости; отдельные jobs не зависят друг от друга без причины |
| `v0.1.7` | Добавить structured logging/correlation IDs | request_id/job_id видны в логах | Логирование централизовано, не `print()` |

**Gate спринта:** любой разработчик клонирует repo, копирует `.env.example`, запускает stack и получает работающий shell.

---

# 5. Sprint 1 — доменная модель и persistence (`v0.2.x`)

Цель: сформировать модель данных, которая не придётся ломать при появлении десятков запусков и версий datasets.

| Версия | Один запрос | DoD | Архитектурный смысл |
|---|---|---|---|
| `v0.2.1` | Уточнить `Project` и working CRS contract | CRUD проекта, `working_srid`, timestamps | CRS является частью project contract, а не скрытым глобальным параметром |
| `v0.2.2` | Реализовать versioned `Dataset` metadata | source, kind, version, checksum, status | Dataset после нормализации immutable; повторный upload = новая version |
| `v0.2.3` | Реализовать `GenerationRun` state model | seed/config/status/commit SHA/dataset refs | Run immutable после `succeeded`; provenance обязателен |
| `v0.2.4` | Добавить `RunStageResult` | stage/status/progress/diagnostics/artifact refs | Поддержка checkpoint/retry без повторного полного pipeline |
| `v0.2.5` | Добавить metadata-модель `Artifact` | URI/hash/type/size/owner/run/dataset | Файл абстрагирован от local/S3 storage |
| `v0.2.6` | Добавить generated entity models | road/block/building/infrastructure с `run_id` | Все результаты namespace-ятся по run; старые run не изменяются |
| `v0.2.7` | Добавить repository/service layer над SQLAlchemy | API не импортирует query details | Позволяет тестировать domain orchestration без HTTP |
| `v0.2.8` | Добавить ключевые DB indexes и constraints | EXPLAIN на типичных lookup без seq scan по крупным таблицам | GiST + B-tree + unique/idempotency constraints |

**Gate:** можно хранить несколько проектов, несколько dataset versions и несколько независимых runs без перезаписи данных.

---

# 6. Sprint 2 — ingest, uploads и нормализация (`v0.3.x`)

Цель: надёжно принимать реальные геоданные и превращать их в одинаковый внутренний формат.

| Версия | Один запрос | DoD | Масштабируемость |
|---|---|---|---|
| `v0.3.1` | Реализовать `ArtifactStore` protocol + LocalArtifactStore | put/get/delete/stat + tests | Storage backend не связан с filesystem path в domain-моделях |
| `v0.3.2` | Реализовать безопасный upload API | size limit, extension whitelist, sanitized filename, checksum | Upload streaming; файл не читается целиком в RAM |
| `v0.3.3` | Реализовать sandbox extraction для Shapefile ZIP | защита от zip-slip, cleanup temp dirs | Лимит количества файлов и распакованного размера |
| `v0.3.4` | Реализовать vector inspection | CRS, layer list, geom types, bbox, count | Использовать Pyogrio/OGR metadata paths, где возможно без полной загрузки |
| `v0.3.5` | Реализовать vector normalization pipeline | make_valid, drop/flag empty, reproject | Batch processing; diagnostics с количеством fixed/rejected features |
| `v0.3.6` | Реализовать batch write в PostGIS | normalized vectors сохраняются транзакционно | Bulk insert/COPY-like strategy, GiST index после/вместе с загрузкой по стратегии |
| `v0.3.7` | Реализовать raster inspection | CRS, transform, nodata, resolution, extent | Не читать весь raster; metadata only |
| `v0.3.8` | Реализовать raster normalization | clip/reproject/resample в derived artifact | Windowed IO; GeoTIFF/COG-friendly output |
| `v0.3.9` | Реализовать ingest job через worker | upload -> queued -> normalized/failed | HTTP не блокируется; retry идемпотентен по dataset version |
| `v0.3.10` | Реализовать OSM extract importer | roads/buildings/POI/landuse/water | PBF обрабатывается streaming/chunked где возможно |
| `v0.3.11` | Добавить source mapping rules | OSM tags -> internal enums | Mapping versioned/configurable, не размазан по importer code |
| `v0.3.12` | Добавить ingest integration tests на fixtures | GeoJSON/GPKG/SHP/GeoTIFF + invalid cases | Небольшие fixtures, одинаковые проверки локально и CI |

**Gate:** реальная территория может быть импортирована без ручной правки геометрий в коде.

---

# 7. Sprint 3 — карта пригодности (`v0.4.x`)

Цель: получить воспроизводимую модель пригодности территории как самостоятельный stage pipeline.

| Версия | Один запрос | DoD | Архитектура |
|---|---|---|---|
| `v0.4.1` | Определить `SuitabilityConfig` и factor protocol | типизированные веса/thresholds | Новые факторы подключаются без изменения aggregator |
| `v0.4.2` | Реализовать hard exclusion mask | water/protected/existing forbidden | Hard mask отделён от soft score |
| `v0.4.3` | Реализовать slope factor из DEM | slope raster + threshold/score | Windowed raster calculations; nodata policy явная |
| `v0.4.4` | Реализовать road proximity factor | distance-based score | Spatial index / rasterized distance transform вместо O(N×M) |
| `v0.4.5` | Реализовать landuse factor | configurable class weights | Mapping не hardcoded в geometry loop |
| `v0.4.6` | Реализовать weighted aggregator | score 0..1 + mask | Числовая стабильность, нормализация весов |
| `v0.4.7` | Сохранить suitability stage artifact | raster/COG + metadata/statistics | Stage result имеет hash/config provenance |
| `v0.4.8` | Добавить deterministic tests | одинаковые входы дают одинаковый raster/statistics | Нет скрытой глобальной random state |

**Gate:** suitability можно пересчитать независимо и использовать последующими стадиями через стабильный контракт.

---

# 8. Sprint 4 — функциональное зонирование (`v0.5.x`)

Цель: делить пригодную территорию на функциональные зоны с контролем долей и ограничений.

| Версия | Один запрос | DoD | Архитектура |
|---|---|---|---|
| `v0.5.1` | Определить zone domain types/config | residential/mixed/public/recreation | Enum/config versioned; алгоритм не зависит от UI labels |
| `v0.5.2` | Реализовать deterministic seed point generator | seed учитывает suitability и fixed zones | Все random операции получают RNG из run seed |
| `v0.5.3` | Реализовать базовый Voronoi partition | полигоны clipped project boundary | Geometry repair после clip обязательно |
| `v0.5.4` | Реализовать suitability-aware zone assignment | target share + suitability | Assignment отделён от geometry partition |
| `v0.5.5` | Реализовать iterative region growth/refinement | min area, adjacency | Ограничить iteration count и логировать convergence |
| `v0.5.6` | Учесть existing/fixed zones | существующие зоны не изменяются | Fixed inputs отделены от generated outputs |
| `v0.5.7` | Добавить zoning diagnostics | доли, unmet targets, tiny polygons | Diagnostics сохраняются как stage metrics |
| `v0.5.8` | Добавить property/integration tests | coverage, non-overlap, valid geometry | Тестировать инварианты, а не точные координаты случайных полигонов |

---

# 9. Sprint 5 — дорожная сеть (`v0.6.x`)

Цель: построить масштабируемое графовое представление существующих дорог и расширять его.

| Версия | Один запрос | DoD | Масштабируемость |
|---|---|---|---|
| `v0.6.1` | Определить RoadGraph domain contract | node/edge attrs, length, class, source | NetworkX остаётся adapter-реализацией, не API контрактом |
| `v0.6.2` | Реализовать line snapping/noding | пересечения становятся узлами | STRtree/spatial index вместо полного попарного сравнения |
| `v0.6.3` | Реализовать graph build from normalized roads | connected graph components | Batch geometry processing, validation diagnostics |
| `v0.6.4` | Реализовать graph cleanup | duplicate edges, tiny segments, dangling artifacts | Thresholds config-driven |
| `v0.6.5` | Реализовать Dijkstra/A* service | shortest path API ядра | Cached weights/graph snapshot в пределах stage |
| `v0.6.6` | Реализовать candidate connection points | suitability/zoning-aware | Ограничивать candidate count spatial sampling'ом |
| `v0.6.7` | Реализовать MST baseline connector | базовая связность новых anchors | MST — вспомогательная стратегия через общий interface |
| `v0.6.8` | Реализовать rule-based road growth | local/collector connections | Стратегия pluggable, bounded iterations |
| `v0.6.9` | Реализовать road classification | arterial/collector/local | Classification правила отделены от генерации геометрии |
| `v0.6.10` | Реализовать road validation | disconnected, excessive dead ends, forbidden intersections | Validation возвращает structured violations |
| `v0.6.11` | Реализовать network metrics | degree, density, circuity, components | Метрики не мутируют graph |
| `v0.6.12` | Добавить performance fixture | средняя реальная сеть проходит под заданный budget | Профилировать noding/pathfinding отдельно |

**Gate:** существующая сеть корректно превращается в граф, расширяется и остаётся связной в пределах выбранной стратегии.

---

# 10. Sprint 6 — кварталы (`v0.7.x`)

| Версия | Один запрос | DoD | Архитектура |
|---|---|---|---|
| `v0.7.1` | Реализовать polygonize roads -> candidate blocks | валидные полигоны | Geometry operations изолированы в blocks module |
| `v0.7.2` | Clip/filter blocks по project/developable area | нет внешних/запрещённых кварталов | Spatial index для constraint intersections |
| `v0.7.3` | Реализовать block metrics | area/perimeter/compactness/aspect | Метрики вычисляются один раз и переиспользуются |
| `v0.7.4` | Реализовать access/frontage validation | block имеет доступ к road | Не выполнять дорогое nearest-search без index |
| `v0.7.5` | Реализовать oversized block split strategy | большие блоки делятся | Strategy interface для будущих альтернатив |
| `v0.7.6` | Реализовать post-split cleanup | tiny slivers merge/drop policy | Все решения отражены в diagnostics |
| `v0.7.7` | Связать blocks с zones и run | FK/domain IDs корректны | Нет implicit spatial join в каждом downstream stage |
| `v0.7.8` | Property tests для block partition | valid/non-overlap/inside boundary | Проверять инварианты на synthetic cases |

---

# 11. Sprint 7 — генерация зданий (`v0.8.x`)

Цель: реалистично заполнять кварталы параметрическими building footprints без 3D и внутренней планировки.

| Версия | Один запрос | DoD | Масштабируемость |
|---|---|---|---|
| `v0.8.1` | Определить BuildingConfig/archetype model | use/floors/setbacks/FAR/coverage | Archetypes data-driven, не ветвления по UI string |
| `v0.8.2` | Реализовать buildable envelope квартала | road/constraint setbacks | Prepared geometries для повторных contains/intersections |
| `v0.8.3` | Реализовать initial parcel/placement grid candidates | bounded candidate set | Не генерировать миллионы random candidates |
| `v0.8.4` | Реализовать basic rectangular footprint placement | valid buildings within envelope | Deterministic RNG; spatial index existing placements |
| `v0.8.5` | Добавить orientation относительно roads/block axis | footprints ориентированы осмысленно | Orientation strategy pluggable |
| `v0.8.6` | Добавить inter-building gap enforcement | нет overlap/min gap | STRtree/index вместо O(N²) по всему run |
| `v0.8.7` | Реализовать coverage/FAR convergence | target density с tolerance | Bounded iterations, diagnostics при недостижимости |
| `v0.8.8` | Назначить floors/use | zone/archetype/config based | Отделить geometry от attribute assignment |
| `v0.8.9` | Рассчитать footprint/GFA | согласованные площади | Все area операции в working CRS |
| `v0.8.10` | Batch persist generated buildings | run/block IDs + metrics | Bulk insert; GiST + run index |
| `v0.8.11` | Добавить stress/property tests | no hard violations, reproducibility | Performance budget на квартал/1000 зданий |

---

# 12. Sprint 8 — население (`v0.9.x`)

| Версия | Один запрос | DoD | Архитектура |
|---|---|---|---|
| `v0.9.1` | Определить PopulationConfig | m²/person, occupancy, residential share | Versioned assumptions сохраняются в run config |
| `v0.9.2` | Реализовать residents per building | deterministic numeric result | Pure function, без DB внутри formula |
| `v0.9.3` | Агрегировать population по blocks/zones | consistency sums | Aggregation service отдельно от persistence |
| `v0.9.4` | Импортировать population raster calibration source | source metadata + clipping | Windowed raster sampling |
| `v0.9.5` | Реализовать spatial calibration | generated distribution приближена к исходному raster | Calibration optional adapter, базовая модель работает без raster |
| `v0.9.6` | Добавить population density metrics | people/ha и diagnostics | Единицы измерения документированы |
| `v0.9.7` | Тесты числовой устойчивости | edge cases: zero GFA, mixed use, nodata | Без NaN/inf в API results |

---

# 13. Sprint 9 — инфраструктура и доступность (`v0.10.x`)

| Версия | Один запрос | DoD | Масштабируемость |
|---|---|---|---|
| `v0.10.1` | Определить InfrastructureType config | demand/capacity/max distance/zones | Категории добавляются конфигом |
| `v0.10.2` | Реализовать demand calculation | unmet demand per block/category | Pure calculation stage |
| `v0.10.3` | Реализовать candidate location generator | bounded set candidates | Spatial sampling/centroids, candidate cap |
| `v0.10.4` | Реализовать snapping demand/candidates к road graph | graph node mapping | Один spatial nearest index на graph snapshot |
| `v0.10.5` | Реализовать batch shortest-path accessibility | distance matrix/coverage | Multi-source Dijkstra там, где выгоднее N отдельных запусков |
| `v0.10.6` | Реализовать greedy facility placement | максимизация incremental coverage | Кэшировать coverage sets; не пересчитывать всё с нуля |
| `v0.10.7` | Учесть existing infrastructure | существующие объекты обслуживают demand | Fixed + generated facilities в общем coverage model |
| `v0.10.8` | Persist facilities/coverage stats | run/category/capacity | Batch writes, structured diagnostics |
| `v0.10.9` | Реализовать category coverage metrics | population covered, p50/p90 distance | Metrics adapter переиспользует accessibility results |
| `v0.10.10` | Integration tests на synthetic town | known demand -> expected coverage range | Проверять диапазоны/инварианты, не хрупкие exact paths |

---

# 14. Sprint 10 — единая система ограничений (`v0.11.x`)

Цель: убрать правила из отдельных алгоритмов в общий расширяемый constraint engine.

| Версия | Один запрос | DoD | Архитектура |
|---|---|---|---|
| `v0.11.1` | Определить Constraint protocol/domain model | hard/soft, stage applicability, code | Constraint не знает FastAPI/SQLAlchemy |
| `v0.11.2` | Реализовать geometry intersection constraints | water/protected etc. | Prepared geometry/index reuse |
| `v0.11.3` | Реализовать distance/setback constraints | roads/buildings/features | Все distance операции требуют metric CRS guard |
| `v0.11.4` | Реализовать raster threshold constraints | slope/other raster factors | Window/sample API, без full raster load |
| `v0.11.5` | Реализовать aggregate constraints | coverage/FAR/density | Отделить feature-level от aggregate validation |
| `v0.11.6` | Реализовать soft penalties | structured penalty score | Не смешивать penalty с hard invalidity |
| `v0.11.7` | Реализовать ValidationReport | violations summary + geometries/refs | UI может показать проблемные области без повторного вычисления |
| `v0.11.8` | Перевести stages на общий engine | нет дублированных hard rules | Regression tests сохраняют поведение |

---

# 15. Sprint 11 — метрики и score (`v0.12.x`)

| Версия | Один запрос | DoD | Архитектура |
|---|---|---|---|
| `v0.12.1` | Определить Metric protocol/registry | name/unit/scope/value | Новая метрика не требует менять центральный if/else |
| `v0.12.2` | Реализовать land/building metrics | developed area, coverage, FAR | Переиспользовать сохранённые stage aggregates |
| `v0.12.3` | Реализовать population metrics | density/distribution | Явные units и null policy |
| `v0.12.4` | Реализовать road metrics adapter | density/connectivity/circuity | Не перестраивать graph повторно |
| `v0.12.5` | Реализовать infrastructure metrics | coverage/network distance percentiles | Использовать accessibility cache/artifact |
| `v0.12.6` | Реализовать constraint metrics | hard count/soft penalty | Metric layer не повторяет validation logic |
| `v0.12.7` | Реализовать configurable composite score | weighted normalized metrics | Weight config versioned; raw metrics всегда сохраняются |
| `v0.12.8` | Добавить metric regression fixtures | ожидаемые диапазоны и стабильность | Detect unintended algorithm drift |

---

# 16. Sprint 12 — pipeline, сценарии и воспроизводимость (`v0.13.x`)

| Версия | Один запрос | DoD | Масштабируемость |
|---|---|---|---|
| `v0.13.1` | Определить Stage interface и pipeline context | stable inputs/outputs | Stage можно запускать отдельно и в pipeline |
| `v0.13.2` | Реализовать stage dependency graph | корректный порядок и skip rules | Не hardcode длинную функцию `run_all()` без metadata |
| `v0.13.3` | Реализовать persistent stage checkpoints | restart с последней completed stage | Artifact hashes + stage config hash |
| `v0.13.4` | Реализовать generation worker job | queued/running/progress/result | Job handler идемпотентен |
| `v0.13.5` | Реализовать cancellation | cooperative cancellation между stages | Не kill DB transaction в случайной точке |
| `v0.13.6` | Реализовать retry policy | transient vs permanent errors | Exponential backoff только для transient classes |
| `v0.13.7` | Реализовать scenario batch 3–10 seeds | parent batch + child runs | Ограничение concurrency/queue pressure |
| `v0.13.8` | Реализовать run compare backend | metrics delta/ranking | Сравнение читает persisted metrics, не запускает GIS заново |
| `v0.13.9` | Реализовать exact rerun | clone config/dataset refs/seed | Проверка dataset availability/version hashes |
| `v0.13.10` | Сохранить provenance manifest | config/code/data/stages/artifacts | Один manifest можно приложить к ВКР как доказательство воспроизводимости |

---

# 17. Sprint 13 — полноценный GIS frontend (`v0.14.x`)

Цель: пользователь может провести весь workflow без прямого обращения к API/БД.

| Версия | Один запрос | DoD | Масштабируемость |
|---|---|---|---|
| `v0.14.1` | Создать frontend data layer/API client | typed requests/errors | Компоненты не вызывают `fetch` хаотично |
| `v0.14.2` | Реализовать Project workspace layout | map/sidebar/panels/routes | Layout готов к lazy modules |
| `v0.14.3` | Реализовать dataset manager | upload/status/version/error UI | Large upload progress, без base64 в браузере |
| `v0.14.4` | Реализовать layer registry/tree | visibility/order/style | Один declarative layer registry |
| `v0.14.5` | Отобразить source layers на карте | boundary/roads/buildings/constraints | bbox-based loading; не обязательно весь слой целиком |
| `v0.14.6` | Реализовать generation parameter forms | schema-driven config | UI model соответствует versioned backend config |
| `v0.14.7` | Реализовать job progress UI | polling/SSE-ready abstraction | Polling с backoff; data layer допускает будущий SSE/WebSocket |
| `v0.14.8` | Отобразить generated zoning/roads/blocks/buildings | run-selectable layers | Run namespace, отсутствие смешивания сценариев |
| `v0.14.9` | Реализовать metrics dashboard | raw metrics + score + units | Не вычислять authoritative metrics на клиенте |
| `v0.14.10` | Реализовать compare mode | 2–N run table + map switch | Lazy-load run layers, не держать все features в памяти |
| `v0.14.11` | Реализовать violations/problem areas | click -> details | Геометрии violations приходят отдельным layer endpoint |
| `v0.14.12` | Добавить frontend e2e happy path | create/upload/run/compare | Playwright test на стабильных fixtures |

### Масштабируемый rendering contract

До условного порога небольшие слои могут отдаваться GeoJSON. Для тяжёлых generated/source layers API должен иметь tile-ready путь:

```text
/api/v1/projects/{project_id}/layers/{layer_id}/tiles/{z}/{x}/{y}.mvt
```

Реализация MVT может появиться в следующем спринте, но frontend layer abstraction не должна быть привязана только к GeoJSON.

---

# 18. Sprint 14 — экспорт, производительность, эксплуатация и эксперименты (`v0.15.x`)

Цель: превратить функциональную систему в проект, который устойчиво демонстрируется, профилируется и пригоден для ВКР-экспериментов.

| Версия | Один запрос | DoD | Архитектура/масштабируемость |
|---|---|---|---|
| `v0.15.1` | Реализовать export job GeoJSON | выбранные run layers -> artifact | Export asynchronous для больших слоёв |
| `v0.15.2` | Реализовать GeoPackage export | multi-layer GPKG | Streaming/batch read из DB |
| `v0.15.3` | Реализовать CSV metrics export | run/scenario comparison | Не дублировать metric calculations |
| `v0.15.4` | Добавить MVT endpoint для больших vector layers | MapLibre tile source работает | `ST_AsMVT`, bbox/spatial index, tile cache headers |
| `v0.15.5` | Добавить S3/MinIO ArtifactStore adapter | local и S3 backends имеют одинаковый contract | Подготовка к горизонтальному масштабированию API/worker |
| `v0.15.6` | Разделить worker queues и concurrency config | ingest/generation/analysis/export | Независимое масштабирование worker pools |
| `v0.15.7` | Провести DB query profiling | список top slow queries + indexes/fixes | `EXPLAIN ANALYZE`, bbox filters, batch operations |
| `v0.15.8` | Добавить performance benchmarks | ingest, graph, buildings, accessibility | Зафиксированы dataset size и machine-independent относительные metrics |
| `v0.15.9` | Добавить operational diagnostics | queue depth, job duration, failure rate | Базовые metrics пригодны для Prometheus adapter позже |
| `v0.15.10` | Подготовить production-like Docker profile | reverse proxy-ready, no dev mounts | Stateless API, shared object storage, DB/Redis externalizable |
| `v0.15.11` | Подготовить experiment runner | matrix seeds/configs -> batch runs | Эксперименты воспроизводимы и не требуют кликов UI |
| `v0.15.12` | Подготовить experiment report exporter | run manifests + metrics tables | Данные сразу пригодны для таблиц/графиков ВКР |

---

# 19. Release `v1.0.0` — интеграционный gate

`v1.0.0` не добавляет крупную новую функцию. Это версия стабилизации.

Один финальный запрос к нейросети можно формулировать как release-hardening, но выполнять только после всех предыдущих minor gates.

Обязательные условия:

- чистый clone разворачивается по README;
- migrations проходят на пустой БД;
- поддерживаются минимум GeoJSON/GPKG/Shapefile/GeoTIFF;
- реальная территория импортируется и нормализуется;
- pipeline проходит suitability → zoning → roads → blocks → buildings → population → infrastructure → validation → metrics;
- существующий город может быть fixed input для expansion mode;
- минимум 3 сценария различаются seed/parameters и сравниваются;
- hard constraint violations отображаются;
- результат экспортируется;
- повтор запуска с теми же inputs/config/seed воспроизводим в заданных tolerance;
- API не блокируется тяжёлыми GIS-операциями;
- большие файлы/слои не обязаны полностью загружаться в RAM/browser;
- backend/core/frontend tests проходят;
- 1–2 реальные территории покрыты экспериментами.

---

# 20. Критический путь

Строгая зависимость:

```text
v0.1 baseline
 -> v0.2 domain/persistence
 -> v0.3 ingest
 -> v0.4 suitability
 -> v0.5 zoning
 -> v0.6 roads
 -> v0.7 blocks
 -> v0.8 buildings
 -> v0.9 population
 -> v0.10 infrastructure
 -> v0.11 constraints consolidation
 -> v0.12 metrics
 -> v0.13 orchestration/scenarios
 -> v0.14 complete UI
 -> v0.15 performance/exports/experiments
 -> v1.0.0
```

Некоторые версии допускают параллельность после стабилизации контрактов. Например frontend shell может развиваться раньше, но UI конкретной стадии нельзя считать завершённым до стабилизации backend contract этой стадии.

---

# 21. Архитектурные gates между спринтами

Перед переходом к следующему minor-релизу нужно отвечать «да» на вопросы:

### Data gate

- данные имеют явного владельца (`project/dataset/run`)?
- CRS известна и проверяется?
- нет неограниченной загрузки всего dataset в память без необходимости?
- есть пространственный индекс?

### API gate

- тяжёлая операция вынесена в job?
- endpoint ограничивает pagination/bbox/size?
- ошибки типизированы?
- повтор запроса не создаёт неконтролируемые дубликаты?

### Algorithm gate

- алгоритм детерминирован относительно seed/config?
- есть synthetic fixture?
- есть time/iteration bound?
- geometry validity проверяется на границе стадии?

### Worker gate

- job можно retry?
- есть progress/stage state?
- есть cleanup временных ресурсов?
- failure одной job не загрязняет run другой job?

### Database gate

- запросы фильтруются по `project_id/run_id`?
- нужные FK/indexes существуют?
- большие вставки batch-овые?
- миграция обратима либо явно документирована как irreversible?

### Frontend gate

- state не дублирует authoritative backend state?
- большие layers lazy/bbox/tile load?
- run selection явный?
- ошибки и progress видимы пользователю?

---

# 22. Что нельзя делать даже ради ускорения

Не следует:

- помещать весь pipeline в один FastAPI endpoint;
- хранить большие GeoTIFF/ZIP/GPKG как BYTEA в основной БД;
- отправлять огромные GeoJSON FeatureCollection без pagination/bbox/tiles;
- использовать EPSG:4326 для area/distance вычислений;
- хранить «текущий результат» без `run_id`;
- мутировать completed run;
- разбрасывать одинаковые constraint checks по buildings/roads/infrastructure;
- выполнять N×M геометрические сравнения без spatial index;
- строить отдельный микросервис для каждого алгоритма на ранней стадии;
- делать frontend источником бизнес-правил;
- скрывать algorithm parameters в magic constants;
- использовать глобальный `random` вместо переданного seeded RNG;
- переписывать предыдущую dataset version новым upload;
- считать интегральный score единственным результатом оценки.

---

# 23. Эволюция после `v1.0.0`

Архитектура должна позволять без полного переписывания добавить:

- Overture как дополнительный source adapter;
- pgRouting вместо/в дополнение NetworkX для отдельных accessibility workloads;
- Celery/RQ/другой queue backend вместо ARQ через job abstraction;
- distributed object storage;
- vector tile cache/CDN;
- facility-location optimization вместо greedy;
- более продвинутые road growth strategies;
- новые infrastructure categories;
- новые constraints/metrics;
- auth/multi-user tenancy;
- 3D визуализацию как отдельный presentation layer, не меняющий 2D algorithmic core.

При этом 3D, BIM и внутренняя планировка зданий не являются условием полноценности первой версии проекта.
