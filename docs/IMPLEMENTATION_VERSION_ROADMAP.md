# Urban Development Generator — атомарный implementation roadmap

> Этот документ детализирует `docs/DEVELOPMENT_PLAN.md` до work items, которые должны быть достаточно узкими, чтобы **одна задача могла быть реализована одним качественным запросом к нейросети/разработчику**, проверена и принята.
>
> Главный принцип: атомарность определяется **законченной тестируемой capability**, а не количеством изменённых файлов.

---

## 1. Идентификаторы задач и релизов

Предыдущая схема «каждая задача = patch SemVer» заменена.

Используются два независимых понятия.

### Work item

```text
S06-T04
```

- `S06` — sprint;
- `T04` — атомарная задача внутри sprint.

Именно work item указывается в запросе к нейросети.

### Release

Sprint закрывается release tag:

```text
v0.7.0
```

Атомарные задачи не обязаны создавать Git tags.

Финальный release:

```text
v1.0.0
```

---

## 2. Шаблон запроса для одной задачи

```text
Реализуй только work item <ID> из docs/IMPLEMENTATION_VERSION_ROADMAP.md.

Перед изменениями прочитай:
1. docs/DEVELOPMENT_PLAN.md;
2. docs/IMPLEMENTATION_VERSION_ROADMAP.md;
3. docs/ARCHITECTURE.md, если он существует;
4. релевантный текущий код.

Требования:
- не реализуй последующие work items;
- не делай несвязанный рефакторинг;
- не нарушай module boundaries;
- добавь/обнови тесты;
- миграцию добавляй только при schema change;
- учитывай idempotency/CRS/units/performance там, где это относится к задаче;
- выполни все доступные required CI checks;
- задача не DONE, пока HEAD CI красный.

В конце дай:
- список изменённых файлов;
- краткое описание контракта;
- какие тесты выполнены;
- CI status;
- известные ограничения;
- что именно должен сделать следующий work item.
```

---

## 3. Global DoD для каждого work item

Каждый work item обязан:

1. сохранять сборку/запуск проекта;
2. иметь типизированный public contract;
3. не переносить domain/GIS rules в HTTP-controller или React-component;
4. иметь тесты на новую capability;
5. иметь deterministic behavior, если используется randomness;
6. явно учитывать CRS/units для spatial calculations;
7. иметь bounded iteration/candidate policy для потенциально дорогого алгоритма;
8. быть retry-safe, если это job;
9. не мутировать completed run/dataset version;
10. обновлять документацию контракта при необходимости;
11. иметь **полностью зелёный required CI на HEAD**.

Если CI падает из-за новой или старой ошибки, item не считается закрытым, пока ошибка не исправлена либо check не удалён обоснованным архитектурным решением.

---

## 4. Глобальные архитектурные инварианты

На протяжении всех sprint:

- основной режим — `EXPANSION`;
- `FROM_SCRATCH` использует тот же pipeline;
- fixed source state не мутируется;
- generated outputs namespace-ятся по `run_id`;
- `core` не зависит от FastAPI/SQLAlchemy/Redis/React;
- heavy GIS выполняется worker'ом;
- API stateless;
- raw files хранятся через `ArtifactStore`;
- large raster обрабатывается windowed;
- large vector layers имеют bbox/tile-ready delivery;
- `Constraint` и `Stage` contracts используются с ранних sprint;
- no global random;
- no area/distance in EPSG:4326;
- no unbounded N×M spatial loops;
- NetworkX — adapter v1, а не domain contract;
- raw metrics сохраняются независимо от composite score.

---

# Sprint S00 — Engineering baseline → `v0.1.0`

**Цель:** полностью зелёная, воспроизводимая инженерная база.

### S00-T01 — Нормализовать Python workspace
**Сделать:** `pyproject.toml`, uv, единый dependency graph backend/core/worker, Ruff, pytest, mypy/pyright policy.  
**DoD:** одна документированная команда установки; lint/test/typecheck доступны локально.

### S00-T02 — Нормализовать frontend toolchain
**Сделать:** React/Vite/TypeScript scripts, lockfile policy, `vite/client` types, build/typecheck scripts.  
**DoD:** clean install + production build проходит.

### S00-T03 — Привести Docker Compose к единому dev stack
**Сделать:** API, worker, PostGIS, Redis, frontend; healthchecks; networks; env config.  
**DoD:** `docker compose up --build` поднимает stack без host-specific paths.

