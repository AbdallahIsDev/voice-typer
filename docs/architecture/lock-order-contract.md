# Lock-Order Contract — `voice_typer/server/`

**Status**: LOW (architecture documentation — no deadlock observed).
**Scope**: `voice_typer/server/app.py` is the central orchestrator and owns
three locks plus several `threading.Event` instances. This document defines
the canonical acquisition order, the rationale, and the regression test that
guards it.

**Origin**: d-review `NEW-CONC-002` (lock-order contract). Prior to this
document, ordering was only *partly* documented inline (config lock before
spawn at `app.py:1087` / `service.py:1222`); no single global contract
existed. There is **no evidence of an actual deadlock** — the contract below
formalises the discipline the code already follows.

---

## 1. Lock / Event Inventory (app.py)

| # | Name | Type | File:Line | Purpose | Scope (held how long?) |
|---|------|------|-----------|---------|------------------------|
| L1 | `self._lock` | `threading.Lock` | `voice_typer/server/app.py:336` | Guards the `recording._transcription_thread = None` write in `dictation_pipeline.py:282` so concurrent readers (`_cancel_streaming_session` in another thread) don't observe a torn `None`↔`Thread` reference. | **Brief** — single attribute assignment; never held across I/O. |
| L2 | `self._config_mutation_lock` | `threading.RLock` | `voice_typer/server/app.py:348` | RACE-011 / SEC-audit-011 / B-4: serialise `Config` read-modify-save between concurrent IPC `set_config` handlers (multiple IPC server threads) and the tray-thread "open config file in editor" path. Held for the full setattr + side-effects + `save()` sequence so each mutation sees a consistent view of the `Config` object. | **Variable** — usually a few hundred microseconds (setattr + JSON write); may be held for the **entire** external-editor session in `_open_config_file` (Windows `ShellExecuteEx` wait, macOS `open -W`, Linux `xdg-open`). RLock chosen defensively in case a side-effect ever re-enters `apply_config`. |
| L3 | `self._pending_timers_lock` | `threading.Lock` | `voice_typer/server/app.py:394` | ARCH-022: guard the `_pending_timers: list[threading.Timer]` against the `RuntimeError: list changed size during iteration` race when `_cancel_pending_timers` iterates while the tray / transcription / timer threads append. | **Brief** — list append / snapshot-and-clear; `timer.cancel()` calls are deliberately performed **outside** the lock. |

### `threading.Event` instances on `VoiceTyperApp` (NOT locks — listed for completeness)

Events are *not* lock-order hazards (they cannot deadlock by themselves);
they are documented here because the d-review explicitly enumerated them.

| Name | File:Line | Purpose |
|------|-----------|---------|
| `self._busy_event` | `app.py:334` | **Set** = "not busy", **cleared** = "transcription in progress". Read with polling (`test_app.py::_wait_for_busy_clear`) and cleared/set in `_stop_dictation` / `dictation_pipeline.py` finally block. |
| `self._shutting_down_event` | `app.py:359` | RACE-020: cross-thread memory-order guarantee for `_shutting_down`. Set in `quit()` / `_do_cleanup()`; polled by executor tasks and IPC dispatch. |
| `self._bubble_level_worker_stop` | `app.py:657` | Sentinel for the `bubble-level-pusher` daemon thread. Set on shutdown; the worker drains its queue and exits. |

### Broader `voice_typer/server/` lock inventory (per-module, summarised)

Each subsystem owns **its own** lock and does not acquire any of the three
app-level locks above while holding its own (and vice-versa). This is the
"coarse-grained, independent locks" pattern — see §2 for why it is safe.

