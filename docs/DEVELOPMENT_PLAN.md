# Urban Development Generator — техническое задание и план разработки

> Статус документа: нормативное ТЗ для версии `v1.0.0`.
>
> Этот документ определяет, **что именно строится, как устроена система, какие архитектурные инварианты нельзя нарушать, какие качества считаются обязательными и как валидируется результат**. Атомарная последовательность реализации вынесена в `docs/IMPLEMENTATION_VERSION_ROADMAP.md`.
>
> Термины **ДОЛЖЕН**, **СЛЕДУЕТ**, **МОЖЕТ** используются в инженерном смысле: обязательное, рекомендуемое и допустимое требование.

---

## 1. Назначение проекта

**Urban Development Generator** — клиент-серверный 2D GIS-сервис процедурного моделирования развития городской застройки с учётом пространственных, инфраструктурных и демографических ограничений.

Тема ВКР:

> **«Разработка сервиса процедурной генерации и моделирования развития городской застройки с учётом пространственных, инфраструктурных и демографических ограничений».**

Система поддерживает два режима:

1. **`EXPANSION` — основной режим.** Существующий город считается фиксированным состоянием, а система генерирует его развитие на доступной территории.
2. **`FROM_SCRATCH` — дополнительный режим.** Используется то же алгоритмическое ядро, но фиксированное городское состояние может быть пустым.

Главный продукт системы — не «картинка города», а **воспроизводимый сценарий изменения территории**:

```text
FixedWorldState
+ DevelopableArea
+ Constraints
+ DemographicScenario
+ GenerationConfig
+ Seed
→
GeneratedDelta
+ ValidationReport
+ Metrics
+ Provenance
```

`GeneratedDelta` не изменяет исходный город: каждый запуск создаёт отдельный immutable-результат.

---

## 2. Критерии полноценности `v1.0.0`

К защите должна существовать полноценная первая версия продукта, которая на 1–2 реальных территориях умеет:

- создавать и хранить проекты;
- задавать режим `EXPANSION` или `FROM_SCRATCH`;
- загружать и версионировать исходные данные;
- поддерживать как минимум GeoJSON, GeoPackage, Shapefile ZIP и GeoTIFF;
- импортировать OSM extract/PBF как источник дорог, зданий, POI, landuse и water;
- хранить оригиналы файлов отдельно от нормализованных данных;
- проверять и исправлять геометрии;
- явно управлять `source_crs`, `working_crs` и `presentation_crs`;
- формировать immutable `TerritorySnapshot` для запуска;
- разделять существующие/fixed и generated сущности;
- применять единый constraint engine начиная с ранних стадий;
- строить каноническую raster-карту пригодности территории;
- генерировать функциональные зоны;
- расширять существующую дорожную сеть или строить базовую сеть с нуля;
- выделять кварталы и при необходимости условные строительные участки;
- генерировать 2D building footprints нескольких архетипов;
- оценивать этажность, GFA, население и рабочие места;
- учитывать демографический сценарий и возрастные группы;
- оценивать спрос на инфраструктуру;
- размещать образование, здравоохранение, торговлю и рекреацию;
- считать доступность по дорожной сети;
- учитывать существующую инфраструктуру;
- проверять hard/soft constraints;
- сохранять геометрию нарушений;
- считать набор исходных метрик и объяснимый composite score;
- запускать серии сценариев с разными seed/параметрами;
- воспроизводить запуск по данным, config и seed;
- показывать прогресс длительных операций;
- визуализировать source/generated/validation layers;
- сравнивать несколько runs;
- экспортировать результаты;
- формировать данные для экспериментов ВКР;
- проходить обязательный CI.

### 2.1. Что сознательно не входит в `v1.0.0`

Не являются обязательными:

- 3D-модели и mesh-геометрия;
- BIM;
- фасады;
- квартиры, комнаты и внутренняя планировка;
- транспортная микросимуляция;
- turn-by-turn автомобильная модель;
- ML/нейросетевая генерация городской формы;
- многопользовательская SaaS-аутентификация;
- кадастрово-юридическая модель участков;
- распределённые микросервисы для каждого алгоритма.

Отсутствие этих функций не делает продукт «демо»: `v1.0.0` должна быть полноценной 2D GIS-системой в заявленной предметной области.

---

## 3. Основные пользовательские сценарии

### 3.1. Расширение существующего города

Пользователь:

1. создаёт проект;
2. задаёт границу анализа и рабочую CRS;
3. импортирует существующие дороги, здания, инфраструктуру, water/landuse/constraints, DEM и демографические источники;
4. нормализует данные;
5. выбирает `EXPANSION`;
6. фиксирует исходный `TerritorySnapshot`;
7. задаёт демографический и пространственный сценарий;
8. запускает генерацию;
9. наблюдает прогресс по стадиям;
10. просматривает generated delta поверх существующего города;
11. анализирует violations и metrics;
12. запускает альтернативные варианты;
13. сравнивает и экспортирует результаты.

