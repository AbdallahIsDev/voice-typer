# Auto-Volume Duck + Noise Filtering: System Audio & Mic Path Management During Dictation

**Status:** Implemented (v2.3 — adds smart-duck background monitor: retroactively ducks when audio starts mid-dictation) · **Priority:** High · **Target:** v1.1.0

---

## 0. AI Agent Quick Reference (read this first)

> **Start here.** This section is a dense cheat sheet — everything a future AI agent needs to understand the feature in ~2 minutes. The full sections below (§1–§16) are the deep-dive reference; read them only if you need detail beyond what's here.

### 0.1 What this feature does (one paragraph)

When dictation starts, system volume is **reduced** to a configurable level (default 25% perceptual) — **but only if audio is actually playing** (smart-duck, v2.2) — and a **mic-path noise filter** (high-pass + noise gate, optional RNNoise + post-capture spectral gating) cleans the microphone signal. When dictation stops, the original volume is **restored exactly** (including mute state) with a short fade ramp — or skipped entirely if smart-duck skipped the duck. Two layers, one goal: clean audio in + clean audio out. Eliminates speaker-to-mic bleed (ducking) and residual fan/keyboard/HVAC noise (filtering).

### 0.2 File map (where things live)

| File | Role | Key lines |
|---|---|---|
| `voice_typer/server/volume_backend.py` | `VolumeBackend` ABC + `VolumeState` dataclass (frozen). All volumes exchanged in perceptual-linear [0.0, 1.0]. **`is_speaker_active()` default returns True** (always duck) — backends override. | `VolumeBackend` L40, `VolumeState` L28, `fade_to` L99, `is_speaker_active` L134 |
| `voice_typer/server/volume_backends.py` | Concrete backends: `WinVolumeBackend` (pycaw + IAudioMeterInformation), `MacVolumeBackend` (CoreAudio→osascript + audio-app heuristic), `LinuxVolumeBackend` (pactl/wpctl/amixer + /proc/asound). All imports guarded. | Win L32, Mac L200, Linux L461; `is_speaker_active`: Win L122, Mac L256, Linux L518, `_alsa_is_playing` L562 |
| `voice_typer/server/volume_ducker.py` | `VolumeDucker` orchestrator. Thread-safe (`self._lock`). Manual-override detection (5% threshold). Crash-recovery hook. **Smart-duck** (v2.2): skips fade when `is_speaker_active()` is False; `_actually_ducked` flag distinguishes logical vs actual duck. | `duck` L160, `restore` L242, `initialize` L100, `is_ducked` L308, `actually_ducked` L314, `smart_duck_enabled` L325, `set_smart_duck_enabled` L329 |
| `voice_typer/server/duck_crash_recovery.py` | Persists pre-duck state to `duck_crash_recovery.json` (atomic temp+rename, 0o600 on POSIX). | `save` L46, `load_stale` L70, `clear` L91 |
| `voice_typer/server/audio_processor.py` | `AudioProcessor` — real-time chain (high-pass → noise gate → RNNoise → quality callback) + post-capture noisereduce. **Handles both 1-D and 2-D `(frames,1)` input.** | `process_chunk` L170, `_apply_highpass` L202, `process_full_audio` L296, `set_quality_callback` L160 |
| `voice_typer/server/recording.py` | `Recorder` — injects `AudioProcessor`, calls `process_chunk` in callback (BEFORE lock-block), `process_full_audio` in `stop()`. | callback L361, `process_chunk` call L401, `stop` L657 |
| `voice_typer/server/app.py` | `VoiceTyperApp` — wires everything. 6 lifecycle points. AudioQualityAnalyzer revival. **Smart-duck config sync** (`_duck_volume` calls `set_smart_duck_enabled` from `config.volume_duck_smart` before each duck). | See §0.3 below |
| `voice_typer/server/audio_quality.py` | `AudioQualityAnalyzer` — was DEAD-014, now revived. Accumulates clipping/low-volume/high-noise stats. | `analyze_full_audio` L95, `reset` L63 |
| `voice_typer/server/platform.py` | `get_volume_backend()` factory — selects backend by `sys.platform`. | L17 |
| `voice_typer/server/ipc_server.py` | `get_volume_backend_status` IPC endpoint — returns `{available, name, supports_per_session, is_windows}` for the Settings UI. | L763 |
| `voice_typer/server/config.py` | 13 new config fields (6 volume incl. `volume_duck_smart` + `volume_duck_smart_poll_interval_ms` + 7 noise) + IPC allowlist. | fields L215–235, allowlist L709–715 |
| `voice_typer/client/.../Settings.tsx` | "Audio Enhancement" section: backend status, 6 duck controls (incl. Smart Duck toggle + poll interval slider), 7 noise controls. | Audio Enhancement section |
| `voice_typer/client/.../types/config.ts` | TypeScript mirror of the 13 config fields. | L125–132 |

### 0.3 The 6 lifecycle wiring points in `app.py`

These are the **only** places volume duck/restore is called. If you change lifecycle behaviour, change it here — not in the ducker.

| # | Method | Line | What it does | Duck/Restore call |
|---|---|---|---|---|
| 1 | `__init__` | L283 | Construct `AudioProcessor` (L297), `AudioQualityAnalyzer` (L310), `DuckCrashRecovery(config_dir=_config_dir())` (L364), `VolumeDucker(crash_recovery=..., on_crash_restore=...)` (L365). Crash-recovery stale-file check runs on first `initialize()`. | — |
| 2 | `_start_dictation` | L1197 | Reset `AudioQualityAnalyzer` (L1281). After `recorder.start()` (L1290) + bubble.show() (L1294), call `self._duck_volume()` (L1297). **`_duck_volume` syncs `config.volume_duck_smart` → `set_smart_duck_enabled()` before calling `duck()`.** | `_duck_volume` → L414 |
| 3 | `_stop_dictation` | L1373 | After `recorder.stop()` (L1398), call `self._restore_volume()` (L1406). Then run `_finalize_audio_quality_report(audio)` (L1420) — surfaces clipping/low-volume/noise warnings via tray. Restore BEFORE transcription thread so user gets audio back ASAP. **If smart-duck skipped the duck, restore is a no-op.** | `_restore_volume` → L435 |
| 4 | `_cancel_dictation` | L1689 | Discard recorder (L1705). Call `self._restore_volume()` (L1712). BUGFIX comment at L1700 documents the removed `_background_audio_monitor.stop()` AttributeError. | `_restore_volume` → L435 |
| 5 | `quit` | L2118 | After `recorder.discard()` (L2143), call `self._restore_volume(fade_ms=0)` (L2150). **fade_ms=0** = instant restore on exit. | `_restore_volume(0)` → L435 |
| 6 | `restart_app` | L2001 | Call `self._restore_volume(fade_ms=0)` (L2029) BEFORE `subprocess.Popen` (L2070). Prevents volume ping-pong between old and new process. | `_restore_volume(0)` → L435 |

### 0.4 The 13 config fields

```python
# Volume ducking (config.py L215–235)
volume_duck_enabled: bool = True              # master toggle
volume_duck_level: float = 0.25               # 0.0–1.0 perceptual
volume_duck_per_session: bool = False         # Windows only — duck other apps, keep alerts
volume_duck_fade_ms: int = 150                # 0–1000, 0 = instant
volume_duck_smart: bool = True                # v2.2: skip duck when no audio playing
volume_duck_smart_poll_interval_ms: int = 500 # v2.3: monitor poll interval, 50–5000

# Noise filtering (config.py L223–229)
noise_filter_enabled: bool = True
noise_filter_highpass: bool = True
noise_filter_highpass_cutoff_hz: float = 80.0 # 20–500
noise_filter_gate: bool = True
noise_filter_gate_threshold: float = 0.015    # 0.0–0.1, ~-45dBFS
noise_filter_rnnoise: bool = False            # opt-in (CPU cost)
noise_filter_post_capture: bool = True        # noisereduce on stop()
```

Plus the pre-existing `audio_quality_warnings: bool = True` (config.py L159) which gates the tray notifications from `_finalize_audio_quality_report`.

All 13 are in `IPC_CONFIG_ALLOWLIST` (config.py L709–715 + L717–723) with validators, so the Electron UI can set them live via `set_config`.

### 0.5 Critical gotchas (do NOT break these invariants)

1. **Callback ordering in `recording.py` (L386–L417):** `filtered` MUST be assigned BEFORE the `with self._lock` block that uses it. v1 had it after — raised silent `NameError` on every chunk (PortAudio swallows callback exceptions → recording captured nothing). Regression-tested in `tests/test_recording_audio_processor.py::test_callback_does_not_raise_with_processor`.

2. **High-pass filter input shape (`audio_processor.py` L202):** `_apply_highpass` ravels 2-D `(frames, 1)` input to 1-D before `scipy.lfilter`, then reshapes back. sounddevice delivers 2-D for mono captures; without ravel, lfilter raises `ValueError: object of too small depth for desired array`.

3. **Mute state preservation (`volume_ducker.py` L292–L295):** `restore()` re-applies the saved mute state AFTER the volume fade completes — fading a muted device is a no-op, so order matters. v1 unmuted users on restore (bug fixed).

4. **Manual-volume-override detection (`volume_ducker.py` L279):** if the current volume differs from the ducked level by >5%, restore to the CURRENT value (user changed it intentionally), not the saved one. Use `force=True` to bypass (crash-recovery path).

5. **Per-session duck is Windows-only:** gated on `backend.supports_per_session`. The Settings UI also auto-disables the toggle when `!is_windows || !supports_per_session`. Don't try to add per-session on macOS/Linux — there's no clean native API (see §5.2, §5.3).

6. **Crash-recovery file is written on duck(), cleared on restore():** if the app crashes while ducked, the next launch's `VolumeDucker.initialize()` finds the stale `duck_crash_recovery.json` and restores the saved volume + fires the `on_crash_restore` callback (which calls `tray.notify`). **Smart-duck skip does NOT write the file** — we didn't change the volume, so there's nothing to recover from.

7. **`fade_ms=0` on quit/restart:** the app is exiting; don't spend 150ms on a fade. Restore instantly. Also: on `restart_app`, restore happens BEFORE `subprocess.Popen` to avoid volume ping-pong between old and new process.

8. **Audio callback MUST be non-blocking:** `process_chunk` runs in the PortAudio callback. High-pass ≈ 0.05ms/chunk, noise gate ≈ 0.02ms — safe. RNNoise ≈ 1ms/frame is borderline; default OFF. If you add a new filter, benchmark it. Noisereduce runs ONLY in `stop()` (offline) — NEVER in the callback.

9. **All optional library imports are guarded:** `pycaw`, `pyobjc-CoreAudio`, `noisereduce`, `rnnoise`, `scipy` — if missing, that filter/backend is silently skipped (logged once at INFO/DEBUG). The app never crashes on a missing optional dep. Don't add unguarded imports.

10. **`DuckCrashRecovery` uses `_config_dir()`** (not `Path.home()` directly) — so tests can monkeypatch `_config_dir` to a tmp_path. v1 used `Path.home() / ".voice-typer"` directly and leaked test artifacts into the developer's home directory.

11. **Smart-duck second-`duck()` bugfix (v2.2, `volume_ducker.py` L224–L234):** if smart-duck skipped the first duck (`_actually_ducked=False`) and `duck()` is called again (e.g. config changed mid-dictation), the `else` branch must NOT call `fade_to()` — doing so would fade the user's volume down to the new duck level with no saved state to restore from. The fix: the `else` branch checks `_actually_ducked` first and skips the fade if False. Regression-tested in `tests/test_smart_duck.py::TestSmartDuckSecondDuckAfterSkip`.

12. **Smart-duck `is_speaker_active()` must be cheap:** it's called on every `duck()` (at dictation start). Windows uses `IAudioMeterInformation.GetPeakValue()` (~0ms COM call). macOS uses `osascript` (200–500ms — borderline, but only fires once per dictation). Linux uses `pactl list sink-inputs` (subprocess, ~50ms). All paths default to `True` (duck anyway) on any error — never silently skip ducking when we should duck.

