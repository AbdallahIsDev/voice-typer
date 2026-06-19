# Smart-Duck Review — v1

This document summarises the review of commit `8818b41` ("feat: smart duck — skip volume ducking when no audio is playing") and the fixes applied.

## Review findings

The local AI's commit added a smart-duck feature: skip the volume change when no application is currently playing audio. The implementation was Windows-only and had a critical correctness bug. Below are the issues found and the fixes applied.

### Issues found in commit 8818b41

#### Issue 1 — CRITICAL: second `duck()` call after smart-duck skip would fade volume with no saved state

**Location:** `voice_typer/server/volume_ducker.py` L209-214 (the `else` branch of `duck()`).

**Bug:** When smart-duck skips the first duck (no audio playing), `_saved_state` is set but `_actually_ducked=False`. If `duck()` is called again (e.g. config changed mid-dictation, or a second dictation starts before stop), the `else` branch runs `self._backend.fade_to(level, fade_ms)` — actually fading the user's volume down to the new duck level with **no saved state to restore from**. The user's volume would be stuck low.

**Fix:** The `else` branch now checks `_actually_ducked` first. If False (smart-duck skipped), it just updates the logical `ducked_level` and returns `True` without calling `fade_to()`. See `volume_ducker.py` L224-234.

**Regression test:** `tests/test_smart_duck.py::TestSmartDuckSecondDuckAfterSkip::test_second_duck_after_skip_does_not_fade`.

#### Issue 2 — CRITICAL: cross-platform gap (the user's main concern)

**Location:** `voice_typer/server/volume_backends.py` — only `WinVolumeBackend` overrode `is_speaker_active()`.

**Bug:** `VolumeBackend.is_speaker_active()` defaults to `True` (always duck). The local AI only implemented the Windows override (`IAudioMeterInformation.GetPeakValue()`). macOS and Linux backends inherited the default → smart-duck never skipped on those platforms → the feature was Windows-only.

**Fix:** Implemented `is_speaker_active()` for both `MacVolumeBackend` and `LinuxVolumeBackend`:

- **macOS** (`volume_backends.py` L256-345): Tries CoreAudio `kAudioDevicePropertyDeviceIsRunning` first (deferred — pyobjc struct handling needs real macOS testing). Falls back to `osascript` querying foreground apps and checking for known audio-producing apps (Spotify, Safari, Chrome, Firefox, Music, Podcasts, TV, QuickTime, VLC, YouTube, Netflix, Zoom, Teams, Discord, etc.). Conservative heuristic — ducks if any known audio app is running. Returns `True` (duck anyway) on any error.

- **Linux** (`volume_backends.py` L518-589): For `pactl` and `wpctl` tools, runs `pactl list sink-inputs` and looks for `State: running` (works on both PulseAudio and PipeWire via the compat layer). For `amixer` (ALSA-only) and as a `wpctl` fallback, scans `/proc/asound/card*/pcm*p/sub*/status` for `state: RUNNING` — the kernel-level signal that an audio stream is actively being rendered. Returns `True` (duck anyway) on any error.

**Tests:** `tests/test_smart_duck.py::TestLinuxIsSpeakerActive` (6 tests covering pactl running/corked/idle/empty, wpctl→ALSA fallback, amixer→ALSA), `TestLinuxAlsaProcfs` (3 tests covering RUNNING/IDLE/no-proc-asound), `TestMacIsSpeakerActive` (6 tests covering Spotify/Chrome/Zoom/TextEdit-only/osascript-failure/CoreAudio-fallthrough).

#### Issue 3 — MEDIUM: `restore()` didn't reset `_actually_ducked`

**Location:** `voice_typer/server/volume_ducker.py` `restore()` method.

**Bug:** After a normal `restore()` (following a real duck), `_actually_ducked` stayed `True` even though we were no longer ducked. This made the `actually_ducked` introspection property misleading.

**Fix:** Both restore paths (smart-duck-skip no-op and normal restore) now set `self._actually_ducked = False` before returning. See `volume_ducker.py` L269 and L302.

**Test:** `tests/test_smart_duck.py::TestSmartDuckNormal::test_restore_fades_back_after_normal_duck`.

#### Issue 4 — MEDIUM: no config toggle for smart-duck

**Bug:** Smart-duck was always-on with no way for users to disable it. Some users may want the original always-duck behaviour (e.g. if the smart-duck heuristic is too aggressive or too conservative on their setup).

**Fix:** Added `volume_duck_smart: bool = True` config field (`config.py` L227), wired it into the IPC allowlist (L706), the TypeScript config type (`types/config.ts` L130), the Settings UI (`Settings.tsx` "Smart Duck" toggle), and the app wiring (`app.py::_duck_volume` calls `set_smart_duck_enabled(config.volume_duck_smart)` before each `duck()`). Default ON matches the v2.2 behaviour; users can disable it to get the pre-smart-duck always-duck behaviour.

**Tests:** `tests/test_smart_duck.py::TestSmartDuckToggle` (3 tests: default-enabled, disabled-skips-is_speaker_active, runtime-toggle-takes-effect-on-next-duck).

