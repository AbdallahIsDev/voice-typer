"""Single source of truth for audio sample-rate constants.

previously the literal ``16000`` was duplicated across 30+ sites
in the server (transcription engines, VAD, recorder, audio filters,
level monitor, microphone test recorder, status payloads). Each site
that referenced 16 kHz also embedded its own implicit assertion that
"this is Whisper's required input rate" — without a named constant, an
intent-level change (e.g. moving to 24 kHz models) would require
hand-editing every site and would silently miss any literal grep can't
disambiguate from "noise" hits in comments / log strings.

This module exposes the four named sample-rate constants used by the
audio pipeline:

- :data:`WHISPER_SAMPLE_RATE` — Whisper models require 16 kHz input.
- :data:`SILERO_VAD_SAMPLE_RATES` — Silero VAD accepts {8000, 16000}.
- :data:`RNNOISE_SAMPLE_RATE` — RNNoise requires 48 kHz.
- :data:`NATIVE_MIC_RATES` — native mic rates include
  {8000, 16000, 44100, 48000}.

All values are :class:`int` / :class:`frozenset` so they are hashable
and immutable; downstream code can ``import`` them at module top and
use them as dataclass field defaults, function argument defaults, set
memberships, and dict keys without any wrapper indirection.
"""

from __future__ import annotations

# Whisper models are trained on 16 kHz mono input. Every audio path
# that ends in a Whisper ``transcribe()`` call must resample to this
# rate (the recorder's AudioProcessor does it once on the inbound
# stream so the model never sees the device's native rate).
WHISPER_SAMPLE_RATE: int = 16000

# Silero VAD accepts only {8000, 16000} Hz input. The recorder uses
# this set to decide whether the post-AudioProcessor buffer rate needs
# a second resample step before being fed to ``compute_vad_prob``.
SILERO_VAD_SAMPLE_RATES: frozenset[int] = frozenset({8000, 16000})

# RNNoise (the noise-suppression filter) is trained on 48 kHz input.
# The NoiseSuppressor filter resamples inbound chunks to this rate,
# runs inference, then resamples back to the pipeline rate.
RNNOISE_SAMPLE_RATE: int = 48000

# Common native microphone sample rates. Used by the microphone-list
# Bluetooth-HFP detector (HFP devices run at 8 or 16 kHz) and by the
# level monitor's "what rate should I open the stream at?" heuristic.
NATIVE_MIC_RATES: frozenset[int] = frozenset({8000, 16000, 44100, 48000})

# PortAudio ``blocksize`` literal. VAD-001 /  — Silero
# VAD requires 512-sample blocks per its model contract; ``vad.py`` pads /
# truncates driver deviations. The literal is load-bearing across
# ``recorder.py`` (StreamLifecycle.open_stream_for_candidates /
# open_stream_fallback, DisconnectHandler.restart_stream,
# SessionState.resize_buffers_for_sample_rate, Recorder._preroll_blocksize
# init). Tag here as a single source of truth so a future change (e.g.
# 1024 for lower callback frequency on slow ARM devices — see )
# lands in one place.
_AUDIO_BLOCKSIZE: int = 512


def scaled_audio_blocksize(native_rate: int) -> int:
    """Rate-scaled PortAudio ``blocksize`` for a recording stream.

    Returns the blocksize that makes each callback chunk represent
    ~32 ms of audio at ``native_rate``: ``max(512, int(rate * 0.032))``.
    The 512 floor preserves the Silero VAD 512-sample contract (and a
    sane minimum block) on low-rate devices; at 16 kHz the formula
    yields exactly 512.

    Why rate-scaling matters: a fixed 512 block at a 48 kHz native rate
    is a 10.7 ms chunk → ~93.75 callbacks/sec, ~3× the designed 16-31 Hz
    worker/VAD cadence — every VAD hysteresis frame count and chunk-based
    time constant then runs ~3× faster than designed, and Silero computes
    ~3× the intended inferences per second on reflect-padded 170→512
    windows. With the scaled block (1536 @ 48 kHz, 1411 @ 44.1 kHz,
    3072 @ 96 kHz), each chunk resamples onto the 512-sample Silero
    window at 16 kHz, restoring the designed cadence at every native
    rate. Precision note: 48/96 kHz are exact integer ratios (512
    post-resample samples precisely); 44.1 kHz is not — 1411 samples
    is ~31.995 ms ≈ 511.93 samples' worth at 16 kHz, so the
    512-sample window there is exact by LENGTH only (the resampler
    rounds the output up to 512; ``vad.py`` additionally pads /
    truncates any driver-level deviation from the window contract).

    Mirrors the sibling level monitor's fix
    (``level_monitor/monitoring.py``: ``max(512, int(native_rate * 0.032))``),
    which corrected this same defect for its own stream.
    """
    rate = int(native_rate)
    if rate <= 0:
        # Unknown / unresolved rate: fall back to the fixed 512 contract
        # rather than computing a nonsense block from a zero or negative
        # rate.
        return _AUDIO_BLOCKSIZE
    return max(_AUDIO_BLOCKSIZE, int(rate * 0.032))


# ``_teardown_stream`` busy-poll budget + interval. The
# ``_is_in_audio_callback`` flag is SET while the PortAudio callback is
# RUNNING and CLEARED on exit — the inverse of the typical
# wait-for-event-set pattern. ``teardown_stream_body`` polls for the flag
# to become *clear* before closing the stream (closing while the callback
# is mid-flight can deadlock PortAudio on some drivers). On a healthy
# system the flag is already clear on the first check → 0ms wait. The
# 300 ms budget matches the original 6×50 ms worst-case backoff.
_TEARDOWN_CALLBACK_DRAIN_BUDGET_S: float = 0.300
_TEARDOWN_CALLBACK_POLL_INTERVAL_S: float = 0.005


# Default smart-duck polling interval (ms). Moved here from volume_ducker.py to
# avoid a layering inversion (config -> volume_ducker). Both config and
# volume_ducker import from here.
_DEFAULT_SMART_DUCK_POLL_MS: int = 500
