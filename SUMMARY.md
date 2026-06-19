# Auto-Volume Duck + Noise Filtering — Implementation Summary

This document summarises the work done to finish the auto-volume-duck and
noise-filtering feature for the voice-typer app.  The architecture is fully
documented in `docs/architecture/auto-volume-duck.md` (now updated to v2.1
with post-implementation line numbers).

## What was done:

- Files modified:
  - `voice_typer/server/recording.py`
  - `voice_typer/server/audio_processor.py`
  - `voice_typer/server/app.py`
  - `voice_typer/server/ipc_server.py`
  - `voice_typer/client/src/renderer/src/pages/Settings.tsx`
  - `tests/test_server.py`
  - `docs/architecture/auto-volume-duck.md`
- Files added:
  - `tests/test_volume_lifecycle.py`
  - `tests/test_volume_backends.py`
  - `tests/test_recording_audio_processor.py`
  - `archive/deleted_files.txt`
- What changed:
  - **CRITICAL bugfix in `recording.py`:** the PortAudio audio callback referenced `filtered` *inside* the lock block before it was assigned *after* the lock block.  This raised `NameError` on every audio chunk.  PortAudio swallows callback exceptions silently, so the recording captured nothing — no audio, no buffer growth, no RMS updates — whenever an `AudioProcessor` was attached.  Reordered the callback so `filtered` is computed BEFORE the lock-block that uses it.  This went undetected because no test exercised the callback with an `AudioProcessor` attached; the new `tests/test_recording_audio_processor.py` regression-tests it.
  - **Bugfix in `audio_processor.py::_apply_highpass`:** `scipy.signal.lfilter` raises `ValueError: object of too small depth for desired array` when given a 2-D `(frames, 1)` array (which is what `sounddevice.InputStream` actually delivers for a mono capture).  Ravel the input before filtering and reshape back to the original shape.  Now handles both 1-D and 2-D input.
  - **Revived `AudioQualityAnalyzer`** (was dead code — `app.py` imported it but never instantiated).  Now: instantiated in `__init__`, wired to `AudioProcessor.set_quality_callback()`, reset per session in `_start_dictation`, finalised in `_stop_dictation` via the new `_finalize_audio_quality_report()` method which calls `analyze_full_audio()` on the captured samples and surfaces clipping/low-volume/high-noise warnings via the tray (gated by `config.audio_quality_warnings`).
  - **New IPC endpoint `get_volume_backend_status`** in `ipc_server.py` — returns `{available, name, supports_per_session, is_windows}` so the Settings UI can show the active backend name (e.g. "pycaw (WASAPI)" / "CoreAudio (pyobjc)" / "linux (pactl)" / "disabled") and gate the Per-Session Duck toggle on `is_windows && supports_per_session`.
  - **Settings.tsx overhaul of the "Audio Enhancement" section:** added a Volume Backend status indicator, a Duck Fade Duration slider (exposes the previously-hidden `volume_duck_fade_ms` config field), auto-disabled the Per-Session Duck toggle on non-Windows, added a High-Pass Cutoff slider (exposes `noise_filter_highpass_cutoff_hz`), and added a Noise Gate Threshold slider (exposes `noise_filter_gate_threshold`).  All sliders use the existing `updateConfigDebounced` helper so changes are saved without spamming the IPC channel.
  - **`DuckCrashRecovery` now uses `_config_dir()`** instead of the hardcoded `Path.home() / ".voice-typer"`.  This makes the crash-recovery file live alongside the rest of the user's voice-typer state and lets tests monkeypatch `_config_dir` to point at a tmp_path.  Previously, tests that exercised the duck lifecycle would leak a `~/.voice-typer/duck_crash_recovery.json` file into the developer's home directory.
  - **Architecture doc updated to v2.1:** refreshed every line-number reference in §6.4, §7.2–§7.7, §10.3, §10.4, §11, and §14 to match the post-implementation code.  Marked each implementation-order item with ✅ / 🔧 / ⏳ status.  Documented the two bugs found during integration testing.
  - **61 new tests** across four new files:
    - `tests/test_volume_lifecycle.py` (18 tests): full start→duck, stop→restore, cancel→restore, quit→restore(no fade), restart→restore-before-subprocess, crash-recovery-on-startup, manual-volume-override, per-session-gated-on-support, and duck-persists/clears crash-recovery-file.
    - `tests/test_volume_backends.py` (32 tests): LinuxVolumeBackend pactl/wpctl/amixer parsing + tool detection priority, MacVolumeBackend osascript fallback, WinVolumeBackend smoke tests (init fails gracefully without pycaw), VolumeBackend.fade_to default implementation.
    - `tests/test_recording_audio_processor.py` (7 tests): regression tests for the recording callback path with an `AudioProcessor` attached.  Catches the `filtered` NameError, verifies the buffer stores FILTERED audio (high-pass actually attenuates 30 Hz), verifies the RMS callback fires with filtered values, verifies the quality callback fires per chunk, verifies post-capture runs in stop(), and verifies the no-processor graceful-degradation path.
    - `tests/test_server.py` (4 new tests in `TestDispatchGetVolumeBackendStatus`): IPC endpoint returns backend name/availability, calls initialize() to detect the backend, handles missing `_volume_ducker` gracefully, and handles initialize() exceptions.
