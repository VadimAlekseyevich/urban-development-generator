# Архитектура

## Компоненты

```text
React + MapLibre
      |
      v
   FastAPI  ---- PostgreSQL/PostGIS
      |               |
      |               +-- проекты, datasets, геометрии, результаты
      |
      +---- Redis ---- Worker (ARQ)
                        |
                        v
                  Algorithmic Core
      suitability -> zoning -> roads -> blocks -> buildings
                 -> population -> infrastructure -> validation -> metrics
```

## Границы модулей

`core/urban_generator` не импортирует FastAPI, SQLAlchemy или React-специфичные типы. Ядро получает нормализованные входные данные и конфигурацию и возвращает пространственные результаты/метрики.

`backend` отвечает за HTTP, валидацию запросов, транзакции, хранение файлов, создание jobs и сериализацию результатов.

`worker` является отдельным процессом. Генерация кварталов, перерасчёт доступности и растровый анализ могут занимать минуты и не должны блокировать HTTP worker.

`frontend` работает как GIS-интерфейс: карта является центральным элементом, а панели управляют слоями, параметрами, сценариями и метриками.

## Backend request boundary

HTTP-контроллеры не строят SQL statements и не управляют ORM query details. Для прикладных use cases используется направление зависимостей:

```text
FastAPI endpoint
    -> application service
        -> repository protocol
            -> SQLAlchemy repository adapter
                -> request-scoped Session
```

Контроллер отвечает за HTTP/Pydantic boundary и отображение прикладных ошибок в HTTP status codes. `ProjectService` реализует project CRUD orchestration и тестируется с fake repository без FastAPI и DB session. `SqlAlchemyProjectRepository` владеет `select/order/limit/offset`, ORM persistence и commit/refresh для project mutations.

Инфраструктурные SQL probes также находятся за DB boundary: readiness endpoint вызывает bounded database probe и не содержит SQL text. Более сложные transaction/unit-of-work abstractions добавляются только когда use case требует координации нескольких repositories; S02-T11 не вводит преждевременный generic UoW.

## Road network backend boundary

Публичный контракт маршрутизации остаётся `core.urban_generator.domain.NetworkBackend`: domain-код работает только с `NetworkNodeRef`, `NetworkPoint`, `NetworkPath`, `NetworkDistanceResult` и `NetworkGraphSnapshot` и не импортирует NetworkX.

`core.urban_generator.roads.NetworkXBackend` — adapter v1. Он принимает уже построенный NetworkX graph, делает собственную immutable-копию и валидирует минимальную метрическую schema:

- node id — непустая строка;
- `x_m` и `y_m` — конечные координаты в `WorkingCRS`;
- `length_m` — неотрицательная конечная стоимость каждого edge.

Адаптер преобразует NetworkX routing results обратно в domain-типы. Начиная с S06-T03 `snap()` делегирует reusable `SpatialSnapIndex` и не выполняет линейный scan всех graph nodes. Начиная с S06-T05 `NetworkXBackend.from_road_graph()` умеет адаптировать backend-independent `RoadGraph` через `MultiGraph`, не схлопывая parallel road edges.

## Canonical OSM road semantics

S06-T02 нормализует source-specific OSM tags до устойчивой canonical road schema до graph construction. Помимо уже существующих `road_class` и boolean `one_way`, `source_roads` хранит `bridge`, `tunnel`, signed `layer` и `one_way_direction` со значениями `both`, `forward` или `reverse` относительно порядка координат geometry.

Явный OSM `oneway` имеет приоритет. `-1` сохраняется как `reverse`, а при отсутствии тега стандартные implied one-way случаи `motorway`/`motorway_link` и `roundabout`/`circular` нормализуются как `forward`. Динамические значения вроде `reversible` не превращаются в фиксированное направление. `bridge`/`tunnel` трактуют любой непустой value кроме явных `no/false/0` как наличие соответствующей структуры; malformed/out-of-range `layer` безопасно становится `0`. Сырые tags остаются в provenance `attributes_json`.

Эти поля являются входом для S06-T04 semantic noding: решение о создании graph intersection не повторно интерпретирует OSM tags. S06-T02 не выполняет noding и не строит graph.

## Spatial snapping boundary