#### Issue 5 — MEDIUM: no tests for the smart-duck feature itself

**Bug:** Commit 8818b41 only added `is_speaker_active() -> True` to the FakeBackend (so smart-duck never triggered in tests). No test verified the skip path, the `_actually_ducked` state, the restore-after-skip no-op, the crash-recovery-not-written invariant, or the second-duck bug.

**Fix:** Created `tests/test_smart_duck.py` with 31 tests across 7 test classes:
- `TestSmartDuckSkip` (4 tests): skip path, no crash-recovery file, restore is no-op, volume unchanged.
- `TestSmartDuckNormal` (2 tests): normal path fades, restore fades back.
- `TestSmartDuckSecondDuckAfterSkip` (2 tests): **the bugfix regression** + sanity check for normal second-duck.
- `TestSmartDuckToggle` (3 tests): default enabled, disabled bypasses is_speaker_active, runtime toggle.
- `TestLinuxIsSpeakerActive` (6 tests): pactl running/corked/idle/empty, wpctl→ALSA fallback, amixer→ALSA.
- `TestLinuxAlsaProcfs` (3 tests): RUNNING substream, IDLE substream, no /proc/asound.
- `TestMacIsSpeakerActive` (6 tests): Spotify/Chrome/Zoom running, TextEdit-only, osascript failure, CoreAudio fallthrough.
- `TestSmartDuckIntrospection` (3 tests): `actually_ducked` property, `smart_duck_enabled` property.
- `TestSmartDuckConcurrency` (1 test): concurrent duck+restore with smart-skip.

Also updated `FakeBackend` in both `tests/test_volume_ducker.py` and `tests/test_volume_lifecycle.py` to accept a `speaker_active` parameter and track `is_speaker_active_calls` count.

#### Issue 6 — LOW: architecture doc not updated

**Bug:** The doc didn't mention `is_speaker_active()`, `_actually_ducked`, smart-duck behaviour, or the new `volume_duck_smart` config field.