| Module | Lock | Purpose |
|--------|------|---------|
| `event_bus.py` | `_lock` (RLock, L63) | Guards subscriber set; RLock allows re-entrant `publish()` from subscribers. |
| `ipc_server.py` | `_lock` (Lock, L237) | Per-`_TCPLineIO` connection write serialization. |
| `ipc_server.py` | `_lock` (RLock, L576) | `IPCServer` connections dict + state. |
| `recording.py` | `_lock` (Lock, L391) | `Recorder` state (recording flag, buffer, sessions). |
| `recording.py` | `_resample_poly_lock` (Lock, L240) | Module-level — `scipy.signal.resample_poly` is not thread-safe. |
| `recording.py` | `_scipy_preloader_lock` (Lock, L276) | One-time lazy import of scipy. |
| `recording_controller.py` | `_toggle_lock`, `_watchdog_lock`, `_streaming_session_lock` (Lock, L63/72/77) | RecordingController state. |
| `transcription.py` | `_lock` (RLock, L385) | TranscriptionEngine state. |
| `transcription.py` | `_nvidia_config_lock` (Lock, L152) | Module-level — NVIDIA config init. |
| `model_manager.py` | `_model_lru_lock` (Lock, L108), `_lazy_init_lock` (Lock, L118) | Model LRU cache + lazy init. |
| `history_db.py` | `_connections_lock` (Lock, L160) | Per-thread SQLite connection pool. |
| `crash_recovery.py` | `_lock` (Lock, L59), `_save_lock` (Lock, L70) | Crash-recovery state + file write. |
| `vocabulary_automation.py` | `_lock` (Lock, L303) | Vocabulary automation state. |
| `volume_ducker.py` | `_lock` (Lock, L129) | VolumeDucker state (see `auto-volume-duck.md` §"Thread safety"). |
| `waveform.py` | `_lock` (Lock, L42) | Waveform bubble state. |
| `streaming.py` | `_lock` (RLock, L200), `_consecutive_failures_lock` (Lock, L442) | Streaming session state. |
| `qwen_engine.py` / `parakeet_engine.py` / `cloud_engines.py` | `_lock` (RLock) | Engine state. |
| `native_hotkeys.py` | `_match_lock` (Lock, L366), `_lock` (Lock, L991) + `_cond` (Condition, L992) | Hotkey matcher + queue. |
| `hotkeys.py` | `_VK_MAP_LOCK` (Lock, L451), `_swap_lock` (Lock, L2279) | VK map + hotkey swap. |
| `keyboard_ownership.py` | `_lock` (Lock, L70) | Keyboard ownership watchdog. |
| `permissions.py` | `_retry_lock` (RLock, L166) | Permission retry state. |
| `level_monitor.py` | `_monitor_lock` (Lock, L50) | Level monitor singleton. |
| `log_rate_limit.py` | `_RATE_LIMIT_LOCK` (Lock, L64) | Log rate limiter dict. |
| `text_cleanup.py` | `_active_state_lock` (Lock, L239) | Text cleanup active state. |
| `asr_setup.py` | `_download_pause_lock` (Lock, L60) | Download pause event guard. |
| `audio_filters/base.py` | `_lock` (Lock, L108) | Audio filter chain state. |
| `thread_registry.py` | `_lock` (Lock, L112) | Thread registry list. |
| `tray.py` | `_queue_lock` (Lock, L174) | Notification queue + pending notifications. |

---

## 2. Canonical Acquisition Order

### Rule 1 (primary — app.py locks): **No nesting.**

The three app.py locks (`_lock`, `_config_mutation_lock`,
`_pending_timers_lock`) are **independent**. They MUST NOT be acquired
nested within one another. Each is held for a single, well-defined critical
section that does not call into the others.

```
 ┌─────────────────────────┐
 │  app._lock              │   ─┐
 └─────────────────────────┘   │  Never acquired while
                              │  another app lock is held.
 ┌─────────────────────────┐   │
 │ _config_mutation_lock   │   │
 └─────────────────────────┘   │
                              │
 ┌─────────────────────────┐   │
 │ _pending_timers_lock    │  ─┘
 └─────────────────────────┘
```

If a future change requires atomicity across two of these, you MUST either:
- promote one to a coarser lock that subsumes the other (and update this
  document), or
- introduce a single higher-level coordinator lock acquired *before* any
  subsystem lock (see Rule 2).

### Rule 2 (broader codebase): **Subsystem locks are leaf locks.**

Every per-module lock listed in §1 is a *leaf* lock — code may acquire it
while holding one of the app.py locks (or any other subsystem lock) ONLY IF
the resulting nesting forms a *acyclic* graph. As of this writing, no such
nesting exists in production paths:

| Outer (held first) | Inner (acquired while outer held) | Site |
|--------------------|----------------------------------|------|
| `app._config_mutation_lock` | *(none — `apply_config_side_effects`, `clipboard.refresh_config`, `tray.invalidate_menu_cache` are all lock-free)* | `service.py:1222-1276` |
| `app._config_mutation_lock` | *(none — `ctrl.apply_settings` + `config.save()` are lock-free)* | `service.py:1398-1401` |
| `app._config_mutation_lock` | *(none — held during external editor subprocess; nothing else acquired)* | `app.py:1117 / 1149 / 1167` |
| `app._pending_timers_lock` | *(none — `timer.cancel()` runs after the lock is released)* | `app.py:568-570 / 581-589` |
| `app._lock` | *(none — single attribute assignment)* | `dictation_pipeline.py:282-283` |

### Rule 3: When a lock MUST be held across a long operation, document it.

`_config_mutation_lock` is the only app.py lock that may be held across an
unbounded-duration operation (the external editor session in
`_open_config_file`). This is **intentional** and documented inline
(`app.py:1094-1106`): the lock prevents a concurrent IPC `set_config` from
atomically clobbering `config.json` mid-edit (TOCTOU, SEC-audit-011).
Callers that need to acquire `_config_mutation_lock` for an IPC `set_config`
will simply block until the user closes the editor — this is the desired
behaviour.

---

## 3. Rationale

The "coarse-grained, independent locks" pattern is used because:

1. **No reentrancy across subsystems.** The app's three locks protect
   disjoint state (`_transcription_thread` ↔ `_lock`, `Config` ↔
   `_config_mutation_lock`, timer list ↔ `_pending_timers_lock`). There is
   no operation that needs to mutate two of these atomically.