### S00-T04 — Конфигурация и secrets boundary
**Сделать:** typed settings, `.env.example`, CORS, DB/Redis/storage URLs, no secrets in repo.  
**DoD:** config validation даёт понятные ошибки.

### S00-T05 — Health/readiness lifecycle
**Сделать:** `/health/live`, `/health/ready`; readiness проверяет DB/Redis краткими bounded probes.  
**DoD:** container health может использовать endpoints.

### S00-T06 — Structured logging и correlation id
**Сделать:** JSON/logging adapter, request id, базовые project/run/job context fields.  
**DoD:** `print()` не используется для operational logging.

### S00-T07 — Alembic migration discipline
**Сделать:** clean upgrade/downgrade smoke, no production `create_all`, migration README.  
**DoD:** пустая PostGIS DB разворачивается миграциями.

### S00-T08 — Required CI matrix
**Сделать:** Python lint/test/typecheck и frontend typecheck/build; cache dependencies.  
**DoD:** HEAD commit полностью зелёный.

**Sprint gate:** clean clone + `.env.example` + стандартная команда запуска дают working shell и зелёный CI.

---

# Sprint S01 — Core domain contracts → `v0.2.0`

**Цель:** определить фундамент до алгоритмов, чтобы не переписывать stages позже.

### S01-T01 — Project и CRS contract
**Сделать:** domain `ProjectRef/ProjectSettings`, `working_srid`, CRS guard types.  
**DoD:** metric operations требуют working CRS явно.

### S01-T02 — RunMode и fixed/generated semantics
**Сделать:** `EXPANSION`, `FROM_SCRATCH`, source/fixed/generated ownership enums/contracts.  
**DoD:** expansion semantics доступны core без HTTP/DB.

### S01-T03 — TerritorySnapshot contract
**Сделать:** immutable snapshot model ссылок на boundary/roads/buildings/facilities/landuse/water/constraints/DEM/demography.  
**DoD:** synthetic snapshot создаётся без DB.

### S01-T04 — RunContext и deterministic RNG
**Сделать:** seed, RNG factory, run id/mode, CRS, config refs, correlation metadata.  
**DoD:** два context с одинаковым seed дают одинаковую RNG sequence.

### S01-T05 — Stage/StageResult protocol
**Сделать:** name/version/dependencies/input validation/execute/result diagnostics/fingerprint.  
**DoD:** dummy stage запускается unit-test без backend.

### S01-T06 — Constraint base contract
**Сделать:** severity/scope/code/result, hard/soft distinction, `ValidationReport` skeleton.  
**DoD:** алгоритмы могут вызывать engine API до появления конкретных rules.

### S01-T07 — ArtifactStore protocol
**Сделать:** put/open/stat/delete/promote temp→ready contract без filesystem path leakage.  
**DoD:** fake in-memory adapter проходит contract tests.

### S01-T08 — NetworkBackend contract
**Сделать:** graph snapshot, snap, shortest path/multi-source interfaces.  
**DoD:** domain code не обязан импортировать NetworkX.

### S01-T09 — Error taxonomy
**Сделать:** domain/config/data/transient/permanent/cancelled error classes + stable codes.  
**DoD:** backend/worker смогут маппить их без string parsing.

### S01-T10 — Зафиксировать benchmark и experiment contracts
**Сделать:** typed definitions/reference profiles/metric IDs, без реализации experiments.  
**DoD:** будущие алгоритмы знают заранее, какие raw metrics/diagnostics обязаны выдавать.

**Sprint gate:** suitability/roads/buildings можно писать поверх стабильных contracts без знания FastAPI/SQLAlchemy.

---

# Sprint S02 — Persistence, versioning и jobs → `v0.3.0`

**Цель:** надёжная модель хранения нескольких проектов, dataset versions и runs.

### S02-T01 — Project persistence
CRUD project + `working_srid`, boundary metadata, timestamps.

### S02-T02 — Dataset + DatasetVersion
Logical dataset отдельно от immutable upload/version; checksum/status/source metadata.

### S02-T03 — GenerationRun persistence
seed/mode/config/schema version/commit SHA/status/dataset refs; immutable-after-success guard.

### S02-T04 — RunStageResult persistence
stage version/status/progress/input hash/config hash/diagnostics/artifact refs.

### S02-T05 — Artifact persistence и lifecycle states
URI/hash/size/type/state/owner; `temporary/ready/referenced/expired`.