### 0.6 Test map

| Test file | Count | Covers |
|---|---|---|
| `tests/test_volume_ducker.py` | 32 | `VolumeDucker` lifecycle, mute state, manual override, concurrency (cancel+stop race), crash recovery, `DuckCrashRecovery` file I/O |
| `tests/test_audio_processor.py` | 22 | High-pass attenuation, noise gate, RNNoise (mocked), post-capture, quality callback, passthrough, dtype preservation |
| `tests/test_audio_quality.py` | 13 | `AudioQualityAnalyzer` chunk analysis, full-audio report, properties |
| `tests/test_volume_backends.py` | 32 | Linux (pactl/wpctl/amixer parsing + tool detection), macOS (osascript fallback), Windows (smoke — init fails gracefully without pycaw), `VolumeBackend.fade_to` default impl |
| `tests/test_volume_lifecycle.py` | 18 | **Integration:** start→duck, stop→restore, cancel→restore, quit→restore(0ms), restart→restore-before-subprocess, crash-recovery-on-startup, manual override, per-session gated on support, crash-recovery file write/clear |
| `tests/test_recording_audio_processor.py` | 7 | **Regression:** callback with AudioProcessor doesn't raise NameError, buffer stores filtered audio (high-pass attenuates 30Hz), RMS callback fires with filtered values, quality callback fires per chunk, post-capture runs in stop(), no-processor graceful degradation, xrun status doesn't break callback |
| `tests/test_smart_duck.py` | 31 | **v2.2:** smart-duck skip path (no fade, no crash-recovery file, `_actually_ducked=False`), normal path (fade proceeds), restore-after-skip is no-op, volume unchanged after skip, **second-duck-after-skip bugfix**, smart-duck toggle (`set_smart_duck_enabled`), cross-platform `is_speaker_active()` (Linux pactl/wpctl/amixer + /proc/asound, macOS osascript audio-app heuristic), introspection properties, concurrency |
| `tests/test_smart_duck_monitor.py` | 19 | **v2.3:** background monitor lifecycle (starts on skip, stops on restore/disable), retroactive duck (audio-starts-mid-dictation, crash-recovery written, audio-never-starts no-op, monitor exits after duck), disable-mid-dictation, second-duck-while-monitoring (picks up new level), is_speaker_active exception retry, poll interval clamping, concurrency (monitor+restore race, multi-thread stress) |
| `tests/test_server.py::TestDispatchGetVolumeBackendStatus` | 4 | IPC endpoint returns backend name/availability, calls initialize(), handles missing `_volume_ducker`, handles initialize() exception |

**Total: 178 tests.** Run the full volume/audio suite with:
```bash
python -m pytest tests/test_volume_ducker.py tests/test_audio_processor.py tests/test_audio_quality.py \
  tests/test_volume_backends.py tests/test_volume_lifecycle.py tests/test_recording_audio_processor.py \
  tests/test_smart_duck.py tests/test_smart_duck_monitor.py tests/test_server.py \
  -k "volume or audio or backend or duck or filter or quality or smart"
```

### 0.7 Platform support matrix

| Platform | Backend | Library | Per-session? | Smart-duck? | Notes |
|---|---|---|---|---|---|
| Windows | `WinVolumeBackend` | `pycaw` + `comtypes` | ✅ (`ISimpleAudioVolume`) | ✅ `IAudioMeterInformation.GetPeakValue()` (peak ≥ 0.01 = active) | Uses `SetMasterVolumeLevelScalar` (perceptual-linear), NOT `SetMasterVolumeLevel` (dB). Per-session default OFF — opt in. pycaw ≥ 20251023 API compat (EndpointVolume property vs Activate). |
| macOS | `MacVolumeBackend` | `pyobjc-framework-CoreAudio` (primary) / `osascript` (fallback) | ❌ | ⚠️ osascript audio-app heuristic (Spotify/Safari/Chrome/etc. running → assume active) | CoreAudio `kAudioDevicePropertyDeviceIsRunning` path deferred (pyobjc struct handling needs real macOS testing). osascript fallback is conservative — ducks if any known audio app is running. |
| Linux | `LinuxVolumeBackend` | `pactl` → `wpctl` → `amixer` (system binaries, no Python deps) | ❌ | ✅ `pactl list sink-inputs` (State: running) + `/proc/asound` fallback for ALSA-only | Detection priority: pactl (PulseAudio/PipeWire compat) → wpctl (PipeWire native) → amixer (ALSA fallback). Smart-duck: pactl checks sink-input states; ALSA scans `/proc/asound/card*/pcm*p/sub*/status` for `state: RUNNING`. |

If no backend is available: `initialize()` returns False, `duck()`/`restore()` are no-ops, Settings UI shows "disabled". App continues normally. Smart-duck also no-ops (no backend to query).

### 0.8 IPC endpoints

| Endpoint | Request | Response | Notes |
|---|---|---|---|
| `get_volume_backend_status` | `{type: "get_volume_backend_status"}` | `{type: "volume_backend_status", data: {available, name, supports_per_session, is_windows}}` | Calls `ducker.initialize()` so the name reflects the real backend. Safe to call before first dictation. |

Plus the existing `get_config` / `set_config` (all 12 new fields are in the allowlist). The Settings UI calls `get_volume_backend_status` on mount to populate the backend status indicator and gate the Per-Session Duck toggle.

### 0.9 How to extend

**Add a new noise filter:**
1. Add a config field + allowlist entry in `config.py`.
2. Add a field to `AudioProcessorConfig` (audio_processor.py L50) + `from_config` (L66).
3. Implement the filter in `AudioProcessor` — call it from `process_chunk` (L170) for real-time, or from `process_full_audio` (L296) for offline.
4. **Benchmark it** — real-time filters MUST be <2ms/chunk to avoid xruns.
5. Add a Settings UI toggle/slider in `Settings.tsx`.
6. Add tests in `tests/test_audio_processor.py`.

**Add a new volume backend (new platform):**
1. Subclass `VolumeBackend` (volume_backend.py L40) in `volume_backends.py`.
2. Implement `name`, `supports_per_session`, `initialize`, `get_state`, `set_linear`. `fade_to` has a working default. **Override `is_speaker_active()`** if the platform has a cheap speaker-activity signal — otherwise the default `True` means smart-duck never skips.
3. Add the platform to `get_volume_backend()` in `platform.py` (L17).
4. Add platform-conditional deps to `pyproject.toml`.
5. Add tests in `tests/test_volume_backends.py` — mock the platform's CLI/library.

**Add a new lifecycle wiring point:**
1. Read §0.3 above — there are only 6, and they're the only sanctioned places to call `duck()`/`restore()`.
2. If you need a new one (e.g. a "pause" feature), add it to `app.py` and call `self._duck_volume()` / `self._restore_volume()` — don't call the ducker directly.
3. Add an integration test in `tests/test_volume_lifecycle.py`.

### 0.10 Performance budget

| Component | Cost | Where | Notes |
|---|---|---|---|
| High-pass filter | ~0.05ms/chunk | `process_chunk` (audio callback) | scipy `lfilter` on 1024 samples |
| Noise gate | ~0.02ms/chunk | `process_chunk` (audio callback) | numpy RMS + in-place fill |
| RNNoise | ~1ms/480-sample frame | `process_chunk` (audio callback) | **Borderline** — default OFF. If xruns appear, move to consumer thread. |
| Post-capture noisereduce | ~200ms for 30s audio | `Recorder.stop()` (offline) | Never in callback. |
| Volume fade | 150ms default (10 steps × 15ms) | `VolumeDucker.duck/restore` (background thread) | Synchronous — caller blocks. Acceptable because caller is a background thread. |
| Smart-duck check (Windows) | ~0ms | `duck()` → `IAudioMeterInformation.GetPeakValue()` | COM call on already-held pointer. |
| Smart-duck check (macOS) | 200–500ms | `duck()` → `osascript` subprocess | Borderline but only fires once per dictation. |
| Smart-duck check (Linux) | ~50ms | `duck()` → `pactl list sink-inputs` subprocess | Or `/proc/asound` scan (~5ms) for ALSA-only. |

### 0.11 Smart-duck behaviour summary (v2.3)

Smart-duck skips the volume change when no application is currently playing audio through the speakers. This avoids a pointless speaker-icon animation during silent dictation. **A background monitor (v2.3) retroactively ducks if audio starts playing mid-dictation** — closing the gap where speaker bleed could leak into the mic.

**State machine (with monitor):**

```
duck() called
    │
    ▼
┌─────────────────────────────┐
│ smart_duck_enabled?         │
│ (config.volume_duck_smart)  │
└──────────┬──────────────────┘
           │
      ┌────┴────┐
      │ Yes     │ No
      ▼         ▼
┌──────────┐  ┌─────────────────────────┐
│ backend. │  │ Skip is_speaker_active()│
│ is_      │  │ → proceed to normal duck│
│ speaker_ │  └─────────────────────────┘
│ active()?│
└────┬─────┘
     │
  ┌──┴──┐
  │ Yes │ No
  ▼     ▼
┌─────────────┐  ┌──────────────────────────────────┐
│ Normal duck │  │ Smart-duck SKIP:                 │
│ (fade, save │  │  • _saved_state = state          │
│  crash-rec) │  │  • _actually_ducked=False        │
│ _actually_  │  │  • NO fade_to() call             │
│ ducked=True │  │  • NO crash-rec file             │
│             │  │  • START background monitor      │
│             │  │    (polls every poll_interval_ms)│
└─────────────┘  └──────────────────────────────────┘
                              │
                              ▼ (monitor running)
                   ┌──────────────────────────┐
                   │ Monitor polls            │
                   │ is_speaker_active()      │
                   │ every poll_interval_ms   │
                   └────────┬─────────────────┘
                            │
                   ┌────────┴────────┐
                   │ Audio started?  │
                   └────────┬────────┘
                     Yes    │    No
              ┌─────────────┴────────────┐
              ▼                          ▼
   ┌─────────────────────┐    ┌────────────────────┐
   │ Retroactive duck:   │    │ Keep polling       │
   │  • fade_to(level)   │    │ (until restore()   │
   │  • _actually_ducked │    │  or disable)       │
   │    = True           │    └────────────────────┘
   │  • write crash-rec  │
   │  • monitor EXITS    │
   │    (job done)       │
   └─────────────────────┘

restore() called (at any point)
    │
    ▼
┌─────────────────────────────────────────┐
│ 1. Stop the monitor (signal + join)     │
│ 2. Acquire _lock                        │
│ 3. _actually_ducked?                    │
│    True  → fade back, clear crash-rec   │
│    False → no-op (smart-duck skipped    │
│            AND monitor never ducked)    │
│ 4. Clear _saved_state, _actually_ducked │
└─────────────────────────────────────────┘
```

