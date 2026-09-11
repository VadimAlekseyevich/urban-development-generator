# План разработки Urban Development Generator

## 1. Цель

Создать полноценный 2D GIS-сервис процедурной генерации и моделирования развития городской застройки с учётом пространственных, инфраструктурных и демографических ограничений.

Основной режим — **расширение существующего города**. Дополнительный режим — **генерация структуры с нуля** на том же алгоритмическом ядре.

Последовательность генерации:

1. анализ территории и ограничений;
2. функциональное зонирование;
3. построение/расширение дорожной сети;
4. формирование кварталов;
5. размещение застройки;
6. расчёт населения и размещение инфраструктуры;
7. проверка ограничений и оценка результата.

## 2. Что считать готовым продуктом

К защите должна существовать не урезанная демонстрация, а первая полноценная версия продукта, которая на 1–2 реальных территориях умеет:

- создавать проекты;
- загружать границы, дороги, здания, инфраструктуру, ограничения, DEM и демографические данные;
- поддерживать GeoJSON, GeoPackage, Shapefile и GeoTIFF;
- импортировать или подготавливать OSM, а при возможности Overture;
- проверять и исправлять геометрии;
- приводить данные к рабочей метрической CRS;
- хранить исходные и нормализованные данные в PostGIS;
- строить карту пригодности;
- выполнять функциональное зонирование;
- расширять существующую дорожную сеть и генерировать новую;
- выделять кварталы и делить слишком крупные;
- размещать параметрические здания с этажностью, площадью и назначением;
- рассчитывать население;
- размещать образование, медицину, торговлю и рекреацию;
- считать доступность по дорожной сети;
- проверять нарушения ограничений;
- считать набор метрик и общий score;
- генерировать несколько сценариев с разными seed/параметрами;
- показывать прогресс фоновых jobs;
- сравнивать сценарии в UI;
- экспортировать GeoJSON/GeoPackage/CSV;
- воспроизводить запуск по seed и конфигурации.

Не входит в обязательный объём: 3D, BIM, фасады, поэтажная/внутренняя планировка помещений, транспортная микросимуляция и ML-генерация.

## 3. Архитектура

### Backend

FastAPI отвечает за проекты, загрузки, datasets, параметры, запуски, статусы, метрики и экспорт.

### Algorithmic Core

Чистый Python-пакет без зависимости от HTTP и UI. Модули:

- `constraints`;
- `io`;
- `suitability`;
- `zoning`;
- `roads`;
- `blocks`;
- `buildings`;
- `population`;
- `infrastructure`;
- `validation`;
- `metrics`;
- `pipeline`.

### Worker

Тяжёлые GIS-операции выполняются отдельным процессом через Redis + ARQ. HTTP-запрос только создаёт job и возвращает ID запуска.

### Database

PostgreSQL + PostGIS. Геометрии индексируются GiST. Для каждого проекта хранится рабочая метрическая CRS. Все площади и расстояния в алгоритмах считаются в ней.

### Frontend

React + TypeScript + MapLibre GL JS. Карта — центральный элемент интерфейса.

## 4. Целевая структура репозитория

```text
urban-development-generator/
├── backend/
│   └── app/
│       ├── api/
│       ├── core/
│       ├── db/
│       ├── models/
│       ├── schemas/
│       └── services/
├── core/
│   └── urban_generator/
│       ├── constraints/
│       ├── io/
│       ├── suitability/
│       ├── zoning/
│       ├── roads/
│       ├── blocks/
│       ├── buildings/
│       ├── population/
│       ├── infrastructure/
│       ├── validation/
│       ├── metrics/
│       └── pipeline/
├── worker/
├── frontend/
├── database/
├── tests/
├── docs/
├── docker/
├── data/
└── storage/
```

## 5. Этап 0 — инженерный baseline

### Цель

Получить воспроизводимую среду, в которой frontend, API, worker, PostGIS и Redis запускаются стандартной командой.

### Задачи