S06-T03 вводит `core.urban_generator.roads.SpatialSnapIndex` как reusable point-index для road endpoints, intersection candidates и уже построенных graph nodes. Индекс принимает только `NetworkPoint` в явно указанной metric `working_srid`, строит Shapely `STRtree` один раз и для каждого запроса использует bounded-by-tolerance `dwithin` candidate search вместо полного point-by-point scan.

Размер индекса ограничен `max_targets` (по умолчанию 500 000), а tolerance задаётся в метрах на каждый query. Все кандидаты дополнительно проверяются точным Cartesian distance; результат сортируется по `(distance_m, target_id)`, поэтому равные расстояния разрешаются детерминированно и не зависят от traversal order `STRtree`. `snap_target()` исключает сам target и предназначен для endpoint/intersection coalescing.

S06-T03 не решает, является ли геометрическое пересечение реальным road junction: bridge/tunnel/layer semantics не интерпретируются внутри snapping index. Это ответственность S06-T04 semantic noding.

## Semantic road noding boundary

S06-T04 вводит `core.urban_generator.roads.SemanticNoder`. Вход `SemanticRoad` уже содержит canonical `layer`, `bridge` и `tunnel`; raw OSM tags внутри noder не читаются. Геометрии должны быть 2D `LineString`/`MultiLineString` в явной metric working CRS.

Кандидатные пересечения находятся через `STRtree`, а не полным N×M сравнением. Размер входа ограничен `max_road_parts` (по умолчанию 500 000), число реально пересекающихся candidate pairs — `max_candidate_pairs` (по умолчанию 2 000 000). Для interior crossing junction создаётся только при совпадении `(layer, bridge, tunnel)`. Exact shared endpoint считается явной топологической связью и сохраняется даже при переходе structure/layer, чтобы bridge/tunnel segment не отрывался от approach geometry. T-junction с несовместимой grade semantics не соединяется.

Допустимые junction points разрезают только те line parts, где точка лежит в интерьере. Порядок и направление resulting parts сохраняются относительно исходной geometry, чтобы downstream traversal semantics могли использовать исходную ориентацию. Частичные линейные overlaps не создают бесконечное число nodes: noding использует только границы overlap и сообщает `overlap_pair_count`; duplicate/tiny-edge cleanup остаётся S06-T06.

Результат S06-T04 содержит split `NodedRoad` geometries, `SemanticJunction` и bounded-work diagnostics, но не создаёт graph nodes и edges. Это ответственность S06-T05 Graph build.

## Road graph build boundary

S06-T05 вводит `RoadGraphBuilder` и backend-independent `RoadGraphNode`/`RoadGraphEdge`. На вход builder получает уже snapped+noded `NodedRoad` и существующий `WorldStateContract`; он не повторяет snapping, noding или OSM tag parsing. Все distance/length операции разрешены только в `WorkingCRS` с metre units.

Exact endpoint coordinates после S06-T03/S06-T04 определяют graph node. Node IDs формируются детерминированно по отсортированным coordinates, road inputs — по `road_id`, поэтому результат не зависит от порядка входного tuple. Edge сохраняет `road_id`, `part_index`, исходное направление `LineString`, metric `length_m` и полный `WorldStateContract`; `is_source`/`is_fixed` выводятся из него. Shared node агрегирует source/fixed participation соседних edges, что позволяет generated road безопасно присоединяться к fixed network, не меняя ownership fixed edge.

Graph build ограничен `max_nodes` и `max_edges` (по умолчанию по 1 000 000). Connected components считаются union-find проходом O(V+E), без NetworkX и без повторного N×M scan. Diagnostics содержат node/edge counts, total length, source/fixed edge counts и детерминированный summary каждой connected component.

`RoadGraph` остаётся предметным контрактом и не равен `networkx.Graph`. `NetworkXBackend.from_road_graph()` создаёт adapter-level `MultiGraph`, чтобы parallel edges не терялись; текущая accessibility topology connectivity-neutral/undirected согласно v1 pedestrian semantics. Применение сложных one-way/turn rules не добавляется в S06-T05.

## Road graph cleanup boundary

S06-T06 вводит `RoadGraphCleaner` как отдельный шаг после graph build и до routing services. Cleanup не повторяет snapping/noding и не использует NetworkX. Он работает только с metric `RoadGraph` и возвращает новый graph с пересчитанными node ownership flags и connected-component diagnostics.

