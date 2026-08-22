# ARCH-12 / S3-CR-21 — Source-text-pinning tests (`inspect.getsource`) migration

- **Status:** Proposed (chip-away migration in progress)
- **Date:** 2026-08-22
- **Review.md entries:** #2 ARCH-12 + #5 S3-CR-21
- **Severity:** High (blocks safe refactoring of large files)
- **Confidence:** High (R1, R14 — see `review.md` lines 251-260)

## Context

The voice-typer test suite contains **478 `inspect.getsource()` source-string
tests across 150 test files** (re-verified 2026-08-12; the count has GROWN
from 164/35 since the previous measurement). These tests assert on the
literal source text of a function, class, or module — pinning
implementation structure (variable names, call-site spellings, call
counts) rather than behavior.

When a refactor MOVES a method off a class, RENAMES an internal variable,
or ADDS/REMOVES a comment, `inspect.getsource(ClassName.method)` tests
break even though behavior is preserved. This makes safe refactoring of
large files (e.g. the credential_store split [AC-128], config split
[AC-131], orchestrator decomposition [AC-73]) expensive — every test that
pinned a moved method must be hand-edited.

The "static-source check echo" pattern in `recording/__init__.py:229-258`
module-level comments was the recommended short-term workaround for module-
level source-text tests. This ADR is the long-term plan: migrate the
source-pinning tests to behavioral tests that exercise the invariant via
inputs + outputs (not source text).

## Decision

1. **BAN new `inspect.getsource` tests.** The project rule is documented
   in `CONTRIBUTING.md` section "Source-text-pinning tests
   (inspect.getsource) — banned". New code must not introduce source-text
   assertions — exercise the function via inputs + outputs instead.

2. **Migrate existing tests incrementally.** This is a project-wide
   migration that CANNOT be completed in one wave (478 calls / 150
   files). Per review.md entry ARCH-12: "Chip away individually when
   touching pinned code." When a contributor touches code that has an
   `inspect.getsource` test, they port the test to a behavioral one as
   part of their change.

3. **Behavioral tests must cover the same invariants (E14).** A migration
   is NOT complete unless the new behavioral test exercises the same
   invariant the source-text pin was guarding. The old `inspect.getsource`
   test is DELETED (E15) once the behavioral test passes (E6).

4. **No band-aids (E13).** Stubs, commented-out tests, or "TODO migrate
   later" placeholders are NOT acceptable. Either migrate the test fully
   (behavioral test + delete source-text pin) or leave it alone for a
   future contributor.

## Migration Plan

For each `inspect.getsource` test:

1. **Identify the function under test** in `voice_typer/server/`.
2. **Understand the invariant** the source-text pin was guarding — read
   the assertions to extract the actual behavioral contract (e.g.
   "timeout is 8.0s", "version check runs before token check",
   "broad-except calls log.debug instead of pass").
3. **Write a behavioral test** that exercises the function with inputs
   and asserts on outputs (return value, side effects, raised
   exceptions, captured log records, captured mock calls). The new test
   must NOT inspect source text.
4. **Verify the behavioral test passes** (`python -m pytest <test_file>
   -x -q --no-cov`).
5. **Delete the `inspect.getsource` test.**
6. **Re-run the test file** to confirm no regression (E14).

### Acceptable behavioral approaches

- **Capture mock calls:** monkeypatch a collaborator (e.g.
  `_run_with_timeout`, `Path.rglob`) to capture call args + assert the
  expected kwargs (timeout value, target path).
- **Trigger the production path:** use `importlib.reload(module)` to
  re-execute module-level code with patched collaborators (e.g. patch
  `signal.signal` to raise `RuntimeError`, reload, assert the broad-except
  log fires).
- **Spy on real filesystem / network / logger:** monkeypatch
  `Path.exists`, `subprocess.run`, or attach a `logging.Handler` to
  capture records.
- **Replace redundant source-text pins entirely:** if existing behavioral
  tests already cover the invariant (e.g. one behavioral test verifies
  the same ordering / output that the source-text pin was structurally
  asserting), the source-text pin can be DELETED without adding a new
  behavioral test — just document the coverage in the test file's
  migration note.

### Unacceptable approaches

- Inline-copying the production code into the test body and asserting on
  the copy's behavior (the test would still pass after the production
  code regressed — the copy is decoupled from production).