- `pyproject.toml`, uv, Ruff, pytest, mypy;
- React/Vite/TypeScript;
- Docker Compose;
- PostGIS;
- Redis;
- Alembic;
- CI;
- `/health/live` и `/health/ready`;
- README и `.env.example`.

### Критерий готовности

`docker compose up --build` поднимает сервисы, миграция выполняется, API отвечает, frontend открывает карту, CI запускает Python и frontend проверки.

## 6. Этап 1 — модель данных и проекты

### Сущности

- `Project`;
- `Dataset`;
- `SpatialFeature`;
- `RasterDataset`;
- `Constraint`;
- `GenerationRun`;
- `RunStageResult`;
- `GeneratedRoad`;
- `GeneratedBlock`;
- `GeneratedBuilding`;
- `GeneratedInfrastructure`;
- `Artifact`.

### Требования

Каждый запуск хранит:

- `seed`;
- полную JSON-конфигурацию;
- версии datasets;
- код/commit SHA;
- CRS;
- timestamps;
- статус;
- метрики;
- ошибки/diagnostics.

## 7. Этап 2 — импорт и нормализация данных

### Вектор

GeoJSON, GPKG, Shapefile через GeoPandas/Pyogrio.

Проверки:

- наличие CRS;
- пустые/битые геометрии;
- `make_valid`;
- допустимый geometry type;
- bbox;
- количество объектов;
- reprojection в working CRS.

### Raster

GeoTIFF через Rasterio:

- CRS;
- transform;
- nodata;
- размер пикселя;
- clip по границе проекта;
- reprojection/resampling при необходимости.

### OSM

Первый полноценный вариант: PBF/подготовленный extract → дороги, здания, POI, landuse, water. Импорт должен быть скриптом и затем стать API job.

### Overture

Добавить после стабильного OSM importer. Использовать как альтернативный/дополнительный источник зданий и POI.

### Безопасность uploads

- лимит размера;
- whitelist расширений;
- нормализация имён;
- запрет path traversal;
- временное хранилище;
- распаковка Shapefile только в sandbox-каталог;
- очистка временных файлов;
- проверка MIME/содержимого где возможно.

## 8. Этап 3 — карта пригодности

### Входные факторы

- уклон;
- вода;
- protected/forbidden areas;
- существующая застройка;
- близость к дорогам;
- landuse;
- пользовательские ограничения.

### Выход

Raster/mesh/полигональная модель suitability со score 0..1 и маской запрещённых зон.

### Проверки

- запрещённые участки всегда дают 0;
- одинаковые входы + seed дают одинаковый результат;
- все distance/slope вычисления выполняются в корректной CRS.

## 9. Этап 4 — функциональное зонирование

Поддержать минимум:

- residential;
- mixed/commercial;
- public/infrastructure;
- recreation/green.

Методы: seed points + Voronoi, рост областей, suitability-aware refinement.

Параметры: целевые доли зон, минимальная площадь, соседство, доступ к магистралям, существующие зоны.

## 10. Этап 5 — дорожная сеть

### Представление

NetworkX graph: node = intersection/end point, edge = road segment.

### Функции

- topology cleanup;
- snapping;
- connected components;
- Dijkstra/A*;
- построение новых connections;
- правила роста;
- MST как вспомогательный способ получить базовую связность;
- проверка тупиков и избыточных петель;
- classification local/collector/arterial.

### Метрики

- connected components;
- average degree;
- intersection density;
- road length density;
- circuity;
- доля доступной территории.

## 11. Этап 6 — кварталы

Дорожная сеть polygonize → blocks.

Проверки:

- минимальная/максимальная площадь;
- compactness;
- frontage/access;
- intersection с ограничениями;
- holes;
- очень вытянутые полигоны.

Крупные кварталы разделяются линиями, ориентированными по principal axis/дорогам.

## 12. Этап 7 — здания

Здание — параметрическое пятно плюс атрибуты:

- use;
- floors;
- footprint area;
- gross floor area;
- estimated residents/jobs;
- run ID;
- block ID.

Генератор обязан учитывать:

- setbacks от дорог;
- gap между зданиями;
- допустимые зоны;
- max coverage;
- target FAR/density;
- slope;
- ограничения.