### S02-T06 — Job model и idempotency key
Authoritative DB job state; unique idempotency constraints; attempts/error class.

### S02-T07 — Outbox/dispatcher foundation
DB-backed pending enqueue state + repeatable dispatcher semantics для Redis.

### S02-T08 — Generated entity schema
Zone/Road/Block/Parcel/Building/Infrastructure tables с `run_id`.

### S02-T09 — Canonical source layer schema
Отдельные normalized roads/buildings/landuse/water/facilities/constraints вместо единой giant EAV model.

### S02-T10 — Spatial/index migration
GiST + B-tree/composite indexes под реальные `project/dataset_version/run` access patterns.

### S02-T11 — Repository/application service boundary
Controllers не содержат SQL query details; unit-test services возможен без HTTP.

### S02-T12 — Persistence integration tests
Пустая DB, FK, immutability, idempotency, indexes/migration smoke.

**Sprint gate:** несколько dataset versions и runs сосуществуют без перезаписи; DB authoritative state готов для workers.

---

# Sprint S03 — Ingest и source visualization → `v0.4.0`

**Цель:** принимать реальные данные и сразу визуально проверять нормализацию.

### S03-T01 — LocalArtifactStore
Filesystem adapter к S01 contract, safe root, atomic-ish temp/promote behavior.

### S03-T02 — Streaming upload API
Size limits, sanitized name, checksum while streaming, no full-file RAM read.

### S03-T03 — Safe Shapefile ZIP extraction
Zip-slip/zip-bomb limits, file count/size limits, temp cleanup.

### S03-T04 — Vector inspection
Layer list, CRS, geom types, bbox, count; metadata-first через Pyogrio/OGR где возможно.

### S03-T05 — Vector normalization
CRS validation, `make_valid`, empty/reject policy, reproject, type filtering, diagnostics.

### S03-T06 — Batch vector persistence
Bulk write normalized canonical tables; transaction boundary; post-load analyze/index policy.

### S03-T07 — Raster inspection
CRS, transform, nodata, resolution, extent без чтения full raster.

### S03-T08 — Raster normalization
Clip/reproject/resample windowed; output GeoTIFF/COG-friendly artifact.

### S03-T09 — Ingest worker job
DatasetVersion `uploaded -> processing -> ready/failed`; retry/idempotency.

### S03-T10 — OSM PBF reader foundation
Extract roads/buildings/POI/landuse/water streams/chunks; preserve relevant tags.

### S03-T11 — OSM mapping rules
Versioned tag→internal enums/config; no mapping magic inside loops.

### S03-T12 — OSM canonical writer
Mapped entities batch-persist в source layer schema.

### S03-T13 — Source layer bbox API
Project/dataset-version scoped GeoJSON/bbox endpoint с limit.

### S03-T14 — Source layers UI vertical slice
Boundary/roads/buildings/water/landuse visibility, legend, fit, click inspector.

### S03-T15 — Ingest integration fixtures
GeoJSON/GPKG/SHP/GeoTIFF/PBF happy + invalid cases.

**Sprint gate:** реальную территорию можно загрузить, нормализовать, сохранить и увидеть на карте без ручной правки кода.

---

# Sprint S04 — Constraints foundation + suitability → `v0.5.0`

**Цель:** единая система ограничений начинает реально использоваться до остальных генераторов.

### S04-T01 — Constraint registry/engine
Регистрация rules по scope/stage; единый evaluation API.

### S04-T02 — Geometry exclusion constraint
Boundary/water/protected intersection, prepared/index reuse.

### S04-T03 — Distance/setback constraint
Metric CRS guard, road/building/feature setback.

### S04-T04 — Raster threshold constraint
Slope/threshold sampling/window policy.

### S04-T05 — SuitabilityConfig + factor protocol
Weights/normalization/thresholds/versioning.

### S04-T06 — Hard exclusion mask
Rasterize relevant hard constraints; mask отдельно от soft score.

### S04-T07 — DEM slope factor
Windowed slope calculation, nodata policy, tests on synthetic DEM.

### S04-T08 — Road proximity factor
Distance transform/indexed strategy без full N×M.

### S04-T09 — Landuse factor
Versioned class weights; configurable mapping.

### S04-T10 — Weighted suitability aggregator
Score 0..1, hard mask precedence, explicit normalization.

### S04-T11 — Suitability artifact
Canonical raster artifact + statistics + provenance.