Exact duplicate определяется консервативно: совпадает полный 2D coordinate chain, при этом обратное направление считается тем же geometry. Простого совпадения пары endpoint nodes недостаточно, поэтому настоящие parallel edges с другой геометрией сохраняются. Если duplicate group содержит fixed source edge, именно он является deterministic representative; в остальных случаях tie-break идёт по stable `edge_id`.

Tiny/dangling cleanup управляется `RoadGraphCleanupPolicy`. Значения `tiny_edge_threshold_m` и `dangling_edge_threshold_m` по умолчанию равны `0.0`: destructive metric cleanup не делает скрытых предположений о допустимых метрах и должен быть явно согласован с snapping/precision policy проекта. Fixed source edges никогда не удаляются как tiny или dangling. Generated dangling edges prunятся leaf-by-leaf не более `max_prune_passes` (по умолчанию 16); если bound исчерпан, diagnostics оставляют `prune_limit_reached` и число remaining generated dangling edges вместо неограниченного цикла.

Cleanup ограничен `max_nodes`/`max_edges` и использует O(E) duplicate/tiny passes плюс O(`max_prune_passes × E`) dangling pruning. После удаления edges orphan nodes удаляются, shared-node `is_source/is_fixed` агрегируются заново по retained edges, а обычные `RoadGraphDiagnostics` пересчитываются. S06-T06 не добавляет Dijkstra/A* API — shortest-path services остаются S06-T07.

## Building spacing boundary

S08-T08 вводит `core.urban_generator.buildings.BuildingSpacingIndex` как metric spatial primitive для проверки уже размещённых 2D footprints. Индекс принимает immutable набор `PlacedBuildingFootprint` в одной явной `WorkingCRS`, строит Shapely `STRtree` и для каждого candidate footprint проверяет два независимых условия: отсутствие площадного overlap и соблюдение `minimum_gap_m`.

При нулевом gap касание границ допустимо, но положительное площадное пересечение всегда является violation. При положительном gap exact geometry distance должен быть не меньше требуемого значения. Candidate discovery выполняется через расширенный bbox и ограничивается `max_candidates`; общий размер индекса ограничивается `max_footprints`. Если несколько существующих зданий нарушают правило одновременно, результат выбирается детерминированно: overlap имеет приоритет, затем меньшая distance и stable `building_id`.

S08-T08 намеренно не выполняет FAR/coverage convergence, выбор следующего footprint или placement loop. Жизненный цикл spacing index и bounded convergence относятся к S08-T09; T09 обязан переиспользовать этот contract вместо повторного полного N×M scan.

## Building placement convergence boundary

S08-T09 вводит `BuildingPlacementConverger` как bounded greedy loop поверх уже построенных 2D footprint proposals. Входные proposals сортируются детерминированно по `(-priority, proposal_id)`; каждый proposal рассматривается не более одного раза, а общий проход ограничен `max_proposals` и `max_iterations`.

Convergence ведётся одновременно по двум target windows относительно явной `site_area_m2`: coverage и FAR. `BuildingPlacementBaseline` позволяет включить уже существующую fixed/generated интенсивность в стартовые метрики. Proposal принимается только если проходит S08-T08 spacing относительно existing footprints и всех ранее принятых proposals и не выводит coverage/FAR выше соответствующей верхней границы tolerance window. Проход завершается как `CONVERGED`, `CANDIDATES_EXHAUSTED` или `MAX_ITERATIONS`; diagnostics сохраняют initial/final значения, unmet/excess target deltas и причины rejection.

Так как Shapely `STRtree` immutable, accepted footprints не добавляются через полный rebuild общего индекса после каждого здания. Existing footprints индексируются один раз, а новые accepted footprints хранятся в immutable power-of-two chunks: при совпадении размеров два chunks сливаются. Поэтому один footprint переиндексируется не более O(log N) раз, а spacing query проверяет base index и O(log N) chunk indexes; это сохраняет S08-T08 exact spacing semantics без квадратичной последовательности полных rebuild.

Поле `planning_floor_area_multiplier` является только provisional planning intensity для FAR convergence. Оно не является назначенной этажностью и не считается финальным GFA. S08-T10 остаётся единственным местом назначения floors/use, а S08-T11 — авторитетного расчёта footprint area/GFA и итоговых coverage/FAR metrics. Если T10/T11 выявляют расхождение с planning target, оно должно быть явно диагностировано, а не скрыто изменением геометрии.

