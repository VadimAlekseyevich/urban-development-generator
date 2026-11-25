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
| Конфигурация | v5: полный типизированный набор 12 Stage; API проверяет v5 до записи задания; UI ещё требует JSON |
| Worker orchestration | Production runtime и все 12 Stage в main (#216); интеграционный тест на подготовленных записях PostGIS |
| Карта, сравнение и экспорт | API/UI работают над готовыми persisted outputs; comparison fixture синтетическая |

Основная ревизия с полным runtime: `cf7f78f`; точная PR-ревизия #216
`2a28eb3` прошла все пять [CI-проверок](https://github.com/VadimAlekseyevich/urban-development-generator/actions/runs/45609533065).
Это не заменяет проверки exact merge head и сквозной пользовательской приёмки.

## Порядок доведения до рабочего состояния

1. **P0 — реальное выполнение.** Полный runtime интегрирован в #216.
   Проверять v5 до постановки задания в очередь; подтвердить кодовую ревизию,
   пригодность реальных входов и отсутствие частичных записей при ошибках.
2. **P0 — UX.** Удобная настройка параметров, несколько ready datasets на запуск,
   прогресс и понятные ошибки; отображение именно нового результата на карте.
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
