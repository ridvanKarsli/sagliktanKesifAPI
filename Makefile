.PHONY: help backend-install backend-test backend-lint backend-run worker frontend-install frontend-dev frontend-build up down logs migrate

help:
	@grep -E '^[a-z-]+:' Makefile | sed 's/:.*//' | tr '\n' ' '; echo

backend-install:
	pip install -e ".[dev]"

backend-test:
	pytest

backend-lint:
	ruff check src tests && mypy

backend-run:
	kesif migrate && kesif api --reload

worker:
	kesif worker

frontend-install:
	cd ../frontend && npm ci

frontend-dev:
	cd ../frontend && npm run dev

frontend-build:
	cd ../frontend && npm run build

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

migrate:
	docker compose run --rm migrate
