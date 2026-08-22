# TEST-2 — `time.sleep` → condition-wait migration

Status: 🟡 In progress (Wave 1, Agent W1-A11 — chip-away per review.md E16).
Last updated: 2026-08-22 (Wave 1).

## Context

`review.md` entry **TEST-2** flags the project's test suite for relying on
fixed `time.sleep(N)` calls as synchronization barriers. Fixed sleeps are
flaky on loaded CI runners — they guess at "how long until the system
reaches state X" and either under-wait (false failure) or over-wait
(slow tests). The fix is to replace each fixed sleep with a condition
wait that returns as soon as the system reaches the target state.

This is explicitly a **chip-away** migration (review.md E16): the full
495-call / 239-file scope cannot be completed in one wave. Each wave
migrates 15-25 calls across 8-12 files; future waves continue the
work on the remaining sites.

## Decision

Adopt **two canonical helpers** as the project-wide replacement for
fixed `time.sleep` synchronization:

1. **`wait_until(predicate, timeout, interval)`** — poll a zero-argument
   predicate until it returns truthy or the timeout elapses. Returns
   `True` on success, `False` on timeout (caller decides whether to
   `assert` / `pytest.fail` / treat as expected).
2. **`wait_for_event(event, timeout)`** — bounded wrapper around
   `threading.Event.wait`. Prefer this over `wait_until` whenever the
   synchronization primitive is already a `threading.Event` — `Event.wait`
   is non-busy (OS-parked) and deterministic.

### Where the helpers live

