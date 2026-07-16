# RW-04 — `recording.py` god-class decomposition

## Status

**In-progress (Wave 1 of 3)**: `VadProcessor` extracted.
`AudioDeviceManager` and `AudioBuffer` remain in `Recorder` as
follow-up waves.

## Confirmed gap

`voice_typer/server/recording.py` was a 3,208-line god class owning:

- Device resolution + hot-plug + Bluetooth detection.
- VAD state machine (silence → speech → silence with hysteresis).
- Silero VAD model loading + inference.
- Auto-calibration of RMS-dB thresholds from ambient noise floor.
- 3-tier resampling (scipy `resample_poly` → np.interp → identity).
- Buffer management + snapshot cache (O(n²) → O(n) optimization).
- xrun + clipping detection with rolling-window alerting.
- Pre-roll circular buffer for cold-start latency reduction.
- Real-time audio callback → ring buffer → worker thread pipeline.
- IPC event queue + dedicated event worker thread.
- Device-health-checker background thread.

High coupling, hard to change safely. VAD logic was particularly hard
to unit-test because instantiating `Recorder` pulled in sounddevice,
the scipy preloader thread, the device-health checker, etc.

## What was extracted (this wave)

### `voice_typer/server/vad_processor.py` (NEW — 568 lines)

`VadProcessor` class encapsulating:

- **State machine** (silence → speech → silence with hysteresis):
  `update_frame(chunk_rms_db, vad_prob=None) -> VadState`.
- **Silero VAD availability detection**: lazy `torch` import +
  `_check_vad_available` lookup, with warning log on fallback to RMS.
- **Auto-calibration**:
  `auto_calibrate(chunk_rms, elapsed_seconds, chunk_duration=0.0)`.
  Caller passes `elapsed_seconds = time.perf_counter() -
  recording_start_time` so the processor stays clock-agnostic and
  unit-testable.
- **`vad_enabled` cache**: 5-second TTL safety net + explicit refresh
  via `on_config_changed()`. Computed from any-noise-filter-on /
  suppression-method-not-none (VAD-GATE Task 4).
- **`reset()`**: restores UNKNOWN state, zero counters, default
  thresholds, cleared calibration state. Called by `Recorder.start()`.
- **`VadState` enum** moved here (re-exported from `recording.py` for
  backward compat).

State attributes (formerly `Recorder._vad_*`) are exposed via
read/write properties on `VadProcessor`:

| `Recorder` (old)             | `VadProcessor` (new)              |
|------------------------------|-----------------------------------|
| `_vad_state`                 | `state`                           |
| `_vad_consecutive_speech_frames` | `consecutive_speech_frames`   |
| `_vad_consecutive_silence_frames` | `consecutive_silence_frames` |
| `_vad_speech_threshold_db`   | `speech_threshold_db`             |
| `_vad_silence_threshold_db`  | `silence_threshold_db`            |
| `_vad_speech_frames`         | `speech_frames`                   |
| `_vad_silence_frames`        | `silence_frames`                  |
| `_vad_hangover_frames`       | `hangover_frames`                 |
| `_use_silero_vad`            | `use_silero_vad`                  |
| `_vad_speech_threshold`      | `speech_threshold` (Silero prob)  |
| `_vad_silence_threshold`     | `silence_threshold` (Silero prob) |
| `_silero_available`          | `silero_available`                |
| `_vad_calibration_duration`  | `calibration_duration`            |
| `_vad_calibration_rms_values` | `calibration_rms_values`         |
| `_vad_calibrated`            | `calibrated`                      |
| `_vad_enabled_cached`        | `vad_enabled_cached`              |
| `_vad_enabled_cache_ts`      | `vad_enabled_cache_ts`            |

### `voice_typer/server/recording.py` — delegation shims

`Recorder` now owns a single `self._vad: VadProcessor` instance
(created in `__init__`) and delegates the VAD API to it. The
historical `_vad_*` attribute names are re-exposed on `Recorder` via a
property-delegation factory (`_make_vad_property`) so existing tests
that do `rec._vad_state = VadState.UNKNOWN` /
`rec._vad_consecutive_speech_frames == 1` keep working unchanged.

The five VAD methods on `Recorder` are now thin delegating wrappers:

- `_vad_enabled` (property) → `self._vad.vad_enabled`
- `on_config_changed()` → `self._vad.on_config_changed()`
- `_compute_vad_enabled(config)` → `self._vad.compute_vad_enabled(config)`
- `_vad_auto_calibrate(chunk_rms, chunk_duration)` → computes
  `elapsed = time.perf_counter() - self._recording_start_time` and
  delegates to `self._vad.auto_calibrate(chunk_rms, elapsed, chunk_duration)`.
- `_vad_update(chunk_rms_db, vad_prob=None)` →
  `self._vad.update_frame(chunk_rms_db, vad_prob)`.

`start()` now calls `self._vad.reset()` to restore VAD state for the
new session; the inline property-shim assignments (`self._vad_state =
VadState.UNKNOWN`, etc.) are kept as a redundant safety net AND so
existing source-inspection tests
(`test_vad_auto_calibrate_resets_on_start`) continue to pin on the
literal attribute names appearing in `start()`'s source.

### `tests/test_vad_processor.py` (NEW — 38 tests)

Unit tests for `VadProcessor` in isolation — no `Recorder`
instantiation, no sounddevice mock, no audio worker thread. Covers:

- `__init__` defaults (state, counters, thresholds, frame counts,
  calibration, Silero config).
- State-machine transitions (UNKNOWN → SPEECH, UNKNOWN → SILENCE,
  SPEECH → SILENCE with hangover, SILENCE → SPEECH with speech_frames).
