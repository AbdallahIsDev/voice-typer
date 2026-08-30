"""Audio format helpers for :class:`Recorder` (extracted from ``recorder.py``).

Continues the god-class decomposition documented in :mod:`._recorder_split`
(``recorder/format.py`` entry of the split plan): the audio-format concern
— mono downmix, per-chunk resample, and stop-time resample-to-target —
moves here as free functions taking the owning ``Recorder`` as their first
argument (the same collaborator pattern as :mod:`.session_state` /
:mod:`.stream_lifecycle`). ``Recorder`` keeps thin delegating methods so
existing internal call sites (``AudioPipeline.process_audio_chunk`` /
``_snapshot_resampled_locked`` / ``stop_recording``), subclass overrides,
and instance-level monkeypatches keep working unchanged.

Cold-start compatibility
------------------------
Importing this module must NOT pull numpy into ``sys.modules`` — the
numpy proxy is created lazily at the bottom of this file, mirroring
``recorder.py`` (the ~250-335 ms cold-start saving documented there).

Patch-path compatibility
------------------------
``prepare_audio`` reads the cached target rate from
``recorder._cached_target_sr`` and routes the actual conversion through
:mod:`voice_typer.server.recording.resampling` (which routes through the
package namespace), so existing test patches of
``voice_typer.server.recording._get_resample_poly`` /
``voice_typer.server.recording.np.interp`` keep affecting production
code exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from voice_typer.server.recording.resampling import resample_audio

if TYPE_CHECKING:
    from .recorder import Recorder

# ── AUDIO-CH: mono conversion ────────────────────────────────────────


def ensure_mono(recorder: Recorder, audio: Any) -> Any:
    """Convert multi-channel audio to mono by averaging channels.

    AUDIO-CH: If the input device only supports stereo (2 channels),
    we record with channels=2 and downmix here. This avoids the
    PortAudio error when requesting channels=1 on a stereo-only device.

    Performance: the stereo (2-channel) path uses an in-place
    ``np.add`` + ``*= 0.5`` into a fresh output array instead of
    ``np.mean``. This avoids ``np.mean``'s internal intermediate-array
    allocation on the 16 Hz audio-worker hot path while keeping the
    number of passes minimal: one allocation, one add pass, one scale
    pass. (An earlier revision computed into a per-thread scratch and
    returned ``view.copy()`` — the copy allocated a fresh array anyway,
    so the scratch saved nothing and added a third memcpy pass. The
    scratch holder ``recorder._mono_scratch_local`` is still declared
    on ``Recorder`` because its presence in ``__init__`` is pinned by
    the mono/downmix regression tests.) The result is a fresh,
    caller-owned array — it can be stored in ``_buffer`` /
    ``_preroll_buffer`` without aliasing anything. The ``>2``-channel
    path (rare — channels are clamped to [1, 2] at stream-open time)
    falls back to ``np.mean`` for simplicity.

    No-copy paths: 1-D input is returned as-is, and a 2-D single-column
    input is returned as a zero-copy ``reshape(-1)`` view of the input
    (safe: callers either read it immediately or copy-on-append into
    the growable recording storage).

    Thread safety: no shared mutable state — every call either returns
    a view of its own input or a freshly allocated array, so the audio
    worker thread and the RT callback's pre-roll path cannot interfere.
    """
    if audio.ndim == 1:
        return audio
    if audio.ndim == 2 and audio.shape[1] > 1:
        n = audio.shape[0]
        if audio.shape[1] == 2:
            # Fast path: stereo downmix via in-place add + scale into a
            # fresh output array. One allocation, two passes — same
            # element-wise operations (and therefore identical output
            # bytes) as the earlier scratch+``view.copy()`` version,
            # without the extra copy pass. The result is caller-owned,
            # so it can be stored without aliasing shared state.
            out = np.empty(n, dtype=np.float32)
            np.add(audio[:, 0], audio[:, 1], out=out)
            out *= 0.5
            return out
        # >2 channels (rare — clamped to [1,2] at stream-open):
        # fall back to np.mean which handles arbitrary channel
        # counts. The allocation cost is acceptable for this rare
        # path.
        return np.mean(audio, axis=1, dtype=np.float32)
    if audio.ndim == 2 and audio.shape[1] == 1:
        return audio.reshape(-1)
    return audio.reshape(-1)


# ── Resampling wrappers ──────────────────────────────────────────────


def resample_chunk(recorder: Recorder, audio: Any, effective_sr: int, target_sr: int) -> Any:
    """Resample a single chunk of audio (body of ``Recorder._resample_chunk``).

    Raises:
        ResampleError: if neither scipy nor linear-interp resampling
            could convert the audio to ``target_sr``. Callers MUST
            handle this; previously the function returned the native-
            rate audio silently, which led to garbage transcriptions
            on the streaming path.

    PERF-: delegates to the shared ``_resample_audio_impl``
        helper (also used by ``prepare_audio``) to avoid duplicating
        the scipy → linear interp → raise fallback chain.
    """
    if len(audio) == 0:
        return np.array([], dtype=np.float32)
    return resample_audio(audio, effective_sr, target_sr, log_resample=False)


def prepare_audio(
    recorder: Recorder,
    audio: Any,
    effective_sr: int,
    log_resample: bool = True,
) -> Any:
    """Convert captured audio to the configured sample rate (body of
    ``Recorder._prepare_audio``).

    previously the except blocks used bare ``Exception``,
        which swallowed ``AttributeError`` / ``MemoryError`` /
        ``KeyboardInterrupt`` (in some interpreters). We narrow to
        ``(ValueError, OSError, TypeError)`` so genuine bugs propagate
        instead of being silently masked as "resampling failed".

    PERF-: delegates to the shared ``_resample_audio_impl``
        helper (also used by ``resample_chunk``) to avoid duplicating
        the scipy → linear interp → raise fallback chain.
    """
    # prefer the cached target sample rate (set once in
    # start()) over re-reading recorder.config.sample_rate on every
    # prepare_audio call. The cached value is authoritative for
    # the current session; reading config every call was an
    # unnecessary attribute lookup on the stop() hot path. Fall
    # back to config.sample_rate if the cache hasn't been populated
    # yet (defensive -- should never happen because start() always
    # sets it before any audio is captured).
    target_sr = getattr(recorder, "_cached_target_sr", None) or recorder.config.sample_rate
    if effective_sr != target_sr and len(audio) > 0:
        return resample_audio(audio, effective_sr, target_sr, log_resample=log_resample)
    return audio


# Lazy numpy proxy — MUST stay a lazy import (see module docstring
# §Cold-start compatibility); sibling modules follow the same pattern.
from voice_typer.server._lazy_import import lazy_module  # noqa: E402

np = lazy_module("numpy")