### 3.2. Генерация структуры с нуля

`FROM_SCRATCH` использует те же stages и constraints, но fixed urban layers могут отсутствовать. Нельзя создавать отдельный «упрощённый второй pipeline»: различие режимов выражается входным состоянием и конфигурацией.

### 3.3. Исследовательский batch

Пользователь или experiment runner задаёт матрицу:

```text
territory × config × seed × ablation
```

и получает серию независимых runs с provenance, metrics и отчётной таблицей.

---

## 4. Нефункциональные требования

### 4.1. Воспроизводимость

Любой completed run ДОЛЖЕН хранить:

- `run_id`;
- `run_mode`;
- `seed`;
- normalized `GenerationConfig`;
- `config_schema_version`;
- список `DatasetVersion`;
- hash входных артефактов;
- `working_srid`;
- commit SHA приложения;
- версии алгоритмических стадий;
- timestamps;
- stage diagnostics;
- итоговые metrics;
- ссылки на stage artifacts.

Повторный запуск с теми же inputs/config/seed должен давать эквивалентный результат в заранее определённых численных tolerances.

### 4.2. Детерминизм

Алгоритмическое ядро не использует скрытый global random state. RNG передаётся через `RunContext`/`StageContext`.

### 4.3. Надёжность

- completed run immutable;
- dataset version immutable после успешной нормализации;
- retry job не создаёт неконтролируемые дубликаты;
- частичный сбой stage не должен помечать весь результат как успешный;
- временные артефакты очищаются;
- orphan artifacts могут быть обнаружены и удалены.

### 4.4. Масштабируемость

Архитектура должна позволять:

- несколько stateless API replicas;
- отдельное масштабирование worker pools;
- замену local artifact storage на S3/MinIO;
- замену/добавление network backend;
- MVT для больших слоёв;
- partitioning generated tables при реальной необходимости;
- выполнение тяжёлых операций вне HTTP request lifecycle.

### 4.5. Целевой envelope `v1`

Оптимизация ведётся для зафиксированного класса задач, а не абстрактного «бесконечного города».

**Reference profile:**

- AOI: 25–100 км²;
- source roads: до ~50 000 segments;
- source/generated buildings: до ~100 000–150 000;
- blocks/parcels: до ~20 000;
- DEM/suitability: типичная resolution 10–30 м;
- scenarios per batch: 3–10;
- reference machine: 8 vCPU, 16 GB RAM, SSD.

**Начальные performance targets** могут быть уточнены после первого benchmark, но изменение targets должно фиксироваться:

- API metadata endpoints: p95 < 300 ms без тяжёлых GIS-вычислений;
- bbox vector query: p95 < 1.5 s на reference dataset;
- map не загружает сотни тысяч объектов единым GeoJSON;
- полный run на 25 км²: целевой порядок < 10 минут;
- полный run на 100 км²: целевой порядок < 30 минут;
- peak memory полного worker process: < 12 GB;
- ни одна стадия не должна иметь неограниченный O(N×M) spatial loop без индекса/ограничения кандидатов.

Targets — измеримый engineering gate, а не обещание одинакового времени на любой машине.

---

## 5. Архитектурный стиль

### 5.1. Решение

Основной стиль: **клиент-серверная архитектура + модульный монолит + отдельные worker-процессы + ports/adapters на внешних границах**.

Ранние микросервисы запрещены: они не дают ценности для ВКР, но усложняют транзакции, отладку, воспроизводимость и deployment.

### 5.2. Физическая схема

```text
Browser
  |
  v
React + TypeScript + MapLibre
  |
  | HTTPS / JSON / GeoJSON / MVT
  v
FastAPI (stateless API)
  | \
  |  \---- PostgreSQL + PostGIS
  |
  +------ Redis / Job Queue
             |
             +-- ingest workers
             +-- generation workers
             +-- analysis workers
             +-- export workers
                      |
                      v
             Algorithmic Core
                      |
             ArtifactStore port
                 /          \
        Local filesystem   S3/MinIO
```

### 5.3. Направление зависимостей

Допустимо:

```text
frontend -> HTTP contracts
backend/api -> application services -> repositories/ports
worker -> application orchestration -> core + ports
core -> domain contracts + GIS/scientific libraries
adapters -> ports
```

Недопустимо:

- `core` импортирует FastAPI/SQLAlchemy/Redis/ARQ;
- React-компонент содержит authoritative business/GIS rules;
- HTTP-controller запускает полный GIS stage синхронно;
- domain object хранит локальный filesystem path как единственную модель storage;
- algorithm stage обращается напрямую к глобальной DB session.