**Key invariants:**
- `is_ducked` returns `True` during a smart-duck skip (logical state — UI shows duck state consistently).
- `actually_ducked` returns `False` during a smart-duck skip (distinguishes "skipped" from "actually faded"). Flips to `True` if the monitor retroactively ducks.
- `is_monitor_running` returns `True` while the background monitor thread is alive.
- No crash-recovery file is written during a smart-duck skip — it's only written when the duck actually happens (either at duck time, or retroactively by the monitor).
- The monitor is a **daemon thread** — won't block app exit.
- The monitor exits automatically after a successful retroactive duck (its job is done).
- The monitor stops on: `restore()`, `set_smart_duck_enabled(False)`, or app exit.
- The second `duck()` call after a smart-duck skip must NOT call `fade_to()` (v2.2 bugfix — see §0.5 gotcha #11). The monitor picks up the new `_ducked_level` on its next poll.

**Thread safety:**
- The monitor calls `is_speaker_active()` OUTSIDE `self._lock` (it may block 200-500ms on macOS osascript).
- The monitor acquires `self._lock` only for brief state checks and the retroactive duck.
- `restore()` calls `_stop_smart_duck_monitor()` BEFORE acquiring `self._lock` — this avoids a deadlock (the monitor needs the lock to check state before exiting).
- `_stop_smart_duck_monitor()` captures the thread reference locally before joining, so concurrent calls don't race on `self._monitor_thread = None`.

**Config fields:**
- `volume_duck_smart: bool = True` — master toggle for smart-duck (skip + monitor).
- `volume_duck_smart_poll_interval_ms: int = 500` — monitor polling interval, clamped to [50, 5000]. Lower = catches audio faster but uses more CPU. Higher = less CPU but slower to duck when audio starts.

**Performance:**
- Windows: `IAudioMeterInformation.GetPeakValue()` is ~0ms, so even 50ms polling is cheap.
- macOS: `osascript` is 200-500ms per call — 500ms polling means the backend is queried ~once per second (one call + one sleep). Don't set the interval below 200ms on macOS or the calls will overlap.
- Linux: `pactl list sink-inputs` is ~50ms — 500ms polling is very comfortable.

**Limitations:**
- The monitor only polls at `poll_interval_ms` granularity. Audio that starts and stops within one poll interval may be missed. This is an acceptable trade-off — sub-500ms audio bursts are rare and the speaker bleed from a 500ms burst is minimal.
- Once the monitor retroactively ducks, it stays ducked until `restore()` — even if audio stops again. Un-ducking mid-dictation would be jarring and would let speaker bleed return.

---

## 1. Executive Summary

When the user starts recording dictation, system volume is **reduced** (default to 25% perceptual) and a **mic-path noise filter** (high-pass + noise gate, optional RNNoise + post-capture spectral gating) cleans the microphone signal. When recording stops, the original volume is **restored exactly** (including mute state) with a short fade ramp.

This eliminates two problems at once:

1. **Ambient speaker output** (YouTube, movies, music, notifications) leaks into the microphone → bad transcripts + user can't focus.
2. **Residual background noise** (HVAC, fans, keyboard, room reverb) that ducking can't silence — the mic still hears it even when speakers are quiet.

**Two layers, one goal: clean audio in + clean audio out.** No AI subtraction, no loopback capture, no over-engineering.

### What changed since v1 of this doc

v1 ducked master volume to 10% and called it done. v2 fixes:

- **Cross-platform unit normalization** (dB vs linear % — v1 was 10× quieter on Windows than macOS).
- **Mute-state save/restore** (v1 unmuted users on restore — bug).
- **Crash recovery** (v1 left volume stuck at 10% if app crashed mid-dictation).
- **Fade ramp** (v1 caused audio clicks/pops on abrupt transitions).
- **Per-session duck on Windows** (v1 killed system alerts and other apps' audio).
- **`quit()` wiring gap** (v1 never restored volume on app quit while recording).
- **Manual-volume-override detection** (v1 slammed volume back even if user intentionally changed it).
- **`_background_audio_monitor` bug** (live `AttributeError` in `_cancel_dictation` — v1 missed it).
- **Added the missing noise filter layer** (v1 assumed ducking was enough; it isn't for fan/keyboard/HVAC noise).
- **Backend abstraction** (v1 inlined `if sys.platform` checks; v2 uses the same ABC pattern as `platform.py`).

---

## 2. Why Not Alternative Approaches

| Approach | Problem | Verdict |
|---|---|---|
| **RNNoise / noisereduce alone** | Cleans mic feed but user still hears loud movie — can't focus. Doesn't stop speaker-to-mic bleed at the source. | Complementary, not replacement. Ship alongside ducking. |
| **Loopback audio subtraction** | Subtract speaker output from mic input. Complex, fragile, no standard cross-platform API, phase alignment is hell. | Over-engineered. Reject. |
| **Tier 1 "background sound" banner** | Nags the user about something they already know. | Redundant once duck works. Remove. |
| **Manual mute** | User must remember to turn volume down/up. | Poor UX, easy to forget. |
| **Ducking only (v1 of this doc)** | Handles speaker bleed but not fan/keyboard/HVAC noise. Master-volume-only kills system alerts. No crash recovery. | Foundation, but incomplete. |
| **Whisper's built-in `vad_filter`** | Already used on some transcription paths (transcription.py:459, 617). Runs inside Whisper, not real-time, can't feed the waveform visualizer. | Keep for transcription. Add a separate real-time VAD for the visualizer + silence auto-stop. |

**Ducking + noise filtering is the correct first-principles fix.** Ducking silences the speakers (user can think, mic hears less bleed). Noise filtering cleans whatever residual leaks through plus fan/keyboard/HVAC that ducking can't touch. Both together give maximum audio quality.

---

## 3. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              VoiceTyperApp                                  │
│                                                                             │
│  ┌─────────────────────────┐         ┌──────────────────────────────────┐   │
│  │  _start_dictation()     │         │          VolumeDucker            │   │
│  │  _stop_dictation()      │────────▶│                                  │   │
│  │  _cancel_dictation()    │         │  duck(level)  ──▶ save+fade↓     │   │
│  │  quit()                 │         │  restore()    ──▶ fade↑+restore  │   │
│  └────────────┬────────────┘         │  backend: VolumeBackend (ABC)    │   │
│               │                      │   ├─ WinVolumeBackend (pycaw)    │   │
│               │                      │   ├─ MacVolumeBackend (CoreAudio)│   │
│               │                      │   └─ LinuxVolumeBackend          │   │
│               │                      │       (pactl→wpctl→amixer)       │   │
│               │                      └──────────────┬───────────────────┘   │
│               │                                     │                       │
│               │  wires callbacks                    ▼                       │
│               ▼                              ┌──────────────┐               │
│  ┌─────────────────────────┐                 │ CrashRecovery│               │
│  │        Recorder         │                 │  (duck state │               │
│  │  ┌───────────────────┐  │                 │   persisted) │               │
│  │  │ PortAudio callback│  │                 └──────────────┘               │
│  │  └─────────┬─────────┘  │                                                │
│  │            ▼            │                                                │
│  │  ┌───────────────────┐  │                                                │
│  │  │  AudioProcessor   │  │  ◀── NEW: noise filter layer                  │
│  │  │  ├─ HighPassFilter│  │                                                │
│  │  │  ├─ NoiseGate     │  │                                                │
│  │  │  ├─ RNNoise (opt) │  │                                                │
│  │  │  └─ QualityDetect │  │                                                │
│  │  └─────────┬─────────┘  │                                                │
│  │            ▼            │                                                │
│  │       buffer            │                                                │
│  │            │            │                                                │
│  │  stop()    ▼            │                                                │
│  │  ┌───────────────────┐  │                                                │
│  │  │ PostCaptureFilter │  │  ◀── noisereduce (offline, safety net)        │
│  │  │  (noisereduce)    │  │                                                │
│  │  └─────────┬─────────┘  │                                                │
│  └────────────┼────────────┘                                                │
│               ▼                                                             │
│        DictationPipeline (transcription thread)                             │
│               │                                                             │
│               ▼                                                             │
│        Whisper / Qwen / Parakeet (with vad_filter=True)                     │
└─────────────────────────────────────────────────────────────────────────────┘
```

**Key principle:** `VolumeDucker` and `AudioProcessor` are **independent** components. Ducking is driven by `VoiceTyperApp` at dictation lifecycle points. The audio processor is wired into `Recorder` and runs on every chunk. Neither knows about the other. They compose.

---

## 4. Component: VolumeDucker

### 4.1 Backend Abstraction

```python
# voice_typer/server/volume_backend.py
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class VolumeState:
    """Snapshot of system volume for save/restore.

    `linear` is perceptual-linear (0.0=silent, 1.0=max) — the same scale
    used by the duck level. Backends convert to/from their native units.
    """
    linear: float          # 0.0–1.0 perceptual volume
    muted: bool            # True if system is muted


class VolumeBackend(ABC):
    """Abstract platform volume controller.

    All volumes are exchanged in perceptual-linear scale [0.0, 1.0].
    Backends handle conversion to dB / percent / native units.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable backend identifier, e.g. 'pycaw (WASAPI)'."""

    @property
    @abstractmethod
    def supports_per_session(self) -> bool:
        """True if the backend can duck individual audio sessions
        (Windows ISimpleAudioVolume). False = master-volume only."""

    @abstractmethod
    def initialize(self) -> bool:
        """Set up the backend. Return False if unavailable (no device,
        missing library, permission denied). Must be safe to call once."""

    @abstractmethod
    def get_state(self) -> Optional[VolumeState]:
        """Read current volume + mute. None on failure."""

    @abstractmethod
    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
        """Set volume in perceptual-linear scale. If `muted` is None,
        leave mute state unchanged. Returns True on success."""

    @abstractmethod
    def fade_to(self, target_linear: float, duration_ms: int = 150) -> bool:
        """Ramp volume to target over `duration_ms`. Default impl uses
        10 steps via set_linear; backends with native fade (Windows
        VolumeStepDown) may override. Non-blocking OK — caller waits."""

    @abstractmethod
    def get_other_sessions(self) -> list:
        """Return other audio sessions (Windows only) for per-session
        ducking. Empty list on platforms without per-session support."""
```

### 4.2 VolumeDucker (orchestrator)

```python
# voice_typer/server/volume_ducker.py
import logging
import threading
from typing import Optional

from voice_typer.server.volume_backend import VolumeBackend, VolumeState

log = logging.getLogger(__name__)


class VolumeDucker:
    """Manages system audio volume ducking during dictation.

    Platform-agnostic: delegates to a VolumeBackend selected by
    platform.get_volume_backend(). Thread-safe.
    """

    def __init__(self, backend: Optional[VolumeBackend] = None,
                 crash_recovery=None):
        self._backend: Optional[VolumeBackend] = backend
        self._crash_recovery = crash_recovery  # DuckCrashRecovery or None
        self._saved_state: Optional[VolumeState] = None
        self._ducked_level: float = 0.25
        self._lock = threading.Lock()
        self._initialized: bool = False

    def initialize(self) -> bool:
        """Detect platform and set up the backend.

        Returns True if a working backend is available, False otherwise
        (non-supported platform / missing library → graceful no-op).
        """
        if self._initialized:
            return self._backend is not None
        if self._backend is None:
            from voice_typer.server.platform import get_volume_backend
            self._backend = get_volume_backend()
        if self._backend is None:
            log.info("[VOLUME] No volume backend available — ducking disabled")
            self._initialized = True
            return False
        ok = self._backend.initialize()
        self._initialized = True
        if ok:
            log.info("[VOLUME] Backend ready: %s (per_session=%s)",
                     self._backend.name, self._backend.supports_per_session)
            # Crash recovery: if a previous run crashed while ducked,
            # restore the saved volume now.
            if self._crash_recovery is not None:
                stale = self._crash_recovery.load_stale()
                if stale is not None:
                    log.warning(
                        "[VOLUME] Previous session crashed while ducked — "
                        "restoring volume to %.0f%%",
                        stale.linear * 100,
                    )
                    self._backend.set_linear(stale.linear, muted=stale.muted)
                    self._crash_recovery.clear()
                    self.tray_notify_crash_restore()  # wired by app
        else:
            log.warning("[VOLUME] Backend %s failed to initialize",
                        self._backend.name)
        return ok

    def duck(self, level: float = 0.25, fade_ms: int = 150) -> bool:
        """Reduce system volume to `level` (0.0–1.0 perceptual-linear).

        Saves the current volume + mute state before ducking so it can
        be restored exactly. Subsequent calls update the level without
        re-saving. Returns True on success, False if backend failed.
        Thread-safe.
        """
        if not self._initialized or self._backend is None:
            return False
        with self._lock:
            if self._saved_state is None:
                state = self._backend.get_state()
                if state is None:
                    log.warning("[VOLUME] get_state failed — not ducking")
                    return False
                self._saved_state = state
                self._ducked_level = level
                ok = self._backend.fade_to(level, fade_ms)
                if ok and self._crash_recovery is not None:
                    self._crash_recovery.save(state)
                log.info("[VOLUME] Duck → %.0f%% (saved %.0f%%, muted=%s)",
                         level * 100, state.linear * 100, state.muted)
                return ok
            else:
                # Already ducked — just update level, don't re-save.
                self._ducked_level = level
                ok = self._backend.fade_to(level, fade_ms)
                log.info("[VOLUME] Duck level updated → %.0f%%", level * 100)
                return ok

    def restore(self, fade_ms: int = 150, force: bool = False) -> bool:
        """Restore system volume to its pre-duck level + mute state.

        Detects manual volume changes during ducking: if the current
        volume differs from the ducked level by >5%, the user changed
        it intentionally → restore to the *current* value, not the saved
        one. Use `force=True` to bypass this and always restore saved.

        Safe to call when not ducked (no-op). Thread-safe.
        """
        if not self._initialized or self._backend is None:
            return False
        with self._lock:
            if self._saved_state is None:
                return True  # not ducked — no-op success
            current = self._backend.get_state()
            if current is None:
                log.warning("[VOLUME] get_state failed on restore — "
                            "using saved value")
                target = self._saved_state
            elif not force and abs(current.linear - self._ducked_level) > 0.05:
                log.info(
                    "[VOLUME] Manual volume change detected during duck "
                    "(current=%.0f%%, ducked=%.0f%%) — restoring to current "
                    "instead of saved (%.0f%%)",
                    current.linear * 100, self._ducked_level * 100,
                    self._saved_state.linear * 100,
                )
                target = current
            else:
                target = self._saved_state
            ok = self._backend.fade_to(target.linear, fade_ms)
            if ok:
                # Restore mute state AFTER volume fade completes,
                # otherwise fading a muted device is a no-op.
                self._backend.set_linear(target.linear, muted=target.muted)
                if self._crash_recovery is not None:
                    self._crash_recovery.clear()
            log.info("[VOLUME] Restore → %.0f%% muted=%s",
                     target.linear * 100, target.muted)
            self._saved_state = None
            return ok

    @property
    def is_ducked(self) -> bool:
        with self._lock:
            return self._saved_state is not None

    @property
    def backend_name(self) -> str:
        if self._backend is None:
            return "none (disabled)"
        return self._backend.name

    @property
    def supports_per_session(self) -> bool:
        if self._backend is None:
            return False
        return self._backend.supports_per_session
```

### 4.3 State Machine

```
IDLE ──duck()──▶ DUCKED ──restore()──▶ IDLE
                   │
                   ├─duck() again──▶ DUCKED (level updated, no re-save)
                   │
                   ├─restore() w/ manual override──▶ IDLE (restores to current, not saved)
                   │
                   └─app crash──▶ [crash_recovery.json persisted]
                                    │
                                    └─next launch initialize()──▶ restore stale + warn
```

### 4.4 Thread Safety

All public methods use `self._lock`. Callers and their threads:

```
_start_dictation()   [background/timer thread]  → duck()     ✅
_stop_dictation()    [background/timer thread]  → restore()  ✅
_cancel_dictation()  [ESC hotkey thread]        → restore()  ✅
quit()               [main or pystray thread]   → restore()  ✅
```

**Note:** `_cancel_dictation` and `_stop_dictation` can fire concurrently (ESC during stop). The lock serializes them; the second call is a no-op because `_saved_state` is already None. This is correct and tested (see §10.2).

---

## 5. Platform Backends

### 5.1 Windows — `WinVolumeBackend` (pycaw)

**Library:** [`pycaw`](https://github.com/AndreMiras/pycaw) + `comtypes`

**Why pycaw over alternatives:** mature, MIT, actively maintained, exposes both master volume (`IAudioEndpointVolume`) and per-session volume (`ISimpleAudioVolume`).

**Critical: use `SetMasterVolumeLevelScalar`, NOT `SetMasterVolumeLevel`.**

`SetMasterVolumeLevel(level_db, guid)` takes decibels. `SetMasterVolumeLevelScalar(scalar, guid)` takes perceptual-linear [0.0, 1.0] — the exact scale we normalize to. Using dB requires manual conversion that differs across hardware (volume curves are non-linear). Scalar is the API Windows itself uses for the volume slider.

```python
class WinVolumeBackend(VolumeBackend):
    @property
    def name(self) -> str:
        return "pycaw (WASAPI)"

    @property
    def supports_per_session(self) -> bool:
        return True

    def initialize(self) -> bool:
        try:
            from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
            from comtypes import CLSCTX_ALL
            from ctypes import cast, POINTER
            devices = AudioUtilities.GetSpeakers()
            interface = devices.Activate(
                IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
            self._vol = cast(interface, POINTER(IAudioEndpointVolume))
            return True
        except Exception as e:
            log.warning("[VOLUME-WIN] init failed: %s", e)
            return False

    def get_state(self) -> Optional[VolumeState]:
        try:
            scalar = self._vol.GetMasterVolumeLevelScalar()  # 0.0–1.0
            muted = bool(self._vol.GetMute())
            return VolumeState(linear=float(scalar), muted=muted)
        except Exception as e:
            log.warning("[VOLUME-WIN] get_state failed: %s", e)
            return None

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
        try:
            level = max(0.0, min(1.0, level))
            self._vol.SetMasterVolumeLevelScalar(level, None)
            if muted is not None:
                self._vol.SetMute(1 if muted else 0, None)
            return True
        except Exception as e:
            log.warning("[VOLUME-WIN] set_linear failed: %s", e)
            return False

    def fade_to(self, target_linear: float, duration_ms: int = 150) -> bool:
        # Windows has VolumeStepUp/StepDown but they're coarse (1 step ≈ 2%).
        # Use scalar steps for smooth fade.
        import time
        current = self.get_state()
        if current is None:
            return self.set_linear(target_linear)
        steps = max(1, duration_ms // 15)
        for i in range(1, steps + 1):
            t = i / steps
            val = current.linear + (target_linear - current.linear) * t
            self._vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, val)), None)
            time.sleep(duration_ms / steps / 1000.0)
        return True

    def get_other_sessions(self) -> list:
        """Return other audio sessions for per-session ducking.

        Windows can duck individual app sessions (like Skype/Teams do)
        without touching the master volume — system alerts and the
        dictation app's own sounds stay audible.
        """
        try:
            from pycaw.pycaw import AudioUtilities
            sessions = []
            for session in AudioUtilities.GetAllSessions():
                if session.Process and session.Process.name():
                    # Don't duck our own process
                    if "voice_typer" in session.Process.name().lower():
                        continue
                    sessions.append(session)
            return sessions
        except Exception:
            return []
```

**Per-session ducking (Windows only, opt-in via config):** When `volume_duck_per_session: bool = True` on Windows, `duck()` calls `session.SetVolumeLevel(scalar)` on each foreign session instead of master volume. This preserves system alerts, other apps' notifications, and the dictation app's own audio feedback. Falls back to master volume if per-session enumeration fails.

**Edge cases handled:**
- No speakers connected → `GetSpeakers()` returns None → `initialize()` returns False.
- Volume already at 0 → save 0, duck to 0, restore 0 (no-op but correct).
- Volume muted → `GetMute()` returns 1 → saved, restored on `restore()`.
- COM threading → `comtypes` handles per-thread COM init. `VolumeDucker` calls happen on background/hotkey threads, not the PortAudio thread.

### 5.2 macOS — `MacVolumeBackend` (CoreAudio via pyobjc)

**Library:** `pyobjc-core` + `pyobjc-framework-CoreAudio` (primary), `osascript` (fallback)

**Why not osascript-only (v1's approach):** `osascript` spawns a subprocess (200–500ms latency), and macOS 13+ requires AppleScript permission grants. CoreAudio via pyobjc is in-process (<5ms) and needs no special permission for volume control.

```python
class MacVolumeBackend(VolumeBackend):
    @property
    def name(self) -> str:
        return "CoreAudio (pyobjc)" if self._use_coreaudio else "osascript"

    @property
    def supports_per_session(self) -> bool:
        return False  # No clean native per-app volume API on macOS

    def initialize(self) -> bool:
        try:
            from CoreAudio import (  # type: ignore
                AudioObjectGetPropertyData,
                AudioObjectSetPropertyData,
                kAudioHardwareServiceDevice_VirtualMasterVolume,
                kAudioObjectPropertyScopeOutput,
                kAudioObjectPropertyElementMaster,
            )
            self._coreaudio = True
            self._default_device = self._get_default_device()
            return self._default_device is not None
        except ImportError:
            log.info("[VOLUME-MAC] pyobjc not available, falling back to osascript")
            self._coreaudio = False
            return True  # osascript always "available" on macOS

    def get_state(self) -> Optional[VolumeState]:
        if self._coreaudio:
            return self._coreaudio_get_state()
        # osascript fallback
        import subprocess
        try:
            r = subprocess.run(
                ["osascript", "-e",
                 "output volume of (get volume settings)"],
                capture_output=True, text=True, timeout=2)
            vol = int(r.stdout.strip()) / 100.0
            r2 = subprocess.run(
                ["osascript", "-e",
                 "output muted of (get volume settings)"],
                capture_output=True, text=True, timeout=2)
            muted = r2.stdout.strip().lower() == "true"
            return VolumeState(linear=vol, muted=muted)
        except Exception as e:
            log.warning("[VOLUME-MAC] osascript get_state failed: %s", e)
            return None

    def set_linear(self, level: float, muted: Optional[bool] = None) -> bool:
        level = max(0.0, min(1.0, level))
        if self._coreaudio:
            return self._coreaudio_set(level, muted)
        import subprocess
        try:
            subprocess.run(
                ["osascript", "-e", f"set volume output volume {int(level*100)}"],
                capture_output=True, timeout=2)
            if muted is not None:
                subprocess.run(
                    ["osascript", "-e",
                     f"set volume output muted {'true' if muted else 'false'}"],
                    capture_output=True, timeout=2)
            return True
        except Exception as e:
            log.warning("[VOLUME-MAC] osascript set failed: %s", e)
            return False

    # ... fade_to uses 10 osascript calls (slow) or CoreAudio (fast)
```

**macOS limitation:** No clean native per-app volume API. CoreAudio's `AudioObjectSetPropertyData` on `kAudioHardwareServiceDevice_VirtualMasterVolume` controls the master output. Per-app volume would require `AudioHijack`/`Loopback` (3rd party, paid) or a virtual audio device — out of scope. `supports_per_session = False`.

### 5.3 Linux — `LinuxVolumeBackend` (pactl → wpctl → amixer)

Linux audio has three major stacks in the wild:

| Stack | Used by | CLI tool | Status |
|---|---|---|---|
| **PulseAudio** | Ubuntu 20.04, Debian, older Fedora | `pactl` | Legacy but universal |
| **PipeWire** | Fedora 34+, Ubuntu 22.04+, Arch | `pactl` (compat) or `wpctl` (native) | Modern default |
| **ALSA (bare)** | Raspbian Lite, minimal servers, embedded | `amixer` / `alsamixer` | No sound server |

`pactl` works on both PulseAudio and PipeWire (via the PulseAudio compat layer), but some PipeWire-native installs drop the compat layer. `wpctl` is the WirePlumber CLI for PipeWire. `amixer` is the ALSA fallback.

```python
class LinuxVolumeBackend(VolumeBackend):
    def initialize(self) -> bool:
        # Detect available toolchain: pactl → wpctl → amixer
        for tool, setter, getter, muter in [
            ("pactl", self._pactl_set, self._pactl_get, self._pactl_mute),
            ("wpctl", self._wpctl_set, self._wpctl_get, self._wpctl_mute),
            ("amixer", self._amixer_set, self._amixer_get, self._amixer_mute),
        ]:
            if shutil.which(tool):
                self._tool = tool
                self._set_fn = setter
                self._get_fn = getter
                self._mute_fn = muter
                log.info("[VOLUME-LINUX] Using %s", tool)
                return True
        log.info("[VOLUME-LINUX] No volume tool found (pactl/wpctl/amixer)")
        return False

    @property
    def name(self) -> str:
        return f"linux ({self._tool})"

    @property
    def supports_per_session(self) -> bool:
        # PulseAudio/PipeWire support per-stream volume via
        # `pactl set-sink-input-volume` but enumeration is fragile.
        # Master volume only for v1 — revisit if users request it.
        return False

    def _pactl_get(self) -> Optional[VolumeState]:
        # pactl get-sink-volume @DEFAULT_SINK@ returns "Volume: front-left: 65536 / 100% / 0.00 dB, ..."
        # pactl get-sink-mute @DEFAULT_SINK@ returns "Mute: yes/no"
        # Convert 65536-scale to 0.0-1.0
        ...

    def _pactl_set(self, level: float, muted: Optional[bool]) -> bool:
        # pactl set-sink-volume @DEFAULT_SINK@ {int(level*65536)}
        # pactl set-sink-mute @DEFAULT_SINK@ {1 if muted else 0}
        ...

    def _wpctl_get(self) -> Optional[VolumeState]:
        # wpctl get-volume @DEFAULT_AUDIO_SINK@ returns "Volume: 0.50"
        # (already 0.0-1.0 linear — no conversion needed)
        ...

    def _amixer_get(self) -> Optional[VolumeState]:
        # amixer -D default sget Master → parse "Front Left: Playback 50% [50%]"
        # amixer -D default sget Master → parse "[on]"/"[off]" for mute
        ...
```

**PipeWire quirk:** `wpctl get-volume @DEFAULT_AUDIO_SINK@` already returns 0.0–1.0 linear (no conversion needed). `pactl` returns 0–65536. The backend normalizes both to `VolumeState.linear`.

**ALSA quirk:** `amixer` controls the hardware mixer, which may not exist on USB audio devices. If `amixer sget Master` fails, `initialize()` returns False and ducking is disabled (graceful).

---

## 6. Component: AudioProcessor (Noise Filter Layer)

### 6.1 Why this exists

Ducking silences the speakers. But the microphone still picks up:

- **HVAC / fans / AC units** — continuous low-frequency rumble (50–200Hz).
- **Mechanical keyboards** — transient broadband clicks.
- **Room reverb / echo** — late reflections after speech.
- **Computer fan noise** — broadband hiss, especially on laptops.
- **Residual speaker bleed** — whatever leaks through despite ducking (headphones off, or duck level set high).

These can't be solved by ducking. They require filtering the mic signal itself.

### 6.2 Filter chain

```
Raw chunk (from PortAudio)
    │
    ▼
┌─────────────────────┐
│  HighPassFilter     │  ◀── 80Hz Butterworth IIR, removes HVAC/fan rumble
│  (scipy.signal)     │      Cost: ~0.05ms/chunk. Default ON.
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  NoiseGate          │  ◀── Below threshold → silence. Removes keyboard
│  (numpy)            │      idle hiss. Cost: ~0.02ms. Default ON, -45dBFS.
└─────────┬───────────┘
          ▼
┌─────────────────────┐
│  RNNoise (optional) │  ◀── Neural denoiser (C library, ~1ms/10ms frame).
│  (rnnoise-webrtc)   │      Best quality, removes broadband noise. Default
└─────────┬───────────┘      OFF (CPU cost). Toggle in settings.
          ▼
    filtered chunk → buffer
          │
          ▼ (on stop(), offline)
┌─────────────────────┐
│  PostCaptureFilter  │  ◀── noisereduce spectral gating on full audio.
│  (noisereduce)      │      Safety net if real-time path disabled or
└─────────┬───────────┘      insufficient. Default ON. ~200ms for 30s audio.
          ▼
    clean audio → transcriber
```

### 6.3 Interface

```python
# voice_typer/server/audio_processor.py
import logging
import numpy as np
from typing import Optional, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass
class AudioProcessorConfig:
    enabled: bool = True
    highpass: bool = True
    highpass_cutoff_hz: float = 80.0
    noise_gate: bool = True
    noise_gate_threshold: float = 0.015  # ~-45dBFS
    rnnoise: bool = False                # CPU-heavy, opt-in
    post_capture: bool = True            # noisereduce on stop()


class AudioProcessor:
    """Real-time + post-capture audio cleaning.

    Real-time filters run in the PortAudio callback (must not block).
    Post-capture filter runs in Recorder.stop() (offline, can block).

    All filters are optional and individually toggleable. If a filter
    library is missing, that filter is silently skipped (graceful).
    """

    def __init__(self, config: AudioProcessorConfig):
        self._config = config
        self._hp_filter = None       # scipy IIR state
        self._rnnoise: Optional[object] = None
        self._rnnoise_frame_size: int = 0
        self._quality_callback: Optional[Callable] = None
        self._init_filters()

    def _init_filters(self):
        if self._config.highpass:
            try:
                from scipy.signal import butter, lfilter, ziplfilter
                # Pre-compute butterworth coefficients at 16kHz target rate.
                # The filter is applied per-chunk using a stateful lfilter
                # so it's continuous across chunk boundaries.
                b, a = butter(2, self._config.highpass_cutoff_hz / 8000.0,
                              btype="high")
                self._hp_filter = (b, a, np.zeros(max(len(a), len(b)) - 1))
            except Exception as e:
                log.warning("[AUDIO-PROC] highpass init failed: %s", e)

        if self._config.rnnoise:
            try:
                import rnnoise  # rnnoise-webrtc
                self._rnnoise = rnnoise.RNNoise()
                self._rnnoise_frame_size = 480  # 30ms at 16kHz
                log.info("[AUDIO-PROC] RNNoise loaded")
            except ImportError:
                log.info("[AUDIO-PROC] rnnoise not installed — skipping")
            except Exception as e:
                log.warning("[AUDIO-PROC] RNNoise init failed: %s", e)

    def process_chunk(self, chunk: np.ndarray) -> np.ndarray:
        """Real-time filter chain. Called from PortAudio callback.

        MUST be non-blocking. Pre-allocated buffers only.
        Returns the filtered chunk (same shape/dtype).
        """
        if not self._config.enabled or chunk.size == 0:
            return chunk

        # 1. High-pass filter (stateful IIR — continuous across chunks)
        if self._hp_filter is not None:
            from scipy.signal import lfilter
            b, a, zi = self._hp_filter
            chunk, zi = lfilter(b, a, chunk, zi=zi)
            self._hp_filter = (b, a, zi)

        # 2. Noise gate
        if self._config.noise_gate:
            rms = float(np.sqrt(np.mean(np.square(chunk))))
            if rms < self._config.noise_gate_threshold:
                chunk = chunk * 0.0  # silence below threshold

        # 3. RNNoise (optional, real-time neural)
        # NOTE: this is the risky one. RNNoise is C and fast (~1ms/frame)
        # but allocates internally. We process in 480-sample frames
        # (30ms at 16kHz). If chunk size != multiple of 480, we buffer
        # the remainder for next call.
        if self._rnnoise is not None:
            chunk = self._apply_rnnoise(chunk)

        # 4. Quality detection (feeds tray warnings)
        if self._quality_callback is not None:
            self._run_quality_check(chunk)

        return chunk.astype(np.float32, copy=False)

    def process_full_audio(self, audio: np.ndarray) -> np.ndarray:
        """Post-capture filter. Called from Recorder.stop().

        Runs noisereduce (spectral gating) on the complete audio.
        Offline — can block. ~200ms for 30s audio at 16kHz.
        """
        if not self._config.post_capture or audio.size == 0:
            return audio
        try:
            import noisereduce as nr
            # noisereduce works best with a noise profile. Use the first
            # 0.5s of audio (assumed to be pre-speech silence) as the
            # noise profile. If audio is <1s, skip.
            if len(audio) < 8000:  # <0.5s at 16kHz
                return audio
            noise_profile = audio[:8000]
            cleaned = nr.reduce_noise(
                y=audio, sr=16000, y_noise=noise_profile,
                stationary=True, prop_decrease=0.8,
            )
            log.info("[AUDIO-PROC] noisereduce: %d → %d samples",
                     len(audio), len(cleaned))
            return cleaned.astype(np.float32, copy=False)
        except ImportError:
            log.debug("[AUDIO-PROC] noisereduce not installed — skipping post-capture")
        except Exception as e:
            log.warning("[AUDIO-PROC] noisereduce failed: %s", e)
        return audio

    def set_quality_callback(self, cb: Callable) -> None:
        """Wire a quality detector (clipping/noise/SNR) to receive
        per-chunk metrics. Revives the dead audio_quality.py module."""
        self._quality_callback = cb

    def _apply_rnnoise(self, chunk: np.ndarray) -> np.ndarray:
        """Process chunk through RNNoise in 480-sample frames.

        Maintains a carry buffer for partial frames at chunk boundaries.
        """
        # ... frame chunk into 480-sample blocks, call self._rnnoise.process(),
        # reassemble, handle remainder ...
        ...

    def _run_quality_check(self, chunk: np.ndarray) -> None:
        """Lightweight quality metrics for the waveform + tray."""
        # Revives AudioQualityAnalyzer from audio_quality.py
        # but runs inline (no separate object) to avoid callback overhead.
        peak = float(np.max(np.abs(chunk))) if chunk.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(chunk)))) if chunk.size else 0.0
        try:
            self._quality_callback(rms, peak)
        except Exception:
            pass
