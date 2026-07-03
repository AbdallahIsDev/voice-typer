# Manual Diagnostic Scripts

CQ-016: These scripts were moved from `scripts/diagnostics/` to
`tests/manual/` to clarify their role as manual diagnostic tools
that live alongside the test suite but are NOT run by pytest.

They are **ad-hoc interactive scripts**, not automated tests. They
are excluded from the pytest suite because they require real hardware
(GPU, microphone, display) or print diagnostic output instead of
making assertions.

| Script | Purpose |
|---|---|
| `diagnose_f2.py` | Traces the full F2 → recording → transcription path with mocked hardware |
| `runtime_proof.py` | End-to-end runtime verification of transcription fallback and stuck-state recovery |
| `cublas_fallback.py` | Proves the cuBLAS DLL failure path is handled correctly |
| `runtime_test_runner.py` | Interactive test runner for runtime verification |

Run individually:

```bash
python tests/manual/diagnose_f2.py
python tests/manual/runtime_proof.py
python tests/manual/cublas_fallback.py
```

All unique test coverage from these scripts is already captured in the
automated test suite under `tests/` — specifically in
`test_round8_e2e.py`, `test_round9_e2e.py`, `test_round13_ipc_regression.py`,
and `test_new_dead_002_scripts.py` (which verifies these scripts parse
and import correctly). The diagnostic scripts themselves are kept for
manual troubleshooting when a developer needs to reproduce a specific
hardware-dependent failure path interactively.

## Why they were moved

CQ-016: The original `scripts/diagnostics/` location was ambiguous —
"scripts" implies build/install tooling, not diagnostic tests. Moving
them to `tests/manual/` makes it clear that:
1. They are test-adjacent (live under `tests/`).
2. They are manual (in the `manual/` subdirectory, not run by default).
3. Developers looking for diagnostic tools will find them in the
   natural location (`tests/`).

## TASK-013: `@pytest.mark.slow` wrappers

Each script now exposes a stable `run()` callable (renamed/aliased from
its historical `main` / `run_runtime_proof` function) so it can be
wrapped as a proper pytest test in [`tests/test_manual_slow.py`](../test_manual_slow.py).
The wrappers are marked with `@pytest.mark.slow` and **skipped by
default** — they only run when `--slow` is passed.

| Script | Slow-test wrapper | What it asserts |
|---|---|---|
| `diagnose_f2.py` | `test_diagnose_f2_deprecated_contract` | `run()` returns `2` and prints a `DEPRECATED` notice pointing at the modern replacement tests |
| `cublas_fallback.py` | `test_cublas_fallback_deprecated_contract` | `run()` returns `2` and prints a `DEPRECATED` notice pointing at `TestFallbackChain` |
| `runtime_proof.py` | `test_runtime_proof_smoke` | Script exits with code `0` or `1` (not `2`) and reaches its `RUNTIME PROOF RESULTS` summary; requires real `numpy` + `faster_whisper` (skipped otherwise) |
| `runtime_test_runner.py` | `test_runtime_test_runner_parses` | Script parses without syntax errors; on Windows, also imports cleanly. Never invokes `main()` (it would launch the real app) |

### Running the slow tests

```bash
# Skipped by default — same as the regular suite:
pytest tests/test_manual_slow.py -v

# Opt in to the slow tests:
pytest tests/test_manual_slow.py -v --slow

# Opt in to ALL slow tests across the suite:
pytest --slow
```

### Why they are skipped by default

`tests/conftest.py` registers the `slow` marker and adds a
`pytest_collection_modifyitems` hook that skips any test marked `slow`
unless `--slow` was passed. This keeps the regular pytest suite fast
and lets the slow wrappers live next to the rest of the tests instead
of being exiled to a separate CI workflow.

### CI integration

The `.github/workflows/build.yml` workflow has a `slow-tests` job
(`continue-on-error: true`, `main`-branch only) that runs
`pytest --slow tests/test_manual_slow.py` on every push to `main`.
Failures there do NOT block the build — the job exists to surface
regressions in the manual scripts themselves, not to gate releases.
