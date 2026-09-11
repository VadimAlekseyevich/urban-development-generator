.PHONY: up down logs api worker test lint format migrate frontend

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

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run mypy backend core worker

format:
	uv run ruff format .
	uv run ruff check --fix .

frontend:
	cd frontend && npm run dev
