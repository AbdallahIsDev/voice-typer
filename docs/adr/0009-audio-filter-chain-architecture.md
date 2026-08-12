# ADR 0009: Audio Filter Chain Architecture

## Status

Accepted

**Date**: 2026-06-30
**Supersedes**: The monolithic `AudioProcessor` design (NATIVE-001 era)
**Related**: ADR 0007 (native hotkey architecture), ADR 0008 (zero-command hotkey)

---

## 1. Context

The current noise filtering system has **7 mechanisms** across 4 files, with 3 critical bugs and 4 missing capabilities. This ADR specifies the target architecture: an OBS-inspired filter chain that fixes all known issues, adds the missing filters, and simplifies the UX to a preset dropdown + progressive-disclosure Custom panel.

### Current-state problems (must all be fixed)

1. **Live config changes don't reach dictation.** `app._audio_processor` is built once at startup and never rebuilt. Settings UI changes take effect in the level bar and mic test, but NOT in actual dictation until app restart.
2. **The "Recommended" preset is broken.** Config defaults (`highpass=True, gate=True, rnnoise=False, post_capture=True`) don't match the preset (`highpass=False, gate=False, rnnoise=True, post_capture=False`). Since RNNoise silently no-ops (library not installed), clicking "Recommended" yields zero filtering.
3. **Two filter layers silently no-op.** `pyrnnoise` and `noisereduce` are optional deps not in the default install. Config flags default ON but libraries are missing → filters do nothing, no warning.
4. **Duplicate AGCs.** Layer B4 (`AudioProcessor._apply_normalization`, per-chunk peak, 4× cap) and Layer C1 (`Recorder._agc_update`, slow RMS, ~1s) run in series, uncoordinated. B4 is undocumented, not in UI, can pump on transients.
5. **Inconsistent defaults.** Three different values for the noise-gate threshold (`0.003` in config, `0.015` in UI tooltip, `0.015` in `level_monitor` fallback). Two different values for gate hold (`300ms` in dataclass, `150ms` in config).
6. **Dead config fields.** `silence_rms_threshold`, `silence_peak_threshold` — declared, validated, never read.
7. **Stale module docstring.** `audio_processor.py` documents 4 layers; code has 5 (B4 added later, doc never updated).
8. **Streaming path misses post-capture denoise.** `noisereduce` runs only in `stop()`; the incremental streaming ASR path gets un-denoised audio.

---

## 2. Decision — Filter Chain Architecture

Adopt the **filter chain pattern** (like OBS Studio). Each filter is an independent class implementing a common interface. Filters are composed into an ordered chain. The chain is rebuilt on every config change so live edits take effect immediately.

### 2.1 The chain

```
Mic → HighPass → NoiseSuppressor → NoiseGate → Equalizer → Compressor → Limiter → ASR
      (80Hz)     (RNNoise/Speex/    (expander)  (3-band)    (3:1)        (-6dB)
                  DeepFilterNet)
```

**Order rationale** (matches OBS best practice):
1. **HighPass** first — removes low-frequency rumble before it hits the neural denoiser (improves denoiser accuracy).
2. **NoiseSuppressor** second — removes stationary/non-stationary noise while the signal is still raw.
3. **NoiseGate** third — closes the mic during silence so downstream filters don't process noise.
4. **Equalizer** fourth — shape the tone after noise is gone.
5. **Compressor** fifth — even out dynamics after tone shaping.
6. **Limiter** last — brick-wall safety net before ASR.

### 2.2 Filter interface

```python
class AudioFilter(ABC):
    """Base class for all audio filters in the chain."""

    name: str  # display name for UI/diagnostics

    @abstractmethod
    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        """Process a chunk of audio. Must be stateful across calls
        (IIR filters, envelope followers, etc. carry state)."""
        ...

    def reset(self) -> None:
        """Reset internal state (called on mic change / restart)."""
        pass

    @property
    def latency_ms(self) -> float:
        """Added latency in milliseconds (0 for sample-by-sample filters)."""
        return 0.0
```

### 2.3 FilterChain

```python
class FilterChain:
    """Ordered list of AudioFilter instances."""

    def __init__(self, filters: list[AudioFilter]):
        self._filters = filters

    def process(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        for f in self._filters:
            audio = f.process(audio, sample_rate)
            if audio is None or audio.size == 0:
                return audio  # filter buffered, propagate
        return audio

    def reset(self) -> None:
        for f in self._filters:
            f.reset()

    @property
    def filters(self) -> list[AudioFilter]:
        return list(self._filters)
```

---

## 3. Filters — Specification

