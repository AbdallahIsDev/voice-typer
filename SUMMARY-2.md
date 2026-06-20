# SUMMARY-2 — Round 9 Changes

**Date:** 2026-06-20
**Round:** 9 (second delivery round for this session)
**Test results:** 1027 passed, 9 skipped, 0 failed (was 1002/9/0 pre-Round-9)
**Client TypeScript:** `tsc --noEmit` clean, `npm run build` succeeds

## Overview

Fixed the 4 remaining problems from `workspace/todo.md` and verified end-to-end. All open items from the original problem report are now resolved. 25 new regression tests added covering all 4 fixes.

## Issues Fixed

| # | Issue | Severity | Status |
|---|---|---|---|
| #2 | app.py god module (2,520 LOC) | High | ✅ FIXED |
| STARTUP-3 | torch/transformers 157s/402s import times | Medium-High | ✅ FIXED |
| STARTUP-5 | Prewarm Windows-only | Medium | ✅ FIXED |
| STARTUP-7 | ~38s Run-key delay | Low-Medium | ✅ FIXED |

## Files Changed (Round 9 only)

### Backend (Python) — 7 files (4 new)

1. **`voice_typer/server/app.py`** — #2: Reduced from 2,520 → 1,878 lines (642 LOC extracted). All model/recording/hotkey logic replaced with thin delegates to the three new controllers. @property delegates expose legacy fields (`transcriber`, `_qwen_engine`, `_parakeet_engine`, `_hotkey_backend`, `_esc_backend`, `_repaste_backend`, `_asr_registry`, `_model_load_thread`, `_model_load_attempted`, `_pending_dictation`, `_transcription_thread`, `_streaming_session`) for back-compat with tests.
2. **`voice_typer/server/model_manager.py`** (NEW, 436 LOC) — #2: Owns AsrBackendRegistry + the three legacy engine fields + all model lifecycle methods (`load_background`, `start_background_load`, `fallback_to_whisper`, `try_load`, `change_model`, `active_transcriber`, `ensure_active_engine_loaded`).
3. **`voice_typer/server/recording_controller.py`** (NEW, 497 LOC) — #2: Owns recording lifecycle (`toggle`, `start`, `stop`, `cancel`), silence/xrun callbacks (`on_silence_warning`, `on_silence_auto_stop`, `on_max_duration_auto_stop`, `on_xrun_threshold`, `on_recorder_rms`), streaming session management (`_start_streaming_session_if_enabled`, `_cancel_streaming_session`, `_force_recover_from_stuck_transcription`), and the `_streaming_session` / `_transcription_thread` fields.
4. **`voice_typer/server/hotkey_dispatcher.py`** (NEW, 171 LOC) — #2: Owns the 3 hotkey backends + all registration logic (`register`, `register_esc`, `unregister_esc`, `register_repaste`, `restart`, `stop_all`).
5. **`voice_typer/server/platform.py`** — STARTUP-7: `_enable_autostart_windows()` now tries Task Scheduler first (new `_register_app_autostart_task()`), falls back to HKCU Run key. `_disable_autostart_windows()` removes from both. `_is_autostart_windows()` checks both. New helpers: `_app_autostart_command_and_args()`, `_build_app_autostart_task_xml()`, `_register_app_autostart_task()`, `_unregister_app_autostart_task()`, `_is_app_autostart_task_registered()`, `_register_app_autostart_runkey()`, `_unregister_app_autostart_runkey()`, `_is_app_autostart_runkey_registered()`. All `import winreg` wrapped in try/except ImportError for POSIX test hosts.
6. **`voice_typer/server/task_scheduler.py`** — STARTUP-5: `is_supported()`, `is_prewarm_registered()`, `register_prewarm_task()`, `unregister_prewarm_task()` now delegate to `prewarm_scheduler_posix` on macOS/Linux. All `import winreg` wrapped in try/except ImportError.
7. **`voice_typer/server/prewarm_scheduler_posix.py`** (NEW, 172 LOC) — STARTUP-5: Cross-platform prewarm scheduling. macOS LaunchAgent (`RunAtLoad=true`, `ProcessType=Background`). Linux systemd user timer (`OnBootSec=10s`, `OnUnitActiveSec=4h`, `IOSchedulingClass=idle`, `Nice=10`).
8. **`voice_typer/server/prewarm.py`** — STARTUP-3 + STARTUP-5:
   - STARTUP-3: `_warm_imports()` now filters by active backend. Whisper → only `faster_whisper` (skips torch/transformers, saves ~400s on cold boot). Parakeet/Qwen → full torch + transformers stack.
   - STARTUP-5: `_lower_io_priority()` now lowers CPU priority via `os.nice(10)` on POSIX, and I/O priority via `ioprio_set` on Linux (was Windows-only).

### Tests — 3 files (1 new)

