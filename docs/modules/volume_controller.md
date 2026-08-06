# VolumeController

**File**: `voice_typer/server/volume_controller.py`

## Responsibility

The `VolumeController` owns the system-volume side effects of the dictation lifecycle. It was extracted from `VoiceTyperApp` during the RW-9 god-class decomposition (§5.3).

It is responsible for:

- Ducking system volume at the start of dictation (smart-duck + master-volume duck)
- Restoring system volume at the end of dictation (or on quit/restart with `fade_ms=0` for an instant restore)
- Surfacing a tray notification when the `VolumeDucker` discovers a stale `duck_crash_recovery.json` on startup (the previous session crashed while ducked)

The controller is stateless — all state lives on the `VolumeDucker` (constructed in `VoiceTyperApp.__init__`, NOT here, because it owns hardware-backend lifecycle and crash-recovery file state). The controller holds a back-reference `self._app` to read `app.config.*` and to drive `app._volume_ducker.*` / `app.tray.notify(...)`.

## Entry Points

- **`_on_volume_crash_restore(state)`** — callback invoked by `VolumeDucker` when it discovers a stale `duck_crash_recovery.json` on startup (i.e. the previous session crashed while ducked). Notifies the user via `app.tray.notify(APP_NAME, ...)` that the system volume was restored (e.g. to 100%). Failures inside `tray.notify` are logged at DEBUG and swallowed — the duck state has already been restored by `VolumeDucker`; the notification is best-effort.
- **`_duck_volume()`** — reduce system volume at the start of dictation. Reads `app.config.volume_duck_enabled` (early-return if disabled), forces smart-duck ON, sets the smart-duck poll interval to the canonical `_DEFAULT_SMART_DUCK_POLL_MS` (500 ms — not user-configurable), calls `app._volume_ducker.initialize()` and, on success, `app._volume_ducker.duck(level=volume_duck_level, fade_ms=volume_duck_fade_ms, per_session=False)`. Smart duck is ALWAYS ON when ducking is enabled; per-session ducking was removed (always master-volume duck cross-platform). Failures are logged at DEBUG and swallowed so a volume-backend hiccup never breaks dictation.
- **`_restore_volume(fade_ms: int | None = None)`** — restore system volume at the end of dictation. If `fade_ms is None`, uses `app.config.volume_duck_fade_ms`; pass `0` explicitly for an instant restore (used by `_do_cleanup()` and `restart_app()` so a quit/restart doesn't wait for the fade ramp). Mirrors `_duck_volume`'s early-return on `volume_duck_enabled == False` and `per_session=False` semantics.

## IPC Surface

None. The `VolumeController` is not directly exposed over IPC. It is invoked internally by `RecordingController._start_dictation` → `app._duck_volume()`, by `VoiceTyperApp._do_cleanup` → `self._restore_volume(fade_ms=0)`, and by `VoiceTyperApp.restart_app` → `self._restore_volume(fade_ms=0)`. The crash-restore callback is wired in `VoiceTyperApp.__init__` (the `VolumeDucker` constructor accepts the callback and invokes it on a stale-duck discovery).