---

## 6. Репозиторий и границы ответственности

```text
urban-development-generator/
├── backend/
│   └── app/
│       ├── api/                  # HTTP controllers
│       ├── application/          # use cases/orchestration
│       ├── core/                 # config/logging/security
│       ├── db/                   # session/repositories
│       ├── models/               # persistence models
│       ├── schemas/              # API schemas
│       └── services/             # adapters/application services
├── core/
│   └── urban_generator/
│       ├── domain/               # pure domain contracts
│       ├── constraints/
│       ├── territory/
│       ├── suitability/
│       ├── zoning/
│       ├── roads/
│       ├── blocks/
│       ├── buildings/
│       ├── demography/
│       ├── infrastructure/
│       ├── validation/
│       ├── metrics/
│       └── pipeline/
├── worker/
│   ├── jobs/
│   └── adapters/
├── frontend/
├── database/
│   └── migrations/
├── tests/
│   ├── unit/
│   ├── property/
│   ├── integration/
│   ├── e2e/
│   └── performance/
├── docs/
├── scripts/
├── data/                         # fixtures/demo, не runtime storage
└── storage/                      # dev-only local artifact root
```

---

## 7. Ключевая доменная модель

### 7.1. `Project`

Содержит id/name, boundary, `working_srid`, locale/units metadata, default mode и timestamps. `working_srid` — часть project contract, а не скрытая настройка.

### 7.2. `Dataset` и `DatasetVersion`

Нужно разделять:

- `Dataset` — логический источник, например «существующие дороги»;
- `DatasetVersion` — immutable конкретная загрузка/версия.

Повторный upload не перезаписывает старую версию.

### 7.3. `TerritorySnapshot`

Immutable domain object, привязанный к run. Содержит ссылки/представления на:

- project boundary;
- fixed roads;
- fixed buildings;
- fixed infrastructure;
- fixed zoning/landuse;
- water;
- environmental/planning constraints;
- terrain/DEM;
- population/demographic source;
- developable area;
- working CRS.

Алгоритмические stages получают `TerritorySnapshot`, а не набор случайных DB-запросов.

### 7.4. `RunMode`

```text
EXPANSION
FROM_SCRATCH
```

В `EXPANSION` source urban objects по умолчанию fixed. Generated entities всегда отделены.

### 7.5. `GenerationRun`

```text
queued -> running -> succeeded
                  -> failed
                  -> cancelled
```

Completed run не мутируется. Новый расчёт = новый run.

### 7.6. `RunStageResult`

```text
pending -> running -> completed
                   -> failed
                   -> skipped
```

Хранит input fingerprint, config fingerprint, artifact refs, diagnostics, duration, counters и error information.

### 7.7. `Artifact`

Metadata: id, owner type/id, URI, content type, logical type, size, checksum, lifecycle state, created_at.

Lifecycle:

```text
temporary -> ready -> referenced
             |
             -> expired/deleted
```

### 7.8. Generated entities

Отдельные модели:

- `GeneratedZone`;
- `GeneratedRoad`;
- `GeneratedBlock`;
- `GeneratedParcel` — условный строительный участок, не юридический кадастр;
- `GeneratedBuilding`;
- `GeneratedInfrastructure`.

Все содержат `run_id`; downstream stage не должен «угадывать текущий run».

---

## 8. CRS и геометрические инварианты

Система явно различает:

1. **`source_crs`** — CRS конкретного исходного файла;
2. **`working_crs`** — метрическая CRS проекта для area/distance/slope-dependent расчётов;
3. **`presentation_crs`** — CRS/проекция доставки данных в web-карту/tiles.

Правила:

- area/distance запрещено считать в EPSG:4326;
- каждый stage проверяет CRS на входной границе;
- перепроекция выполняется централизованно;
- вход без CRS не «угадывается» молча;
- geometry validity проверяется после операций, способных её нарушить;
- допустимые geometry types задаются layer contract;
- precision/tolerance policy хранится централизованно.

---

## 9. Архитектура данных

### 9.1. Raw

Оригинальные ZIP/GPKG/PBF/GeoTIFF не хранятся как BYTEA в основной БД.

Хранилище:

- dev: LocalArtifactStore;
- scalable deployment: S3/MinIO-compatible store.

БД содержит metadata, checksum и URI.

### 9.2. Ingest staging

Допускается generic staging representation для инспекции/нормализации.

### 9.3. Canonical normalized layers

После нормализации предпочтительны предметные таблицы/слои:

- source_roads;
- source_buildings;
- source_landuse;
- source_water;
- source_facilities;
- source_constraints;
- raster dataset metadata.