### S04-T12 — Suitability layer API/UI
Heatmap/raster visualization, stats, factor metadata.

### S04-T13 — Determinism/property tests
Same inputs/config => same raster/statistics; hard mask invariants.

**Sprint gate:** constraint-aware suitability пересчитывается независимо и визуализируется.

---

# Sprint S05 — Functional zoning → `v0.6.0`

### S05-T01 — Zone domain/config
residential/mixed/public/recreation; target shares/min area/adjacency rules.

### S05-T02 — Fixed existing zones adapter
Existing zones попадают в TerritorySnapshot и не мутируются.

### S05-T03 — Deterministic seed generator
Suitability-aware seeds + RNG from RunContext.

### S05-T04 — Base partition geometry
Voronoi/partition clipped to developable area; validity repair.

### S05-T05 — Zone assignment strategy
Suitability + target shares; assignment отделён от partition geometry.

### S05-T06 — Region growth/refinement
Adjacency/min-area, bounded iterations, convergence diagnostics.

### S05-T07 — Zone constraint evaluation
Engine rules применяются через общий contract, без локальных duplicate checks.

### S05-T08 — Persist GeneratedZone
run refs, area, class, diagnostics.

### S05-T09 — Zoning UI vertical slice
Generated vs fixed zones, opacity, run selection.

### S05-T10 — Zoning property tests
Non-overlap, coverage policy, validity, target tolerance, determinism.

**Sprint gate:** expansion mode сохраняет existing zoning и генерирует delta zones на developable area.

---

# Sprint S06 — Road network и generation → `v0.7.0`

**Цель:** корректный graph pipeline с учётом существующих дорог и grade-separated crossings.

### S06-T01 — NetworkXBackend
Adapter S01 NetworkBackend; graph snapshot/domain conversion.

### S06-T02 — OSM road semantics normalizer
bridge/tunnel/layer/oneway/class metadata в canonical roads.

### S06-T03 — Spatial snapping
Endpoint/intersection snapping через STRtree/spatial index, configurable tolerance.

### S06-T04 — Semantic noding
Create graph intersections только там, где geometry + layer semantics допускают crossing.

### S06-T05 — Graph build
Nodes/edges, lengths, source/fixed flags, connected components diagnostics.

### S06-T06 — Graph cleanup
Duplicate/tiny edges, dangling artifacts policy, bounded thresholds.

### S06-T07 — Shortest-path services
Dijkstra/A*, multi-source path where applicable, stable domain result.

### S06-T08 — Candidate road anchors
Zoning/suitability-aware bounded sampling; max candidate count.

### S06-T09 — Least-cost connector
A*/cost surface path для новых connections с hard masks.

### S06-T10 — MST baseline connector
Вспомогательная pluggable strategy для базовой связности anchors.

### S06-T11 — Rule-based growth
Local/collector growth, bounded iterations/length budget.

### S06-T12 — Road classification
Arterial/collector/local configurable rules; existing classes preserved.

### S06-T13 — Road validation
Connectivity, dead-end ratio, forbidden crossings, invalid geometry.

### S06-T14 — Road metrics
Length density, components, degree, intersection density, circuity.

### S06-T15 — Persist GeneratedRoad
Bulk insert, run/source refs, indexes.

### S06-T16 — Roads UI vertical slice
Existing/generated distinction, classes, graph diagnostics.

### S06-T17 — Performance fixture
Reference road network benchmark; snapping/noding/pathfinding timing.

**Sprint gate:** fixed network корректно читается, generated roads расширяют её и проходят validation.

---

# Sprint S07 — Blocks и simplified parcels → `v0.8.0`

### S07-T01 — Polygonize roads
Candidate blocks from network lines with geometry cleanup.

### S07-T02 — Developable clipping
Project/developable mask + hard constraints.

### S07-T03 — Block metrics
Area/perimeter/compactness/aspect/holes.

### S07-T04 — Frontage/access validation
Road frontage/access via indexed nearest/intersection operations.

### S07-T05 — Oversized block split
Principal axis/road-informed bounded strategy.

### S07-T06 — Sliver cleanup
Merge/drop policy with diagnostics, no silent deletion.

### S07-T07 — Zone association
Persist explicit block→zone relation where possible; no repeated downstream spatial join.

### S07-T08 — Parcel domain model
Clarify non-cadastral meaning, frontage/buildable attrs.

