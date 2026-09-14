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

Адаптер преобразует NetworkX routing results обратно в domain-типы. Построение graph из canonical roads, OSM semantics и semantic noding не входят в S06-T01 и реализуются последующими work items. Начиная с S06-T03 `snap()` делегирует reusable `SpatialSnapIndex` и не выполняет линейный scan всех graph nodes.

## Canonical OSM road semantics

S06-T02 нормализует source-specific OSM tags до устойчивой canonical road schema до graph construction. Помимо уже существующих `road_class` и boolean `one_way`, `source_roads` хранит `bridge`, `tunnel`, signed `layer` и `one_way_direction` со значениями `both`, `forward` или `reverse` относительно порядка координат geometry.

Явный OSM `oneway` имеет приоритет. `-1` сохраняется как `reverse`, а при отсутствии тега стандартные implied one-way случаи `motorway`/`motorway_link` и `roundabout`/`circular` нормализуются как `forward`. Динамические значения вроде `reversible` не превращаются в фиксированное направление. `bridge`/`tunnel` трактуют любой непустой value кроме явных `no/false/0` как наличие соответствующей структуры; malformed/out-of-range `layer` безопасно становится `0`. Сырые tags остаются в provenance `attributes_json`.

Эти поля являются входом для S06-T04 semantic noding: решение о создании graph intersection не повторно интерпретирует OSM tags. S06-T02 не выполняет noding и не строит graph.

## Spatial snapping boundary

S06-T03 вводит `core.urban_generator.roads.SpatialSnapIndex` как reusable point-index для road endpoints, intersection candidates и уже построенных graph nodes. Индекс принимает только `NetworkPoint` в явно указанной metric `working_srid`, строит Shapely `STRtree` один раз и для каждого запроса использует bounded-by-tolerance `dwithin` candidate search вместо полного point-by-point scan.

Размер индекса ограничен `max_targets` (по умолчанию 500 000), а tolerance задаётся в метрах на каждый query. Все кандидаты дополнительно проверяются точным Cartesian distance; результат сортируется по `(distance_m, target_id)`, поэтому равные расстояния разрешаются детерминированно и не зависят от traversal order `STRtree`. `snap_target()` исключает сам target и предназначен для endpoint/intersection coalescing.

S06-T03 не решает, является ли геометрическое пересечение реальным road junction: bridge/tunnel/layer semantics не интерпретируются внутри snapping index. Это ответственность S06-T04 semantic noding. Построение directed graph и применение `one_way_direction` остаются S06-T05.

## Semantic road noding boundary

S06-T04 вводит `core.urban_generator.roads.SemanticNoder`. Вход `SemanticRoad` уже содержит canonical `layer`, `bridge` и `tunnel`; raw OSM tags внутри noder не читаются. Геометрии должны быть 2D `LineString`/`MultiLineString` в явной metric working CRS.

Кандидатные пересечения находятся через `STRtree`, а не полным N×M сравнением. Размер входа ограничен `max_road_parts` (по умолчанию 500 000), число реально пересекающихся candidate pairs — `max_candidate_pairs` (по умолчанию 2 000 000). Для interior crossing junction создаётся только при совпадении `(layer, bridge, tunnel)`. Exact shared endpoint считается явной топологической связью и сохраняется даже при переходе structure/layer, чтобы bridge/tunnel segment не отрывался от approach geometry. T-junction с несовместимой grade semantics не соединяется.

Допустимые junction points разрезают только те line parts, где точка лежит в интерьере. Порядок и направление resulting parts сохраняются относительно исходной geometry, чтобы downstream `one_way_direction` оставался корректным. Частичные линейные overlaps не создают бесконечное число nodes: noding использует только границы overlap и сообщает `overlap_pair_count`; duplicate/tiny-edge cleanup остаётся S06-T06.

Результат S06-T04 содержит split `NodedRoad` geometries, `SemanticJunction` и bounded-work diagnostics, но не создаёт NetworkX/domain graph nodes и edges. Это строго S06-T05 Graph build; именно там применяются source/fixed flags, length metadata и traversal direction.

## Обязательный конечный продукт

Полноценный 2D-сервис: импорт реальных данных, CRS/валидация, все стадии генерации, инфраструктура и демография, несколько сценариев, прогресс jobs, интерактивная карта, сравнение, экспорт, тесты и воспроизводимость.

Не требуется: BIM, моделирование интерьеров/квартир, детальная архитектура фасадов и обязательная 3D-сцена.