Для ВКР не требуется генерировать комнаты/квартиры.

## 13. Этап 8 — население

Расчёт строится на usable floor area и коэффициентах:

- residential GFA;
- vacancy/occupancy coefficient;
- m²/person;
- при наличии raster population — калибровка по исходному распределению.

Результат агрегируется по buildings, blocks и zones.

## 14. Этап 9 — инфраструктура

Минимальные категории:

- education;
- healthcare;
- retail;
- recreation.

Для каждой категории задаются:

- capacity;
- demand per population;
- max network distance/time;
- допустимые zones;
- candidate locations.

Алгоритм размещения:

1. вычислить неудовлетворённый спрос;
2. построить candidate points;
3. посчитать shortest-path accessibility;
4. выбрать объект с максимальным приростом coverage;
5. повторять до достижения порога/лимита.

Позже можно заменить greedy на facility-location optimization.

## 15. Этап 10 — единая система ограничений

Ограничение имеет:

- type;
- severity: hard/soft;
- geometry/raster predicate;
- threshold;
- message;
- stage applicability.

Примеры:

- нельзя строить в воде;
- нельзя пересекать protected zone;
- slope <= X;
- setback от дороги >= Y;
- building coverage <= Z;
- объект инфраструктуры должен находиться в доступной зоне.

Важно: ограничения не размазываются по модулям, а вызываются через общий validation/constraint API.

## 16. Этап 11 — метрики и score

Отдельно считать:

- developed area;
- building coverage ratio;
- FAR;
- population density;
- road density;
- graph connectivity;
- average circuity;
- infrastructure coverage;
- mean/percentile network distance;
- green/recreation coverage;
- количество hard violations;
- weighted soft penalty.

Интегральный score не заменяет отдельные показатели. UI показывает и итоговый score, и исходные метрики.

## 17. Этап 12 — сценарии и воспроизводимость

Каждый scenario/run:

- immutable после завершения;
- имеет seed;
- хранит config;
- связан с dataset versions;
- сохраняет промежуточные стадии;
- может быть повторён;
- сравнивается с другими runs одного проекта.

Нужно генерировать 3–10 вариантов за серию и уметь сортировать их по выбранным метрикам.

## 18. Этап 13 — frontend

### Основной layout

- карта;
- дерево слоёв;
- панель проекта/datasets;
- параметры генерации;
- очередь/прогресс jobs;
- таблица метрик;
- compare mode.

### Карта

Слои:

- boundary;
- existing roads/buildings;
- constraints;
- suitability;
- zoning;
- generated roads;
- blocks;
- buildings;
- infrastructure;
- violations.

Нужны opacity, visibility, legend, click-inspector и fit-to-layer.

### Compare

Минимум side-by-side metrics + переключение вариантов на карте. Желательно split map или swipe позже.

## 19. Этап 14 — экспорт

Поддержать:

- GeoJSON;
- GeoPackage;
- CSV метрик;
- JSON конфигурации запуска.

Экспорт должен содержать run ID, seed, CRS и metadata версии.

## 20. Тестирование

### Unit

- geometry utilities;
- constraints;
- suitability weights;
- graph helpers;
- block split;
- building placement predicates;
- population formulas;
- metrics.

### Property/geospatial

- здания не выходят за допустимый block;
- hard constraints не нарушаются;
- invalid geometry после normalization отсутствует;
- одинаковый seed воспроизводим.

### Integration

- upload → normalize → DB;
- create project → dataset → run;
- worker updates run status;
- migration на пустой БД.

### E2E

Создать проект в UI, загрузить демо-территорию, запустить generation и увидеть слои/metrics.

## 21. Производительность

Сразу предусмотреть:

- GiST indexes;
- bbox prefilter;
- spatial joins вместо Python loops;
- prepared geometries/STRtree;
- vectorized GeoPandas/Shapely;
- raster windowing;
- stage caching;
- сохранение промежуточных артефактов;
- ограниченную concurrency workers;
- profiling крупных стадий.