Не следует превращать одну гигантскую `SpatialFeature(properties JSONB)` в единственный источник всех данных.

### 9.4. Generated data

Generated tables namespace-ятся по `run_id`.

Индексы:

- GiST/SP-GiST по необходимости на geometry;
- B-tree на `project_id`, `dataset_version_id`, `run_id`, `status`;
- composite indexes под реальные access patterns.

Partitioning вводится только после измерений, а не заранее.

---

## 10. Ранние core contracts

Эти контракты должны существовать **до реализации suitability/zoning/roads**.

### 10.1. `RunContext`

Core `RunContext` содержит только infrastructure-neutral deterministic run semantics: run id/mode, seed/namespaced RNG factory, project working CRS, immutable config refs и correlation identifiers.

Cancellation, logging sinks, DB sessions, repositories, queue clients и progress reporters принадлежат orchestration/application layer, а не core `RunContext`. Канонический execution boundary определён в `docs/02-architecture/PIPELINE_MODEL.md`.

### 10.2. `Stage`

Каждая coarse algorithmic стадия доступна через общий typed contract:

```text
name
version
dependencies
validate_input(value)
execute(snapshot, context, stage_input, config) -> StageResult
```

Stage запускается независимо от HTTP/DB/queue и тестируется in-memory. Существующие алгоритмы могут оставаться отдельными composable capabilities; Stage adapter собирает их и не дублирует реализацию.

### 10.3. `StageResult`

Core `StageResult` содержит typed output, deterministic fingerprint и structured non-fatal diagnostics.

Lifecycle/progress/timestamps, persisted input/config hashes, artifact publication и retry/cancellation state принадлежат execution/persistence layer (`RunStageResult`, `Artifact`, `Job`) и не входят в core StageResult.

### 10.4. Constraint contract

Минимальная модель:

```text
Constraint
ConstraintSeverity: HARD | SOFT
ConstraintScope
ConstraintResult
ValidationReport
```

Конкретные ограничения добавляются по мере стадий, но общий contract существует заранее.

### 10.5. Storage/network ports

Как минимум:

- `ArtifactStore`;
- `NetworkBackend`;
- repository/query ports там, где это реально снижает связанность.

`NetworkX` — реализация backend для v1, а не публичный domain contract.

---

## 11. Job orchestration и консистентность

### 11.1. Job model

HTTP создаёт job/run и быстро отвечает. Worker исполняет тяжёлую задачу. API не держит request открытым во время генерации.

### 11.2. Идемпотентность

У job есть `idempotency_key`/уникальные invariants.

Retry:

- не создаёт второй normalized dataset для того же dataset version;
- не дублирует stage output;
- либо продолжает, либо безопасно возвращает готовый stage result.

### 11.3. DB + queue consistency

PostgreSQL и Redis не находятся в одной транзакции. Для v1 DB хранит authoritative `Job`/outbox state, а dispatcher/enqueue logic может повторно доставить ещё не подтверждённую работу.

### 11.4. Artifact publish protocol

```text
write temporary artifact
-> upload/fsync complete
-> checksum
-> DB transaction records Artifact
-> mark ready
-> link StageResult
-> stage completed
```

При сбое temporary/orphan artifacts обнаруживаются GC-процедурой.

---

## 12. Алгоритмический pipeline

Канонический порядок:

```text
prepare_snapshot
-> evaluate_constraints
-> suitability
-> zoning
-> roads
-> blocks_and_parcels
-> buildings
-> demography
-> infrastructure
-> final_validation
-> metrics
-> persist_manifest
```

Stages могут иметь дополнительные зависимости, но не должны существовать как одна гигантская `run_all()` функция.

Канонический stage/execution contract, граница между core StageResult и persisted RunStageResult, а также правила адаптации уже реализованных capabilities определены в `docs/02-architecture/PIPELINE_MODEL.md`. Отдельная параллельная модель stage identity запрещена.

---

## 13. Constraint Engine

Constraint engine является фундаментом, а не поздним рефакторингом.

### 13.1. Geometry constraints

- water exclusion;
- protected area exclusion;
- boundary containment;
- road/building setbacks;
- minimum gap.

### 13.2. Raster constraints

- max slope;
- suitability threshold;
- иные grid-based ограничения.

### 13.3. Aggregate constraints

- max coverage;
- target FAR bounds;
- density bounds;
- capacity constraints.

### 13.4. Hard/soft

**Hard** делает состояние недопустимым. **Soft** создаёт penalty/diagnostic, но не обязательно блокирует решение.

`ConstraintResult` обязан иметь code, severity, object/stage reference и при возможности problem geometry.

---

## 14. Suitability

Каноническое представление — **raster**.