All filters operate on `float32` numpy arrays, mono, sample-rate-agnostic (passed as argument). State is per-instance. All dynamics filters use the **OBS one-pole envelope smoother**: `coefficient = exp(-1 / (sample_rate * time_seconds))`.

### 3.1 HighPassFilter

- **Algorithm**: scipy `butter(order=4, cutoff, btype="high")` via `lfilter` with `zi` state. Order 4 (was 2 — steeper rolloff, 24dB/oct).
- **Anti-denormal**: add `1.0 / 4294967295.0` to the first `zi` state on init.
- **Config**: `noise_filter_highpass` (bool, default True), `noise_filter_highpass_cutoff_hz` (float, default 80.0, range 20–500).
- **Latency**: 0ms (IIR).

### 3.2 NoiseSuppressor (multi-backend)

- **Backends** (runtime-switchable via `noise_suppression_method`):
  - `"rnnoise"` — `pyrnnoise` package, 480-sample frames, default model. **Default dependency** (not optional).
  - `"deepfilternet"` — `deepfilternet` package (requires torch, already installed). Higher quality, 2-3× CPU. Offered as premium option.
  - `"speex"` — `speexdsp` preprocessor. Lightest CPU. Fallback for low-end devices.
  - `"none"` — passthrough.
- **Resampling**: RNNoise requires 48kHz; DeepFilterNet requires 48kHz; Speex is rate-agnostic. If source rate ≠ 48kHz, round-trip resample via `scipy.signal.resample_poly`.
- **Frame buffering**: maintain input/output deques (like OBS). Return `None` from `process()` when buffer is underfilled — `FilterChain.process()` propagates `None` to signal the recorder callback to skip this chunk.
- **Config**: `noise_filter_rnnoise` (bool, default True), `noise_suppression_method` (str, default `"rnnoise"`).
- **Latency**: ~10ms (one frame).
- **Graceful degradation**: if the selected backend's library is missing, log a WARNING once, fall back to `"none"`, and set a `degraded` flag the UI can read via `get_audio_status` IPC.

### 3.3 NoiseGate (downward expander)

- **Algorithm**: OBS-style peak-hold level estimator + state machine + linear attack/release ramp.
- **Detection**: peak (not RMS) — `level = max(level, |sample|) - decay_rate`, where `decay_rate = (open_threshold - close_threshold) / (sample_rate / 75)`.
- **State machine**:
  - `level > open_threshold` → `is_open = True`
  - `level < close_threshold` → `is_open = False`, `held_time = 0`
  - If open: `attenuation = min(1, attenuation + attack_rate)`
  - If closed and `held_time > hold_time`: `attenuation = max(0, attenuation - release_rate)`
- **All thresholds in dB**, converted to linear via `db_to_mul(db) = 10^(db/20)`.
- **Config**: `noise_filter_gate` (bool, default True), `noise_filter_gate_open_threshold_db` (float, default -26.0), `noise_filter_gate_close_threshold_db` (float, default -32.0), `noise_filter_gate_attack_ms` (float, default 25.0), `noise_filter_gate_hold_ms` (float, default 200.0), `noise_filter_gate_release_ms` (float, default 150.0).
- **Latency**: 0ms.
- **Migration**: the old `noise_filter_gate_threshold` (single threshold, linear 0.003) is migrated to `open=-26dB, close=-32dB` (closest equivalent). Old field kept in config for backward compat but ignored.

### 3.4 Equalizer (3-band)

- **Algorithm**: OBS-style cascaded one-pole crossovers at 800Hz (low/mid) and 5kHz (mid/high), 3-sample delay line for phase alignment, anti-denormal epsilon on first stage.
- **Bands**: Low (<800Hz), Mid (800Hz–5kHz), High (>5kHz).
- **Config**: `noise_filter_eq` (bool, default True), `noise_filter_eq_low_db` (float, default -3.0, range -20..+20), `noise_filter_eq_mid_db` (float, default +3.0, range -20..+20), `noise_filter_eq_high_db` (float, default +2.0, range -20..+20).
- **Latency**: 3 samples (~0.06ms at 48kHz).

### 3.5 Compressor

