# Database and migrations

Схема PostgreSQL/PostGIS управляется только через Alembic. Runtime-код не должен вызывать `Base.metadata.create_all()` для production/dev schema bootstrap.

## Основные команды

```bash
uv run alembic upgrade head
uv run alembic current
uv run alembic history
```

Новая миграция после изменения SQLAlchemy metadata:

```bash
uv run alembic revision --autogenerate -m "describe schema change"
```

Перед merge миграция должна пройти smoke-последовательность на чистой PostGIS БД:

```bash
uv run alembic upgrade head
uv run alembic downgrade base
uv run alembic upgrade head
```

## Persistence integration tests

`tests/integration/test_persistence_database.py` проверяет persistence contracts на реальном PostgreSQL/PostGIS, а не только SQLAlchemy metadata. Тестовый module самостоятельно выполняет `alembic upgrade head`, очищает application rows между test cases и проверяет:

- наличие текущего Alembic revision и PostGIS schema;
- FK enforcement и unique idempotency key для `Job`;
- database-level immutability успешного `GenerationRun`;
- immutability generated rows завершённого run и canonical source rows готового `DatasetVersion`;
- физическое наличие scoped B-tree и spatial GiST indexes в PostgreSQL catalog.

Для запуска нужен disposable PostGIS database из `DATABASE_URL`:

```bash
uv run pytest tests/integration/test_persistence_database.py
```

CI запускает эти проверки в общем `uv run pytest` на отдельном PostGIS service, после чего отдельно выполняет полный `upgrade -> downgrade -> upgrade` migration smoke.

## Правила

- одна логическая schema change — одна миграция;
- миграции не редактируются после того, как на них начали ссылаться последующие revisions;
- PostGIS extension и spatial indexes создаются миграциями;
- downgrade обязан быть определён для pre-1.0 разработки;
- destructive migration требует явного описания потери данных;
- CI проверяет DB-level persistence contracts и полный upgrade/downgrade/upgrade цикл на чистой БД.
