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
- `docs/IMPLEMENTATION_VERSION_ROADMAP.md` — атомарные work items;
- `docs/DEMO_REFERENCE.md` — ориентир по demo/reference-сценарию; для реальных примеров используется Рязань без привязки архитектуры к конкретному городу;
- `docs/ARCHITECTURE.md` — архитектурные решения;
- `docs/DATA_MODEL.md` — модель данных;
- `docs/API.md` — контракт API по мере реализации.

## Статус

Текущая точка реализации в `main` — **Sprint S08 завершён полностью, следующий work item — `S09-T01 — DemographicScenario schema`**.

- полностью реализованы sprint `S00`–`S08`;
- `S08 Buildings и archetypes` закрыт: `T01`–`T14` реализованы и прошли required CI;
- следующий этап критического пути — `S09 Demography`;
- по количеству implementation work items выполнено **111 из 210 (≈52,9%)**, осталось **99 из 210 (≈47,1%)**;
- после `S09` критический путь продолжается через `S10`–`S15`, затем выполняются release-hardening acceptance gates `R1`–`R10`.

Процент выше — только арифметика по атомарным work items из roadmap, без попытки приравнять их по трудоёмкости.

Актуальный состав и границы задач определяет `docs/IMPLEMENTATION_VERSION_ROADMAP.md`.