`SuitabilityRaster`:

- float score `0..1`;
- hard exclusion mask;
- CRS;
- affine transform;
- resolution;
- nodata policy;
- factor metadata.

Минимальные факторы:

- slope;
- water/protected mask;
- existing urban footprint;
- road proximity;
- landuse;
- user constraints.

Полигональные представления — derived artifacts для визуализации/экспорта, но не альтернативная каноническая модель.

---

## 15. Функциональное зонирование

Минимальные classes:

- residential;
- mixed/commercial;
- public/infrastructure;
- recreation/green.

Требования:

- fixed existing zones сохраняются;
- generated zones не перекрываются;
- зоны покрывают только developable area согласно policy;
- seed deterministic;
- target shares и minimum areas configurable;
- suitability влияет на assignment;
- iterative refinement имеет max iterations/convergence diagnostics.

---

## 16. Дорожная сеть

### 16.1. Domain contract

Road graph не равен `networkx.Graph`. Domain содержит node/edge records, attributes и network semantics.

### 16.2. Семантика v1

Для accessibility используется упрощённая пешеходно-дорожная сеть:

- length-based cost;
- без сложных turn restrictions;
- one-way автомобильная семантика может сохраняться как metadata, но не обязана определять pedestrian accessibility.

### 16.3. Импорт

При noding учитываются OSM hints:

- bridge;
- tunnel;
- layer/grade separation.

Геометрическое пересечение линий не всегда является graph intersection.

### 16.4. Генерация

Поддерживаются:

- fixed existing network;
- snapping/noding;
- candidate anchors;
- least-cost/A* connection;
- MST как baseline connector;
- rule-based growth;
- classes local/collector/arterial;
- validation connectivity/dead ends/forbidden crossings.

### 16.5. Масштабирование

`NetworkBackend` позволяет позднее добавить pgRouting/другую реализацию без переписывания предметных алгоритмов.

---

## 17. Кварталы и условные участки

Blocks формируются из road network polygonization и developable mask.

Проверяются:

- area;
- perimeter;
- compactness;
- aspect ratio;
- holes;
- frontage/access;
- constraint intersections.

Oversized blocks делятся bounded strategy.

`GeneratedParcel` — упрощённый planning lot для building placement:

- не является кадастровым участком;
- помогает моделировать frontage;
- имеет buildable envelope;
- может отсутствовать для типов застройки, где block-level placement достаточен.

---

## 18. Здания

`v1.0` генерирует только 2D footprint + attributes.

### 18.1. Building archetypes

Минимальный data-driven набор:

- detached/point;
- bar;
- perimeter;
- simplified courtyard;
- public;
- commercial.

Не каждый zone обязан использовать все archetypes.

### 18.2. Ограничения

- buildable envelope;
- road setbacks;
- inter-building gap;
- hard constraints;
- max coverage;
- FAR target/range;
- slope;
- orientation/frontage.

### 18.3. Атрибуты

- use;
- floors;
- footprint area;
- GFA;
- residents;
- jobs estimate;
- block/parcel/run refs.

---

## 19. Демографическая модель

Простого `GFA / m² per person` недостаточно для формулировки темы ВКР.

### 19.1. `DemographicScenario`

Минимальные параметры:

- total population target или growth target;
- occupancy/vacancy;
- m² per person;
- average household size;
- share of residential GFA;
- age groups;
- working population ratio.

Рекомендуемые группы:

```text
0–6
7–17
18–64
65+
```

### 19.2. Расчёт

```text
residential GFA
-> usable residential area
-> residents
-> demographic composition
```

При наличии population raster разрешается spatial calibration, но базовая модель должна работать без него.

### 19.3. Выход

Агрегации:

- building;
- parcel/block;
- zone;
- project/run.

Никаких NaN/Inf в API.

---

## 20. Инфраструктура и доступность

Минимальные категории:

- education;
- healthcare;
- retail;
- recreation.

Каждый `InfrastructureType` задаёт:

- demand model;
- demographic dependency;
- capacity;
- max network distance;
- allowed zones;
- minimum/target site area;
- candidate policy.

Алгоритм:

1. учесть existing infrastructure;
2. рассчитать unmet demand;
3. сформировать bounded candidate sites;
4. snap demand/sites к network;
5. посчитать accessibility;
6. выбрать объект с максимальным приростом coverage;
7. обновить остаточный спрос;
8. повторять до target/limit.

Generated facility — не только point: у неё должна быть site/footprint geometry либо явная ссылка на building/site.

Greedy — основной v1 algorithm; random или naive baseline используется в экспериментах.

---

## 21. Validation, metrics и score

### 21.1. Validation

Итоговый `ValidationReport`:

- hard violation count;
- soft penalties;
- codes/messages;
- affected entity refs;
- problem geometries;
- stage source.

### 21.2. Metrics

**Land:** developable area, developed area, green/recreation share.

**Buildings:** coverage, FAR, GFA, archetype distribution.

**Demography:** total population, density, age-group distribution, jobs estimate.

**Roads:** length density, connected components, average degree, intersection density, circuity, dead-end ratio.

**Infrastructure:** population coverage, age-specific coverage, p50/p90 network distance, unmet demand, capacity utilization.

**Constraints:** hard violations, area affected, weighted soft penalty.

### 21.3. Composite score

Score не может быть «магической суммой».

Каждая metric definition содержит:

- direction: maximize/minimize/target;
- unit;
- normalization range/method;
- clamp policy;
- missing-value policy;
- weight;
- config version.

Raw metrics всегда сохраняются. Нужен sensitivity analysis весов хотя бы для experiment/report path.

---

## 22. Backend API

Основные группы:

```text
/api/v1/projects
/api/v1/projects/{id}/datasets
/api/v1/datasets/{id}/versions
/api/v1/runs
/api/v1/runs/{id}/stages
/api/v1/jobs
/api/v1/layers
/api/v1/exports
/api/v1/scenario-batches
```

Правила:

- API versioned;
- typed Pydantic schemas;
- heavy operation возвращает job/run id;
- error response имеет stable code;
- list endpoints paginated;
- spatial layer endpoints поддерживают bbox/limits;
- огромный GeoJSON запрещён;
- MVT используется для больших vector layers;
- upload streaming;
- user-supplied paths никогда не интерпретируются как server filesystem paths.

---

## 23. Frontend

Frontend является полноценным клиентом client-server системы.

### 23.1. Layout

- project/dataset panel;
- map;
- layer tree;
- generation/scenario panel;
- job progress;
- metrics;
- validation problems;
- compare mode.

### 23.2. Data layer

Компоненты не должны хаотично вызывать `fetch`. Нужен typed API client + query/cache layer.

### 23.3. Vertical slices

UI развивается параллельно backend:

- после ingest — source layers;
- после suitability — heatmap;
- после zoning — zones;
- после roads — network;
- после blocks/buildings — generated layers;
- после validation — violation layer;
- после metrics — dashboard.

Это обязательное правило: карта используется и как debugging surface.

### 23.4. Rendering

Малые слои: GeoJSON.

Большие слои: bbox/MVT/lazy loading.

Frontend никогда не является authoritative источником metrics или constraint decisions.

---

## 24. Observability

Минимальный production-like уровень:

- structured JSON logs;
- request ID;
- job ID;
- run/project IDs;
- stage name/version;
- duration;
- input/output feature counts;
- warnings;
- exception traceback только в server logs;
- безопасная ошибка в API;
- queue depth;
- running/failed jobs;
- stage duration metrics;
- memory-sensitive diagnostics для graph/raster stages.

Readiness проверяет критичные зависимости, но не выполняет тяжёлый GIS-запрос.

---

## 25. Security и deployment scope

### 25.1. Scope v1

`v1.0` рассчитана на **trusted single-user / controlled deployment**.

Публично доступный анонимный production deployment без auth/quota не является поддерживаемым сценарием.

### 25.2. Обязательная защита

- upload size limits;
- extension/content validation;
- path traversal protection;
- ZIP slip / zip bomb limits;
- temp directory isolation;
- parameterized SQL;
- non-superuser DB account;
- CORS configuration;
- secrets only through environment/secret store;
- job concurrency limits;
- per-project/run limits;
- cleanup policy.

Архитектура остаётся auth-ready для post-v1.

---

## 26. Тестовая стратегия

### 26.1. Unit

Pure/domain logic: CRS guards, config normalization, constraints, geometry utilities, factors, demographics, metric normalization, score.

### 26.2. Property/geospatial

Через Hypothesis/synthetic fixtures:

- generated geometry valid;
- buildings внутри допустимого envelope;
- no hard overlap;
- fixed entities не мутируются;
- zoning non-overlap;
- block partition invariants;
- deterministic seed.

### 26.3. Integration

С реальным PostGIS/Redis:

- migrations;
- upload → normalize → persist;
- job → worker → stage result;
- artifact lifecycle;
- retry/idempotency;
- bbox/layer queries.

### 26.4. E2E

```text
create project
-> upload demo territory
-> normalize
-> create run
-> wait
-> inspect layers/metrics/violations
-> compare
-> export
```

### 26.5. Regression

Synthetic/golden fixtures проверяют ranges/invariants, а не хрупкое равенство каждой случайной координаты.

### 26.6. Performance

Benchmark dataset и reference hardware фиксируются в документации.

---

## 27. CI/CD quality gate