`tests/fixtures/wait_helpers.py` (NEW in this wave) is the canonical
entry point. For DRY (E7 / P2), `wait_until` is a *thin alias* for the
existing `tests/fixtures/wait_for.wait_for` poller (which a previous
wave extracted from the deleted `tests.conftest.wait_until` helper —
see that module's docstring for the history). The alias exists so TEST-2
migrations can adopt a single, descriptive import name without forcing
churn on the existing `wait_for` importers (`tests/test_microphone_watcher.py`,
`tests/test_hotkeys_win32.py`, `tests/hotkeys/test_polling_strategy.py`).

`wait_for_event` is a new wrapper — there was no canonical "wait for an
event with a timeout and return a bool" helper before. The wrapper
exists so test code can import a single canonical name alongside
`wait_until`.

### Library choice (W2)

`polling2` is NOT installed in the project `.venv` (verified at migration
time). Per W2's "else build minimal `wait_until`" clause, we fall back
to the in-repo minimal poller. The existing `wait_for` implementation is
~10 lines and uses `time.monotonic()` (immune to wall-clock adjustments)
— sufficient for the test-suite use case.

## Migration rules

When migrating a `time.sleep(N)` call site:

1. **Identify the target condition** the sleep is waiting for (read the
   surrounding code — what state should the system be in after the
   sleep?).
2. **Replace with a condition wait**:
   - If the wait is for a `threading.Event`: `assert wait_for_event(event, timeout=N*10)`.
   - If the wait is for an observable state: `assert wait_until(lambda: <state>, timeout=N*10)`.
   - If the wait is to verify "X does NOT happen": `assert not wait_until(lambda: <X happens>, timeout=N*10)`.
3. **Use 10x timeout headroom** over the original sleep duration — CI
   runners can be 10x slower than dev machines. The headroom costs
   nothing on a fast machine (the predicate returns early) and prevents
   false failures on a slow one.
4. **Add a TEST-2 migration comment** above the new call so future
   readers know this is a deliberate migration (not a fresh condition
   wait) and can find the original sleep in git history.
5. **Keep `time.sleep` for real-time delays** — sleeps that simulate
   external latency (slow D-Bus probes, slow disk I/O, slow teardowns,
   sub-millisecond thread-interleave yields, mtime-granularity spacing)
   are NOT synchronization barriers and should be left in place. The
   migration is only for sleeps that gate on system state.

## Completed this wave (W1-A11)

Migrated **16 `time.sleep(...)` calls across 12 test files**:

| File | Sleeps migrated | Pattern |
|------|-----------------|---------|
| `tests/test_ipc_shutdown_registry.py` | 1 | Poll loop → `wait_until(lambda: call_count > 0)` |
| `tests/test_buffer_clear_worker.py` | 1 | `_drain_queue` helper poll loop → `wait_until(lambda: not unfinished_tasks)` |
| `tests/test_pack_checksum_background.py` | 1 | "Give bg thread a moment" sleep → `wait_until(lambda: bg.done)` |
| `tests/test_pack_early_transcribe.py` | 1 | "Let some requests queue first" sleep → `wait_until(lambda: enqueued_count > 0)` |
| `tests/test_config_save_lock.py` | 1 | "Save thread blocked on lock" poll loop → `wait_until(lambda: thread_done or result_landed)` |
| `tests/test_qwen_unload_race.py` | 1 | "Give unload a moment to enter wait()" → `wait_until(lambda: unload_done)` (asserting it stays False) |
| `tests/test_config_editor_lock.py` | 1 | "Give setter thread a moment to attempt acquire" → `wait_until(lambda: acquired)` (asserting it stays False) |
| `tests/test_event_bus_snapshot.py` | 1 | "Wait for async dispatch" poll loop → `wait_until(lambda: bool(received))` |
| `tests/test_history_db_drain_remaining.py` | 1 | "Give writer a moment to pick up closure" → added `closure_started` Event + `wait_until(lambda: closure_started.is_set())` |
| `tests/test_update_check.py` | 3 | Two "wait for daemon download" poll loops + one "wait for finally guard release" poll loop → three `wait_until` calls |
| `tests/test_volume_ducker.py` | 3 | Three "monitor wind-down" / "retroactive duck applied" poll loops → three `wait_until` calls |
| `tests/test_tray_pending_drain.py` | 1 | "Give run() time to enter wait()" → `wait_until(lambda: t.is_alive())` |

All 170 tests in the 12 migrated files PASS on LINUX (sandbox) after
migration (no regressions, E14).

### Sleeps deliberately KEPT (real-time delays, not sync barriers)

These sleeps were analyzed and intentionally NOT migrated — they
simulate external latency or sub-millisecond thread interleaving, not
synchronization barriers:

- `tests/test_shutdown_sounddevice_wait.py` (3 calls, `time.sleep(10)`)
  — simulates a blocking `sd.wait()` / `sd.stop()` call (PortAudio
  deadlock). The sleep IS the simulated blocking operation.
- `tests/test_shutdown_deadline.py` (3 calls, `time.sleep(0.3)` / `0.05`)
  — simulates slow teardown functions (`_slow_history_db`,
  `_slow_crash_recovery`, `_spy_recorder`). The sleep IS the slow work.
- `tests/test_config_migration_schema_version.py` (1 call, `time.sleep(1.1)`)
  — forces a 1s-granularity timestamp difference for unique backup
  filenames. The sleep IS the time separation.
- `tests/test_config_backup_secure.py` (1 call, `_time.sleep(0.01)`)
  — spaces out file mtime for retention pruning test. Real-time delay.
- `tests/test_integrity_cache.py` (1 call, `time.sleep(0.01)`)
  — forces mtime granularity difference. Real-time delay.
- `tests/test_credential_store_keyring_reprobe.py` (1 call, `time.sleep(0.05)`)
  — simulates a slow D-Bus probe round-trip. The sleep IS the slow I/O.
- `tests/test_pack_download_queue.py` (1 call, `time.sleep(0.1)`)
  — simulates a user download that lasts 0.1s before being released.
- `tests/test_pack_dual_instance.py` (1 call, `time.sleep(0.1)`)
  — simulates work being done under a held lock. Real-time delay.
- `tests/test_dictation_pipeline_orchestrator_decomposition.py` (1 call, `_time.sleep(0.005)`)
  — inside a `_timed_stage` context, the sleep IS the operation being timed.
- `tests/test_dictation_pipeline_review_fixes.py` (1 call, `time.sleep(0.005)`)
  — same: sleep IS the operation being timed.
- `tests/test_model_idle_unload.py` (1 call, `time.sleep(0.05)`)
  — "wait to verify nothing happened" (assertion of absence). The sleep
  IS the absence window.
- `tests/test_tray_pending_drain.py` (2 calls, `time.sleep(0.001)` / `0.002`)
  — sub-millisecond yields inside concurrent appender / drain loops to
  simulate thread interleaving. Real-time yields.

## Remaining sites

Re-measured at end of W1-A11:

- **Total `time.sleep(` matches via `rg 'time\.sleep\(' tests/`**: 404
  (down from 417 at start of wave — net -13. The 16 actual call sites
  migrated are partially offset by docstring mentions of `time.sleep(...)`
  in the new `tests/fixtures/wait_helpers.py` module + migration comments
  that quote the original sleep durations; the actual call-site delta is
  -16.)
- **Files containing `time.sleep(`**: 146 (down from 155 — net -9, which
  matches the 12 migrated files minus 3 files that still have a kept
  sleep after migration: `test_tray_pending_drain.py` keeps 2 sub-ms
  yields, `test_buffer_clear_worker.py` and `test_event_bus_snapshot.py`
  have no remaining calls — the rg matches in those are docstring
  references only).

### Top remaining sites (by call count, for next wave's targeting)

(Re-run `rg 'time\.sleep\(' tests/ -c | sort -t: -k2 -nr | head -30` for
a fresh list at the start of the next wave — the rankings shift as
migrations land.)

The highest-density files (16, 12, 11, 10, 8, 8, 7, 7, 7, 7 calls) are
the next chip-away targets. Each one needs individual analysis to
separate sync-barrier sleeps (migrate) from real-time-delay sleeps (keep).

## When is the migration "done"?

The migration is complete when ALL of the following hold:

1. **<50 `time.sleep(` call sites remain** in `tests/` (measured via
   `rg 'time\.sleep\(' tests/ --no-filename | wc -l` minus docstring
   matches — a clean grep without comment matches). Current: ~400 call
   sites (estimated, after subtracting ~5 docstring mentions).
2. **All remaining sleeps are real-time-delay contexts** (slow I/O
   simulation, mtime spacing, sub-millisecond yields, absence-window
   assertions) — verified by a full audit of the remaining sites.
3. **No file has >3 `time.sleep(` calls** for synchronization (high-density
   files refactored first).
4. **`tests/fixtures/wait_helpers.wait_until` is the canonical poller**
   for new test code — enforced via a `ruff` / `flake8` lint rule
   (deferred to a future wave; the rule would flag new `time.sleep(`
   calls in `tests/` and require an inline `# noqa: TEST-2 — real-time
   delay` exemption comment for kept sites).

Estimated remaining waves: 4-6 more waves of 15-25 calls each (per
review.md's "~2-4 day effort" estimate, distributed across waves).

## Validation

- `python -m pytest <12 migrated files> -q --no-cov` → **170 passed in 63.95s** on LINUX (sandbox).
- `python -m pytest tests/ --collect-only -q` → 14382 tests collected (collection succeeds — E1 wiring check).

## Cross-references

- `review.md` entry TEST-2 (lines 196-204).
- `tests/fixtures/wait_for.py` — canonical `wait_for` poller implementation.
- `tests/fixtures/wait_helpers.py` — NEW canonical entry point for TEST-2 migrations.
- `AGENTS.md` — E7 (DRY), E14 (regression prevention), E16 (chip-away), P2 (no copy/paste).