- **Algorithm**: OBS-style peak envelope follower + dB-domain gain + one-pole attack/release smoothing.
- **Envelope**: `env = sample + attack_coeff * (env - sample)` (attack if rising, release if falling). Per-channel, max-merged.
- **Gain**: `gain_db = slope * (threshold_db - env_db)`, clamped to `<= 0`, where `slope = 1 - 1/ratio`.
- **Output**: `sample *= db_to_mul(gain_db) * db_to_mul(output_gain_db)`.
- **Config**: `noise_filter_compressor` (bool, default True), `noise_filter_compressor_threshold_db` (float, default -18.0), `noise_filter_compressor_ratio` (float, default 3.0, range 1..32), `noise_filter_compressor_attack_ms` (float, default 6.0), `noise_filter_compressor_release_ms` (float, default 60.0), `noise_filter_compressor_output_gain_db` (float, default 0.0, range -32..+32).
- **Latency**: 0ms.
- **Replaces**: B4 (`_apply_normalization`) and C1 (`_agc_update`). Both deleted.

### 3.6 Limiter

- **Algorithm**: Compressor with `slope=1.0`, hardcoded `attack=1ms`.
- **Config**: `noise_filter_limiter` (bool, default True), `noise_filter_limiter_ceiling_db` (float, default -6.0, range -60..0), `noise_filter_limiter_release_ms` (float, default 60.0).
- **Latency**: 0ms.

### 3.7 NotchFilter (50/60Hz hum) — optional, default OFF

- **Algorithm**: scipy `iirnotch(f0, Q=30)` via `lfilter` with `zi` state.
- **Config**: `noise_filter_notch` (bool, default False), `noise_filter_notch_frequency_hz` (float, default 0.0 — 0 means auto-detect from locale: 50 for EU/Asia, 60 for Americas).
- **Latency**: 0ms.

### 3.8 Post-capture denoise — REMOVED

- **Decision**: Delete the post-capture *denoise filter* (Layer B3 in the
  original `AudioProcessor`) entirely.
- **Rationale**: Real-time NoiseSuppressor makes it redundant. The streaming path never used it. The "first 0.5s is silence" assumption was fragile. `noisereduce` is removed from dependencies.
- **Note (GT-58)**: the `noise_filter_post_capture` *Config field* is
  retained as a runtime gate (read by `level_monitor.py` and
  `microphone_test.py`). See §5.4 below — it is NOT deprecated.

---

## 4. VAD (Voice Activity Detection) — Changes

VAD is NOT part of the filter chain (it's decision-only, doesn't modify audio). But it has related changes:

### 4.1 Enable Silero VAD by default

- **Current**: `use_silero_vad=False` because "torch not installed." But torch IS installed (project depends on it).
- **New default**: `use_silero_vad=True`. Silero model loaded via `torch.hub.load('snakers4/silero-vad', 'silero_vad')` with local cache fallback.
- **Graceful degradation**: if torch import fails or model download fails, fall back to RMS-dB VAD with a WARNING log.

### 4.2 Feed recording VAD timestamps to Whisper

- **Current**: Whisper re-runs its own Silero VAD on the post-processed audio. Recording VAD output is not passed to Whisper.
- **New**: pass `vad_parameters=dict(min_silence_duration_ms=500, speech_pad_ms=200)` AND the recording VAD's speech timestamps as `vad_timestamps` to `transcribe()`. Whisper skips its internal VAD when timestamps are provided. Saves one Silero pass.

### 4.3 Delete dead config fields

- `silence_rms_threshold` — removed (never read).
- `silence_peak_threshold` — removed (never read).

---

## 5. Config Changes

### 5.1 New fields

```python
# Filter chain master switch (replaces noise_filter_enabled)
audio_preset: str = "auto"  # "auto" | "studio" | "noisy_room" | "off" | "custom"

# NoiseSuppressor backend selection
noise_suppression_method: str = "rnnoise"  # "rnnoise" | "deepfilternet" | "speex" | "none"

# NoiseGate (OBS-style, replaces single threshold)
noise_filter_gate_open_threshold_db: float = -26.0
noise_filter_gate_close_threshold_db: float = -32.0
noise_filter_gate_attack_ms: float = 25.0
noise_filter_gate_release_ms: float = 150.0

# Equalizer
noise_filter_eq: bool = True
noise_filter_eq_low_db: float = -3.0
noise_filter_eq_mid_db: float = 3.0
noise_filter_eq_high_db: float = 2.0

# Compressor
noise_filter_compressor: bool = True
noise_filter_compressor_threshold_db: float = -18.0
noise_filter_compressor_ratio: float = 3.0
noise_filter_compressor_attack_ms: float = 6.0
noise_filter_compressor_release_ms: float = 60.0
noise_filter_compressor_output_gain_db: float = 0.0

# Limiter
noise_filter_limiter: bool = True
noise_filter_limiter_ceiling_db: float = -6.0
noise_filter_limiter_release_ms: float = 60.0

# Notch (optional, off by default)
noise_filter_notch: bool = False
noise_filter_notch_frequency_hz: float = 0.0  # 0 = auto-detect
```

