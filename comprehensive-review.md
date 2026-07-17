# Voice Typer — Open Findings (Comprehensive Review)

Only unresolved findings are listed below. Completed/verified items are
marked **Fixed** with a brief note about what was changed.

## Findings

### CR-25 — `TestRateLimiter` docstring describes stale per-connection behavior (NEW)
- **Category**: Documentation
- **Severity**: Low
- **Status**: Open
- **Description**: The `TestRateLimiter` class docstring in
  `tests/test_server.py:1334` states: "Each connection gets its own
  limiter instance." The rate limiter was changed so it is now shared
  per `IPCServer` instance (not per connection) — its budget persists
  across reconnects. The tests themselves are unaffected — they
  construct `_RateLimiter` directly and test its internal
  sliding-window behavior — but the docstring is now inaccurate and
  could mislead future developers.
- **Recommended fix**: Update the docstring to: "`_RateLimiter` is a
  sliding-window limiter shared across all connections to a given
  `IPCServer` instance (looked up via `_get_rate_limiter(server)`).
  The limiter allows a burst of `burst` messages and a sustained rate
  of `sustained_per_sec` within a sliding window."
- **Files**: `tests/test_server.py` (lines 1330-1342)
- **Note**: A docstring-only inaccuracy (no behavior impact); documented
  here for a future pass to address.

## Summary

| ID    | Category    | Severity | Status  |
|-------|-------------|----------|---------|
| CR-25 | Documentation| Low    | Pending (open) |

**Test results (verified on Windows host, project venv)**:
- `cargo check` (src-tauri, stable-x86_64-pc-windows-gnu + MinGW) →
  EXIT:0 (Rust host compiles clean — no MSVC/link.exe needed).
- `tsc --noEmit` (voice_typer/client) → EXIT:0 (TS host, 15 IPC
  channels preserved verbatim).
- `pytest tests/regressions/ tests/test_waveform_bubble.py
  tests/test_cr_fixes.py` → 241 passed, 6 skipped, 0 failed
- `vitest run src/renderer/src/__tests__/rw1-rewrite/` → 137 passed.
- `pytest tests/test_app.py tests/test_app_cleanup.py` → 123 passed.
- `pytest tests/handlers/test_status_handlers.py` → 16 passed.
- TypeScript type-check (`tsc --noEmit`) passes clean.

**New files**:
- `voice_typer/client/src/main/tray_available.ts`
- `tests/test_cr_fixes.py`
- `src-tauri/src/{state,util}.rs` + `src-tauri/src/commands/*` +
  `src-tauri/src/sidecar/*` + `src-tauri/src/platform/*`
- `voice_typer/client/src/main/{state,constants,logging,branding,
  bootstrap,single_instance}.ts` + `ipc/*` + `python/*` + `windows/*`
- `voice_typer/server/{single_instance,logging_setup,env_validation,
  platform_launch}.py`
- `tests/regressions/*_test.py`

**Modified files**:
- `voice_typer/server/ipc_server.py` (`_get_rate_limiter` + TCP call site)
- `voice_typer/server/sidecar_ws.py` (WS call site + docstring)
- `voice_typer/server/tray.py` (TODO comment)
- `voice_typer/server/app.py` (`log.exception` + entry-module extraction)
- `voice_typer/client/src/main/index.ts` (import + pre-warm + handler; wiring)
- `pyproject.toml` (pystray pin)
- `requirements.txt` (pystray pin)
- `src-tauri/src/main.rs` (wiring-only; verified)
- `src-tauri/capabilities/migrate-runtime.json` (verified clean)
- `tests/test_*_bubble.py`, `tests/test_electron_ipc_and_build.py`,
  `tests/handlers/test_status_handlers.py`
- `scripts/build/voice-typer.spec`, `scripts/build/nuitka_freeze.sh`