### S07-T09 — Simplified parcel subdivision
Optional frontage-based lot subdivision для подходящих residential blocks.

### S07-T10 — Persist blocks/parcels
Bulk write + run/zone refs + indexes.

### S07-T11 — Blocks/parcels UI
Layer toggle, metrics inspector, validation flags.

### S07-T12 — Property/performance tests
Inside boundary, non-overlap policy, access, valid geometry, split bounds.

**Sprint gate:** downstream building stage получает explicit block/parcel objects, а не сырые polygons без контекста.

---

# Sprint S08 — Buildings и archetypes → `v0.9.0`

### S08-T01 — BuildingConfig/archetype schema
Data-driven detached/point/bar/perimeter/courtyard/public/commercial.

### S08-T02 — Buildable envelope
Setbacks/constraints/slope/developable mask через engine.

### S08-T03 — Candidate placement model
Parcel/block candidates, bounded grid/frontage candidates.

### S08-T04 — Rectangular/point footprint strategy
Базовый deterministic footprint strategy.

### S08-T05 — Bar/frontage strategy
Linear footprint вдоль frontage/road/block axis.

### S08-T06 — Perimeter/courtyard simplified strategy
2D polygonal archetype без внутренней планировки.

### S08-T07 — Orientation strategy
Road/frontage/principal-axis orientation contract.

### S08-T08 — Inter-building spacing
Spatial index for existing placed footprints, min gap/no overlap.

### S08-T09 — FAR/coverage convergence
Bounded placement loop, tolerance, unmet-target diagnostics.

### S08-T10 — Floors/use assignment
Zone/archetype/config-driven attributes отдельно от geometry.

### S08-T11 — Area/GFA calculation
Footprint/GFA/coverage/FAR в working CRS.

### S08-T12 — Persist GeneratedBuilding
Batch insert, block/parcel/run refs, GiST/run indexes.

### S08-T13 — Buildings UI vertical slice
Archetype/use styling, click attributes, run switch.

### S08-T14 — Property/stress tests
No hard violations, deterministic, target ranges, 1k/10k building fixture.

**Sprint gate:** застройка визуально и метрически различается по archetypes, но остаётся 2D и воспроизводимой.

---

# Sprint S09 — Demography → `v0.10.0`

### S09-T01 — DemographicScenario schema
Population/growth, occupancy, m²/person, household size, age groups, working ratio.

### S09-T02 — Residential capacity per building
Pure deterministic GFA→residential capacity calculation.

### S09-T03 — Population allocation
Residents per building с constraints/capacity; no NaN/Inf.

### S09-T04 — Age-group allocation
0–6 / 7–17 / 18–64 / 65+ или configurable equivalent.

### S09-T05 — Jobs/workforce estimate
Mixed/commercial/public floor area → approximate jobs; explicit assumptions.

### S09-T06 — Block/zone aggregation
Population/age/jobs sums with consistency checks.

### S09-T07 — Population raster calibration adapter
Optional source sampling/windowing; base model не зависит от raster.

### S09-T08 — Spatial calibration
Adjust distribution while preserving totals/tolerances.

### S09-T09 — Demographic demand profile
Typed output для infrastructure stage: demand by block/category/demographic group.

### S09-T10 — Demography metrics/API/UI
Density, totals, age shares, jobs; choropleth/inspector.

### S09-T11 — Numeric/property tests
Zero GFA, mixed use, nodata, target sum, determinism.

**Sprint gate:** тема «демографические ограничения» поддерживается реальной domain-моделью, а не одной формулой residents.

---

# Sprint S10 — Infrastructure и accessibility → `v0.11.0`

### S10-T01 — InfrastructureType schema
Demand model/capacity/max distance/allowed zones/site area.

### S10-T02 — Existing infrastructure adapter
Fixed facilities + capacity/use mapping into TerritorySnapshot.

### S10-T03 — Unmet demand calculation
By block/category/demographic cohort.

### S10-T04 — Candidate site generator
Blocks/parcels/buildings, bounded candidates, allowed-zone filter.

### S10-T05 — Candidate site geometry
Generated facility has site/footprint or explicit host building, not only point.

### S10-T06 — Snap demand/sites to network
Reusable nearest index on graph snapshot.

### S10-T07 — Accessibility matrix/service
Multi-source Dijkstra/batched path logic where beneficial; max distance cutoffs.

### S10-T08 — Greedy placement
Incremental coverage cache; update remaining demand; bounded facilities/iterations.

