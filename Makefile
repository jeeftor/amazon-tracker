.DEFAULT_GOAL := help
.PHONY: help setup check test up down logs

help:
	@echo "setup  Install development dependencies with uv"
	@echo "check  Check formatting, lint, types, and tests"
	@echo "test   Run unit and browser integration tests"
	@echo "up     Build and start the local login panel"
	@echo "down   Stop containers, preserving your browser profile"
	@echo "logs   Follow service logs"

setup:
	uv sync --locked

check:
	uv run ruff format --check .
	uv run ruff check .
	uv run mypy
	uv run pytest

test:
	uv run pytest

up:
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f --tail=100