## Building attribute assignment boundary

S08-T10 назначает только geometry-free attributes: canonical `BuildingUse` и положительное
число `floors`. Правила versioned и задаются точной парой
`(ZoneClass, BuildingArchetype)`; неявных fallback-правил нет, поэтому неполная
конфигурация завершается явной domain error вместо скрытой смены типа использования.

Если правило задаёт диапазон этажности, `BuildingAttributeAssigner` использует отдельный
детерминированный RNG stream на каждый `building_id`, полученный из `RunContext`,
config fingerprint, zone и archetype. Поэтому результат воспроизводим при одинаковом seed
и не зависит от порядка входного tuple или добавления несвязанного building subject.

T10 не принимает и не изменяет geometry и не вычисляет footprint area/GFA/FAR. Эти
метрики остаются S08-T11. Таким образом назначение этажности можно менять и тестировать
отдельно от placement geometry, а расхождение с provisional FAR target S08-T09 остаётся
наблюдаемым, а не маскируется повторной геометрической генерацией.

## Building area and GFA metrics boundary

S08-T11 является авторитетным numeric stage для building intensity. Для каждого
`BuildingAreaSubject` площадь footprint берётся непосредственно из polygonal geometry в
явной metric `WorkingCRS`, а `gfa_m2` вычисляется как
`footprint_area_m2 × assigned floors` из результата S08-T10.

Aggregate summary принимает явную `site_area_m2` и, при необходимости,
`BuildingAreaBaseline` с уже измеренными fixed-state footprint/GFA. Coverage считается
как `total_footprint_area / site_area`, FAR — как `total_gfa / site_area`.
Калькулятор не использует provisional `planning_floor_area_multiplier` из S08-T09 и не
clamp-ит некорректную coverage: если суммарная footprint area превышает site area,
контракт завершается ошибкой.

T11 не сохраняет результаты в БД и не добавляет API/UI; persistence остаётся S08-T12.
Также T11 не переоценивает use/floors и не меняет geometry.

## Generated building persistence boundary

S08-T12 специализирует `generated_buildings` как run-scoped persistence результата
building stage. SQLAlchemy adapter принимает T10 assignments, T11 authoritative area/GFA
и исходные `BuildingAreaSubject`, проверяет их полное совпадение до любых destructive
операций и только затем атомарно заменяет rows незавершённого run.

Core `source_id` не заменяется DB UUID. Persistence adapter разрешает его внутри
конкретного run по `GeneratedParcel.parcel_key` или `GeneratedBlock.block_key`;
parcel-based building хранит одновременно `parcel_id` и родительский `block_id`,
block-based building — только `block_id`. Database id здания детерминирован как UUID5
от `run_id` и core `building_id`, поэтому retry сохраняет идентичность rows.

В typed columns сохраняются building/source refs, zone, archetype, use, floors,
footprint area и GFA; config/assignment provenance остаётся в `attributes_json`.
Геометрия обязана быть `POLYGON` в `GenerationRun.working_srid`. Writer блокирует
успешный run, пишет bounded chunks и не реализует API/UI — это граница S08-T13.

## Generated buildings UI boundary

S08-T13 доставляет persisted `GeneratedBuilding` через project/run-scoped bbox GeoJSON
и отображает его отдельным MapLibre layer. Backend остаётся authoritative для run list,
typed properties и spatial filtering; frontend не реконструирует floors/GFA или
block/parcel relations из других слоёв.

UI позволяет переключать generation run, независимо скрывать generated buildings,
визуально различать archetype/use, видеть viewport truncation и инспектировать typed
атрибуты кликом. Source buildings остаются отдельным source layer и не смешиваются с
generated rows. T13 использует bounded GeoJSON path; MVT/cache/export/общий layer catalog
остаются S13 и не внедряются преждевременно.

## Building property/stress boundary

S08-T14 закрепляет building stage как проверяемую capability, а не набор отдельных unit contracts. Property tests проверяют, что accepted footprints после bounded convergence не имеют площадных overlap и соблюдают minimum inter-building gap, а reference pipeline повторно даёт идентичный digest и попадает в coverage/FAR target window при одинаковом seed/config.

