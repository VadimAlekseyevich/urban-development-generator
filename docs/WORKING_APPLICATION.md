# Рабочее приложение — статус и план приёмки

Обновлено 25.11.2026. Источник текущих инженерных задач:
[GitHub issue #202](https://github.com/VadimAlekseyevich/urban-development-generator/issues/202).
Цель — реально работающий локальный 2D GIS-сервис для ВКР, а не количество
закрытых задач. Основной режим EXPANSION, второй FROM_SCRATCH; одно ядро.
Исторические спринты остаются справочником, не активным порядком работ.

## Что подтверждено

| Возможность | Уровень доказательства |
|---|---|
| Compose, API, React/MapLibre | CI запускает полный стек |
| Проекты, граница, импорт | Реальный browser ingest без seeded DatasetVersion, #201/#207 |
| Векторные данные | GeoJSON, GeoPackage, ZIP/Shapefile; OSM PBF отдельным импортом |
| Рельеф | GeoTIFF с CRS; нормализация, чтение готового DEM и расчёт уклонов (#208) |
| Ограничения, дороги | Run-scoped source adapters, алгоритмы и тесты |
| Землепользование/существующие здания | #209 merged; exact PR CI green |
| Конфигурация | v5, проверка совместимости исходных данных (#221), профиль и импорт JSON; профиль требует отдельной сквозной приёмки |
| Worker orchestration | #220/#222: оба режима прошли реальный unseeded API/Compose тест, 12/12 Stage |
| Карта, сравнение и экспорт | API/UI работают над готовыми persisted outputs; comparison fixture синтетическая |

Основная ревизия с полным runtime: `cf7f78f`; точная PR-ревизия #216
`2a28eb3` прошла все пять [CI-проверок](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/45609533065).
Оба smoke-режима подтвердились в #222 ([CI 45684956177](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/45684956177)): FROM_SCRATCH 88 дорожных элементов, 52 квартала, 52 участка, 192 здания и одна facility на искусственном квадрате 100 м. Это не доказательство градостроительного качества или масштабируемости.

## Порядок доведения до рабочего состояния

1. **P0 — реальное выполнение.** Полный runtime интегрирован в #216.
   Проверка v5 до постановки задания в очередь интегрирована в #217.
   Подтвердить кодовую ревизию,
   пригодность реальных входов и отсутствие частичных записей при ошибках.
2. **P0 — UX.** Выбор нескольких ready datasets отделён от карты; доступен
   импорт полного JSON и стартовый структурированный профиль района. Остались
   проверка профильных допущений, понятные ошибки,
   проверка объединённой сборки и отображение именно нового результата на карте.
3. **P0 — приёмка.** Один реальный небольшой пользовательский сценарий каждого
   режима через Compose/API/UI, без ручного SQL и seeding готовых результатов.
4. **P1 — качество и воспроизводимость.** Реальная территория 25 км², затем
   100 км²; валидность, фиксированные источники, повторяемость, сравнение и
   экспорт, отказоустойчивость. Исследовательские эксперименты после п. 3.
5. **Публичная эксплуатация:** auth/quota и отдельная проверка безопасности.
   До этого только доверенная локальная среда.

## Как проверить сейчас

В корне репозитория:
```bash
cp .env.example .env
export GENERATION_BUILD_REVISION=$(git rev-parse HEAD)
docker compose up --build
```
Открыть http://localhost:5173, создать проект с подходящей метрической CRS,
загрузить один GeoJSON Polygon/MultiPolygon в EPSG:4326 как границу,
загрузить исходные слои и DEM .tif/.tiff с геопривязкой, запустить импорт,
дождаться ready. Обычный TIFF без геопривязки не является валидным DEM;
растровые категории кроме DEM через обычный ingest пока не поддерживаются.
Для API см. [API.md](API.md), [INGEST_SUBMISSION.md](INGEST_SUBMISSION.md).

## Критерий «рабочее приложение»

- Пользователь загружает собственные входы и задаёт территорию и параметры.
- Реальный deployed worker завершает запуск, все обязательные Stage имеют
  достоверный результат, ошибок/исключений без фиктивного success нет.
- Новые зоны, дороги, кварталы и здания, инфраструктура, население, метрики
  и нарушения сохранены с принадлежностью к этому запуску, видны на карте.
- В EXPANSION существующие источники не изменены; FROM_SCRATCH использует
  тот же DAG. Повтор с теми же входами/seed проверяем, результаты доступны для
  сравнения и экспорта.
- На exact merged head зелёные обязательные CI, включая не подменённый
  сквозной браузерный тест. Синтетические тестовые входы допустимы для
  регрессии, но не могут заменить пользовательский сценарий и реальную
  территорию.

## Карта документации

[README](../README.md) — запуск и статус; [ТЗ](DEVELOPMENT_PLAN.md) — цель;
[ARCHITECTURE](ARCHITECTURE.md), [PIPELINE_MODEL](02-architecture/PIPELINE_MODEL.md)
и [STAGE_CATALOG](02-architecture/STAGE_CATALOG.md) — архитектурные контракты;
[DATA_MODEL](DATA_MODEL.md), [API](API.md) — схема и транспорт;
[RUN_CONTROL](RUN_CONTROL.md), [WORKSPACE_UI](WORKSPACE_UI.md),
[PLAYWRIGHT_E2E](PLAYWRIGHT_E2E.md) — пользовательские границы;
[07-planning](07-planning/README.md) — исторический контекст.
Подсистемные документы в docs/ сохраняются как технические справочники;
название sprint/work item в них не является заявлением о готовности продукта.

### Documentation review scope (25.11.2026)

Inventory: 166 existing Markdown documents (including README) plus this page.
All 167 were retrieved and screened for stale issue #178, conflicting explicit
next-task statements, misleading production-readiness phrases and missing
relative Markdown link targets. The main navigation, readiness, run/ingest
contracts, browser-test descriptions and affected subsystem notes were
reviewed and corrected. This is a targeted consistency review, not a claim
that every historical algorithm specification has been revalidated against
all current source code. Preserve contract evidence; correct subsystem details
as part of the corresponding functional changes.


### User-reported infrastructure routing failure (2026-11-25)

Manual local run at main `4fde175`, seed 42, failed at infrastructure after
39.38 seconds: 4,775,310 candidate-demand pairs > 1,000,000 result budget.
Exact user's form values are not confirmed. The candidate fix retains all
block demand rows and all existing capacity accounting; when the dense matrix
would exceed configured result/routing bounds it selects a deterministic
spatially distributed candidate shortlist, targeting at most 250,000 pairs
when feasible. The existing exact calculation and hard validity checks still
apply to shortlisted sites; approximate search can miss feasible alternatives.
Stage diagnostics disclose the shortlist. A new unseeded 400 m, 10 m grid,
seed 42, 1000 residents regression exercises the default-like configuration.
A second user run (seed 20260919) also failed: 4,188,528 > 1,000,000.
CI 45698917851 completed six browser tests but the dense profile did not
trigger the shortlist; the corrected regression forces a 4,000-pair test
budget and asserts the diagnostic and actual pair counts. A separate unit
case checks a 4.8 million pair input against the production default budget.
The previously suggested 40 m / 300 residents workaround also failed locally.
Do not request further manual reruns before the corrected overflow gate passes.
Exact local configuration and source-normalization differences remain unverified.


### Manual acceptance — quality and metrics

A user successfully displayed run 92d4bb0f locally after #226. The screenshot
shows over-triangulated roads/blocks, sparse buildings, overlapping map layers,
and only one score criterion (hard violations). A 1.0 score means only that
this criterion passed, not satisfactory urban quality. The dashboard fix
persists all already-calculated canonical scalar metrics during final validation
and displays them separately from the score. Old successful runs are immutable
and explicitly lack the new envelope; no fabricated or retroactive metric data.
Population attainment, road geometry and land-use quality remain open work.


### Real Ryazan acceptance (manual, 2026-11-26)

Cached PBF, 800 m square, EPSG:32637, EXPANSION; 196 roads, 117 buildings,
23 landuse polygons, 3 water polygons and 4 water lines imported through
actual API/worker. Run 733814f4: first five stages succeeded, block
polygonization failed because GEOS returned an invalid candidate. Map showed
partial output, not a successful plan. Fix repairs invalid polygonal faces
without noding or mutating the routing graph; refuses overlapping repairs,
reports repair counts, tests a real GEOS touching-hole case. Profile candidate
limits growth attempts to 40 and uses strict Gabriel neighbours for extra
anchor pairs (MST remains); avoids square-grid diagonal shortcuts at the
candidate-selection level, not a guarantee of orthogonal streets. Exact
Ryazan topology requires replay of the extracted local fixture; no real-data
success claim before that replay. No DEM in cached OSM extract.

The actual uploaded repro archive was inspected: all 343 vector features
(196 roads, 117 buildings, 23 landuse, 7 water) are individually valid.
The run reports 634 nodes, 817 edges, eight components and 153 road
validation issues. A separate geometric probe using the exact source
geometries, projected 21×21 40 m grid and a center-based obstacle mask
found 296/1048 candidate adjacent-cell segments intersect buildings or water.
This probe is not the production road graph or the exact pipeline mask.
Road routing now checks each candidate segment against the exact vector
exclusions via a shared STRtree; fixed-road attachment extensions also
must be clear. Final forbidden-crossing validation remains unchanged.
Source network disconnections and post-snap intersections still need real
replay analysis. No arbitrary relaxation of hard constraints.


Follow-up: CI 45707315461 found zero parcels on the sparse profile and
did not exercise the dense infrastructure overflow gate. Sparse road
growth is now opt-in (`sparse_road_growth=true`), not the ordinary
profile default. The original nonempty and overflow acceptance assertions
are unchanged. Analysis of the extracted Ryazan source: 196 features /
200 line parts, 99 service, 81 path, 16 other streets. A distance-based
1 m connectivity probe found 8 components (190 parts in the largest).
A separate planar union/polygonize probe found 83 faces using all roads
(34 below 1000 m²), 30 without paths (7 below 1000 m²), and only two
using neither paths nor service roads. This is not the actual semantic
road graph, and does not prove viable blocks. Added opt-in pedestrian
boundary filtering (keeps paths for routing/access), and an opt-in
EXPANSION validation policy that records inherited fixed components but
rejects newly disconnected generated components, even within the original
component budget. The original strict policy remains default.
The calibrated replay enables all three experiments; exact archived
replay remains unchanged. Full real replay still pending.
