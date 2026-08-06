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
#   make test         # Python test suite (no coverage — fast local loop)
#   make test-cov     # Python test suite with coverage (CI parity)
#   make test-fast    # Python tests skipping slow/integration markers (no coverage)
#   make test-client  # Vitest client suite
#   make lint         # ruff + biome
#   make format       # ruff format + biome format
#   make typecheck    # TypeScript + mypy + ruff (run in parallel)
#   make build        # production build (Electron renderer + main)
#   make bench        # all bench/bench_*.py --json (CI perf ratchet input)
#
# Notes:
#   - Targets that delegate to npm run from `voice_typer/client/`; the
#     recipe cd's there and back so the contributor's CWD is unchanged.
#   - `make test` mirrors the CI invocation but passes `--no-cov` so the
#     local loop is ~15-25% faster (C-TEST-4). Use `make test-cov` for
#     explicit coverage runs (matches CI's --cov --cov-fail-under=65).
#   - `make typecheck` runs TypeScript typecheck, mypy, and ruff in
#     PARALLEL (background `&` + `wait`) — they touch disjoint file sets
#     so the wall-clock time is max(tsc, mypy, ruff) instead of the sum.
#     The mypy scope matches the pre-commit hook (`voice_typer/server/`).
#   - `make bench` runs every `bench/bench_*.py --json` script and
#     concatenates the output into `bench-current.json` for the CI perf
#     ratchet comparison against `bench/bench-baseline.json`. The bench
#     scripts are also runnable directly (`python bench/bench_startup.py`).

.DEFAULT_GOAL := help

.PHONY: help setup test test-cov test-fast test-client lint format build build-tauri typecheck bench bench-quick clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

setup: ## Install all dependencies (Python + Node)
	uv venv && uv pip install -e ".[test,dev]"
	cd voice_typer/client && npm ci

test: ## Run Python tests (parallel, NO coverage for fast local loop — C-TEST-4)
	python -m pytest tests/ -n auto --dist=loadgroup -q --timeout=60 --no-cov

test-cov: ## Run Python tests WITH coverage (CI parity — slow, but enforces the 65% gate)
	python -m pytest tests/ -n auto --dist=loadgroup -q --timeout=60 --cov=voice_typer --cov-report=term-missing --cov-fail-under=65

test-fast: ## Run Python tests, skipping slow/integration markers (no coverage)
	python -m pytest tests/ -n auto --dist=loadgroup --no-cov -q --timeout=30 -m "not slow and not integration"

test-client: ## Run client tests (no coverage for speed)
	cd voice_typer/client && npx vitest run --no-coverage

lint: ## Run linters (ruff + biome, with auto-fix)
	ruff check voice_typer/ tests/ --fix
	cd voice_typer/client && npm run lint:fix

format: ## Run formatters (ruff format + biome format)
	ruff format voice_typer/ tests/
	cd voice_typer/client && npm run format

typecheck: ## Run TypeScript + mypy + ruff IN PARALLEL (disjoint file sets — wall-clock = max(tsc, mypy, ruff))
	@cd voice_typer/client && npm run typecheck & \
	python -m mypy voice_typer/server/ & \
	ruff check voice_typer/ tests/ & \
	wait

build: ## Build the app (Electron renderer + main)
	cd voice_typer/client && npm run build

build-tauri: ## Build the Tauri v2 host (sidecar + prewarm + native + cargo tauri build)
	bash scripts/build/build_tauri_all.sh

bench: ## Run all bench/bench_*.py --json (concatenated into bench-current.json for CI ratchet)
	@echo "Running all bench scripts with --json output..."
	@python -c "import subprocess, json, sys, os, datetime; scripts = ['bench_startup','bench_transcription','bench_audio_filter_chain','bench_streaming','bench_vad','bench_ipc','bench_memory']; out = {'version': 1, 'generated_at': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z'), 'results': {}}; [out['results'].__setitem__(n, (lambda p, r: json.loads(r.stdout) if (os.path.isfile(p) and r.returncode == 0) else {'skipped': True, 'error': (('missing' if not os.path.isfile(p) else (r.stderr.strip().splitlines()[-1] if r.stderr.strip() else 'failed')))})(os.path.join('bench', n + '.py'), subprocess.run([sys.executable, os.path.join('bench', n + '.py'), '--json'], capture_output=True, text=True))) for n in scripts]; open('bench-current.json', 'w').write(json.dumps(out, indent=2) + '\n'); print('Wrote bench-current.json -> compare against bench/bench-baseline.json (see .github/workflows/perf.yml)')"

bench-quick: ## Run a single fast bench iteration (startup only — useful for smoke tests)
	python bench/bench_startup.py --runs 3

clean: ## Remove build artifacts and caches
	rm -rf voice_typer/client/dist voice_typer/client/out
	rm -rf voice_typer/dist
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