Reference benchmark запускает полный T09→T10→T11 путь на синтетической grid fixture. CI выполняет профили 1 000 и 10 000 зданий; benchmark не задаёт performance SLA, но делает алгоритмический drift и внезапный рост стоимости видимыми вместе с machine-independent correctness checks.

T14 не добавляет новые building rules и не меняет persistence/UI semantics. После зелёного T14 sprint S08 закрывается, следующий contract — S09-T01 DemographicScenario.

## Demographic scenario boundary

S09-T01 вводит `core.urban_generator.demography.DemographicScenario` как immutable
versioned input демографического pipeline. Population objective выражается ровно одним
typed `PopulationTarget`: либо абсолютным `total_population`, либо fractional
`growth_rate` относительно baseline, который разрешается на более поздней стадии.

Housing assumptions (`occupancy_ratio`, `residential_area_per_person_m2`,
`average_household_size`, `residential_gfa_share`) хранятся отдельно от building
geometry/metrics. Age groups являются configurable exhaustive partition с уникальными
codes, непрерывными непересекающимися age ranges и shares, суммирующимися в 1.0.
`working_population_ratio` остаётся отдельным scenario-level assumption.

Scenario canonicalizes age-group order и имеет stable SHA-256 fingerprint. S09-T01 не
распределяет жителей, не вычисляет residential capacity/jobs, не читает population
raster и не добавляет persistence/API/UI; эти обязанности остаются S09-T02–T11.

## Residential capacity boundary

S09-T02 связывает authoritative S08-T10 use/floors и S08-T11 GFA без повторного чтения
geometry. `ResidentialCapacitySubject` требует совпадения building id и floors между
`AssignedBuildingAttributes` и `BuildingAreaMetrics`.

Residential building использует весь GFA, mixed building — scenario-level
`residential_gfa_share`, public/commercial — нулевую residential capacity. Далее
occupancy применяется к residential GFA, resident capacity вычисляется через
`residential_area_per_person_m2`, а fractional household capacity — через
`average_household_size`.

T02 сохраняет fractional capacities и scenario provenance, но намеренно игнорирует
population target, age-group shares и working ratio при расчёте физической вместимости.
Integer resident allocation и target reconciliation принадлежат S09-T03.

## Population allocation boundary

S09-T03 преобразует fractional S09-T02 resident capacity в integer generated-resident
allocation. Absolute target учитывает optional baseline как уже существующее население;
growth target требует baseline и сначала детерминированно разрешается в target total.
Generated target равен положительной разнице target total и baseline.

Per-building integer capacity получается из fractional capacity без превышения физической
вместимости. Распределение использует exact integer quotient/remainder apportionment,
взвешенное integer capacities; tie-break выполняется по stable `building_id`. Поэтому
результат не зависит от порядка входа и не требует floating-point normalization.

Если generated target выше доступной capacity, результат остаётся валидным и возвращает
`CAPACITY_EXHAUSTED` с unmet count. Если baseline уже выше target, новые residents не
назначаются и возвращается `BASELINE_EXCEEDS_TARGET`. Zero capacity использует
utilization 0.0, поэтому NaN/Inf не возникают.

S09-T03 не распределяет age groups, не оценивает jobs и не агрегирует demographics;
следующий слой композиции — S09-T04.

## Age-group allocation boundary

S09-T04 распределяет фактически назначенных S09-T03 generated residents по configurable
age groups из `DemographicScenario`. Сначала общий allocated population переводится в
integer cohort totals через largest-remainder apportionment; затем эти column totals
раскладываются по building rows, сохраняя exact resident count каждого здания.

Обе размерности матрицы являются инвариантами: сумма cohorts внутри building равна
building residents, а сумма одного cohort по всем buildings равна cohort total.
Building tie-break выполняется по stable `building_id`, cohort tie-break — по
canonical age-group order. Zero population даёт нулевые counts/shares без NaN/Inf.

T04 не выполняет workforce/jobs estimation, block/zone aggregation или raster
calibration; эти обязанности начинаются с S09-T05.

## Jobs/workforce estimate boundary