```

### 6.4 Integration with Recorder

`AudioProcessor` is injected into `Recorder` and called from the existing callback. The callback already does RMS/peak/silence detection — `AudioProcessor` slots in **before** the buffer append so the stored audio is already filtered.

```python
# recording.py — modified callback (simplified)
def callback(indata, frames, time_info, status):
    # ... existing xrun/clipping tracking ...

    # NEW: filter the chunk before buffering
    filtered = self._audio_processor.process_chunk(indata.copy())

    with self._lock:
        self._buffer.append(filtered)  # store FILTERED audio
        # ... existing counters ...

    # RMS/peak computed on FILTERED audio (so silence detection
    # and the waveform bubble reflect what the transcriber will see)
    chunk_rms = float(np.sqrt(np.mean(np.square(filtered))))
    chunk_peak = float(np.max(np.abs(filtered)))
    # ... existing silence/bubble logic uses these ...

# recording.py — modified stop()
def stop(self) -> np.ndarray:
    # ... existing concat + resample ...
    audio = self._prepare_audio(audio, effective_sr)

    # NEW: post-capture noise reduction (offline, safe to block)
    if self._audio_processor is not None:
        audio = self._audio_processor.process_full_audio(audio)

    return audio
```

**Critical: the audio callback must not block.** `AudioProcessor.process_chunk()` is designed for this:

- High-pass filter: `scipy.signal.lfilter` on a 1024-sample chunk ≈ 0.05ms. Safe.
- Noise gate: numpy RMS + multiply ≈ 0.02ms. Safe.
- RNNoise: C library, ~1ms per 480-sample frame. A 1024-sample chunk ≈ 2ms. **Borderline.** If xruns appear with RNNoise enabled, move it to a separate consumer thread reading from a ring buffer (1-chunk latency). For v1, keep it in-callback but default OFF.
- Noisereduce: **never** in callback. Runs only in `stop()`.

**Quality detector callback:** `AudioProcessor.set_quality_callback()` wires the revived `AudioQualityAnalyzer`. The callback receives `(rms, peak)` per chunk and accumulates statistics. On `stop()`, the analyzer produces an `AudioQualityReport` (clipping count, low-volume flag, noise ratio) that can be shown in the mic diagnostics screen. This replaces the dead `audio_quality.py` wiring that the old `app.py:331` comment called out. The revived wiring lives at:

- `app.py:37` — `from voice_typer.server.audio_quality import AudioQualityAnalyzer`
- `app.py:310` — `self._audio_quality = AudioQualityAnalyzer()` (instantiation in `__init__`)
- `app.py:311` — `self._audio_processor.set_quality_callback(self._on_audio_quality_chunk)` (callback wire)
- `app.py:1300` — `_on_audio_quality_chunk(self, rms, peak)` (per-chunk accumulator)
- `app.py:1331` — `_finalize_audio_quality_report(self, audio)` (called from `_stop_dictation` at line 1420)
- `app.py:1281` — `self._audio_quality.reset()` (per-session reset in `_start_dictation`)

### 6.5 VAD consolidation

| VAD | Where | Purpose |
|---|---|---|
| **Whisper `vad_filter=True`** | `transcription.py:459, 617` | Inside transcription — strips silence from audio before ASR. Already exists. Keep. |
| **Silero VAD (new, optional)** | `AudioProcessor` or separate `VadDetector` | Feeds the waveform bubble visualizer (show only speech segments) + more accurate silence auto-stop than RMS threshold. Future enhancement, not in v1.1.0. |
| **RMS-based silence detection** | `recording.py` callback | Current silence auto-stop. Keep as baseline; Silero VAD can replace it later. |

Two VADs serving different purposes is correct — document it, don't consolidate.

---

## 7. Integration Points with Existing Codebase

### 7.1 Recording lifecycle — `recording.py`

`Recorder` gains an `AudioProcessor` injected via constructor:

```python
class Recorder:
    def __init__(self, config: Config,
                 audio_processor: Optional[AudioProcessor] = None):
        self._audio_processor = audio_processor
        # ... existing init ...
