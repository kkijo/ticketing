.PHONY: up down migrate dev worker test lint

up:
	docker compose up -d postgres redis

down:
	docker compose down

migrate:
	alembic upgrade head

dev:
	uvicorn app.main:app --reload

worker:
	arq app.workers.settings.WorkerSettings

test: migrate
	pytest -q

lint:
	ruff check app tests && mypy app