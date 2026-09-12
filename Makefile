.PHONY: install frontend-install up down logs api worker test lint typecheck check format migrate migration-smoke frontend frontend-check

install:
	uv sync --frozen --dev

frontend-install:
	cd frontend && npm ci --no-audit --no-fund

up:
	docker compose up --build

down:
	docker compose down

logs:
	docker compose logs -f

api:
	uv run uvicorn backend.app.main:app --reload --host 0.0.0.0 --port 8000

worker:
	uv run arq worker.settings.WorkerSettings

migrate:
	uv run alembic upgrade head

migration-smoke:
	uv run alembic upgrade head
	uv run alembic downgrade base
	uv run alembic upgrade head

test:
	uv run pytest

lint:
	uv run ruff check .

typecheck:
	uv run mypy backend core worker

check: lint typecheck test

format:
	uv run ruff format .
	uv run ruff check --fix .

frontend:
	cd frontend && npm run dev

frontend-check:
	cd frontend && npm run typecheck && npm run build