### 5.2 Removed fields

> **GT-58 (2026-07 update)**: the fields listed below were *previously*
> kept on the `Config` dataclass with `# DEPRECATED` comments "for
> backward compat". They have now been **removed from the dataclass
> entirely** — they were declared, validated, and persisted but never
> read at runtime. Existing `config.json` files written by older app
> versions that still carry these keys load without raising because the
> v3 schema migration (`_migrate_to_v3`) silently scrubs them before
> construction. Do NOT re-add these fields.

- `normalize_audio` — removed (replaced by Compressor).
- `normalize_target_peak` — removed (replaced by Compressor).
- `silence_rms_threshold` — removed (dead code).
- `silence_peak_threshold` — removed (dead code).
- `noise_filter_gate_threshold` — removed (replaced by open/close thresholds).
- `volume_duck_per_session` — removed (ducking now always applies to master volume cross-platform).
- `volume_duck_smart` — removed (smart duck is always ON when `volume_duck_enabled` is True).

### 5.3 Modified defaults

- `use_silero_vad`: `False` → `True` (torch is installed).
- `noise_filter_rnnoise`: `False` → `True` (RNNoise is now a default dep).
- `noise_filter_highpass_cutoff_hz`: stays `80.0` (but filter order goes from 2 to 4).
- `noise_filter_gate_hold_ms`: stays `200.0` (was inconsistent — dataclass said 300, config said 150; now unified to 200 to match OBS).

### 5.4 Runtime switches (NOT deprecated)

> **GT-58 (2026-07 update)**: previous revisions of this ADR labelled
> these fields "deprecated" and listed `noise_filter_post_capture` under
> §5.2 "Removed fields". That was incorrect — they are actively read at
> runtime by `level_monitor.py` and synced by `config_applier.py`. The
> misleading `# DEPRECATED` comments on the dataclass fields have been
> removed. These fields remain first-class `Config` dataclass members
> and are NOT scrubbed by the v3 schema migration.

- `noise_filter_enabled` — runtime switch read by `level_monitor.py`
  (`if not config_dict.get("noise_filter_enabled", True)`). Synced from
  `audio_preset` by `config_applier.py` (`config.noise_filter_enabled =
  preset != "off"`), so its on-disk value is a derived cache. Old
  configs with `noise_filter_enabled=False` are migrated to
  `audio_preset="off"` by the v1→v2 schema migration, after which
  `apply_preset` re-derives the runtime value on every load.
- `noise_filter_post_capture` — runtime switch read by
  `level_monitor.py` and `microphone_test.py`. The post-capture
  *filter* (Layer B3 in the original `AudioProcessor`) was removed, but
  the flag itself is still consulted as a runtime gate, so the
  dataclass field is retained.

### 5.5 Preset definitions (single source of truth)

**File**: `voice_typer/server/audio_presets.py` (NEW — eliminates the 3-way duplication).

```python
PRESETS = {
    "auto": {
        "noise_filter_highpass": True,
        "noise_suppression_method": "rnnoise",
        "noise_filter_gate": True,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": False,
    },
    "studio": {
        "noise_filter_highpass": True,
        "noise_suppression_method": "none",
        "noise_filter_gate": False,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": False,
    },
    "noisy_room": {
        "noise_filter_highpass": True,
        "noise_suppression_method": "deepfilternet",  # best quality
        "noise_filter_gate": True,
        "noise_filter_eq": True,
        "noise_filter_compressor": True,
        "noise_filter_limiter": True,
        "noise_filter_notch": True,
    },
    "off": {
        "noise_filter_highpass": False,
        "noise_suppression_method": "none",
        "noise_filter_gate": False,
        "noise_filter_eq": False,
        "noise_filter_compressor": False,
        "noise_filter_limiter": False,
        "noise_filter_notch": False,
    },
    # "custom" = no automatic changes; user controls each filter
}
```

**Applied at**: (a) startup in `Config.load()` via `apply_preset_if_set()`, (b) on explicit `set_config` with `audio_preset` key. NOT just on explicit click — this fixes the "preset never applied at startup" bug.

---

## 6. Bug Fixes (mandatory)

### 6.1 Live config rebuild

**File**: `voice_typer/server/service.py` — `apply_config_side_effects()`

When any `noise_filter_*` or `audio_preset` or `noise_suppression_method` key is in the update:
1. Call `app._rebuild_audio_processor()` (new method).
2. `_rebuild_audio_processor()` creates a new `FilterChain` from the current config, atomically swaps `app._audio_processor._chain`, and calls `reset()` on the old chain.
3. The next `process_chunk()` call uses the new chain.