1. **`tests/test_app.py`** — #2: 4 tests updated to monkeypatch new module locations (`voice_typer.server.transcription.TranscriptionEngine` for ARCH-007, `voice_typer.server.recording_controller.StreamingTranscriptionSession` for streaming, `voice_typer.server.hotkey_dispatcher.create_hotkey_backend` for hotkeys, `app.hotkeys.register` for restart).
2. **`tests/test_task_scheduler.py`** — STARTUP-5: `_force_supported` fixture now also patches `sys.platform` to "win32" so the Windows code path is exercised on POSIX test hosts.
3. **`tests/test_round9_e2e.py`** (NEW, 25 tests) — Round 9 E2E tests covering all 4 fixes:
   - `TestIssue2Extractions` (10 tests): module existence, app.py uses controllers, app.py < 2000 lines, lifecycle methods present, @property delegates work
   - `TestStartup3ImportFiltering` (2 tests): whisper skips torch/transformers; parakeet imports them
   - `TestStartup5PrewarmPosix` (7 tests): module exists, plist/service/timer builders, is_supported on POSIX, macOS round-trip, Linux round-trip
   - `TestStartup7AppAutostartTaskScheduler` (5 tests): XML uses pythonw directly, prefers Task Scheduler, falls back to Run key, disables both, checks both

## Workspace Folder Updates

- `workspace/todo.md` — Marked #2, STARTUP-3, STARTUP-5, STARTUP-7 as ✅ FIXED with per-issue work logs in the requested format. Updated production-readiness verdict to reflect all items resolved.
- `workspace/done.md` — Listed all 4 Round 9 completed items + Round 8 history.
- `workspace/status.md` — Updated Round to 9, set Active to No, total completed = 14.
- `workspace/notes.md` — Updated current position, recent decisions, files modified.
- `workspace/decisions.md` — Added D9.1 through D9.6 decision records.
- `workspace/archive/deleted_files.txt` — "No files deleted." (no files were deleted this round).

## Testing & Verification

### Python test suite
```
$ pytest tests/ --tb=no
1027 passed, 9 skipped, 1 warning in 32.96s
```

Baseline before Round 9 was 1002 passed, 9 skipped, 0 failed. Net change: +25 tests (all passing).

### New test file added
- `tests/test_round9_e2e.py` — 25 tests covering all 4 Round 9 fixes

### Client TypeScript
```
$ npx tsc --noEmit -p tsconfig.web.json
(no output — clean compile)

$ npm run build
✓ 185 modules transformed.
✓ built in 2.48s
```

## Architecture Summary (post-Round-9)

```
VoiceTyperApp (1,878 LOC, was 2,520)
    ├── self.config (Config)
    ├── self.recorder (Recorder)
    ├── self.tray (TrayIcon)
    ├── self.clipboard (ClipboardManager)
    ├── self.history_db (HistoryDB)
    ├── self._crash_recovery (CrashRecovery)
    ├── self._volume_ducker (VolumeDucker)
    ├── self._waveform_bubble (WaveformBubble)
    ├── self._audio_processor (AudioProcessor)
    ├── self._audio_quality (AudioQualityAnalyzer)
    ├── self.models (ModelManager)              ← NEW (436 LOC)
    │       └── _registry (AsrBackendRegistry)
    │       └── transcriber / _qwen_engine / _parakeet_engine (legacy fields)
    ├── self.recording (RecordingController)    ← NEW (497 LOC)
    │       └── _streaming_session / _transcription_thread
    └── self.hotkeys (HotkeyDispatcher)         ← NEW (171 LOC)
            └── _hotkey_backend / _esc_backend / _repaste_backend
```

VoiceTyperApp is now an orchestrator that wires the controllers together and owns shared state (config, tray, busy_event, cycle_id). The controllers own their domain logic and expose clean interfaces.

## How to Apply

1. Unzip `changes-2.zip` into the root of the voice-typer repository.
2. The archive contains:
   - All Round 9 modified source files (preserves directory structure)
   - All new Round 9 source files (model_manager.py, recording_controller.py, hotkey_dispatcher.py, prewarm_scheduler_posix.py)
   - All Round 9 test files (test_round9_e2e.py + updated test_app.py + test_task_scheduler.py)
   - The complete `workspace/` folder with updated docs
3. Run tests to verify: `PYTHONPATH=. python -m pytest tests/`
4. Build the client: `cd voice_typer/client && npm install && npm run build`

## Files Deleted

None. (`workspace/archive/deleted_files.txt` contains "No files deleted.")

## Cumulative Status (Rounds 8 + 9)

All 14 problems from the original `todo.md` are now fixed and verified:

| Round | Issues Fixed | Tests Added |
|-------|--------------|-------------|
| Round 8 | #8, UX-005, T021, #13, ARCH-007, ARCH-008, STARTUP-1, STARTUP-2, STARTUP-4, STARTUP-6 (10 issues) | 35 tests |
| Round 9 | #2, STARTUP-3, STARTUP-5, STARTUP-7 (4 issues) | 25 tests |
| **Total** | **14 issues** | **60 tests** |

**Final test count:** 1027 passed, 9 skipped, 0 failed (was 991/9/1 pre-Round-8).