- Why:
  - The `filtered` NameError was a silent recording-breaking bug — PortAudio swallows callback exceptions, so the symptom was "recording captures nothing when noise filter is enabled".  No automated test caught it because the existing tests mocked the recorder.  The new regression tests construct a real Recorder with a real AudioProcessor and drive the callback via a FakeInputStream, so this class of bug can't regress.
  - The 2-D-array high-pass bug had the same symptom (silent capture failure) and the same root cause (no test exercised the AudioProcessor with the actual 2-D shape sounddevice delivers).
  - AudioQualityAnalyzer was imported but never instantiated — DEAD-014 in the codebase.  The architecture doc called out its revival as a goal; this implementation finishes it.
  - The architecture doc's line numbers were stale (referenced v1's line numbers from before the feature was implemented).  Refreshing them makes the doc useful for future maintenance.
  - Exposing `volume_duck_fade_ms`, `noise_filter_highpass_cutoff_hz`, and `noise_filter_gate_threshold` in the Settings UI (they were already in the config schema and IPC allowlist but invisible to users) gives users the control the architecture doc §7.8 promised.
  - Auto-disabling the Per-Session Duck toggle on macOS/Linux prevents users from enabling a feature that the backend silently ignores — better UX than "I toggled it but nothing changed".
- Tests run:
  - `tests/test_volume_ducker.py` — 32 tests, all pass.
  - `tests/test_audio_processor.py` — 22 tests, all pass.
  - `tests/test_audio_quality.py` — 13 tests, all pass.
  - `tests/test_volume_lifecycle.py` — 18 tests, all pass.
  - `tests/test_volume_backends.py` — 32 tests, all pass.
  - `tests/test_recording_audio_processor.py` — 7 tests, all pass.
  - `tests/test_server.py` — 127 tests (including 4 new), all pass.
  - `tests/test_app.py` — 92 tests, all pass.
  - `tests/test_recording.py` — 21 tests, all pass.
  - Full suite: `895 passed, 9 skipped, 1 failed` in 25s.  The 1 failure is `tests/test_task_scheduler.py::TestPrewarmCommand::test_falls_back_to_sys_pythonw` — pre-existing (verified by `git stash` + re-run on the unmodified main branch), unrelated to this feature, caused by the test expecting `pythonw.exe` on a Linux test runner.
- Tests skipped:
  - RNNoise in-callback performance test — RNNoise is not installed in the CI environment (it's an optional `rnnoise-webrtc` dep).  The in-callback path is exercised (the AudioProcessor's `_apply_rnnoise` is covered by unit tests with a mocked RNNoise), but the actual neural-denoise performance characteristics can only be validated on a machine with `rnnoise-webrtc` installed.  Documented in architecture doc §10.3.
  - Manual cross-platform testing on real Windows/macOS hardware — automated tests cover the parsing logic and lifecycle, but real-hardware testing of pycaw's COM threading, macOS's CoreAudio permission prompts, and PipeWire's `wpctl` quirks still needs to happen before the v1.1.0 release.  Documented as ⏳ in architecture doc §14 item 24.
  - The pre-existing `tests/test_task_scheduler.py::TestPrewarmCommand::test_falls_back_to_sys_pythonw` failure is unrelated to this work (verified by re-running on unmodified `main`).