Любой атомарный work item считается DONE только если **HEAD commit имеет полностью зелёный required CI**.

Обязательные проверки по мере появления подсистем:

```text
python lint
python typecheck
unit tests
migration smoke
integration tests
frontend typecheck
frontend build
frontend tests
e2e smoke
```

Нельзя трактовать «CI не стал хуже» как DoD. Требование — **required checks success**.

Release tag создаётся только на sprint/release gate, а не на каждую атомарную задачу.

---

## 28. Масштабирование по слоям

### API

Stateless. Horizontal replicas behind reverse proxy.

### Worker

Логические очереди:

- ingest;
- generation;
- analysis;
- export.

Queue pressure ограничивается concurrency/backpressure.

### PostgreSQL/PostGIS

- GiST;
- bbox prefilters;
- batch operations;
- `EXPLAIN ANALYZE`;
- server-side pagination;
- VACUUM/ANALYZE;
- partitioning только после benchmark.

### Raster

- windowed IO;
- COG-friendly artifacts;
- не загружать весь большой raster без необходимости.

### Graph

- bounded candidate generation;
- spatial index для snapping;
- backend abstraction;
- cache graph snapshot в пределах stage.

### Map

- MVT;
- tile cache headers;
- lazy loading;
- no giant FeatureCollection.

---

## 29. Экспериментальная методология ВКР

Методика определяется **до завершения алгоритмов**, чтобы метрики не подбирались задним числом.

### 29.1. Research questions

**RQ1.** Насколько учёт пространственных hard/soft constraints снижает нарушения по сравнению с упрощённым baseline?

**RQ2.** Как seed и параметры плотности влияют на морфологию и инфраструктурную доступность?

**RQ3.** Улучшает ли network-aware greedy infrastructure placement покрытие спроса по сравнению с random/naive placement?

**RQ4.** Насколько результаты устойчивы к изменению весов composite score?

**RQ5.** Может ли один алгоритмический pipeline работать и для expansion, и для from-scratch без отдельной архитектуры?

### 29.2. Территории

Минимум:

- Territory A — расширение существующего города;
- Territory B — другой тип рельефа/плотности либо from-scratch.

### 29.3. Эксперименты

- E1 reproducibility: одинаковые seed/config;
- E2 seeds: серия 5–10 seed;
- E3 density: low/medium/high;
- E4 constraints ablation: constraint-aware vs baseline;
- E5 infrastructure: greedy vs random/naive;
- E6 score sensitivity;
- E7 при наличии временных данных — optional holdout real-growth comparison.

### 29.4. Baselines

Нужен хотя бы один алгоритмически простой baseline, например:

- random/grid candidate placement с теми же hard masks;
- random infrastructure placement;
- constraints-disabled/softened variant.

Baseline должен быть простым, но честным и воспроизводимым.

### 29.5. Reporting

Для каждого experiment сохраняются manifest, inputs, config, seed, timing, raw metrics, score, validation и CSV/JSON report.

---

## 30. План спринтов верхнего уровня

Атомарные tasks определены во втором документе.

| Sprint | Release | Результат |
|---|---|---|
| S00 | `v0.1.0` | Engineering baseline и зелёный CI |
| S01 | `v0.2.0` | Core contracts: territory/stage/constraint/RNG/CRS |
| S02 | `v0.3.0` | Persistence, versioning, jobs, artifacts |
| S03 | `v0.4.0` | Ingest + source visualization |
| S04 | `v0.5.0` | Constraint foundation + suitability |
| S05 | `v0.6.0` | Functional zoning |
| S06 | `v0.7.0` | Road graph и road generation |
| S07 | `v0.8.0` | Blocks + simplified parcels |
| S08 | `v0.9.0` | Buildings/archetypes |
| S09 | `v0.10.0` | Demography |
| S10 | `v0.11.0` | Infrastructure/accessibility |
| S11 | `v0.12.0` | Final validation + metrics + score |
| S12 | `v0.13.0` | Pipeline orchestration + scenarios |
| S13 | `v0.14.0` | Scalable layer API + export + complete GIS UI |
| S14 | `v0.15.0` | Performance/operations/hardening |
| S15 | `v0.16.0` | Experiments/research/demo package |
| Release | `v1.0.0` | Stabilized defendable product |

---

## 31. Global Definition of Done

Изменение считается завершённым только если:

- соблюдены module boundaries;
- contract typed/documented;
- нет скрытой бизнес-логики в controller/UI;
- migration добавлена только при реальном schema change;
- tests обновлены;
- required CI зелёный;
- retry/idempotency рассмотрены для job;
- CRS/units указаны для spatial/numeric contract;
- algorithm имеет bounded iterations/candidate count;
- geometry validity проверяется на нужной boundary;
- performance regression не внесён без объяснения;
- документация обновлена при изменении API/schema/algorithm contract.