```

If `audio_processor` is None (feature disabled), the callback and `stop()` skip filtering. Zero behavior change — graceful.

### 7.2 Dictation start — `app.py:_start_dictation()` (line 1197)

After `self.recorder.start()` (line 1290) and `self._waveform_bubble.show()` (line 1294), **before** the function returns:

```python
# Duck system volume (if enabled and backend available)
if self.config.volume_duck_enabled:
    if self._volume_ducker.initialize():
        self._volume_ducker.duck(self.config.volume_duck_level)
```

**Implementation note:** the actual wiring delegates to `self._duck_volume()` (defined at `app.py:414`), called from `_start_dictation()` at `app.py:1297`.  This helper reads `config.volume_duck_enabled`, `volume_duck_level`, `volume_duck_fade_ms`, and `volume_duck_per_session` (gated on `backend.supports_per_session`) so the wiring matches the config schema 1:1.

**Ordering matters:** duck happens AFTER `recorder.start()` so the mic is already capturing when volume drops — the first chunk of audio benefits from the ducked speakers.

### 7.3 Dictation stop — `app.py:_stop_dictation()` (line 1373)

After `audio = self.recorder.stop()` (line 1398), **before** the transcription thread starts:

```python
# Restore system volume
if self.config.volume_duck_enabled:
    self._volume_ducker.restore()