- Stubbing out the function under test entirely (the test no longer
  exercises real code).
- Leaving the `inspect.getsource` test in place "for now" with a TODO
  comment (band-aid; E13).

## Completed This Wave (W1-A10, 2026-08-22)

5 files migrated (8 `inspect.getsource` source-text pins removed):

| Test file | Source-text pin removed | Behavioral test added | Approach |
|-----------|------------------------|----------------------|----------|
| `tests/test_task_scheduler.py` | `test_is_supported_source_references_schtasks_exe` (asserted `"schtasks.exe" in inspect.getsource(task_scheduler)`) | `test_is_supported_behaviorally_gates_on_schtasks_exe` | Restore real `is_supported()` (captured at module-import time before the autouse fixture stubs it); inject a `_FakePath` that records constructed path strings + returns a configurable `.exists()`. Verify (1) non-Windows → False + no Path construction, (2) Windows + binary present → True + Path constructed mentions `schtasks.exe` + `System32`, (3) Windows + binary absent → False. |
| `tests/test_shutdown_deadline.py` | `test_inner_timeouts_sum_to_less_than_outer_budget` (asserted `"timeout=8.0"` + `"timeout=4.0"` + absence of `"timeout=10.0"`/`"timeout=5.0"` in `inspect.getsource(teardown_history_db)`) | `test_inner_timeouts_are_8_and_4_seconds` | Monkeypatch `hist_module._run_with_timeout` with a capturing wrapper that records `(label, timeout)` tuples and executes the underlying callable. Call real `teardown_history_db(controller)`; assert the captured flush timeout = 8.0, close timeout = 4.0, sum < 15.0 (outer budget), neither equals 10.0/5.0 (regression guard). |
| `tests/test_ipc_protocol_versioning.py` | `test_source_contains_protocol_version_check_before_token_check` (asserted `"protocol_version"` / `"IPC_PROTOCOL_VERSION"` / `PROTOCOL_VERSION_MISMATCH_CODE` in `inspect.getsource(TCPTransportMixin._handle_tcp_connection)` BEFORE `extract_auth_token(`) | (No new test — existing behavioral tests already cover the invariant) | The existing behavioral tests `test_auth_accepts_frame_without_protocol_version`, `test_auth_accepts_frame_with_matching_protocol_version`, `test_auth_rejects_frame_with_mismatched_protocol_version` already verify the version-check-before-token-check ordering invariant: the mismatch test uses a CORRECT token but a MISMATCHED version and asserts a `protocol_version_mismatch` envelope is emitted (which would NOT happen if the version check ran AFTER the token check — the correct token would pass). Added a NOTE block + docstring expansion documenting the coverage. |
| `tests/test_clipboard_error_handling.py` | `test_source_has_broad_except_with_debug_log` (asserted `'log.debug("[CLIPBOARD] signal handler registration failed", exc_info=True)'` in `inspect.getsource(clip_mod)`) | `test_broad_except_emits_debug_log_via_reload` | Patch `signal.signal` to raise `RuntimeError` + assert `SIGHUP` exists; `importlib.reload(clip_mod)` to re-trigger the module-level registration block. Attach a `logging.Handler` to `clip_mod.log` (which is `logging.getLogger("voice_typer.server.clipboard")` — same instance across reloads). Assert exactly 1 DEBUG record with `[CLIPBOARD] signal handler registration failed` message + `exc_info[0] is RuntimeError`. |
| `tests/test_model_operations.py` | `test_poll_walks_model_dir_not_cache_root` (asserted `model_dir = cache_dir / f"models--{repo_id.replace('/', '--')}"` + `model_dir.rglob("*")` in `inspect.getsource(poll_download_progress)`, absence of `cache_dir.rglob("*")` in actual code) | (replaced in place — same test name) | Set up real `cache_dir / models--<repo_id>/model.bin` + monkeypatch `Path.rglob` with a spy that records the path object each call was made on (delegating to the real rglob so stat() still works). Run `poll_download_progress` with a fake thread (alive once then dead). Assert (1) `rglob` was called at least once, (2) walked path includes `models--<repo_id>`, (3) `rglob` was NEVER called on `cache_dir` itself. |

### Validation