Целевой размер эксперимента для ВКР — район/небольшой город, но архитектура не должна жёстко зависеть от одного размера территории.

## 22. Логирование и наблюдаемость

Для каждого job:

- structured log;
- run ID/project ID;
- stage;
- progress %;
- duration;
- warnings;
- exception traceback;
- input/output counts.

UI получает state: queued/running/succeeded/failed/cancelled.

## 23. Итерации

### Sprint 0 — baseline

Docker, FastAPI, frontend, PostGIS, Redis, worker, CI, migration, health.

### Sprint 1 — Project + Dataset

CRUD проектов, uploads, metadata, storage, dataset API.

### Sprint 2 — Geodata normalization

GeoJSON/GPKG/Shapefile/GeoTIFF, CRS, make_valid, PostGIS import.

### Sprint 3 — OSM + demo territory

OSM import, road/building/POI layers, первый реальный проект.

### Sprint 4 — Suitability + constraints

DEM, slope, forbidden masks, suitability layer.

### Sprint 5 — Zoning

Полноценные функциональные зоны и параметры.

### Sprint 6 — Roads

Graph normalization, expansion/generation, routing metrics.

### Sprint 7 — Blocks

Polygonize, validation, subdivision.

### Sprint 8 — Buildings

Footprints, floors, density, setbacks.

### Sprint 9 — Population

Population model + calibration.

### Sprint 10 — Infrastructure

Demand, accessibility, greedy facility placement.

### Sprint 11 — Metrics + scenarios

Validation report, score, multi-seed runs, compare API.

### Sprint 12 — UI complete

Layer controls, project workflow, progress, compare, exports.

### Sprint 13 — experiments and hardening

Tests, profiling, 1–2 real territories, screenshots, thesis experiments.

## 24. Критический путь

```text
Project/Upload
  -> normalization/CRS
  -> suitability/constraints
  -> roads
  -> blocks
  -> buildings
  -> population
  -> infrastructure
  -> metrics
  -> scenarios/compare
  -> UI/export
```

Zoning можно развивать параллельно suitability/roads. Frontend можно наращивать после появления каждого API слоя, но сравнение сценариев зависит от полного pipeline.

## 25. Экспериментальная часть ВКР

Выбрать 1–2 территории:

1. участок существующего города для expansion mode;
2. свободная/слабо застроенная территория для generation-from-scratch.

Для каждой территории:

- подготовить одинаковый набор входов;
- запустить 10–30 seed;
- выбрать 3–5 representative scenarios;
- сравнить метрики;
- измерить runtime по стадиям;
- показать нарушения;
- выполнить sensitivity analysis для 2–4 параметров.

## 26. Метрики экспериментов

- доля освоенной пригодной территории;
- GFA/FAR;
- population density;
- road length/km²;
- connected components;
- intersection density;
- circuity;
- coverage инфраструктуры;
- median/p90 network distance;
- violations count;
- runtime;
- reproducibility by seed.

## 27. Структура пояснительной записки

1. Введение, актуальность, цель и задачи.
2. Анализ предметной области и существующих подходов.
3. Формализация ограничений и метрик.
4. Архитектура информационной системы.
5. Модель данных и обработка геоданных.
6. Алгоритмы suitability/zoning/roads/blocks/buildings.
7. Демография и инфраструктура.
8. Backend, worker и frontend.
9. Эксперименты и оценка результатов.
10. Тестирование, ограничения и направления развития.
11. Заключение.

## 28. Текущее состояние репозитория

На старте уже должны существовать:

- Docker Compose;
- FastAPI health endpoints;
- PostgreSQL/PostGIS;
- Redis + ARQ worker;
- React + MapLibre frontend;
- SQLAlchemy models `Project`, `Dataset`, `GenerationRun`;
- Alembic initial migration;
- CRUD проекта;
- базовые pipeline contracts;
- GeoData loader;
- CI;
- unit/integration smoke tests.

Следующая практическая задача — **Sprint 1: полноценный Dataset/upload pipeline**, а не дальнейшее раздувание scaffold.