**Fix:** Updated `docs/architecture/auto-volume-duck.md` to v2.2:
- Added §0 AI Agent Quick Reference (was missing from the local AI's commit) with 11 subsections covering file map, lifecycle points, config fields, 12 critical gotchas (including the smart-duck second-duck bugfix), test map, platform matrix, IPC endpoints, extension guide, performance budget, and a smart-duck state-machine diagram.
- Updated status line to v2.2.
- Updated §0.4 config fields (12 fields, up from 11).
- Updated §0.5 gotchas (12 entries, up from 10 — added #11 second-duck bugfix and #12 is_speaker_active cost).
- Updated §0.6 test map (added `test_smart_duck.py`, total 159 tests up from 128).
- Updated §0.7 platform matrix (added Smart-duck column).
- Added §0.11 Smart-duck behaviour summary with ASCII state-machine diagram.

---

## What was done:

- Files modified:
  - `voice_typer/server/volume_ducker.py`
  - `voice_typer/server/volume_backends.py`
  - `voice_typer/server/app.py`
  - `voice_typer/server/config.py`
  - `voice_typer/client/src/renderer/src/types/config.ts`
  - `voice_typer/client/src/renderer/src/pages/Settings.tsx`
  - `tests/test_volume_ducker.py`
  - `tests/test_volume_lifecycle.py`
  - `docs/architecture/auto-volume-duck.md`
- Files added:
  - `tests/test_smart_duck.py`
- What changed:
  - **Fixed critical bug** in `volume_ducker.py`: second `duck()` call after smart-duck skip no longer calls `fade_to()` (would have faded user's volume with no saved state). The `else` branch now checks `_actually_ducked` first.
  - **Fixed `restore()` not resetting `_actually_ducked`** — both restore paths now set it to `False`.
  - **Added `_smart_duck_enabled` flag + `set_smart_duck_enabled()` method + `smart_duck_enabled` property** to `VolumeDucker` so smart-duck can be toggled at runtime from config.
  - **Added `actually_ducked` property** to `VolumeDucker` — distinguishes "logically ducked" (`is_ducked=True` during smart-duck skip) from "volume was actually changed" (`actually_ducked=True` only after a real fade).
  - **Implemented `is_speaker_active()` for `MacVolumeBackend`**: CoreAudio `kAudioDevicePropertyDeviceIsRunning` path (deferred to osascript per existing pattern) + osascript fallback that checks foreground apps for known audio-producing apps (Spotify, Safari, Chrome, Firefox, Edge, Music, Podcasts, TV, QuickTime, VLC, YouTube, Netflix, Disney, HBO, Plex, Audible, Amazon Music, Tidal, Deezer, OBS, Zoom, Teams, Discord, Slack, Meet, WebEx, Google Meet). Conservative — ducks if any known audio app is running. Returns `True` (duck anyway) on any error.
  - **Implemented `is_speaker_active()` for `LinuxVolumeBackend`**: `pactl list sink-inputs` (looks for `State: running`) for pactl/wpctl tools; `/proc/asound/card*/pcm*p/sub*/status` scan for `state: RUNNING` for amixer and as a wpctl fallback. Added `_alsa_is_playing()` helper. Returns `True` (duck anyway) on any error.
  - **Added `volume_duck_smart: bool = True` config field** (config.py L227) + IPC allowlist entry (L706) + TypeScript type (types/config.ts L130).
  - **Wired `volume_duck_smart` into `app.py::_duck_volume`** — calls `set_smart_duck_enabled(config.volume_duck_smart)` before each `duck()` so the Settings UI toggle takes effect on the next dictation without an app restart.
  - **Added "Smart Duck" toggle to `Settings.tsx`** Audio Enhancement section — between Duck Fade Duration and Per-Session Duck. Info tooltip explains the cross-platform behaviour.
  - **Added `Path` import to `volume_backends.py`** (needed by `_alsa_is_playing()`).
  - **Updated `FakeBackend` in `tests/test_volume_ducker.py` and `tests/test_volume_lifecycle.py`** — added `speaker_active` constructor parameter and `is_speaker_active_calls` counter so tests can simulate "no audio playing" and verify smart-duck queried the backend.
  - **Created `tests/test_smart_duck.py`** — 31 tests across 9 classes covering: skip path (no fade, no crash-recovery file, `_actually_ducked=False`, volume unchanged), normal path, the second-duck-after-skip bugfix, smart-duck toggle (default/disabled/runtime), cross-platform `is_speaker_active()` (Linux pactl/wpctl/amixer + `/proc/asound`, macOS osascript audio-app heuristic), introspection properties, concurrency.
  - **Updated `docs/architecture/auto-volume-duck.md` to v2.2** — added §0 AI Agent Quick Reference (11 subsections including smart-duck state-machine diagram), updated §0.4 config fields (12), §0.5 gotchas (12 — added second-duck bugfix + is_speaker_active cost), §0.6 test map (159 tests), §0.7 platform matrix (Smart-duck column), §0.10 performance budget (smart-duck check costs), §0.11 smart-duck behaviour summary.
- Why:
  - The second-duck bug was a silent volume-stuck-low risk — PortAudio swallows callback exceptions and the user would have no easy way to recover their volume. The fix is a 6-line guard that prevents the fade entirely.
  - The user explicitly said "I don't believe he reviewed it using all the other platforms (cross-platform)". The local AI's implementation only worked on Windows. macOS and Linux users would get no smart-duck benefit. The cross-platform implementations use the best available signal per platform: Windows `IAudioMeterInformation` (peak meter), Linux `pactl list sink-inputs` (sink-input states) + `/proc/asound` (kernel-level ALSA), macOS `osascript` (audio-app heuristic). All default to `True` (duck anyway) on error — never silently skip ducking when we should duck.
  - The `volume_duck_smart` config toggle gives users an escape hatch if the heuristic doesn't match their workflow (e.g. a DAW that doesn't show up in the audio-app list, or a Linux setup where `/proc/asound` isn't readable).
  - The 31 new tests catch all six issues found in review — the second-duck bug, the restore-doesn't-reset-`_actually_ducked` issue, the cross-platform gaps, and the toggle behaviour. Future regressions will fail fast.
  - The doc update ensures future AI agents reading the architecture doc immediately understand smart-duck, the `_actually_ducked`/`is_ducked` distinction, the second-duck invariant, and the cross-platform signals.
- Tests run:
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
  - Full suite: `926 passed, 9 skipped, 1 failed` in 25s. The 1 failure is `tests/test_task_scheduler.py::TestPrewarmCommand::test_falls_back_to_sys_pythonw` — pre-existing (verified by `git stash` + re-run on the unmodified commit `8818b41`), unrelated to smart-duck, caused by the test expecting `pythonw.exe` on a Linux test runner.
- Tests skipped:
  - **CoreAudio pyobjc path on macOS**: the `kAudioDevicePropertyDeviceIsRunning` query needs the full pyobjc struct handling for `AudioObjectPropertyAddress`, which can only be validated on real macOS hardware. The osascript fallback is tested and works; the CoreAudio path is stubbed to fall through to osascript (matching the existing `_get_default_output_device` pattern). Documented in `volume_backends.py` L289-304.
  - **Manual cross-platform testing on real Windows/macOS/Linux hardware**: automated tests cover the parsing logic and lifecycle, but real-hardware testing of `IAudioMeterInformation.GetPeakValue()` on Windows, `osascript` AppleScript permissions on macOS 13+, and `pactl`/`wpctl` quirks on various Linux distros still needs to happen before the v1.1.0 release. Documented as ⏳ in architecture doc §14 item 24.
  - **RNNoise in-callback performance test**: RNNoise is not installed in the CI environment. Documented in architecture doc §10.3.
  - The pre-existing `tests/test_task_scheduler.py::TestPrewarmCommand::test_falls_back_to_sys_pythonw` failure is unrelated to this work (verified by re-running on unmodified commit `8818b41`).