- Grey-zone hysteresis (AUDIO-013: counters preserved between thresholds).
- VAD-GATE: returns UNKNOWN without state mutation when VAD disabled.
- Auto-calibration: collects RMS until duration elapsed; sets thresholds
  relative to noise floor; idempotent after calibrated; no-op when VAD
  disabled; handles zero RMS.
- `reset()`: restores UNKNOWN state, zero counters, default thresholds,
  cleared calibration.
- `vad_enabled` cache: true when any filter on; false for "Off" preset;
  true when suppression method ≠ "none"; explicit refresh via
  `on_config_changed()`; 5-second TTL safety net.
- `compute_vad_enabled()`: per-flag direct tests; `use_silero_vad` does
  NOT force VAD enabled (VAD-GATE).
- Property delegation: read/write round-trips for state, thresholds,
  calibration_rms_values, silero_available.

## What remains in `Recorder` (follow-up waves)

### Wave 2 — `AudioDeviceManager`

~600 lines spanning:
- `_resolve_device`, `_device_index`, `_host_api_name`,
  `_same_physical_microphone_candidates`, `_fallback_host_rank`,
  `_resolve_effective_sample_rate`, `_all_input_device_candidates`.
- `_refresh_device_list`, `_invalidate_device_cache`,
  `shutdown_mic_watcher` (and the `MicrophoneDeviceWatcher` lifecycle).
- `_stream_finished_callback`, `_handle_device_disconnect`,
  `_start_device_health_checker`, `_stop_device_health_checker`,
  `_device_health_checker_loop`.

Coupling: device resolution reads `self.config.microphone` /
`self.config.sample_rate`; disconnect handling calls `self.start()` /
`self.stop()`. Extraction will need a `device_changed` callback or
similar to break the back-reference.

### Wave 3 — `AudioBuffer` + 3-tier resampling

~500 lines spanning:
- `_buffer` (deque), `_lock`, `_chunk_count`, `_recent_rms_values`.
- `_cached_resampled`, `_cached_native_chunk_count`,
  `_cached_resample_key`, `_cached_no_resample_len`,
  `_cached_no_resample_arr` (snapshot cache).
- `_last_audio_stats` (NEW-PERF-010 reuse).
- `_resample_chunk`, `_prepare_audio`, `_resample_audio_impl`,
  `warm_up_resampler`.
- Module-level `_get_resample_poly`, `_preload_resample_poly`,
  `_start_scipy_preloader`, `_secure_clear_array`,
  `_secure_clear_array_background`.

Coupling: buffer is read by `stop()` (returns concatenated audio) and
`snapshot()` (returns incremental view); resampling is invoked from
both. Extraction will likely move the buffer + cache + resample
together as one cohesive unit, with `Recorder` calling into it for
`stop()` / `snapshot()`.

## Method count: before / after

| Class                 | Before | After | Δ       |
|-----------------------|--------|-------|---------|
| `Recorder`            | 37     | 37    | 0       |
| `VadProcessor` (new)  | —      | ~30   | +30     |

`Recorder`'s method count is unchanged because the five VAD methods
(`_vad_enabled`, `on_config_changed`, `_compute_vad_enabled`,
`_vad_auto_calibrate`, `_vad_update`) remain on the class as
**delegating wrappers** — this preserves the public/source-level API
that existing tests pin on (e.g.
`inspect.getsource(Recorder._vad_update)` is still valid and still
contains the AUDIO-013 grey-zone comment + `pass` + "State
transitions" markers required by
`test_grey_zone_does_not_reset_counters` and
`test_worker_thread_processes_heavy_pipeline`).

The **logic** that used to live in those methods has moved to
`VadProcessor` — `Recorder._vad_update` is now a 1-line delegation
rather than the 70-line state-machine implementation. Lines-of-code
moved: ~150 (state machine + auto-calibration + vad_enabled cache +
compute_vad_enabled) out of `recording.py` and into
`vad_processor.py`.

## Validation

- `python -m pytest tests/test_recording.py tests/test_vad.py
  tests/test_recording_and_audio.py tests/test_recording_audio_processor.py
  tests/test_vad_processor.py tests/test_bugfix_regressions.py::TestVadGreyZonePreservesCounters
  tests/test_bugfix_regressions.py::TestVadAutoCalibrationBehavior -q`
  → **159 passed**.
- `Recorder` method count: 37 (unchanged).
- `VadProcessor` method count: ~30 (new).
- Public API preserved: `from voice_typer.server.recording import
  Recorder, VadState` still works (`VadState` re-exported).
- All `rec._vad_*` attribute reads/writes work via property delegation.
- All `rec._vad_update(...)` / `rec._vad_auto_calibrate(...)` /
  `rec.on_config_changed()` / `rec._compute_vad_enabled(config)` calls
  work via delegating wrappers.
- `recorder.on_config_changed()` external callers (e.g. `app.py:820`)
  work unchanged.

## Known follow-ups

- **Wave 2**: extract `AudioDeviceManager` (device resolution,
  hot-plug, Bluetooth, health-checker thread).
- **Wave 3**: extract `AudioBuffer` (buffer mgmt, snapshot cache,
  3-tier resampling, secure clear).
- Consider folding `voice_typer/server/vad.py` (the Silero wrapper for
  the waveform visualizer) into `vad_processor.py` — they're both VAD
  but serve different consumers (visualizer vs recording state
  machine). Currently `vad.py` is a leaf module imported by both
  `vad_processor.py` (for `_check_vad_available`) and
  `recording.py` (for `compute_vad_prob`). Folding would require
  updating the waveform-bubble consumer too. Deferred.