S09-T05 хранит job-density assumptions отдельно от demographic scenario в versioned
`EmploymentConfig`. Для mixed/public/commercial use обязателен явный positive
`area_per_job_m2`; residential use имеет нулевой jobs estimate. Mixed building использует
только non-residential GFA `total_gfa × (1 - residential_gfa_share)`, public/commercial —
полный GFA.

Generated workforce считается отдельно как фактически allocated S09-T03 population ×
`working_population_ratio`. Requested-but-unallocated residents не создают workforce.
Jobs и workforce не принудительно балансируются и не связываются worker→workplace.

T05 сохраняет scenario/config provenance и не выполняет block/zone aggregation,
calibration, persistence или UI; следующий слой — S09-T06.

## Block/zone demographic aggregation boundary

S09-T06 выполняет strict join по stable `building_id` между S09-T03 population,
S09-T04 cohorts, S09-T05 jobs и явным `BuildingAggregationRef` с block/zone ownership.
Все четыре набора building ids обязаны совпадать, scenario provenance обязан быть
идентичным.

Block обязан принадлежать ровно одной zone, а один `zone_id` — одному `ZoneClass`.
Block и zone outputs сохраняют generated population, каждый age cohort и jobs estimate.
Cross-level consistency checks доказывают, что суммы block и zone rows совпадают с
authoritative T03/T04/T05 totals; output canonicalized по stable ids и bounded по building
count.

T06 не выполняет raster calibration или redistribution, persistence/API/UI и demand
mapping; population-raster adapter начинается с S09-T07.

## Population raster calibration adapter boundary

S09-T07 добавляет optional `PopulationRasterSource`/sampler и не меняет base
demographic pipeline S09-T01–T06. Source contract содержит только working CRS, raster
dimensions, metric cell area, value semantics и bounded `read_window`; core demography
не открывает GeoTIFF и не зависит от rasterio.

Поддерживаются явные value kinds `population_per_cell` и `density_per_km2`, которые
нормализуются в sampled population и mean density. До первого read проверяются subject,
window и total-cell limits, bounds и CRS. Nodata policy явный; all-nodata sample
возвращает zero values и `has_valid_data=False`, без NaN/Inf. Перекрывающиеся windows
одного subject запрещены для защиты от double counting.

T07 возвращает только external calibration evidence. Изменение spatial distribution с
сохранением demographic totals является S09-T08.

## Spatial demographic calibration boundary

S09-T08 применяет optional S09-T07 raster evidence только к spatial distribution внутри
каждой functional zone. Zone/project population и age-cohort totals сохраняются exact;
jobs не изменяются. Raster share смешивается с S09-T06 baseline share через explicit
`raster_weight`; samples ниже `minimum_valid_fraction` исключаются, а zone без positive
eligible evidence детерминированно fallback-ится к baseline.

Integer population использует largest-remainder apportionment со stable block-order
tie-break. После изменения block row totals age cohorts перераспределяются exact-margin
алгоритмом, поэтому block rows и zone cohort columns одновременно сохраняются. Result
содержит per-block audit и moved-population diagnostics.

T08 не меняет target/capacity, не читает raster напрямую и не добавляет persistence/API/UI;
следующий слой — S09-T09 typed demographic demand profile.

## Demographic demand profile boundary

S09-T09 является стабильной границей между demography и infrastructure. На каждый block
формируется canonical набор typed signals: total population, configurable age-group
cohorts, workforce и jobs. Signal хранит category, unit и, для cohort, stable demographic
group code + age bounds. Project totals имеют ту же schema, а block values обязаны
суммироваться обратно в totals.

Profile не кодирует education/healthcare/retail/recreation и не содержит facility-specific
coefficients. Эти правила принадлежат S10-T01 InfrastructureType/demand model. Поэтому
S10 может потреблять population/cohort/workforce/jobs без reverse dependency из
demography в infrastructure.

Builder принимает обычный S09-T06 aggregation либо calibrated
S09-T08 result.aggregation, проверяет scenario provenance, age-group metadata, bounded
block count и canonical signal schema. T09 не считает unmet demand и не добавляет
persistence/API/UI; UI/metrics начинаются в S09-T10, infrastructure semantics — в S10.

## Demography metrics/API/UI boundary

S09-T10 добавляет pure `DemographyMetricsBuilder`, который объединяет authoritative
S09 block aggregation с metric block area и вычисляет population density, total
population/jobs и exact age-group shares без NaN/Inf. Metrics сохраняют scenario и
employment provenance.

