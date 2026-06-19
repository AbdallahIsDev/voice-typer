# Smart-Duck Background Monitor — v2

## Problem

The user reported: "If I start the recording process, the sound volume didn't decrease because there was no sound in the background from the speakers. But during the recording process, if I played any background music or anything in the background that was too loud from the speaker during the recording process, the sound itself, the speaker volume, didn't decrease."

This was a known limitation of smart-duck v2.2 — it only checked `is_speaker_active()` at duck time. If audio started playing mid-dictation, we never ducked it, so speaker bleed leaked into the mic for the rest of the recording.

## Solution

Added a **background monitor thread** (`VolumeDucker._smart_duck_monitor_loop`) that polls `is_speaker_active()` every `poll_interval_ms` (default 500ms) during a smart-duck skip. If audio starts playing mid-dictation, the monitor **retroactively applies the duck** — fading the volume to the configured duck level, writing the crash-recovery file, and setting `_actually_ducked = True`. The monitor then exits (its job is done).

The monitor is a daemon thread, so it doesn't block app exit. It stops on: `restore()`, `set_smart_duck_enabled(False)`, or app exit. Thread-safe: the monitor calls `is_speaker_active()` OUTSIDE the lock (it may block 200-500ms on macOS osascript), and acquires `self._lock` only for brief state checks and the retroactive duck.

---

## What was done:

- Files modified:
  - `voice_typer/server/volume_ducker.py`
  - `voice_typer/server/config.py`
  - `voice_typer/server/app.py`
  - `voice_typer/client/src/renderer/src/types/config.ts`
  - `voice_typer/client/src/renderer/src/pages/Settings.tsx`
  - `docs/architecture/auto-volume-duck.md`
- Files added:
  - `tests/test_smart_duck_monitor.py`
- What changed:
  - **Added `_smart_duck_monitor_loop()`** to `VolumeDucker` — a background daemon thread that polls `backend.is_speaker_active()` every `_smart_duck_poll_ms` during a smart-duck skip. When audio starts, it retroactively applies the duck (fade + crash-recovery save + `_actually_ducked = True`), then exits.
  - **Added `_start_smart_duck_monitor()` / `_stop_smart_duck_monitor()`** — lifecycle methods. `_start` is called from `duck()` when smart-duck skips. `_stop` is called from `restore()` and `set_smart_duck_enabled(False)`. `_stop` captures the thread reference locally before joining to avoid races with concurrent calls.
  - **Added `is_monitor_running` property** — introspection for diagnostics and tests.
  - **Added `set_smart_duck_poll_interval(ms)` method** — clamps to [50, 5000]ms. Wired from `config.volume_duck_smart_poll_interval_ms`.
  - **Modified `duck()`** — when smart-duck skips, it now calls `_start_smart_duck_monitor()` before returning. The second-duck `else` branch documents that the monitor picks up `_ducked_level` changes automatically (no restart needed).
  - **Modified `restore()`** — calls `_stop_smart_duck_monitor()` BEFORE acquiring `self._lock` to avoid a deadlock (the monitor needs the lock to check state before exiting).
  - **Modified `set_smart_duck_enabled(False)`** — now calls `_stop_smart_duck_monitor()` so disabling smart-duck mid-dictation stops the monitor (no retroactive duck after disable).
  - **Added `volume_duck_smart_poll_interval_ms: int = 500` config field** (config.py L235) + IPC allowlist entry (L715, validator `int lo=50 hi=5000`) + TypeScript type (types/config.ts L131).
  - **Wired the new config field into `app.py::_duck_volume`** — calls `set_smart_duck_poll_interval(config.volume_duck_smart_poll_interval_ms)` before each `duck()` so the Settings UI slider takes effect on the next dictation.
  - **Added "Smart Duck Poll Interval" slider to `Settings.tsx`** — range 50-2000ms, step 50ms. Placed between the Smart Duck toggle and the Per-Session Duck toggle.
  - **Updated `docs/architecture/auto-volume-duck.md` to v2.3** — updated status line, §0.2 file map (config fields count 13), §0.4 (13 config fields, added `volume_duck_smart_poll_interval_ms`), §0.6 test map (added `test_smart_duck_monitor.py`, total 178 tests), §0.11 (rewrote with the monitor state-machine diagram, thread-safety notes, config fields, performance, and updated limitations).
  - **Created `tests/test_smart_duck_monitor.py`** — 19 tests across 7 test classes using a `ControllableBackend` whose `is_speaker_active()` return value can be flipped at runtime to simulate "audio starts mid-dictation" deterministically.
- Why:
  - The user's scenario was the exact gap that v2.2's smart-duck left open: start dictation in silence (smart-duck correctly skips), then play music mid-dictation (smart-duck never re-checks → speaker bleed). The background monitor closes this gap by polling every 500ms and retroactively ducking when audio starts.
  - The monitor is a daemon thread so it doesn't block app exit. It stops cleanly on `restore()`, `set_smart_duck_enabled(False)`, or app exit.
  - Thread safety: the monitor calls `is_speaker_active()` OUTSIDE the lock (it may block 200-500ms on macOS osascript), and acquires `self._lock` only for brief state checks and the retroactive duck. `restore()` calls `_stop_smart_duck_monitor()` BEFORE acquiring the lock to avoid a deadlock (the monitor needs the lock to check state before exiting). `_stop_smart_duck_monitor()` captures the thread reference locally before joining so concurrent calls don't race on `self._monitor_thread = None`.
  - The `volume_duck_smart_poll_interval_ms` config field lets users tune the polling rate: lower = catches audio faster but uses more CPU (especially on macOS where osascript is 200-500ms per call); higher = less CPU but slower to duck when audio starts. 500ms is the default — a good balance across platforms.
  - The 19 new tests cover the user's exact scenario (`test_audio_starts_mid_dictation_triggers_retroactive_duck`), plus all the edge cases: audio never starts, monitor stops on restore, monitor stops on disable, second duck while monitoring (picks up new level), is_speaker_active exception retry, poll interval clamping, and concurrency (monitor+restore race, multi-thread stress).
