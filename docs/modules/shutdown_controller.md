# ShutdownController

**File**: `voice_typer/server/shutdown_controller.py`

## Responsibility

The `ShutdownController` manages the entire cleanup and shutdown lifecycle of the Voice Typer application. It was extracted from `VoiceTyperApp` during the RW-9 god-class decomposition.

It is responsible for releasing all subsystems in the correct order, including:
- Recorder (stop audio capture)
- Hotkey backends (unregister global hotkeys)
- History database (flush write queue, close connection)
- Crash recovery (save recovery state)
- Bubble level worker (stop level push thread)
- Win32 mutex (release single-instance mutex)
- Electron subprocess (terminate the main process)
- Devnull file descriptors (close background FDs)

## Entry Points

- **`quit()`** — the primary entry point. Called when the application receives a shutdown signal or the user quits. Sets `self._shutting_down = True`, calls `thread_registry.shutdown_all()`, delegates to `_do_cleanup()`, then `sys.exit(0)` (only when called from the main thread). Mirrors the historical `VoiceTyperApp.quit()` behaviour; `VoiceTyperApp.quit_app` (the IPC handler) remains a thin delegate that calls this.
- **`_do_cleanup()`** — the shared, idempotent cleanup body invoked by `quit()`, `restart_app()`, and `_atexit_cleanup()`. 30+ try/except blocks release every subsystem in order (recorder, hotkeys, history DB, crash recovery, bubble level worker, Win32 mutex, Electron subprocess, devnull FDs). Short-circuits on `_shutting_down` so concurrent callers (e.g. an atexit hook racing with an explicit `quit()`) don't double-release.
- **`_do_fast_cleanup()`** — emergency fast-path used when the process is in an unrecoverable state (e.g. supervisor kill-children backstop, or the regular `_do_cleanup()` is taking too long). Skips the slow / blocking teardown steps and forcibly releases the critical resources only.
- **`_atexit_cleanup()`** — the `atexit` safety net. Runs `_do_cleanup()` if the process is killed externally without `quit()` / `restart_app()` having had a chance to run. Idempotent — short-circuits on `_shutting_down` and never raises (an `atexit` handler that raises would mask the real exit cause).

The shutdown-in-progress flag is the **private** `self._shutting_down` attribute (a plain `bool`) plus the companion `self._shutting_down_event` (`threading.Event`). There is **no** public `is_shutting_down` property — callers that need to consult the flag read `app._shutting_down` directly (or `app._shutting_down_event.is_set()` for the race-free variant).

## IPC Surface

None. The `ShutdownController` is not directly exposed over IPC. It is invoked internally by `VoiceTyperApp.quit_app()` and `VoiceTyperApp.restart_app()`, which are themselves exposed as IPC commands (`quit_app`, `restart_app`).
