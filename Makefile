.PHONY: check test lint typecheck dev smoke

check: lint typecheck test

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

typecheck:
	uv run pyright

dev:
	uv run uvicorn lang_ai_agent.api.app:create_default_app --factory --reload

smoke:
	uv run python scripts/smoke.py