This replaces the current behavior where only `update_level_processor()` and `update_test_filters()` are called.

### 6.2 Preset defaults match config defaults

The `audio_preset="auto"` preset (the new default) exactly matches the new config defaults. This fixes the "Recommended preset doesn't match defaults" bug.

### 6.3 RNNoise is a default dependency

**File**: `pyproject.toml`

Move `pyrnnoise` from the `[noise-filter]` optional extra to the main `[dependencies]` list. Remove the `[noise-filter]` extra entirely (noisereduce is deleted, RNNoise is now required).

Add `deepfilternet` to a new `[deepfilternet]` optional extra (requires torch, which is already a dep):
```toml
[project.optional-dependencies]
deepfilternet = ["deepfilternet>=0.5"]
```

### 6.4 Graceful degradation reporting

**New IPC**: `get_audio_status` returns:
```json
{
  "filter_chain": ["HighPass", "NoiseSuppressor(rnnoise)", "NoiseGate", "EQ", "Compressor", "Limiter"],
  "degraded": false,
  "degraded_reasons": [],
  "vad_backend": "silero",
  "sample_rate": 48000
}
```

If a filter's library is missing, `degraded=true` and `degraded_reasons` lists which filters fell back. The UI shows a warning banner.

---

## 7. Settings UI

### 7.1 Preset dropdown (replaces 7 individual controls)

```
┌─ Audio Enhancement ─────────────────────────────────┐
│ Microphone Quality:     [Auto ▼]                     │
│   ○ Auto (recommended)                               │
│   ○ Studio (clean environment)                       │
│   ○ Noisy Room (keyboard/fan/HVAC)                   │
│   ○ Off                                               │
│   ○ Custom (advanced)                                │
└──────────────────────────────────────────────────────┘
```

- Only visible if `audio_preset` is `"custom"`.
- Each filter is a row with a toggle. When ON, the row expands (progressive disclosure) to reveal its parameters.
- When OFF, parameters collapse away.

### 7.2 Custom panel layout

```
Noise Suppression:        [ON] ▼
  Method:                 (●) RNNoise  ( ) DeepFilterNet  ( ) Speex

Noise Gate:               [ON] ▼
  Open Threshold:         ████░░░░░░  -26dB
  Close Threshold:        ███░░░░░░░  -32dB
  Attack Time:            ░░░░░░░░░░  25ms
  Hold Time:              ████░░░░░░  200ms
  Release Time:           ███░░░░░░░  150ms

Equalizer:                [ON] ▼
  Low (bass):             ░░░░░░░░░░  -3dB
  Mid (speech):           █░░░░░░░░░  +3dB
  High (treble):          ░░░░░░░░░░  +2dB

Compressor:               [ON] ▼
  Threshold:              ████░░░░░░  -18dB
  Ratio:                  ███░░░░░░░  3:1
  Attack:                 ░░░░░░░░░░  6ms
  Release:                █░░░░░░░░░  60ms
  Output Gain:            ░░░░░░░░░░  0dB

Limiter:                  [ON] ▼
  Ceiling:                ████████░░  -6dB
  Release:                █░░░░░░░░░  60ms

High-Pass Filter:         [ON] ▼
  Cutoff:                 ██░░░░░░░░  80Hz

Notch Filter (hum):       [OFF] ▼
  Frequency:              (●) Auto-detect  ( ) 50Hz  ( ) 60Hz
```

### 7.3 Preset behavior

- Selecting a named preset (Auto/Studio/Noisy Room/Off): hide Custom panel, apply preset to config, save.
- Selecting Custom: show Custom panel, populate with current filter values (inherited from active preset). User tweaks from there.
- The preset dropdown is the ONLY control visible by default. Most users never see the Custom panel.

---

## 8. File Inventory

### 8.1 New files

| File | Purpose | ~LOC |
|---|---|---|
| `voice_typer/server/audio_filters/__init__.py` | Package init, exports | 10 |
| `voice_typer/server/audio_filters/base.py` | `AudioFilter` ABC, `FilterChain`, `db_to_mul`, `mul_to_db`, `one_pole_coeff` | 80 |
| `voice_typer/server/audio_filters/highpass.py` | `HighPassFilter` | 50 |
| `voice_typer/server/audio_filters/noise_suppressor.py` | `NoiseSuppressor` (RNNoise/DeepFilterNet/Speex backends) | 250 |
| `voice_typer/server/audio_filters/noise_gate.py` | `NoiseGate` (OBS-style) | 120 |
| `voice_typer/server/audio_filters/equalizer.py` | `Equalizer` (3-band) | 100 |
| `voice_typer/server/audio_filters/compressor.py` | `Compressor` | 130 |
| `voice_typer/server/audio_filters/limiter.py` | `Limiter` | 60 |
| `voice_typer/server/audio_filters/notch.py` | `NotchFilter` | 40 |
| `voice_typer/server/audio_presets.py` | Single source of truth for preset → filter mapping | 60 |
| `voice_typer/server/audio_chain_builder.py` | `build_chain(config) -> FilterChain` factory | 80 |
| `tests/test_audio_filters.py` | Unit tests for each filter | 400 |
| `tests/test_audio_chain.py` | Integration tests for chain + presets + live rebuild | 200 |