```

**Implementation note:** delegated to `self._restore_volume()` (`app.py:429`), called from `_stop_dictation()` at `app.py:1406`.  The audio-quality finalization (`_finalize_audio_quality_report`) runs at `app.py:1420` after the restore — see §6.4.

**Why before transcription thread:** restore is fast (fade 150ms) and should not be delayed by transcription. The transcription thread runs for seconds; volume restore must be immediate so the user hears their audio back ASAP.

### 7.4 Cancel — `app.py:_cancel_dictation()` (line 1689)

**Bug fix:** the v1 code at the (now-removed) line 1528 called `self._background_audio_monitor.stop()` which threw `AttributeError` because `_background_audio_monitor` was never initialized. Fix: remove that line, add volume restore.  **Implemented:** the buggy line is gone (see the BUGFIX comment at `app.py:1700`), and `_restore_volume()` is called from `_cancel_dictation()` at `app.py:1712`.

```python
def _cancel_dictation(self):
    log.info("[CANCEL] Cancelling current dictation (cycle=%s)", self._cycle_id)
    self._cancel_pending_timers()

    if self.recorder.recording:
        try:
            self.recorder.on_rms_level = None
            self._waveform_bubble.reset_level()
            # REMOVED: self._background_audio_monitor.stop()  ← AttributeError bug
            self.recorder.discard()
            log.info("[CANCEL] Recording discarded (cycle=%s)", self._cycle_id)
        except Exception as e:
            log.warning("[CANCEL] Failed to discard recording: %s (cycle=%s)",
                        e, self._cycle_id)

    self._cancel_streaming_session()

    # Restore volume on cancel (was missing in v1)
    if self.config.volume_duck_enabled:
        self._volume_ducker.restore()

    if self.config.bubble_behavior != 'always_visible':
        self._waveform_bubble.hide()
    self.tray.set_state(AppState.IDLE, "Cancelled")
    self._busy_event.set()
```

### 7.5 Quit — `app.py:quit()` (line 2118) — **NEW wiring point (v1 missed this)**

After `self.recorder.discard()` (line 2143), the volume restore is wired at `app.py:2150`:

```python
# Restore volume if we were ducked when the app quit.
# Without this, a quit-during-recording leaves volume stuck at 10%.
if self.config.volume_duck_enabled:
    try:
        self._volume_ducker.restore(fade_ms=0)  # no fade on quit — fast exit
    except Exception as e:
        log.warning("[SHUTDOWN] volume restore failed: %s", e)
```

**`fade_ms=0` on quit:** the app is exiting; don't spend 150ms on a fade. Restore instantly.

### 7.6 Restart — `app.py:restart_app()` (line 2001)

**Race fix:** v1's claim that "restore happens on old process" was wrong because `restart_app()` launches the new process BEFORE calling `quit()`. If the new process ducks before the old process restores, volume ping-pongs.

Fix: restore volume **before** launching the new process.  **Implemented:** `self._restore_volume(fade_ms=0)` runs at `app.py:2029`, before `subprocess.Popen` at `app.py:2070`.

```python
def restart_app(self) -> None:
    log.info("[RESTART] Restarting Voice Typer...")
    # Restore volume BEFORE launching new process to avoid ping-pong.
    if self.config.volume_duck_enabled:
        try:
            self._volume_ducker.restore(fade_ms=0)
        except Exception:
            pass
    # ... existing restart logic (sleep, launch subprocess, quit) ...
```

### 7.7 Crash recovery on startup — `app.py:__init__()` (line 283)

`VolumeDucker.initialize()` (§4.2) checks for a stale `duck_crash_recovery.json`. If found, it restores the saved volume and warns the user. This runs during app init, before any recording.  **Implementation:** `self._duck_crash_recovery = DuckCrashRecovery(config_dir=_config_dir())` at `app.py:364`, `self._volume_ducker = VolumeDucker(crash_recovery=..., on_crash_restore=self._on_volume_crash_restore)` at `app.py:365`.  The crash-restore callback `_on_volume_crash_restore` (`app.py:400`) calls `self.tray.notify(...)` to surface the warning.

### 7.8 Configuration

New config fields in `config.py`:

```python
# Volume ducking
volume_duck_enabled: bool = True
volume_duck_level: float = 0.25              # 0.0–1.0 perceptual
volume_duck_per_session: bool = False        # Windows only
volume_duck_fade_ms: int = 150               # 0–1000

# Noise filtering
noise_filter_enabled: bool = True
noise_filter_highpass: bool = True
noise_filter_highpass_cutoff_hz: float = 80.0  # 20–500
noise_filter_gate: bool = True
noise_filter_gate_threshold: float = 0.015    # 0.0–0.1
noise_filter_rnnoise: bool = False            # opt-in (CPU cost)
noise_filter_post_capture: bool = True        # noisereduce on stop()
```

Add to `IPC_CONFIG_ALLOWLIST` (config.py:569):

```python
# ── Volume ducking ───────────────────────────────────────────────
"volume_duck_enabled":          (bool, _bool_validator),
"volume_duck_level":            (float, _make_float_validator(lo=0.0, hi=1.0)),
"volume_duck_per_session":      (bool, _bool_validator),
"volume_duck_fade_ms":          (int, _make_int_validator(lo=0, hi=1000)),

# ── Noise filtering ──────────────────────────────────────────────
"noise_filter_enabled":         (bool, _bool_validator),
"noise_filter_highpass":        (bool, _bool_validator),
"noise_filter_highpass_cutoff_hz": (float, _make_float_validator(lo=20.0, hi=500.0)),
"noise_filter_gate":            (bool, _bool_validator),
"noise_filter_gate_threshold":  (float, _make_float_validator(lo=0.0, hi=0.1)),
"noise_filter_rnnoise":         (bool, _bool_validator),
"noise_filter_post_capture":    (bool, _bool_validator),
```

### 7.9 Settings UI (`Settings.tsx`)

Add two new sections in the "Recording" tab:

```tsx
{/* ── Auto Duck Volume ── */}
<SettingRow label="Auto Duck Volume"
  info="Reduce system volume during dictation to prevent speaker bleed into the mic.">
  <Switch checked={config.volume_duck_enabled ?? true} ... />
</SettingRow>
<SettingRow label="Duck Level"
  info="How quiet to make system audio. 25% = whisper-quiet, 50% = slight dip.">
  <Slider value={config.volume_duck_level ?? 0.25}
    min={0} max={0.5} step={0.05} ... />
</SettingRow>
<SettingRow label="Per-Session Duck (Windows)"
  info="Duck only other apps' audio, keeping system alerts audible. Windows only.">
  <Switch checked={config.volume_duck_per_session ?? false}
    disabled={!isWindows} ... />
</SettingRow>

{/* ── Noise Filtering ── */}
<SettingRow label="Noise Filter"
  info="Clean the microphone signal: removes fan noise, keyboard clicks, HVAC rumble.">
  <Switch checked={config.noise_filter_enabled ?? true} ... />
</SettingRow>
<SettingRow label="High-Pass Filter"
  info="Remove low-frequency rumble (HVAC, traffic) below 80Hz.">
  <Switch checked={config.noise_filter_highpass ?? true} ... />
</SettingRow>
<SettingRow label="Noise Gate"
  info="Silence audio below a threshold to remove idle hiss.">
  <Switch checked={config.noise_filter_gate ?? true} ... />
</SettingRow>
<SettingRow label="RNNoise (Neural)"
  info="AI-based real-time denoising. Higher quality but uses more CPU. Experimental.">
  <Switch checked={config.noise_filter_rnnoise ?? false} ... />
</SettingRow>
<SettingRow label="Post-Capture Cleanup"
  info="Run spectral noise reduction on the full recording after stop. Improves quality if real-time filters miss noise.">
  <Switch checked={config.noise_filter_post_capture ?? true} ... />
</SettingRow>

{/* Backend status indicator */}
<SettingRow label="Volume Backend"
  info="The active audio control backend. 'disabled' means ducking won't work on this platform.">
  <Text>{backendStatus}</Text>  {/* "pycaw (WASAPI)" / "CoreAudio" / "disabled" */}