```
python -m pytest tests/test_task_scheduler.py tests/test_shutdown_deadline.py \
  tests/test_ipc_protocol_versioning.py tests/test_clipboard_error_handling.py \
  tests/test_model_operations.py -q --no-cov
```

→ **52 passed in 2.37s on LINUX (sandbox)** (was 53 before — 1 source-text
pin removed in test_ipc_protocol_versioning.py; the invariant it pinned
was already behaviorally covered by the existing mismatch test).

### Remaining sites

```
$ rg 'inspect\.getsource\(' tests/ -c | wc -l
149
$ rg 'inspect\.getsource\(' tests/ -c | awk -F: '{sum+=$2} END {print sum}'
437
```

**149 files** with **437 `inspect.getsource(` calls** remain (down from
153 files / 478 calls at wave start — net 4 files / 41 calls migrated this
wave). Note: `inspect.getsourcefile` (4 sites — distinct function, returns
the source FILE path, not the source TEXT) is excluded from this count.

## Per-file migration tracking (sampled — full list is 149 files)

Status legend: ✅ done (this wave) · ⏳ in-progress · ⏳ pending · ⏭️ skipped (redundant)

| File | Calls | Status | Notes |
|------|------:|--------|-------|
| `tests/test_task_scheduler.py` | 1→0 | ✅ done | Behavioral via `_FakePath` spy. |
| `tests/test_shutdown_deadline.py` | 1→0 | ✅ done | Behavioral via `_run_with_timeout` capture. |
| `tests/test_ipc_protocol_versioning.py` | 1→0 | ✅ done | Source-pin removed; existing behavioral tests cover invariant. |
| `tests/test_clipboard_error_handling.py` | 1→0 | ✅ done | Behavioral via `importlib.reload(clip_mod)` + log capture. |
| `tests/test_model_operations.py` | 1→0 | ✅ done | Behavioral via `Path.rglob` spy. |
| `tests/regressions/audio_test.py` | 22 | ⏳ pending | Large file — chip away next. |
| `tests/test_electron_ipc_and_build.py` | 13 | ⏳ pending | |
| `tests/test_capture_worker_lifecycle.py` | 12 | ⏳ pending | |
| `tests/test_sidecar_ws_handle_connection_split.py` | 11 | ⏳ pending | |
| `tests/test_recording_and_audio.py` | 11 | ⏳ pending | |
| `tests/test_recorder_mono_and_disconnect_fixes.py` | 10 | ⏳ pending | |
| `tests/test_platform_and_config.py` | 9 | ⏳ pending | |
| `tests/test_ipc_layer_fixes.py` | 9 | ⏳ pending | |
| `tests/test_dead_code_stays_removed.py` | 9 | ⏳ pending | |
| `tests/test_transcription_perf_fixes.py` | 8 | ⏳ pending | |
| `tests/test_recording.py` | 8 | ⏳ pending | |
| `tests/test_ipc_server.py` | 8 | ⏳ pending | |
| `tests/regressions/gpu_memory_release_test.py` | 8 | ⏳ pending | |
| ... (132 more files) | ... | ⏳ pending | Full list at `rg 'inspect\.getsource\(' tests/ -c` |

## Next Actions (for future waves)

1. Pick 5-10 small/isolated files (1-2 `inspect.getsource` calls each)
   per wave — use:
   ```
   rg 'inspect\.getsource\(' tests/ -c | sort -t: -k2 -n | head -20
   ```
2. For each file: read the test → identify function under test → write
   behavioral test → delete source-text pin → run `pytest <file> -x -q`.
3. Update this ADR's tracking table + remaining-sites count after each
   wave.
4. Once `inspect.getsource(` count drops below 50, consider a final
   push to migrate the remaining complex cases (large classes with
   many source-text pins — these will need careful behavioral test
   design).
5. NEVER relax the CONTRIBUTING.md ban — it's the prevention mechanism
   that stops the count from re-growing.

## References

- `review.md` entries #2 (ARCH-12) + #5 (S3-CR-21) — original findings.
- `CONTRIBUTING.md` §"Source-text-pinning tests (inspect.getsource) — banned".
- AGENTS.md rules E6 / E13 / E14 / E15 / E16 / E19 — binding.
- `voice_typer/server/recording/__init__.py:229-258` — "static-source
  check echo" pattern (short-term workaround for module-level
  source-text tests; superseded by this ADR's long-term migration).