### 8.2 Modified files

| File | Changes |
|---|---|
| `voice_typer/server/audio_processor.py` | Gut the monolithic processor. Keep `AudioProcessor` as a thin wrapper around `FilterChain`. Delete `_apply_normalization` (B4), `_apply_noise_gate` (moved to chain), `_apply_rnnoise` (moved to chain), `_apply_highpass` (moved to chain), `process_full_audio` (post-capture deleted). Keep `process_chunk` as `chain.process()`. Update docstring. |
| `voice_typer/server/recording/` | Delete `_agc_update` (C1 — replaced by Compressor in chain). Delete `_apply_normalization` call. Keep VAD. |
| `voice_typer/server/config.py` | Add new fields (§5.1), remove deleted fields (§5.2), change defaults (§5.3). Add migration logic in `load()`: if `noise_filter_enabled=False` → `audio_preset="off"`. |
| `voice_typer/server/service.py` | Fix `apply_config_side_effects` to call `app._rebuild_audio_processor()` on noise_filter_* changes (§6.1). Move preset mapping to `audio_presets.py`. |
| `voice_typer/server/app.py` | Add `_rebuild_audio_processor()` method. Delete `_audio_processor` construction in `__init__` (deferred to `_rebuild_audio_processor` called from `__init__`). |
| `voice_typer/server/ipc_server.py` | Add `get_audio_status` IPC handler. |
| `voice_typer/server/vad.py` | Change `use_silero_vad` default to True. Add graceful fallback if torch/model unavailable. |
| `voice_typer/server/transcription.py` | Pass recording VAD timestamps to Whisper to skip duplicate VAD pass (§4.2). |
| `voice_typer/server/level_monitor.py` | Unify gate threshold fallback to `-26dB` (was `0.015` linear — different from recorder). |
| `voice_typer/client/src/renderer/src/pages/Settings.tsx` | Replace 7 noise filter controls with preset dropdown + progressive-disclosure Custom panel (§7). |
| `voice_typer/client/src/renderer/src/components/AudioPresetSelector.tsx` | Update preset list to 5 (Auto/Studio/Noisy Room/Off/Custom). Fetch preset definitions from backend (single source of truth). |
| `voice_typer/client/src/renderer/src/types/config.ts` | Add new fields, remove deleted fields. |
| `voice_typer/client/src/renderer/src/i18n/translations/en.json` | Add translation keys for all new UI strings. |
| `pyproject.toml` | Move `pyrnnoise` to main deps. Remove `[noise-filter]` extra. Add `[deepfilternet]` extra. Remove `noisereduce`. |
| `scripts/build/voice-typer.spec` | Add `pyrnnoise` to hiddenimports (was optional, now required). |

### 8.3 Deleted code

- `AudioProcessor._apply_normalization` (B4)
- `AudioProcessor._apply_noise_gate` (moved to `audio_filters/noise_gate.py`)
- `AudioProcessor._apply_rnnoise` (moved to `audio_filters/noise_suppressor.py`)
- `AudioProcessor._apply_highpass` (moved to `audio_filters/highpass.py`)
- `AudioProcessor.process_full_audio` (post-capture noisereduce — deleted)
- `Recorder._agc_update` (C1 — replaced by Compressor)
- `service._apply_audio_preset` (moved to `audio_presets.py`)
- `Microphone.tsx::PRESET_TO_FILTERS` (moved to backend `audio_presets.py`)

---

## 9. Edge Cases

### 9.1 Filter chain