</SettingRow>
```

### 7.10 Graceful degradation

If no volume backend is available (missing library, unsupported platform, no audio device):

- `VolumeDucker.initialize()` returns False.
- `duck()` / `restore()` are no-ops, return False.
- Logged once at INFO level.
- Settings UI shows "Volume Backend: disabled".
- App continues normally — just no ducking.

If a noise filter library is missing (`scipy`, `noisereduce`, `rnnoise`):

- That specific filter is skipped.
- Other filters still run.
- Logged at DEBUG/INFO level.
- No user-visible error.

---

## 8. Edge Cases & Error Handling

| Scenario | Behavior |
|---|---|
| **pycaw/pyobjc/pactl not installed** | `initialize()` returns False. `duck()`/`restore()` no-ops. Logged once. Settings UI shows "disabled". |
| **No speakers/audio device connected** | `initialize()` returns False. No crash. |
| **Volume already at 0%** | Save 0, duck to 0, restore 0. No-op but correct. |
| **Volume muted** | `GetMute()`/equivalent saved. Duck sets volume + leaves mute. Restore sets volume AND restores mute state. **v1 bug fixed.** |
| **User manually changes volume while ducked** | `restore()` detects current ≠ ducked level (>5% diff) → restores to **current** (manual) value, not saved. Logged. |
| **`force=True` on restore** | Bypasses manual-override detection. Restores saved value exactly. Used on crash recovery. |
| **Stopped before duck()** | `restore()` checks `_saved_state is None` → no-op. |
| **duck() called twice** | Second call updates level without re-saving. Correct. |
| **restore() called twice** | Second call is a no-op (`_saved_state` already None). |
| **Cancel + Stop concurrent** | Lock serializes. First call restores + clears state. Second call no-op. Tested (§10.2). |
| **COM not initialized on thread (Windows)** | `comtypes` handles per-thread COM init automatically. |
| **Recording stopped by silence auto-stop** | `_on_silence_auto_stop` (app.py:1422) schedules `_stop_dictation` via timer → calls `restore()`. Correct. |
| **Recording stopped by max duration** | Same path. Correct. |
| **ESC cancel** | `_cancel_dictation()` calls `restore()`. Correct. |
| **App quit while recording** | `quit()` (app.py:1934) → `recorder.discard()` → `restore(fade_ms=0)`. **v1 wiring gap fixed.** |
| **App crash while ducked** | `duck_crash_recovery.json` persists saved state. Next launch `initialize()` restores + warns user. |
| **App restart while recording** | `restart_app()` restores volume BEFORE launching new process. No ping-pong. |
| **Audio callback blocks due to RNNoise** | RNNoise default OFF. If enabled and xruns appear, log warns. Future: move to consumer thread. |
| **noisereduce not installed** | `process_full_audio()` skips post-capture filter. Logged at DEBUG. |
| **PipeWire without PulseAudio compat** | `LinuxVolumeBackend` detects `wpctl` as fallback. |
| **ALSA-only system (no sound server)** | `amixer` fallback. If `amixer sget Master` fails (USB device w/o mixer), ducking disabled gracefully. |
| **macOS AppleScript permission denied** | `osascript` path fails silently. CoreAudio (pyobjc) path used if available. If neither, ducking disabled. |
| **Multiple audio sessions (Windows)** | If `volume_duck_per_session=True`, duck each foreign session via `ISimpleAudioVolume`. Own process excluded. |

---

## 9. Duck Crash Recovery

```python
# voice_typer/server/duck_crash_recovery.py
import json
import logging
from pathlib import Path
from typing import Optional
from voice_typer.server.volume_backend import VolumeState

log = logging.getLogger(__name__)


class DuckCrashRecovery:
    """Persists ducked volume state so a crash doesn't leave the
    system stuck at 10% volume.

    File: {config_dir}/duck_crash_recovery.json
    Written on duck(), deleted on restore().
    On startup, if file exists, VolumeDucker restores the saved volume.
    """

    def __init__(self, config_dir: Optional[Path] = None):
        self._path = (config_dir or Path.home() / ".voice_typer") / \
                     "duck_crash_recovery.json"

    def save(self, state: VolumeState) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({
                "linear": state.linear,
                "muted": state.muted,
            }))
        except Exception as e:
            log.warning("[VOLUME-CRASH] Failed to persist duck state: %s", e)

    def load_stale(self) -> Optional[VolumeState]:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text())
            return VolumeState(linear=float(data["linear"]),
                               muted=bool(data["muted"]))
        except Exception as e:
            log.warning("[VOLUME-CRASH] Failed to read stale state: %s", e)
            return None

    def clear(self) -> None:
        try:
            if self._path.exists():
                self._path.unlink()
        except Exception:
            pass
```

---

## 10. Testing Strategy

### 10.1 VolumeDucker unit tests — `tests/test_volume_ducker.py`

```python
class FakeBackend(VolumeBackend):
    def __init__(self, current=0.5, muted=False):
        self._current = current
        self._muted = muted
        self._set_calls = []
        self._fade_calls = []
    @property
    def name(self): return "fake"
    @property
    def supports_per_session(self): return False
    def initialize(self): return True
    def get_state(self): return VolumeState(self._current, self._muted)
    def set_linear(self, level, muted=None):
        self._current = level
        if muted is not None: self._muted = muted
        self._set_calls.append((level, muted))
        return True
    def fade_to(self, target, duration_ms=150):
        self._current = target
        self._fade_calls.append((target, duration_ms))
        return True
    def get_other_sessions(self): return []


def test_duck_saves_and_restores():
    b = FakeBackend(current=0.5)
    d = VolumeDucker(backend=b)
    d.initialize()
    d.duck(0.25)
    assert b._fade_calls[-1] == (0.25, 150)
    d.restore()
    assert b._fade_calls[-1] == (0.5, 150)

def test_restore_without_duck_is_noop():
    d = VolumeDucker(backend=FakeBackend())
    d.initialize()
    assert d.restore() is True  # no-op success
    assert not d.is_ducked

def test_double_duck_does_not_resave():
    b = FakeBackend(current=0.5)
    d = VolumeDucker(backend=b)
    d.initialize()
    d.duck(0.25)
    saved = d._saved_state
    b._current = 0.25
    d.duck(0.15)
    assert d._saved_state is saved  # same object, not re-saved
    assert b._fade_calls[-1][0] == 0.15

def test_mute_state_preserved_on_restore():
    b = FakeBackend(current=0.5, muted=True)
    d = VolumeDucker(backend=b)
    d.initialize()
    d.duck(0.25)
    b._muted = False  # duck left it unmuted (set_linear(None))
    d.restore()
    assert b._muted is True  # mute restored

def test_manual_volume_override_detected():
    b = FakeBackend(current=0.5)
    d = VolumeDucker(backend=b)
    d.initialize()
    d.duck(0.25)
    b._current = 0.8  # user cranked volume while ducked
    d.restore()
    assert b._fade_calls[-1][0] == 0.8  # restored to current, not saved 0.5

def test_force_restore_ignores_override():
    b = FakeBackend(current=0.5)
    d = VolumeDucker(backend=b)
    d.initialize()
    d.duck(0.25)
    b._current = 0.8
    d.restore(force=True)
    assert b._fade_calls[-1][0] == 0.5  # restored to saved

def test_concurrent_cancel_and_stop():
    """ESC cancel + stop fire simultaneously — lock must serialize."""
    import threading
    b = FakeBackend(current=0.5)
    d = VolumeDucker(backend=b)
    d.initialize()
    d.duck(0.25)
    errors = []
    def cancel(): 
        try: d.restore()
        except Exception as e: errors.append(e)
    def stop():
        try: d.restore()
        except Exception as e: errors.append(e)
    t1 = threading.Thread(target=cancel)
    t2 = threading.Thread(target=stop)
    t1.start(); t2.start()
    t1.join(); t2.join()
    assert not errors
    assert not d.is_ducked  # exactly one restore happened

def test_crash_recovery_restores_stale_state():
    from voice_typer.server.duck_crash_recovery import DuckCrashRecovery
    import tempfile, json
    with tempfile.TemporaryDirectory() as td:
        cr = DuckCrashRecovery(config_dir=Path(td))
        cr.save(VolumeState(linear=0.7, muted=False))
        b = FakeBackend(current=0.1)  # system stuck at ducked level
        d = VolumeDucker(backend=b, crash_recovery=cr)
        d.initialize()
        assert b._current == 0.7  # restored from stale file
        assert not cr.load_stale()  # file cleared

def test_backend_missing_returns_false():
    d = VolumeDucker(backend=None)
    assert not d.initialize()
    assert not d.duck(0.25)
    assert not d.restore()
    assert not d.is_ducked
```

### 10.2 AudioProcessor unit tests — `tests/test_audio_processor.py`

```python
def test_highpass_removes_low_frequency():
    # Generate 50Hz + 500Hz sine, filter at 80Hz, check 50Hz attenuated
    ...

def test_noise_gate_silences_below_threshold():
    cfg = AudioProcessorConfig(noise_gate=True, noise_gate_threshold=0.05)
    proc = AudioProcessor(cfg)
    quiet = np.full(1024, 0.01, dtype=np.float32)
    out = proc.process_chunk(quiet.copy())
    assert np.all(out == 0.0)

def test_noise_gate_passes_above_threshold():
    cfg = AudioProcessorConfig(noise_gate=True, noise_gate_threshold=0.005)
    proc = AudioProcessor(cfg)
    loud = np.full(1024, 0.1, dtype=np.float32)
    out = proc.process_chunk(loud.copy())
    assert np.allclose(out, loud)

def test_post_capture_runs_noisereduce():
    # Mock noisereduce, verify it's called on process_full_audio
    ...

def test_disabled_processor_is_passthrough():
    cfg = AudioProcessorConfig(enabled=False)
    proc = AudioProcessor(cfg)
    chunk = np.random.randn(1024).astype(np.float32) * 0.1
    out = proc.process_chunk(chunk)
    assert np.array_equal(out, chunk)

def test_missing_scipy_skips_highpass_gracefully():
    # Mock scipy import failure, verify highpass skipped but gate still works
    ...