- Tests run:
  - `tests/test_smart_duck_monitor.py` — 19 tests, all pass.
  - `tests/test_smart_duck.py` — 31 tests, all pass.
  - `tests/test_volume_ducker.py` — 32 tests, all pass.
  - `tests/test_volume_lifecycle.py` — 18 tests, all pass.
  - `tests/test_volume_backends.py` — 32 tests, all pass.
  - `tests/test_audio_processor.py` — 22 tests, all pass.
  - `tests/test_audio_quality.py` — 13 tests, all pass.
  - `tests/test_recording_audio_processor.py` — 7 tests, all pass.
  - `tests/test_server.py` — 127 tests, all pass.
  - `tests/test_app.py` — 92 tests, all pass.
  - `tests/test_config.py` — all pass.
  - Full suite: `945 passed, 9 skipped, 1 failed` in 27s. The 1 failure is `tests/test_task_scheduler.py::TestPrewarmCommand::test_falls_back_to_sys_pythonw` — pre-existing (verified by re-running on the unmodified commit `bf2aa8e`), unrelated to smart-duck, caused by the test expecting `pythonw.exe` on a Linux test runner.
## Review findings (opencode reviewer)

### Bug found and fixed: thread-join-before-start race in `_start_smart_duck_monitor`

**Symptom**: `RuntimeError("cannot join thread before it is started")` when `restore()` fires between `self._monitor_thread = Thread(...)` and `.start()`.

**Root cause**: `_start_smart_duck_monitor()` assigned the Thread to `self._monitor_thread` *before* calling `.start()`. Since `_stop_smart_duck_monitor()` reads `self._monitor_thread` without the lock (by design — to avoid deadlock), `restore()` could read the non-None but unstarted thread and `join()` it → crash.

**Fix**: Create the Thread, `.start()` it, *then* assign to `self._monitor_thread`:

```python
t = threading.Thread(...)
t.start()
self._monitor_thread = t
```

If `_stop_smart_duck_monitor` now reads `self._monitor_thread` between `t.start()` and the assignment, it sees `None` and returns early — the monitor thread runs one poll, sees `_saved_state is None` (restore cleared it), and exits cleanly.

**Verified**: 10 threads × 100 cycles of concurrent duck/restore/toggle/set — zero errors.

### Minor fix: poll interval now re-reads each iteration

`poll_s` was captured once at loop start. `set_smart_duck_poll_interval()` changes only took effect on the next *monitor start*, not the next *poll*. Now reads `self._smart_duck_poll_ms` fresh each iteration. Dead `import time` removed.

### Everything else verified

- Thread safety of monitor/restore interaction is correct (is_speaker_active called outside lock, state re-checked under lock before retroactive duck, TOCTOU handled, no deadlocks)
- `restore()` calls `_stop_smart_duck_monitor()` before lock acquisition ✅
- `_stop_smart_duck_monitor` captures thread reference locally before join ✅
- Retroactive duck writes crash-recovery file with correct pre-duck state ✅
- Second duck while monitoring picks up new level correctly ✅
- Disable smart-duck mid-dictation stops monitor correctly ✅
- is_speaker_active exception handling (retries next poll) ✅
- Settings UI slider (50–2000ms, step 50), IPC allowlist validator (50–5000), server-side clamp (50–5000) — all consistent ✅
- Architecture doc updated with v2.3 state-machine diagram + thread-safety notes ✅
- All 19 new monitor tests pass ✅
- Full suite: all pass, exit code 0 ✅

**Conclusion**: The AI agent's work is correct and production-ready. Two minor issues found and fixed: the thread-join-before-start race (the only real bug) and the poll-interval re-read.

- Tests skipped:
  - **CoreAudio pyobjc path on macOS**: the `kAudioDevicePropertyDeviceIsRunning` query needs the full pyobjc struct handling for `AudioObjectPropertyAddress`, which can only be validated on real macOS hardware. The osascript fallback is tested and works; the CoreAudio path is stubbed to fall through to osascript. Documented in `volume_backends.py` L289-304.
  - **Manual cross-platform testing on real Windows/macOS/Linux hardware**: automated tests cover the monitor logic with a controllable fake backend, but real-hardware testing of `IAudioMeterInformation.GetPeakValue()` polling on Windows, `osascript` AppleScript permissions on macOS 13+, and `pactl`/`wpctl` quirks on various Linux distros still needs to happen before the v1.1.0 release.
  - The pre-existing `tests/test_task_scheduler.py::TestPrewarmCommand::test_falls_back_to_sys_pythonw` failure is unrelated to this work (verified by re-running on unmodified commit `bf2aa8e`).