---

## 32. Архитектурные gates

### Data gate

- известен owner (`project/dataset_version/run`)?
- известна CRS?
- нет silent overwrite?
- есть index для частого spatial access?
- большой файл не хранится как BYTEA?

### Algorithm gate

- stage независим от HTTP/DB details?
- input/output typed?
- deterministic?
- bounded?
- synthetic fixture существует?
- fixed state не мутируется?

### Worker gate

- retry safe?
- progress видим?
- cancellation возможен между bounded units?
- temp resources cleaned?
- artifact publication consistent?

### API gate

- heavy work asynchronous?
- pagination/bbox/limits?
- error code stable?
- idempotent create where needed?

### Frontend gate

- authoritative state приходит с backend?
- run/dataset version выбран явно?
- large layers lazy/tiled?
- error/progress видимы?

### Research gate

- metric definition заранее зафиксирована?
- baseline существует?
- experiment reproducible?
- raw metrics сохраняются?

---

## 33. Запрещённые архитектурные сокращения

Даже ради скорости разработки нельзя:

- запускать весь pipeline внутри FastAPI request;
- считать area/distance в EPSG:4326;
- смешивать fixed и generated entities без явного source/run;
- мутировать completed run;
- перезаписывать старую dataset version;
- использовать global `random`;
- размазывать одинаковые hard constraints по алгоритмам вместо engine;
- создавать stage без общего Stage contract;
- выполнять полный N×M spatial compare без index/candidate bound;
- отдавать огромный layer одним GeoJSON;
- хранить GeoTIFF/PBF/GPKG как BYTEA в основной БД;
- превращать одну generic `SpatialFeature` таблицу в единственную domain model;
- считать composite score единственным результатом;
- добавлять микросервис «потому что масштабируемость» без измеренной причины;
- объявлять атомарную задачу выполненной при красном CI.

---

## 34. Основные риски

| Риск | Митигирование |
|---|---|
| GIS-алгоритмы становятся слишком медленными | ранний benchmark, spatial indexes, bounded candidates, profiling |
| поздний рефакторинг constraints/pipeline | contracts реализуются до алгоритмов |
| expansion mode теряется в generic generator | `RunMode`, `TerritorySnapshot`, fixed/generated invariants |
| демография слишком примитивна | `DemographicScenario`, age groups, demand linkage |
| infrastructure становится набором точек | site/footprint geometry + capacity/site area |
| road graph некорректен на мостах/тоннелях | semantic noding policy |
| frontend интегрируется слишком поздно | vertical slices после каждого spatial stage |
| DB и object storage расходятся | artifact lifecycle + publish protocol + GC |
| очередь теряет job между DB и Redis | authoritative Job/outbox state + retry dispatcher |
| score субъективен | raw metrics, explicit normalization, sensitivity |
| ВКР превращается в «мы что-то сгенерировали» | заранее определённые RQ, baselines, experiments |

---

## 35. Критерии релиза `v1.0.0`

Release допускается только если:

- clean clone разворачивается по README;
- migrations проходят на пустой БД;
- CI полностью зелёный;
- client-server workflow работает без прямого обращения пользователя к DB;
- GeoJSON/GPKG/Shapefile/GeoTIFF ingest работает;
- OSM source path работает;
- expansion mode сохраняет fixed city;
- from-scratch использует тот же pipeline;
- constraint engine используется всеми релевантными stages;
- suitability canonical raster сохраняется;
- zoning, roads, blocks/parcels, buildings, demography, infrastructure выполняются;
- final validation возвращает problem geometries;
- raw metrics и composite score доступны;
- scenario batch ≥ 3 runs работает;
- exact rerun/provenance работает;
- source/generated layers доступны в UI;
- large layer имеет bbox/MVT path;
- export работает;
- reference benchmark выполнен и зафиксирован;
- experiment package содержит минимум две территории либо обоснованную альтернативу;
- результаты пригодны для таблиц/графиков ВКР;
- демонстрация не зависит критически от внешнего интернета.

---

## 36. Эволюция после `v1.0.0`

Архитектура должна позволять без полного переписывания добавить:

- Overture adapters;
- pgRouting backend;
- более сложный facility-location solver;
- новые road generation strategies;
- temporal urban growth;
- multi-user auth/tenancy;
- quotas;
- CDN/tile cache;
- distributed object storage;
- Kubernetes/несколько worker replicas;
- 3D как presentation layer;
- дополнительные demographic models;
- дополнительные constraint/metric plugins.

Ни одно из этих расширений не должно требовать переноса domain rules в frontend или отказа от immutable run model.