### S10-T09 — Capacity/site feasibility
Reject impossible candidate if capacity/site/building envelope inconsistent.

### S10-T10 — Persist GeneratedInfrastructure
run/category/capacity/site/network node refs.

### S10-T11 — Infrastructure metrics
Coverage, unmet demand, p50/p90 distance, utilization.

### S10-T12 — Infrastructure UI
Existing/generated distinction, service radius/accessibility result, unmet demand layer.

### S10-T13 — Synthetic town integration tests
Known demand, existing facility, expected coverage ranges.

### S10-T14 — Greedy performance fixture
Candidate/demand size budget, no repeated all-pairs recomputation.

**Sprint gate:** infrastructure responds to demographic demand and real network distance.

---

# Sprint S11 — Final validation, metrics и score → `v0.12.0`

### S11-T01 — Cross-stage ValidationReport implementation
Collect violations from all stages with entity refs/problem geometries.

### S11-T02 — Aggregate constraints
Coverage/FAR/density/capacity bounds through engine.

### S11-T03 — Soft penalty rules
Structured penalty independent from hard invalidity.

### S11-T04 — Metric registry
Metric id/unit/scope/direction/source/version.

### S11-T05 — Land/building metrics
Developed area, green share, coverage, FAR, GFA, archetype distribution.

### S11-T06 — Road metrics adapter
Reuse road graph/metrics artifacts, no graph rebuild.

### S11-T07 — Demography metrics adapter
Population/density/age/jobs.

### S11-T08 — Infrastructure metrics adapter
Coverage/distance/unmet demand/utilization.

### S11-T09 — Constraint metrics
Hard count/affected area/soft penalties.

### S11-T10 — Metric normalization
Direction/range/clamp/missing policy explicitly versioned.

### S11-T11 — Composite score
Configurable weights, raw metrics always persisted.

### S11-T12 — Score sensitivity
Recalculate rankings under weight perturbation without rerunning GIS.

### S11-T13 — Violations layer API/UI
Problem geometries, severity/code/message/entity click.

### S11-T14 — Metrics dashboard
Raw values + units + score + normalization explanation.

### S11-T15 — Regression fixtures
Expected ranges/invariants detect unintended algorithm drift.

**Sprint gate:** качество сценария объяснимо через validation + raw metrics; score не скрывает причины.

---

# Sprint S12 — Orchestration, jobs и scenarios → `v0.13.0`

### S12-T01 — Stage dependency graph
Pipeline DAG metadata, dependencies, skip rules.

### S12-T02 — PipelineContext adapter
Assemble RunContext + TerritorySnapshot + service ports from persisted run.

### S12-T03 — Persistent checkpoints
Stage input/config fingerprints, reuse only when fingerprints match.

### S12-T04 — Generation worker job
Run full DAG outside HTTP, progress updates.

### S12-T05 — Cooperative cancellation
Check cancellation between bounded units/stages, consistent state.

### S12-T06 — Retry policy
Transient/permanent classification, bounded exponential backoff.

### S12-T07 — Outbox dispatcher hardening
Recover DB jobs not delivered to Redis; duplicate delivery remains safe.

### S12-T08 — Artifact publish/GC job
Temp→ready→referenced lifecycle, orphan detection/cleanup.

### S12-T09 — ScenarioBatch model
Parent batch + 3–10 child runs, concurrency limit.

### S12-T10 — Batch seed/config matrix
Create reproducible runs from matrix spec.

### S12-T11 — Exact rerun
Clone config/dataset versions/seed with availability/hash checks.

### S12-T12 — Provenance manifest
One JSON manifest of code/config/data/stages/artifacts/metrics.

### S12-T13 — Run compare backend
Raw metrics delta/ranking/validation summary without GIS recompute.

### S12-T14 — Run controls/progress UI
Create/cancel/retry, polling abstraction with backoff, SSE-ready interface.

### S12-T15 — Compare UI vertical slice
2–N metrics table + selected run map switching.

**Sprint gate:** end-to-end run управляется как fault-tolerant job и может быть воспроизведён/сравнен.

---

# Sprint S13 — Scalable layer delivery, exports и complete GIS UI → `v0.14.0`

### S13-T01 — Layer catalog contract
Source/generated/validation layers, owner/run/dataset version, rendering metadata.

### S13-T02 — Bbox/pagination vector API
Strict limits, projection policy, geometry simplification options.