- **Chunk size mismatch**: RNNoise requires 480-sample frames. If the recorder delivers 1024-sample chunks, the NoiseSuppressor buffers internally and returns `None` when underfilled. `FilterChain.process()` propagates `None`. The recorder skips that chunk's callback output (no audio added to buffer). Next chunk, the suppressor has enough buffered and returns processed audio.
- **Sample rate mismatch**: RNNoise/DeepFilterNet require 48kHz. If source is 16kHz, the suppressor resamples in → process → resamples out. The resampler state is kept across calls.
- **Stereo input**: all filters downmix to mono first (`np.mean(channels, axis=1)`). The chain is mono-only. Stereo is preserved for the recorder's raw buffer if needed for other features.
- **Empty/silence chunks**: filters must handle 0-length arrays without crashing. `np.empty(0)` in → `np.empty(0)` out.
- **NaN/Inf in audio**: clamp to `[-1.0, 1.0]` at the start of each filter's `process()`. Log a WARNING if clamping fires (indicates upstream bug).
- **Filter state reset**: on mic change, `app._rebuild_audio_processor()` calls `reset()` on all filters. This clears IIR `zi` states, envelope followers, gate state, etc. Prevents artifacts from stale state.
- **Very long recordings**: IIR filters are O(n) with no accumulation, so no memory growth. RNNoise/DeepFilterNet carry fixed-size state. No issue.

### 9.2 Config migration

- **Old config with `noise_filter_enabled=False`**: `Config.load()` migrates to `audio_preset="off"`.
- **Old config with `noise_filter_gate_threshold=0.015`**: migrated to `open_threshold_db=-26, close_threshold_db=-32` (closest equivalent in dB). Old field ignored.
- **Old config with `normalize_audio=True`**: ignored (field deleted). Compressor replaces it.
- **Old config with `noise_filter_post_capture=True`**: ignored (feature deleted).
- **Old config with `audio_preset="recommended"`**: migrated to `audio_preset="auto"` (renamed).
- **Schema version bump**: `_CURRENT_SCHEMA_VERSION` incremented. `load()` runs migration logic only for older schemas.

### 9.3 Backend availability

