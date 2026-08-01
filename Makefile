# Voice Typer — top-level convenience Makefile.
#
# This is a thin wrapper around the existing uv / pytest / npm commands
# documented in CONTRIBUTING.md and AGENTS.md. It exists so a new
# contributor can run `make help` and discover the common dev-loop
# targets without reading the docs first. The Makefile does NOT
# introduce new tooling — every recipe shells out to commands that are
# already canonical for the project.
#
# Usage:
#   make help         # list available targets
#   make setup        # one-time install (Python + Node deps)
#   make test         # Python test suite
#   make test-client  # Vitest client suite
#   make lint         # ruff + biome
#   make build        # production build (Electron renderer + main)
#
# Notes:
#   - Targets that delegate to npm run from `voice_typer/client/`; the
#     recipe cd's there and back so the contributor's CWD is unchanged.
#   - `make test` mirrors the CI invocation (`pytest tests/ -q
#     --timeout=60`). To skip the coverage gate locally, pass
#     `--no-cov` via the PYTEST_ADDOPTS env var or run pytest directly
#     with `-o addopts=""`.

.DEFAULT_GOAL := help

.PHONY: help setup test test-client lint build typecheck clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Install all dependencies (Python + Node)
	uv venv && uv pip install -e ".[test,dev]"
	cd voice_typer/client && npm ci

test: ## Run Python tests
	python -m pytest tests/ -q --timeout=60

test-client: ## Run client tests
	cd voice_typer/client && npm test

lint: ## Run linters (ruff + biome, with auto-fix)
	ruff check voice_typer/ tests/ --fix
	cd voice_typer/client && npm run lint:fix

typecheck: ## Run TypeScript + Python type checks
	cd voice_typer/client && npm run typecheck
	ruff check voice_typer/ tests/

build: ## Build the app (Electron renderer + main)
	cd voice_typer/client && npm run build

clean: ## Remove build artifacts and caches
	rm -rf voice_typer/client/dist voice_typer/client/out
	rm -rf voice_typer/dist
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