### S13-T03 — MVT endpoint
`ST_AsMVT`, tile bounds, GiST prefilter, run/layer scoping.

### S13-T04 — Tile cache headers
ETag/cache-control using immutable dataset/run semantics.

### S13-T05 — Frontend layer registry
Declarative source type: GeoJSON/bbox/MVT/raster; styles separated from components.

### S13-T06 — Full layer tree
Source/suitability/zones/roads/blocks/parcels/buildings/facilities/violations.

### S13-T07 — GeoJSON export job
Selected run/source layers -> artifact asynchronously.

### S13-T08 — GeoPackage export job
Multi-layer GPKG, batch/stream reads.

### S13-T09 — Metrics CSV export
Single run + scenario compare tables.

### S13-T10 — Config/provenance export
Normalized config + manifest + CRS/seed/dataset refs.

### S13-T11 — S3/MinIO ArtifactStore adapter
Contract parity с LocalArtifactStore; no domain changes.

### S13-T12 — Large upload UX
Progress/error/retry, no base64, dataset version visibility.

### S13-T13 — Complete project workspace UI
Project/datasets/map/parameters/jobs/metrics/compare integrated.

### S13-T14 — Playwright E2E
Create/upload/run/inspect/compare/export on stable fixture.

**Sprint gate:** весь основной workflow проходит из браузера, большие layers имеют tile-ready path.

---

# Sprint S14 — Performance, operations и hardening → `v0.15.0`

### S14-T01 — Worker queue separation
ingest/generation/analysis/export queues + per-queue concurrency.

### S14-T02 — Backpressure/resource limits
Max active jobs, candidate limits, file limits, scenario batch bounds.

### S14-T03 — DB query profiling
Top slow queries, `EXPLAIN ANALYZE`, index fixes documented.

### S14-T04 — Raster memory profiling
Window/chunk sizes, peak memory diagnostics, no accidental full-raster load.

### S14-T05 — Graph performance profiling
Noding/snapping/pathfinding timings and candidate counts.

### S14-T06 — Building/infrastructure performance profiling
Placement/accessibility hotspots, cache/index fixes.

### S14-T07 — Reference benchmark suite
25 км² и 100 км² profiles, timings/memory, machine metadata.

### S14-T08 — Operational metrics
Queue depth, job duration, failure rate, stage duration; Prometheus-ready adapter.

### S14-T09 — Artifact garbage collection policy
Expired/temp/orphan detection, dry-run, bounded delete.

### S14-T10 — Production-like Docker profile
Reverse-proxy ready, no dev mounts, externalizable DB/Redis/storage.

### S14-T11 — Security hardening
Upload/content checks, SQL/path audit, CORS, non-root/non-superuser where reasonable.

### S14-T12 — Backup/restore smoke for metadata DB
Documented dump/restore of project/run metadata; raw artifacts remain external.

### S14-T13 — Full migration-from-zero CI
Fresh DB + all migrations + integration smoke.

### S14-T14 — Reliability E2E
Worker crash/retry, duplicate enqueue, cancelled run, orphan temp artifact scenarios.

### S14-T15 — Documentation hardening
Deployment, troubleshooting, architecture invariants, benchmark reproduction.

**Sprint gate:** система не только работает, но измеримо выдерживает reference envelope и предсказуемо восстанавливается после типовых сбоев.

---

# Sprint S15 — Experiments, ВКР и demo package → `v0.16.0`

**Важно:** методика уже зафиксирована в основном ТЗ. Этот sprint реализует и выполняет её.

### S15-T01 — Experiment runner schema
territory × config × seed × ablation matrix.

### S15-T02 — Territory A reproducible package
Expansion-mode real dataset manifest/preprocessing script.

### S15-T03 — Territory B reproducible package
Contrasting relief/density или from-scratch dataset.

### S15-T04 — Reproducibility experiment
Same seed/config repeated; compare hashes/tolerance metrics.

### S15-T05 — Seed variability experiment
5–10 seeds; morphology/accessibility distributions.

### S15-T06 — Density scenario experiment
Low/medium/high population/FAR targets.

### S15-T07 — Constraints ablation baseline
Constraint-aware vs simplified/disabled-soft baseline; violations and score components.

### S15-T08 — Infrastructure baseline
Greedy network-aware vs random/naive placement.

### S15-T09 — Score sensitivity experiment
Weight perturbations; ranking stability.