```

### 10.3 Integration tests

Implemented in `tests/test_volume_lifecycle.py` (18 tests) and `tests/test_recording_audio_processor.py` (7 tests).  Coverage:

- Start recording → verify system volume drops to configured level (backend-specific assertion). ✓ `TestStartDictationDucksVolume`
- Stop recording → verify volume returns to original (including mute state). ✓ `TestStopDictationRestoresVolume`
- Cancel recording → verify volume returns to original. ✓ `TestCancelDictationRestoresVolume`
- Quit app while recording → verify volume restored (no fade). ✓ `TestQuitRestoresVolumeInstantly`
- Crash simulation: write `duck_crash_recovery.json`, restart app, verify volume restored + warning shown. ✓ `TestCrashRecoveryOnStartup`
- Start recording with noise filter ON → verify high-pass filter applied to buffered audio (FFT comparison). ✓ `TestRecorderCallbackWithAudioProcessor::test_buffer_contains_filtered_audio`
- RNNoise enabled → verify no xrun increase in callback timing. ⏳ Skipped — RNNoise is not installed in CI; the in-callback performance path is exercised but RNNoise itself is mocked/no-op.
- Manual volume change mid-dictation → verify restore uses current, not saved. ✓ `TestManualVolumeOverride`
- Restart → restore BEFORE subprocess.Popen (no ping-pong). ✓ `TestRestartRestoresBeforeLaunching`
- Restart → restore uses fade_ms=0. ✓ (same test)
- Per-session duck only attempted when backend supports it. ✓ `TestPerSessionDuckGatedOnSupport`
- Duck writes crash-recovery file, restore clears it. ✓ `TestDuckCrashRecoveryPersistsOnDuck`
- Recording callback with AudioProcessor doesn't raise NameError (regression). ✓ `TestRecorderCallbackWithAudioProcessor::test_callback_does_not_raise_with_processor`
- RMS callback receives filtered values. ✓ `test_rms_callback_receives_filtered_values`
- Quality callback fires per chunk. ✓ `test_quality_callback_fires_per_chunk`
- Post-capture processing runs in stop(). ✓ `test_post_capture_runs_in_stop`

### 10.4 Platform-specific CI

| Platform | Backend | Test approach | Implemented in |
|---|---|---|---|
| Windows | pycaw | Smoke tests (no pycaw installed in CI).  Backend name, per-session flag, graceful init failure. | `tests/test_volume_backends.py::TestWinBackendSmoke` |
| macOS | CoreAudio / osascript | Mock `subprocess.run`.  CoreAudio path can't be tested without macOS. | `tests/test_volume_backends.py::TestMacBackendOsascript` |
| Linux | pactl/wpctl/amixer | Mock `shutil.which` and the backend's `_run`. | `tests/test_volume_backends.py::TestLinuxBackendPactl/Wpctl/Amixer` |

---

## 11. File Changes Summary

| File | Change | Status |
|---|---|---|
| `voice_typer/server/volume_backend.py` | **NEW** — `VolumeBackend` ABC + `VolumeState` dataclass | ✅ Shipped |
| `voice_typer/server/volume_ducker.py` | **NEW** — `VolumeDucker` orchestrator (crash recovery, manual override, fade) | ✅ Shipped |
| `voice_typer/server/duck_crash_recovery.py` | **NEW** — persists ducked state for crash recovery | ✅ Shipped |
| `voice_typer/server/audio_processor.py` | **NEW** — `AudioProcessor` (highpass, gate, RNNoise, post-capture). **v2 fix:** `_apply_highpass` now ravels 2-D `(frames, 1)` input before filtering — sounddevice delivers 2-D, the v1 code crashed `scipy.lfilter`. | ✅ Shipped + bugfix |
| `voice_typer/server/volume_backends.py` | **NEW** — `WinVolumeBackend`, `MacVolumeBackend`, `LinuxVolumeBackend` concrete impls | ✅ Shipped |
| `voice_typer/server/platform.py` | Add `get_volume_backend()` factory (selects per-platform backend) | ✅ Shipped |
| `voice_typer/server/recording.py` | Inject `AudioProcessor`. Call `process_chunk()` in callback, `process_full_audio()` in `stop()`. **v2 fix:** reordered callback so `filtered` is assigned BEFORE the lock-block that uses it (v1 referenced `filtered` before assignment → silent NameError on every chunk). | ✅ Shipped + critical bugfix |
| `voice_typer/server/app.py` | Fix `_background_audio_monitor` AttributeError. Wire `VolumeDucker` at 6 points: `__init__`, `_start_dictation`, `_stop_dictation`, `_cancel_dictation`, `quit()`, `restart_app()`. Wire `AudioProcessor` into `Recorder` construction. **v2:** revive `AudioQualityAnalyzer` — instantiate, wire to `set_quality_callback`, reset per session, finalize+notify after stop. | ✅ Shipped + AudioQuality revival |
| `voice_typer/server/config.py` | Add 11 new fields (4 volume + 7 noise). Validators. IPC allowlist. | ✅ Shipped |
| `voice_typer/server/audio_quality.py` | Revived — wired via `AudioProcessor.set_quality_callback()` and `_finalize_audio_quality_report()`. No longer dead. | ✅ Revived |
| `voice_typer/server/ipc_server.py` | **v2 NEW:** `get_volume_backend_status` IPC endpoint so the Settings UI can show the active backend name and gate the Per-Session Duck toggle on `is_windows && supports_per_session`. | ✅ Shipped |
| `voice_typer/client/src/renderer/src/pages/Settings.tsx` | Add "Audio Enhancement" section with: Volume Backend status indicator, Auto Duck Volume toggle, Duck Level slider, **Duck Fade Duration slider (new — exposes `volume_duck_fade_ms`)**, Per-Session Duck toggle (auto-disabled on non-Windows), Noise Filter toggle, High-Pass Filter toggle, **High-Pass Cutoff slider (new — exposes `noise_filter_highpass_cutoff_hz`)**, Noise Gate toggle, **Noise Gate Threshold slider (new — exposes `noise_filter_gate_threshold`)**, RNNoise toggle, Post-Capture Cleanup toggle. | ✅ Shipped + 3 new sliders |
| `voice_typer/client/src/renderer/src/types/config.ts` | Add 11 new config fields | ✅ Shipped |
| `docs/architecture/auto-volume-duck.md` | This document (v2 → v2.1 with post-impl line numbers) | ✅ Updated |
| `tests/test_volume_ducker.py` | **NEW** — backend ABC, mute state, manual override, fade, crash recovery, concurrency | ✅ Shipped |
| `tests/test_audio_processor.py` | **NEW** — highpass, gate, RNNoise (mock), post-capture, passthrough | ✅ Shipped |
| `tests/test_volume_backends.py` | **v2 NEW** — 32 cross-platform tests for Linux (pactl/wpctl/amixer parsing), macOS (osascript fallback), Windows (smoke). | ✅ Shipped |
| `tests/test_volume_lifecycle.py` | **v2 NEW** — 18 integration tests for the full start/stop/cancel/quit/restart/crash-recovery lifecycle. | ✅ Shipped |
| `tests/test_recording_audio_processor.py` | **v2 NEW** — 7 regression tests for the recording callback path with an AudioProcessor attached (catches the v1 `filtered` NameError). | ✅ Shipped |
| `tests/test_server.py` | **v2 NEW** — 4 tests for the `get_volume_backend_status` IPC endpoint. | ✅ Shipped |
| `pyproject.toml` | Add platform-conditional deps (see §12) | ✅ Shipped |

---

## 12. Dependencies

### 12.1 Platform-conditional (volume backends)

```toml
# pyproject.toml
[project.optional-dependencies]
windows = ["pycaw", "comtypes"]
macos = ["pyobjc-core", "pyobjc-framework-CoreAudio"]
# Linux: no extra deps (pactl/wpctl/amixer are system binaries)
```

### 12.2 Cross-platform (noise filters)

```toml
[project.optional-dependencies]
noise-filter = [
    "noisereduce>=3.0",          # post-capture spectral gating
    "rnnoise-webrtc>=1.0",       # optional real-time neural denoise (prebuilt wheels)
]
# scipy already a dependency (used by recording.py for resampling)
```

### 12.3 Runtime detection

All imports are guarded:

```python
# volume_ducker.py / volume_backend.py
try:
    import pycaw  # Windows
except ImportError:
    pass

# audio_processor.py
try:
    import noisereduce
except ImportError:
    noisereduce = None

try:
    import rnnoise
except ImportError:
    rnnoise = None
```

If a library is missing, the corresponding feature is silently disabled (logged once). The app never crashes on a missing optional dep.

---

## 13. Relationship to Other Features

| Feature | Interaction |
|---|---|
| **Noise cancellation (AudioProcessor)** | Complementary to ducking. Ducking silences speakers (prevents bleed); noise filter cleans residual mic noise (fans, keyboard, HVAC). **Both ON = best quality.** Ducking runs first (at `_start_dictation`), then the filter processes the cleaner signal. |
| **Whisper `vad_filter`** | Independent. Whisper's VAD strips silence inside transcription. AudioProcessor's filters run before transcription. Both can be ON simultaneously. |
| **Silero VAD (future)** | Would feed the waveform bubble visualizer + replace RMS-based silence auto-stop. Separate from AudioProcessor. Not in v1.1.0. |
| **AudioQualityAnalyzer** | **Revived.** Was dead code (app.py:331). Now wired via `AudioProcessor.set_quality_callback()`. Produces clipping/noise/SNR reports for the mic diagnostics screen. |
| **Waveform bubble** | Receives RMS from the filtered audio (post-AudioProcessor). The bubble reflects what the transcriber will see, not raw mic input. |
| **Tier 1 background sound banner** | **Remove.** With auto-duck + noise filter, the banner is redundant. The user hears silence (ducked) and gets clean transcripts (filtered). |
| **Crash recovery (existing)** | Independent. `DuckCrashRecovery` is a new, separate file for volume state. The existing `CrashRecovery` class handles transcription text. |

---

## 14. Implementation Order

**Status legend:** ✅ shipped · 🔧 shipped + v2.1 bugfix · ⏳ not yet done

**Phase 1: Bug fixes (blockers, do first)**

1. ✅ Fix `_background_audio_monitor` AttributeError in `app.py:_cancel_dictation()` (the v1 line 1528). This was a live bug independent of this feature.

**Phase 2: Volume ducking (cross-platform)**

2. ✅ Create `volume_backend.py` — `VolumeBackend` ABC + `VolumeState`.
3. ✅ Implement `WinVolumeBackend` (pycaw, `SetMasterVolumeLevelScalar`, per-session support).
4. ✅ Implement `MacVolumeBackend` (CoreAudio via pyobjc, osascript fallback).
5. ✅ Implement `LinuxVolumeBackend` (pactl → wpctl → amixer detection).
6. ✅ Add `get_volume_backend()` factory in `platform.py`.
7. ✅ Create `duck_crash_recovery.py`.
8. ✅ Create `volume_ducker.py` — orchestrator with fade, manual-override, crash recovery.
9. ✅ Wire `VolumeDucker` into `app.py` at 6 points: `__init__`, `_start_dictation`, `_stop_dictation`, `_cancel_dictation`, `quit()`, `restart_app()`.
10. ✅ Add config fields + IPC allowlist entries.
11. ✅ Write `tests/test_volume_ducker.py`.
12. ✅ Add Settings UI (duck toggle, level slider, **fade duration slider (v2.1 new)**, per-session toggle, backend status indicator).

**Phase 3: Noise filtering**

13. ✅ Create `audio_processor.py` — highpass + noise gate (cheap, default ON).  🔧 v2.1: fix `_apply_highpass` to handle 2-D `(frames, 1)` sounddevice input.
14. ✅ Wire `AudioProcessor` into `Recorder.__init__()` and callback.  🔧 v2.1: fix callback ordering — `filtered` must be assigned BEFORE the lock-block that uses it (v1 raised silent NameError on every chunk).
15. ✅ Add post-capture `noisereduce` in `Recorder.stop()`.
16. ✅ Revive `audio_quality.py` — wire to `AudioProcessor.set_quality_callback()` and `_finalize_audio_quality_report()`.
17. ✅ Add RNNoise path (optional, default OFF).
18. ✅ Add config fields + IPC allowlist entries.
19. ✅ Write `tests/test_audio_processor.py`.
20. ✅ Add Settings UI (noise filter toggles + **high-pass cutoff slider + noise-gate threshold slider (v2.1 new)**).

**Phase 4: Polish**

21. ✅ Add platform-conditional deps to `pyproject.toml`.
22. ✅ Integration tests (start/stop/cancel/quit volume assertions).  v2.1 added `tests/test_volume_lifecycle.py` (18 tests), `tests/test_recording_audio_processor.py` (7 tests), `tests/test_volume_backends.py` (32 tests), and 4 new tests in `tests/test_server.py` for the `get_volume_backend_status` IPC endpoint.
23. ✅ Documentation (this file — updated with actual line numbers post-implementation).
24. ⏳ Manual cross-platform testing (Windows + macOS + Linux VM).  Automated tests cover the parsing logic; manual testing on real hardware is still recommended before release.

**Estimated effort:**
- Volume ducking: ~400 lines Python (backends + orchestrator + crash recovery) + ~80 lines tests + ~40 lines React.
- Noise filtering: ~250 lines Python (processor + quality wiring) + ~60 lines tests.
- Total: ~650 lines Python + ~140 lines tests + ~80 lines React.
- **Risk:** Medium. pycaw/pyobjc are mature, but cross-platform audio volume APIs have edge cases. Crash recovery + manual-override + fade add complexity. Noise filter in audio callback is performance-sensitive.

---

## 15. Open Questions

| # | Question | Default decision |
|---|---|---|
| 1 | Should per-session ducking (Windows) be default ON? | No — default OFF. Master volume is simpler and matches macOS/Linux behavior. Users who want it opt in. |
| 2 | Should RNNoise be in the audio callback or a consumer thread? | Callback for v1.1.0 (default OFF). If xruns appear in testing, move to consumer thread in v1.2. |
| 3 | Should the noise gate threshold be auto-calibrated (sample first 0.5s of silence)? | No for v1.1.0 — fixed default (-45dBFS) + user-configurable slider. Auto-calibration is a v1.2 enhancement. |
| 4 | Should ducking apply to Bluetooth headsets differently? | No — master volume applies regardless of output device. If a user wears headphones, ducking still works (reduces headphone volume). |
| 5 | Should there be a "duck only if speakers detected" heuristic? | No — too fragile. User can disable ducking in settings if they always use headphones. |
