# Urban Development Generator

Информационная система для процедурной генерации и моделирования развития городской застройки с учётом пространственных, инфраструктурных и демографических ограничений.

Основной сценарий — **расширение существующего города**. Дополнительный режим — генерация городской структуры с нуля на том же алгоритмическом ядре.

## Что должен уметь продукт

Система должна поддерживать полный 2D GIS-конвейер:

1. создание проекта и загрузку геоданных;
2. импорт GeoJSON, Shapefile, GeoPackage, GeoTIFF и подготовленных данных OSM/Overture;
3. нормализацию CRS и валидацию геометрий;
4. анализ пригодности территории;
5. функциональное зонирование;
6. расширение/генерацию дорожной сети;
7. выделение и разбиение кварталов;
8. процедурное размещение зданий без внутренней планировки помещений;
9. расчёт населения и спроса на инфраструктуру;
10. размещение инфраструктуры с учётом сетевой доступности;
11. проверку ограничений и расчёт метрик;
12. генерацию нескольких вариантов с фиксированным seed;
13. сравнение сценариев на интерактивной карте;
14. экспорт пространственных результатов и метрик.

3D-визуализация, поэтажные планы и внутренняя планировка зданий не входят в обязательный объём.

## Архитектура

Монорепозиторий состоит из четырёх основных частей:

- `backend/` — FastAPI, REST API, проекты, загрузки, запуски и экспорт;
- `core/` — независимое Python-ядро пространственного анализа и генерации;
- `worker/` — выполнение тяжёлых GIS-задач вне HTTP-процесса;
- `frontend/` — React + TypeScript + MapLibre GL JS.

Хранилище — PostgreSQL + PostGIS. Redis используется как брокер/хранилище состояния фоновых задач.

## Быстрый старт

Требования: Docker Compose, Python 3.12+, Node.js 22+ (если запускать frontend вне Docker).

Полный dev stack поднимается одной стандартной командой:

```bash
cp .env.example .env
docker compose up --build
```

После запуска:

- API: `http://localhost:8000`
- Swagger: `http://localhost:8000/docs`
- Frontend: `http://localhost:5173`
- PostgreSQL: `localhost:5432`

Проверка готовности backend и его зависимостей:

```bash
curl http://localhost:8000/api/v1/health/ready
```

## Разработка

Python-зависимости фиксируются в `uv.lock`; установка должна быть воспроизводимой:

```bash
make install
make check
```

Эквивалентные команды:

```bash
uv sync --frozen --dev
uv run ruff check .
uv run mypy backend core worker
uv run pytest
```

Frontend-зависимости фиксируются в `frontend/package-lock.json`:

```bash
make frontend-install
make frontend-check
```

Для запуска frontend отдельно:

```bash
cd frontend
npm ci --no-audit --no-fund
npm run dev
```

## Quality gates

Required CI проверяет Python lint/typecheck/tests, полный Alembic `upgrade → downgrade → upgrade`, frontend typecheck/build и smoke-запуск полного Docker Compose stack. Work item не считается завершённым при красном HEAD CI.

## Документация

- `docs/PROJECT_DESCRIPTION.md` — исходная постановка ВКР;
- `docs/DEVELOPMENT_PLAN.md` — техническое задание и общий roadmap;
- `docs/IMPLEMENTATION_VERSION_ROADMAP.md` — capability roadmap и sprint/release gates;
- `docs/07-planning/AI_EXECUTION_TASKS.md` — ordered one-request-sized execution backlog;
- `docs/07-planning/IMPLEMENTATION_READINESS.md` — архитектурный gate перед feature work;
- `docs/07-planning/ARCHITECTURE_DEBT_AUDIT.md` — текущий аудит архитектурного долга;
- `docs/02-architecture/PIPELINE_MODEL.md` — канонический Stage/pipeline execution contract;
- `docs/DEMO_REFERENCE.md` — ориентир по demo/reference-сценарию; для реальных примеров используется Рязань без привязки архитектуры к конкретному городу;
- `docs/METRIC_REGISTRY.md` — canonical RawMetricId/MetricDefinition registry и runtime metadata;
- `docs/ARCHITECTURE.md` — стабильные системные границы;
- `docs/DATA_MODEL.md` — модель данных;
- `docs/API.md` — контракт API по мере реализации.

## Статус

**Sprint S10 / M1 Infrastructure Complete завершён; S11 реализован through UG-AI-050**: validation detail/hard-soft semantics остаются в одном ConstraintEngine, а existing RawMetricId получил единственный typed MetricDefinition registry со scope/direction/source/version metadata без второго metric-id vocabulary.

Обязательный **M0 Architecture Stabilization Gate завершён**. Stabilization evidence commit:
`39eaa1688e06669b0e01e999304710873fd9cf0e`; required Python, frontend и Docker Compose CI на нём зелёные.

Результат M0:

- один канонический `Stage/StageResult` contract и coarse stage catalog;
- S04–S09 доступны через typed Stage adapters;
- deterministic in-memory pipeline spine проходит через demography;
- fixed/generated, CRS, determinism, bounds, persistence/job/artifact invariants защищены regression gates;
- `RunStageResult` отдельно хранит input/config provenance и canonical output fingerprint;
- Critical/High architecture debt: **0 open**;
- оставшиеся Medium-пункты назначены конкретным будущим S11/S13 задачам;
- future roadmap S10–S15/R1–R10 проверен против стабилизированных contracts.

Readiness остаётся **ACCEPTED**; M1 закрыт, и следующий ordered execution item —
**`UG-AI-051 / S11-T05`** из `docs/07-planning/AI_EXECUTION_TASKS.md`.

Историческая арифметика work items не является оценкой end-to-end готовности продукта. Текущий
прогресс определяется milestone gates и ordered UG-AI backlog.

Актуальный порядок выполнения определяют `IMPLEMENTATION_READINESS.md`, `MILESTONES.md`,
`AI_EXECUTION_TASKS.md` и capability roadmap.