### S15-T10 — Optional real-growth holdout
Если доступны временные данные: сравнить generated expansion с поздней реальной застройкой по агрегированным morphology metrics.

### S15-T11 — Experiment report exporter
CSV/JSON tables, manifest links, timing, validation, raw metrics.

### S15-T12 — Thesis figure/table dataset
Стабильные данные для графиков и таблиц пояснительной записки.

### S15-T13 — Offline demo dataset
Небольшой подготовленный набор, не зависящий от интернета.

### S15-T14 — Defense demo flow
5–8 минут: import/snapshot/run/layers/violations/compare/export.

### S15-T15 — Fallback demo artifacts
Screenshots/video/exported reports на случай внешнего сбоя.

**Sprint gate:** результаты воспроизводимы и непосредственно используются в экспериментальной части ВКР.

---

# Release hardening → `v1.0.0`

`v1.0.0` не добавляет новый алгоритмический модуль.

### R1 — Clean-clone acceptance
Новый environment разворачивается строго по README.

### R2 — Full CI acceptance
Все required checks green на release commit.

### R3 — Full end-to-end real territory
At least Territory A проходит source→run→compare→export.

### R4 — Second-territory/generalization acceptance
Territory B либо эквивалентный second-case проходит ключевой pipeline.

### R5 — Expansion invariants
Fixed roads/buildings/infrastructure не мутируются; generated delta отделён.

### R6 — From-scratch invariants
Тот же pipeline работает при пустом/minimal fixed state.

### R7 — Reproducibility acceptance
Exact rerun соответствует tolerance policy.

### R8 — Performance acceptance
Reference benchmarks опубликованы; нет критического budget violation без documented waiver.

### R9 — Research package acceptance
Experiments, raw metrics, manifests и report tables доступны.

### R10 — Release tag
Только после R1–R9 создаётся `v1.0.0`.

---

## 5. Зависимости критического пути

```text
S00 baseline
 -> S01 contracts
 -> S02 persistence/jobs
 -> S03 ingest
 -> S04 constraints+suitability
 -> S05 zoning
 -> S06 roads
 -> S07 blocks/parcels
 -> S08 buildings
 -> S09 demography
 -> S10 infrastructure
 -> S11 validation/metrics
 -> S12 orchestration/scenarios
 -> S13 delivery/export/UI
 -> S14 hardening/performance
 -> S15 experiments
 -> v1.0.0
```

Некоторые UI/workflow tasks можно вести параллельно после стабилизации соответствующего backend contract, но sprint gate закрывается только целиком.

---

## 6. Architecture gates между sprint

### Contract gate
- следующий stage получает typed result предыдущего?
- нет DB/HTTP leakage в core?
- version/fingerprint определён?

### Data gate
- ownership/version/run явны?
- CRS/units явны?
- indexes соответствуют query path?
- raw artifact не смешан с normalized rows?

### Constraint gate
- новое правило использует общий engine?
- hard/soft distinction сохранён?
- problem geometry/diagnostic доступен?

### Algorithm gate
- deterministic?
- bounded?
- spatial index/candidate cap?
- synthetic fixture?

### Job gate
- retry?
- idempotency?
- cancellation boundary?
- artifact cleanup?
- DB/queue consistency?

### Frontend gate
- backend authoritative?
- explicit run/version selection?
- layer scalable?
- errors/progress visible?

### CI gate
- HEAD required checks green?
- если нет — sprint/task не DONE.

---

## 7. Что запрещено «сделать заодно»

При реализации одного work item нельзя без отдельного item:

- менять public API несвязанного модуля;
- внедрять новый queue/storage framework;
- менять CRS policy;
- объединять fixed/generated tables;
- переносить GIS logic в controller;
- переписывать соседний алгоритм ради «красоты»;
- отключать test/lint rule вместо исправления причины без обоснования;
- использовать `--force` upgrade зависимостей как способ убрать audit warning;
- реализовывать следующий sprint заранее.

Если обнаружен blocker архитектуры, создаётся отдельный work item/исправление, а не скрытый рефакторинг.

---

## 8. Definition of ready для запроса нейросети

Перед передачей work item нейросети должны быть известны:

- ID задачи;
- текущий branch/HEAD;
- входные contracts;
- acceptance criteria;
- релевантные fixtures;
- какие checks являются required.

Если какой-то из этих пунктов отсутствует, сначала уточняется/создаётся contract task, а не начинается большой speculative implementation.