2. **Coarse first, fine later.** If a future feature needs cross-cutting
   atomicity (e.g. a "config + pending timers" snapshot), introducing a new
   coordinator lock at the *top* of the order is safer than retrofitting
   nesting into existing locks — existing call sites don't have to learn
   about the new lock.
3. **RLock used defensively for `_config_mutation_lock`.** A plain `Lock`
   would suffice for the current code paths (no `apply_config` re-entry),
   but `RLock` future-proofs against a side-effect that calls back into a
   `apply_config`-family method. The cost (an extra owner check on
   acquire/release) is negligible vs. a single hard-to-diagnose deadlock.
4. **Locks held across I/O are explicitly flagged.** Only
   `_config_mutation_lock` (editor subprocess) and the `Recorder._lock`
   (briefly held during PortAudio callbacks — see `recording.py`) cross
   I/O boundaries; both have inline comments explaining why.

---

## 4. Known Exceptions / Intentional Violations

| Site | What | Why |
|------|------|-----|
| `app.py:1117 / 1149 / 1167` | `_config_mutation_lock` held across `subprocess.run(["open", "-W", ...])` / `xdg-open` / `ShellExecuteEx` wait | SEC-audit-011 / B-4: prevents TOCTOU clobber of `config.json` mid-edit. See inline docstring at `app.py:1076-1099`. |
| `recording.py:2800` (`NEW-PERF-007`) | `Recorder._lock` deliberately NOT acquired on the audio-callback fast path | Avoids blocking the PortAudio thread on a contended lock; the fast path uses atomic field swaps. |
| `volume_ducker.py:378` | `_stop_smart_duck_monitor()` called BEFORE acquiring `self._lock` | Avoids deadlock: the monitor loop needs `self._lock` to check `_saved_state` before exiting. See `auto-volume-duck.md:258-260`. |
| `ipc_server.py:876` (PR-3-FIX-1) | TCP auth handshake performed OUTSIDE `self._lock` | Prevents a stalled auth read from blocking `push()` events and other IPC dispatch threads. See ADR-0014 §6. |

---

## 5. Testing

### Regression test
`tests/test_lock_order_contract.py` enforces this contract with two layers:

1. **Static analysis** — parses `voice_typer/server/app.py`,
   `voice_typer/server/service.py`, and
   `voice_typer/server/dictation_pipeline.py` for `with self._lock:`,
   `with self._config_mutation_lock:`, and `with self._pending_timers_lock:`
   blocks and asserts that NO block acquires another of the three app
   locks. Catches accidental nesting introduced by future edits.
2. **Concurrency stress** — constructs a real `VoiceTyperApp()` with mocked
   deps and runs N threads concurrently through the production lock-using
   paths (`_schedule_timer`, `_cancel_pending_timers`,
   `_config_mutation_lock` holders). Asserts no thread hangs within 2 s
   (no deadlock).

### Related concurrency tests (existing)
- `tests/test_bugfix_regressions.py::TestConcurrentConfigWritesNoCorruption` — 8-thread `Config` attribute-write stress (GIL atomicity).
- `tests/test_bugfix_regressions.py::TestConcurrentDispatchNoDeadlock` — 8-thread IPC `_dispatch` stress.
- `tests/test_event_bus.py::TestReentrantPublish` — re-entrant `publish()` does not deadlock (RLock).
- `tests/test_concurrent_resample_safety.py` — PortAudio callback + resample lock-free append.
- `tests/test_thread_registry.py` — `shutdown_all()` join timeout (deadlock watchdog).

### Running
```sh
cd /home/z/my-project/voice-typer
python -m pytest tests/test_lock_order_contract.py -v --timeout=30
```

---

## 6. Change-Review Checklist

Before adding a new lock or changing an acquisition site, ask:

1. **Is the new lock leaf or coordinator?** If leaf, ensure no other lock is
   held when it is acquired. If coordinator, update §2 with the new order.
2. **Does it nest inside `_config_mutation_lock`?** If yes — STOP. That lock
   can be held for the duration of an external editor session. A nested
   lock there means a stuck editor window blocks your subsystem
   indefinitely.
3. **Is it held across I/O?** If yes — document why in an inline comment
   and add an entry to §4.
4. **Did you add a regression test?** If you added a new lock, extend
   `tests/test_lock_order_contract.py` with a static-analysis assertion for
   your new lock's no-nesting property.

---

## 7. Line-number stability

The line numbers in §1, §2, and §4 are pinned to the file at the time of
writing (NEW-CONC-002 round). They will drift as the surrounding code
changes; the **`tests/test_lock_order_contract.py`** suite uses regex
searches (not fixed line numbers) and so is robust to drift. If a line
number above becomes stale, treat it as a doc-freshness issue — not a
contract regression. The contract is enforced by the test suite, not by
the line numbers in this document.