- **RNNoise missing** (shouldn't happen — it's a default dep now, but defensive): `NoiseSuppressor` falls back to `"none"`, sets `degraded=True`, `degraded_reasons=["rnnoise library not found"]`. UI shows warning banner.
- **DeepFilterNet missing** (user didn't install the extra): if `noise_suppression_method="deepfilternet"` and library missing, fall back to `"rnnoise"` with a WARNING log. `degraded=True`, `degraded_reasons=["deepfilternet not installed, using rnnoise"]`.
- **Speex missing**: same pattern — fall back to `"rnnoise"`, then `"none"`.
- **Silero VAD model download fails** (no network): fall back to RMS-dB VAD. Log WARNING. `degraded=True`, `degraded_reasons=["silero vad model unavailable, using rms"]`.
- **torch import fails** (shouldn't happen — it's a dep): Silero VAD disabled, RMS-dB used. DeepFilterNet unavailable.

### 9.4 Live config rebuild

- **Rebuild during active recording**: the swap is atomic (`app._audio_processor._chain = new_chain` under a lock). The next `process_chunk` uses the new chain. The old chain's `reset()` is called after the swap. No audio gap — the new chain starts processing the next chunk immediately. State (gate openness, compressor envelope) starts fresh, which may cause a brief level change but no click/artifact.
- **Rapid config changes** (user dragging a slider): debounce 300ms in the UI before sending `set_config`. Backend rebuild is idempotent and cheap (<5ms). No throttling needed beyond the UI debounce.
- **Rebuild fails** (e.g. invalid config): log ERROR, keep the old chain. Don't crash. The UI shows the error via `get_audio_status`.

### 9.5 Presets

- **Preset applied at startup**: `Config.load()` calls `apply_preset_if_set()` after loading. If `audio_preset` is a named preset (not "custom"), it overrides the individual `noise_filter_*` fields. This ensures the preset always matches the actual filter state.
- **User in Custom mode changes a filter**: `audio_preset` stays "custom". No automatic preset change.
- **User switches from Custom to a named preset**: preset overrides all individual fields. User's custom tweaks are lost (expected — switching presets is a deliberate action).
- **Preset defines a field the config doesn't have**: ignore it (forward-compat — newer preset references a field added in a future version).

### 9.6 UI

- **Custom panel expand/collapse animation**: 200ms CSS transition. No layout shift for elements below (use `max-height` transition).
- **Slider value formatting**: dB values show with sign (`+3dB`, `-3dB`). Hz values show as integers. ms values show as integers. Ratio shows as `N:1`.
- **Degraded warning banner**: if `get_audio_status` returns `degraded=true`, show a yellow banner above the preset dropdown: "Some filters are running in degraded mode — click for details."
- **Preset dropdown disabled while loading**: if `get_audio_status` is in-flight, disable the dropdown for 100ms. Prevents race conditions.

### 9.7 Cross-platform

- **All filters are pure Python + numpy/scipy**: no platform-specific code. Same behavior on Windows, macOS, Linux.
- **RNNoise**: `pyrnnoise` ships pre-built wheels for Windows/macOS/Linux x64. On Linux ARM64 (Raspberry Pi), may need compilation — documented in README. If unavailable, falls back to `"none"`.
- **DeepFilterNet**: requires torch (already a dep). Works on all platforms torch supports. On ARM64, may be slow — documented.
- **Speex**: `speexdsp` Python package ships wheels for major platforms. If unavailable, falls back.
- **No native C code**: all filters are Python. No compilation step needed. No platform-specific binaries.

### 9.8 Performance

- **Target latency**: total chain < 15ms (HighPass 0 + NoiseSuppressor 10 + Gate 0 + EQ 0.06 + Compressor 0 + Limiter 0).
- **Target CPU**: < 5% on a modern CPU for real-time 16kHz mono. RNNoise is the bottleneck (~1ms per 480-sample frame). DeepFilterNet is 2-3× heavier.
- **If CPU overloaded**: the recorder callback's `time.monotonic()` check detects overrun and logs a WARNING. If sustained, the UI shows a "audio processing overload" warning. User can switch to `"speex"` or `"none"` to reduce load.
- **Streaming path**: the chain runs on every chunk for both the live buffer and the streaming ASR path. No separate processing — one chain, one pass.

---

## 10. Implementation Order

1. **Create `audio_filters/` package** — base classes + all 8 filters. Unit-test each in isolation.
2. **Create `audio_presets.py`** — single source of truth for preset → filter mapping.
3. **Create `audio_chain_builder.py`** — factory that builds a `FilterChain` from config.
4. **Refactor `AudioProcessor`** — gut the monolith, replace with `FilterChain` wrapper. Delete B4, post-capture.
5. **Refactor `Recorder`** — delete C1 (AGC). Keep VAD.
6. **Update `Config`** — add/remove fields, change defaults, add migration.
7. **Fix `service.apply_config_side_effects`** — rebuild dictation processor on config change.
8. **Update VAD** — enable Silero by default, add fallback, pass timestamps to Whisper.
9. **Update `pyproject.toml`** — RNNoise as default dep, DeepFilterNet as extra, remove noisereduce.
10. **Add `get_audio_status` IPC** — for UI degraded-mode reporting.
11. **Update Settings UI** — preset dropdown + progressive-disclosure Custom panel.
12. **Update tests** — new unit tests for filters, integration tests for chain + presets + live rebuild.
13. **Update docs** — README, PLATFORM_STATUS, this ADR's status → "Implemented".

---

## 11. Verification Plan

- **Unit tests**: each filter tested in isolation with synthetic signals (sine waves, impulses, noise). Verify frequency response, gain reduction, state continuity.
- **Integration tests**: `FilterChain` with all filters enabled, process 10s of synthetic audio, verify no NaN/Inf, no clipping, correct output shape.
- **Preset tests**: apply each preset, verify the resulting chain matches the preset definition.
- **Live rebuild test**: change config mid-recording, verify the chain swaps atomically, no crash, no audio gap.
- **Migration test**: load old config files (schema N-1), verify migration to new schema produces correct filter state.
- **Degradation test**: simulate missing libraries (mock ImportError), verify graceful fallback + `degraded` flag.
- **Performance test**: process 60s of 48kHz audio, verify < 5% CPU, < 15ms latency, no dropouts.
- **Regression**: all existing `test_audio_*.py` tests must still pass (with updated assertions for removed B4/C1).

---

## 12. Alternatives Considered

1. **Keep monolithic AudioProcessor, just add filters as methods** — rejected. Makes the code harder to maintain, test, and extend. The filter chain pattern is strictly better.
2. **Use pedalboard (Spotify's audio library)** — rejected. Adds a heavy dependency, doesn't include RNNoise or DeepFilterNet, and the filters it has (compressor, EQ) are similar quality to our OBS-style implementations.
3. **Use webrtc-audio-processing (Google's full APM)** — rejected. Includes AEC/NS/AGC, but the Python bindings are unmaintained, and AEC is not needed (volume ducking + OS AEC suffice).
4. **Make DeepFilterNet the default** — rejected for now. Higher CPU than RNNoise; some users on older hardware will prefer the lighter option. Offer as a choice, default to RNNoise.
5. **Keep post-capture noisereduce as an option** — rejected. Real-time NoiseSuppressor makes it redundant. The streaming path never used it. Removes complexity and a fragile "first 0.5s is silence" assumption.

---

End of ADR.
