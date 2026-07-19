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

- **`shutdown()`** — the primary entry point. Called when the application receives a shutdown signal or the user quits. Performs a graceful, ordered release of all subsystems.
- **`force_shutdown()`** — emergency shutdown path that skips graceful teardown and terminates immediately. Used when the application is in an unrecoverable state.
- **`is_shutting_down`** — property/flag that other modules can check to avoid starting new work during shutdown.

## IPC Surface

None. The `ShutdownController` is not directly exposed over IPC. It is invoked internally by `VoiceTyperApp.quit_app()` and `VoiceTyperApp.restart_app()`, which are themselves exposed as IPC commands (`quit_app`, `restart_app`).