Persistence не вводит новую spatial table: `SqlAlchemyGeneratedDemographyWriter`
использует существующие extension points. Block metrics записываются namespaced в
`GeneratedBlock.attributes_json.demography`, run/project totals — в
`GenerationRun.metrics_json.demography`. Writer требует exact block-key alignment,
positive authoritative area и запрещает mutation successful run.

Read API предоставляет список demography runs, run-level metrics и bounded block GeoJSON.
GeoJSON flatten-ит population, density и jobs для MapLibre data-driven styling, сохраняя
cohort array для inspector. Frontend panel поддерживает choropleth modes density,
population и jobs, total metric cards, age shares и block inspector. Viewport limit
сохраняет bounded read contract.

T10 не определяет infrastructure demand coefficients и не меняет core demographic
allocation/calibration. Numeric/property hardening остаётся S09-T11.

## Demography numeric/property hardening

S09-T11 не добавляет новый production layer. Он закрепляет cross-module invariants для
всей demographic chain: zero capacity остаётся finite, mixed-use GFA согласованно делится
между residential capacity и jobs, target/cohort sums сохраняются exact, all-nodata raster
является neutral fallback, а результаты pipeline не зависят от input permutation.

Эти property tests являются sprint gate для S09. Следующий architectural boundary —
S10 InfrastructureType/demand/accessibility, который потребляет S09 demographic demand
profile и metrics, не переопределяя их semantics.

## Infrastructure type contract

S10-T01 вводит новый core boundary `core.urban_generator.infrastructure`.
`InfrastructureType` является immutable/versioned schema для facility semantics и
напрямую использует S09 `DemographicDemandCategory` как demand dependency, не дублируя
population/cohort/workforce/jobs vocabulary.

Контракт хранит category, demand rate, capacity, max network distance, allowed zones,
minimum/target site area и canonical candidate sources. Zone/source ordering
нормализуется, а fingerprint стабилен для эквивалентной конфигурации. Category-specific
presets не захардкожены: T01 задаёт schema, а не policy data.

Existing-facility mapping начинается в S10-T02; unmet demand, candidates, accessibility и
placement остаются последующими слоями и не входят в T01.

## Existing infrastructure adapter boundary

S10-T02 materializes fixed facilities referenced by an immutable `TerritorySnapshot`
without mutating the snapshot itself. A normalized source record carries snapshot
`source_ref`, source feature id/use, geometry, working SRID and optional capacity.

`ExistingFacilityMappingRule` maps external facility use to a concrete
`InfrastructureType.code`. Effective capacity is deterministic: explicit source capacity
wins, then rule default, then the configured InfrastructureType capacity. Source refs must
belong to `snapshot.facilities`, SRID must match snapshot settings, and duplicate source
features/type codes/mapping uses are rejected.

The adapter output preserves geometry and provenance for later S10 network snapping and
coverage calculation. It does not query the database, calculate unmet demand, generate
sites or run accessibility; those belong to T03+.

## Infrastructure unmet-demand boundary

S10-T03 converts the S09 demographic demand profile through each S10-T01
`InfrastructureType.demand_model` into canonical block/type demand items. Each item keeps
the source demographic signal/cohort, demand rate, gross demand, explicit served demand and
remaining unmet demand. Work is bounded by an explicit block×type item budget.

Existing S10-T02 facilities contribute typed available-capacity summaries only. Their
capacity is not automatically subtracted from any block because network reachability and
service distance do not exist until S10-T06/T07. Later accessibility/placement stages can
feed explicit `InfrastructureServedDemand` assignments back into the same calculator,
which then reduces unmet demand for the exact block/type pair.

This boundary prevents global capacity subtraction from masquerading as network-aware
coverage and keeps T03 deterministic and reusable during greedy T08 iterations.

## Обязательный конечный продукт

Полноценный 2D-сервис: импорт реальных данных, CRS/валидация, все стадии генерации, инфраструктура и демография, несколько сценариев, прогресс jobs, интерактивная карта, сравнение, экспорт, тесты и воспроизводимость.

Не требуется: BIM, моделирование интерьеров/квартир, детальная архитектура фасадов и обязательная 3D-сцена.
