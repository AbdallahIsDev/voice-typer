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
