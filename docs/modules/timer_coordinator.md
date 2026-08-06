# TimerCoordinator

**File**: `voice_typer/server/timer_coordinator.py`

## Responsibility

The `TimerCoordinator` owns the lifecycle of fire-and-forget `threading.Timer` instances scheduled by the application. It was extracted from `VoiceTyperApp` during the RW-9 god-class decomposition.

It is responsible for:

- Creating, tracking, and starting scheduled timers
- Cancelling all pending timers in one call (e.g. at shutdown / restart)
- Preventing stale callbacks (scheduled before a cancel) from firing after the cancel has bumped the generation counter (the *generation guard*)
- Evicting fired timers from the pending list so a long-running app does not accumulate thousands of stale `threading.Timer` shells (PERF-TMR)
- Reusing a fast-path bare daemon `Thread` for `delay <= 0` instead of allocating a `threading.Timer(0, ...)` (XV-134)

State owned by the coordinator:

- `self._pending_timers: list[threading.Timer]`
- `self._pending_timers_lock = threading.Lock()` — guards `_pending_timers` against the tray / transcription / timer threads racing with the snapshot-and-clear iteration in `_cancel_pending_timers`
- `self._timer_generation: int = 0` — bumped by `_cancel_pending_timers`; captured by each scheduled timer; mismatch at fire time ⇒ stale ⇒ skip

## Entry Points

- **`_schedule_timer(delay: float, func) -> threading.Thread`** — create, track, and start a timer that runs `func` after `delay` seconds. Captures the *current* `_timer_generation` inside the `_pending_timers_lock` critical section (so the capture is paired with the `append` — closes the TOCTOU window where a concurrent cancel could bump the generation between an unlocked read and the append). Wraps `func` in a `guarded_func` closure that: (1) checks the captured generation unlocked and short-circuits if stale; (2) consults `app._shutting_down_event` to suppress callbacks that race against the very start of shutdown; (3) re-checks the generation under the lock and evicts the timer from `_pending_timers` BEFORE invoking `func()` (so fired timers don't accumulate). For `delay <= 0`, short-circuits to a `_ZeroDelayThread` (bare daemon `Thread`) — `Timer(0)` still allocates an internal `threading.Event` and cancel-bookkeeping that is wasted when the callback runs immediately. Returns the underlying `Timer` / `Thread` so callers can `join()` if they need to.
- **`_cancel_pending_timers()`** — cancel and clear all pending timers. Takes the lock, snapshots the list, clears it, and bumps `_timer_generation` (so any in-flight `guarded_func` whose unlocked check already passed will fail the locked re-check and short-circuit). The `timer.cancel()` calls happen outside the lock so the lock is not held longer than necessary. Exceptions from individual `cancel()` calls are logged and swallowed (one bad timer must not abort the iteration).

The coordinator does **not** own the `app._shutting_down_event` — it consults it read-only via the back-reference `self._app` so a callback racing against the start of shutdown is suppressed even before `_cancel_pending_timers` has run.

## IPC Surface

None. The `TimerCoordinator` is a private internal helper. It is invoked by `VoiceTyperApp` (and the recording / transcription / tray call sites that previously scheduled timers directly on `app`), which delegate to `_schedule_timer` / `_cancel_pending_timers` via thin forwarder methods so existing callers (and tests that `monkeypatch.setattr(app, "_schedule_timer", spy)`) keep working unchanged.
