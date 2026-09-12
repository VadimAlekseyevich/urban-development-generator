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

## Правила

- одна логическая schema change — одна миграция;
- миграции не редактируются после того, как на них начали ссылаться последующие revisions;
- PostGIS extension и spatial indexes создаются миграциями;
- downgrade обязан быть определён для pre-1.0 разработки;
- destructive migration требует явного описания потери данных;
- CI проверяет полный upgrade/downgrade/upgrade цикл на чистой БД.
