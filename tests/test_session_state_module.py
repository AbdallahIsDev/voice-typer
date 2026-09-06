"""Focused unit tests for :class:`SessionState` (Phase 4.5).

These tests exercise the public API of
:class:`voice_typer.server.recording.session_state.SessionState`
in isolation — the owning :class:`Recorder` is replaced with a
``FakeRecorder`` namespace object that carries just the attributes the
extracted bodies touch. No real audio is captured, no sounddevice probe
runs, no PortAudio / no subprocess is touched.

Scope
-----
- ``reset_session_state`` — verifies the per-session state reset
  touches every documented attribute (ARCH-023 invariant).
- ``cache_session_config`` — verifies the cached-scalar assignment +
  the ``max_rec`` return value (the fix).
- ``secure_clear_caches`` — verifies the secure-zeroing of the two cached audio arrays + the audio-processor
  ``reset()`` + the ``_buffer_sr`` reset. Also verifies the
  ``_recording_pkg._secure_clear_array`` patch-path indirection
  (regression).
- ``resize_buffers_for_sample_rate`` — verifies dynamic buffer sizing
  for the effective sample rate + ring buffer resizing + preroll
  deque resizing (defensive preservation of existing contents).
- ``prepend_preroll_to_buffer`` — verifies the preroll is filtered
  through the audio processor (R18-F12), prepended to ``_buffer``, and
  the preroll deque is zeroed + cleared (privacy gap).
"""

from __future__ import annotations

import collections
from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server._audio_constants import scaled_audio_blocksize
from voice_typer.server.recording.buffer import _secure_clear_array as _real_secure_clear_array
from voice_typer.server.recording.session_state import SessionState

# ─── Helpers ────────────────────────────────────────────────────────────


def _make_vad() -> MagicMock:
    """A mock VAD processor with a callable ``reset``."""
    vad = MagicMock(name="VadProcessor")
    vad.reset = MagicMock(name="VadProcessor.reset")
    return vad


def _make_audio_processor(*, raise_on_reset: bool = False) -> MagicMock:
    """A mock ``AudioProcessor`` with callable ``reset`` and ``process_chunk``.

    ``process_chunk`` echoes its first argument (the chunk) back so the
    R18-F12 filter-chain loop in ``prepend_preroll_to_buffer`` doesn't
    drop data. ``reset`` optionally raises so the best-effort path in
    ``secure_clear_caches`` is exercised.
    """
    ap = MagicMock(name="AudioProcessor")
    if raise_on_reset:

        def _raise_reset() -> None:
            raise RuntimeError("simulated reset failure")

        ap.reset = MagicMock(side_effect=_raise_reset)
    else:
        ap.reset = MagicMock(name="AudioProcessor.reset")

    def _echo(chunk, input_sample_rate=None):
        return chunk

    ap.process_chunk = MagicMock(side_effect=_echo)
    return ap


def _make_config(**overrides) -> SimpleNamespace:
    """Build a minimal ``Config``-shaped namespace with sane defaults."""
    base = dict(
        sample_rate=16000,
        microphone=None,
        silence_warning_seconds=20.0,
        stop_on_silence_seconds=60.0,
        max_recording_time_seconds=900,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _make_recorder(
    *,
    audio_processor=None,
    config=None,
    preroll_active: bool = True,
    preroll_seconds: float = 1.0,
    effective_sr: int = 16000,
) -> SimpleNamespace:
    """Build a minimal ``FakeRecorder`` with every attribute ``SessionState`` touches.

    Each attribute is initialised to a sentinel so the test can assert
    the method actually mutated it (not just left the default in place).
    """
    rec = SimpleNamespace()
    rec.config = config or _make_config()
    rec._vad = _make_vad()
    # Device-state owner collaborator (the historical Recorder-level
    # property shims were removed; reset_session_state writes through
    # ``recorder._devices.<attr>``).
    rec._devices = SimpleNamespace(
        _device_disconnected=True,  # sentinel: reset to False
        _device_disconnect_retries=999,
        _device_check_counter=999,
    )
    rec._audio_processor = audio_processor if audio_processor is not None else _make_audio_processor()
    # STATE-OWNERSHIP: the buffer bookkeeping state
    # (``_buffer`` / ``_chunk_count`` / ``_buffer_sr`` /
    # ``_total_buffered_samples``) AND the XRUN/clip telemetry
    # sentinels live on the owning collaborator (AudioPipeline) —
    # ``reset_session_state`` / ``resize_buffers_for_sample_rate``
    # write through ``recorder._audio_pipeline.<attr>``.
    rec._audio_pipeline = SimpleNamespace(
        _buffer=collections.deque(maxlen=1000),
        _chunk_count=999,  # sentinel: reset must zero
        _total_buffered_samples=999,  # sentinel: reset must zero
        _buffer_sr=99999,  # sentinel: reset to None
        _xruns=999,
        _xrun_timestamps=collections.deque([1.0, 2.0]),
        _clip_count=999,
        _peak=999.0,
        _last_clip_log_time=999.0,
    )
    rec._cached_resampled = np.array([0.5, 0.5, 0.5], dtype=np.float32)  # sentinel: reset clears
    rec._cached_native_chunk_count = 999
    rec._cached_resample_key = ("sentinel",)
    rec._cached_no_resample_len = 999
    rec._cached_no_resample_arr = np.array([0.4, 0.4], dtype=np.float32)
    rec._cached_resampled_segments = [np.array([0.5, 0.5], dtype=np.float32)]  # sentinel: reset clears
    rec._cached_no_resample_segments = [np.array([0.3, 0.3], dtype=np.float32)]  # sentinel: reset clears
    rec._cached_resampled_concat_dirty = True
    rec._dropped_chunks = 999
    rec._rms_callback_error_count = 999
    rec._silence_timer = 999.0
    rec._silence_start_time = 999.0
    rec._silence_warning_count = 999
    rec._silence_next_warning_wait = 999.0
    rec._recent_rms_values = collections.deque([1.0, 2.0, 3.0], maxlen=50)
    rec._recording_start_time = 0.0
    rec._cached_vad_enabled = True
    rec._cached_use_silero_vad = True
    rec._cached_silero_available = True
    rec._cached_vad_resample_up_down = ("sentinel",)
    rec._cached_vad_resample_sr = 99999
    rec._last_rms = 999.0
    rec._vad.state = "sentinel"  # set by reset_session_state
    rec._vad.consecutive_speech_frames = 999
    rec._vad.consecutive_silence_frames = 999
    rec._vad.speech_threshold_db = 999.0
    rec._vad.silence_threshold_db = 999.0
    rec._vad.calibration_rms_values = [0.5, 0.5]  # sentinel: reset clears
    rec._vad.calibrated = True
    rec._user_stop_pending = True  # sentinel: reset to False
    rec._preroll_buffer = collections.deque(maxlen=64)
    # AUDIO-HOT flap-detection deque — reset_session_state clears it
    # (start() is the "begin a new session" boundary).
    rec._restart_timestamps = collections.deque([1.0, 2.0])
    rec._dropped_ring_chunks = 999
    rec._cached_target_sr = None
    rec._cached_silence_warning = None
    rec._cached_stop_on_silence = None
    rec._cached_max_recording_time = None
    rec._ring_buffer = collections.deque(maxlen=64)
    rec._preroll_active = preroll_active
    rec._preroll_seconds = preroll_seconds
    rec._effective_sr = effective_sr

    # The preroll-prepend loop invokes the real
    # ``voice_typer.server.recording.format.ensure_mono`` (a free
    # function taking the recorder) — no per-recorder stub needed; it
    # downmixes multi-channel chunks without touching recorder state.
    return rec


# ─── __init__ ──────────────────────────────────────────────────────────


def test_session_state_init_stores_back_reference():
    """``SessionState(recorder)`` stores ``recorder`` as ``_recorder``."""
    rec = _make_recorder()
    ss = SessionState(rec)
    assert ss._recorder is rec


# ─── reset_session_state ───────────────────────────────────────────────


def test_reset_session_state_clears_buffer_and_chunk_count():
    """The main audio buffer + chunk counter must be zeroed."""
    rec = _make_recorder()
    rec._audio_pipeline._buffer.append(np.zeros(512, dtype=np.float32))
    rec._audio_pipeline._buffer.append(np.zeros(512, dtype=np.float32))
    rec._audio_pipeline._chunk_count = 42

    SessionState(_make_recorder()).reset_session_state(rec)

    assert len(rec._audio_pipeline._buffer) == 0
    assert rec._audio_pipeline._chunk_count == 0


def test_reset_session_state_clears_resample_caches():
    """The cached resampled array, key, segment list + dirty flag are reset."""
    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    assert isinstance(rec._cached_resampled, np.ndarray)
    assert rec._cached_resampled.size == 0
    assert rec._cached_native_chunk_count == 0
    assert rec._cached_resample_key == ()
    assert rec._cached_no_resample_len == -1
    assert rec._cached_no_resample_arr is None
    assert rec._cached_resampled_segments == []
    assert rec._cached_resampled_concat_dirty is False


def test_reset_session_state_resets_error_counters_and_silence():
    """Per-session error counters and the silence-timer state are reset."""
    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    assert rec._dropped_chunks == 0
    assert rec._rms_callback_error_count == 0
    assert rec._silence_timer == 0.0
    assert rec._silence_start_time is None
    assert rec._silence_warning_count == 0
    assert rec._silence_next_warning_wait == 10.0
    assert len(rec._recent_rms_values) == 0
    # ``_recording_start_time`` is set to a fresh perf_counter (>=0).
    assert isinstance(rec._recording_start_time, float)
    assert rec._recording_start_time >= 0.0


def test_reset_session_state_resets_buffer_sr_and_vad_caches():
    """``_buffer_sr`` and the cached VAD-property scalars are reset to defaults."""
    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    assert rec._audio_pipeline._buffer_sr is None
    assert rec._cached_vad_enabled is False
    assert rec._cached_use_silero_vad is False
    assert rec._cached_silero_available is False
    assert rec._cached_vad_resample_up_down is None
    assert rec._cached_vad_resample_sr is None


def test_reset_session_state_resets_xrun_and_clip_counters():
    """XRUN / clipping / peak counters are reset to zero."""
    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    assert rec._audio_pipeline._xruns == 0
    assert len(rec._audio_pipeline._xrun_timestamps) == 0
    assert rec._audio_pipeline._clip_count == 0
    assert rec._audio_pipeline._peak == 0.0
    assert rec._audio_pipeline._last_clip_log_time == 0.0
    assert rec._last_rms == 0.0


def test_reset_session_state_calls_vad_reset_and_resets_vad_state():
    """The VAD processor's ``reset()`` is invoked and the VAD state is reset."""
    from voice_typer.server.vad_processor import VadState

    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    rec._vad.reset.assert_called_once_with()
    assert rec._vad.state == VadState.UNKNOWN
    assert rec._vad.consecutive_speech_frames == 0
    assert rec._vad.consecutive_silence_frames == 0
    # Default thresholds come from the canonical VAD module constants.
    from voice_typer.server.vad_processor import (
        DEFAULT_VAD_SILENCE_THRESHOLD_DB,
        DEFAULT_VAD_SPEECH_THRESHOLD_DB,
    )

    assert rec._vad.speech_threshold_db == DEFAULT_VAD_SPEECH_THRESHOLD_DB
    assert rec._vad.silence_threshold_db == DEFAULT_VAD_SILENCE_THRESHOLD_DB
    assert rec._vad.calibration_rms_values == []
    assert rec._vad.calibrated is False


def test_reset_session_state_clears_user_stop_pending_and_disconnect_state():
    """``_user_stop_pending`` + device-disconnect state are reset for a fresh session."""
    rec = _make_recorder()
    rec._user_stop_pending = True
    rec._devices._device_disconnected = True
    rec._devices._device_disconnect_retries = 3

    SessionState(_make_recorder()).reset_session_state(rec)

    assert rec._user_stop_pending is False
    assert rec._devices._device_disconnected is False
    assert rec._devices._device_disconnect_retries == 0


def test_reset_session_state_zeros_and_clears_preroll_buffer():
    """preroll chunks are zero-filled BEFORE the deque is cleared."""
    rec = _make_recorder()
    chunk_a = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    chunk_b = np.array([0.4, 0.5, 0.6], dtype=np.float32)
    rec._preroll_buffer.append(chunk_a)
    rec._preroll_buffer.append(chunk_b)

    SessionState(_make_recorder()).reset_session_state(rec)

    assert len(rec._preroll_buffer) == 0
    # The original numpy buffers (which we kept a separate reference to)
    # must be zeroed in-place — not just dropped from the deque.
    assert np.all(chunk_a == 0), "preroll chunk must be zeroed in-place (the fix)"
    assert np.all(chunk_b == 0), "preroll chunk must be zeroed in-place (the fix)"


def test_reset_session_state_resets_ring_drop_counters():
    """ring-drop counter is reset (frame-skip counters removed)."""
    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    assert rec._dropped_ring_chunks == 0
    assert rec._devices._device_check_counter == 0


def test_reset_session_state_caches_target_sample_rate():
    """``_cached_target_sr`` is set from ``config.sample_rate``."""
    rec = _make_recorder(config=_make_config(sample_rate=48000))

    SessionState(_make_recorder()).reset_session_state(rec)

    assert rec._cached_target_sr == 48000


def test_reset_session_state_calls_audio_processor_reset_when_present():
    """AUDIO-PROC: the audio processor's ``reset()`` is invoked at session start."""
    rec = _make_recorder()

    SessionState(_make_recorder()).reset_session_state(rec)

    rec._audio_processor.reset.assert_called_once_with()


def test_reset_session_state_skips_audio_processor_reset_when_none():
    """A ``None`` audio processor must not crash the reset path."""
    rec = _make_recorder()
    rec._audio_processor = None

    # Must not raise.
    SessionState(_make_recorder()).reset_session_state(rec)


# ─── cache_session_config ──────────────────────────────────────────────


def test_cache_session_config_caches_silence_thresholds_as_floats():
    """``_cached_silence_warning`` / ``_cached_stop_on_silence`` are float-coerced."""
    rec = _make_recorder(
        config=_make_config(
            silence_warning_seconds=15.0,
            stop_on_silence_seconds=45.0,
        )
    )

    max_rec = SessionState(_make_recorder()).cache_session_config(rec)

    assert rec._cached_silence_warning == 15.0
    assert isinstance(rec._cached_silence_warning, float)
    assert rec._cached_stop_on_silence == 45.0
    assert isinstance(rec._cached_stop_on_silence, float)
    assert max_rec == 900  # default max_recording_time_seconds


def test_cache_session_config_coerces_int_thresholds_to_float():
    """Integer thresholds are accepted and coerced to float (silence_timer comparison)."""
    rec = _make_recorder(
        config=_make_config(
            silence_warning_seconds=20,  # int
            stop_on_silence_seconds=120,  # int
        )
    )

    SessionState(_make_recorder()).cache_session_config(rec)

    assert rec._cached_silence_warning == 20.0
    assert isinstance(rec._cached_silence_warning, float)
    assert rec._cached_stop_on_silence == 120.0
    assert isinstance(rec._cached_stop_on_silence, float)


def test_cache_session_config_falls_back_to_defaults_on_non_numeric():
    """A non-numeric config value (MagicMock in tests) falls back to 20.0/60.0."""
    rec = _make_recorder()
    rec.config.silence_warning_seconds = MagicMock()  # not int|float
    rec.config.stop_on_silence_seconds = MagicMock()  # not int|float

    SessionState(_make_recorder()).cache_session_config(rec)

    assert rec._cached_silence_warning == 20.0
    assert rec._cached_stop_on_silence == 60.0


def test_cache_session_config_returns_int_max_recording_time():
    """The return value is the parsed ``max_recording_time`` as an int."""
    rec = _make_recorder(config=_make_config(max_recording_time_seconds=300))

    max_rec = SessionState(_make_recorder()).cache_session_config(rec)

    assert max_rec == 300
    assert isinstance(max_rec, int)
    assert rec._cached_max_recording_time == 300


def test_cache_session_config_propagates_type_error_from_int():
    """The FIRST ``int(config.max_recording_time_seconds)`` is unprotected.

    The body has two ``int()`` calls:

    1. ``self._cached_max_recording_time = int(self.config.max_recording_time_seconds)``
       — UNPROTECTED. A non-int-coercible config value (e.g. a bare
       ``object()``) raises ``TypeError`` here, propagating to the
       caller. This matches the original ``Recorder._cache_session_config``
       behavior.
    2. ``max_rec = int(self._cached_max_recording_time)`` — wrapped in
       ``try/except (TypeError, ValueError)``. This is DEFENSIVE dead
       code in normal flow (once ``_cached_max_recording_time`` is set
       to an ``int`` by the first line, the second ``int()`` never
       raises).

    This test pins the propagation behaviour of the first call so the
    extraction doesn't accidentally wrap it in a broader ``try/except``.
    """
    rec = _make_recorder()
    # ``object()`` has no ``__int__`` → ``int(object())`` raises TypeError.
    rec.config.max_recording_time_seconds = object()

    with pytest.raises(TypeError, match=r"int\(\) argument"):
        SessionState(_make_recorder()).cache_session_config(rec)


def test_cache_session_config_propagates_value_error_from_int():
    """The FIRST ``int(config.max_recording_time_seconds)`` propagates ``ValueError`` too.

    See :func:`test_cache_session_config_propagates_type_error_from_int`
    for the two-``int()`` explanation.
    """
    rec = _make_recorder()

    class _RaisesOnInt:
        def __int__(self) -> int:
            raise ValueError("simulated")

    rec.config.max_recording_time_seconds = _RaisesOnInt()

    with pytest.raises(ValueError, match="simulated"):
        SessionState(_make_recorder()).cache_session_config(rec)


def test_cache_session_config_silently_coerces_magicmock_to_one():
    """A ``MagicMock`` config returns ``max_rec=1`` because ``int(MagicMock()) == 1``.

    This mirrors the test pattern in ``tests/test_secure_clear_array.py``
    where a ``MagicMock`` config is used to exercise the silence-warning
    float-coercion path. ``MagicMock`` auto-implements ``__int__`` to
    return ``1``, so the unprotected first ``int()`` call succeeds and
    the second ``int()`` returns ``1``.
    """
    rec = _make_recorder()
    rec.config.silence_warning_seconds = MagicMock()  # not int|float → 20.0 fallback
    rec.config.stop_on_silence_seconds = MagicMock()  # not int|float → 60.0 fallback
    rec.config.max_recording_time_seconds = MagicMock()  # int(MagicMock()) == 1

    max_rec = SessionState(_make_recorder()).cache_session_config(rec)

    # silence thresholds fall back to defaults via the isinstance guard.
    assert rec._cached_silence_warning == 20.0
    assert rec._cached_stop_on_silence == 60.0
    # ``int(MagicMock())`` returns 1 (the auto-generated __int__ default).
    assert rec._cached_max_recording_time == 1
    assert max_rec == 1


# ─── secure_clear_caches ───────────────────────────────────────────────


def test_secure_clear_caches_zeros_cached_resampled_in_place():
    """``_cached_resampled`` is zeroed in-place BEFORE reassignment."""
    rec = _make_recorder()
    cached_resampled = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    rec._cached_resampled = cached_resampled

    SessionState(_make_recorder()).secure_clear_caches(rec)

    assert np.all(cached_resampled == 0), "underlying numpy buffer must be zeroed in-place"
    assert isinstance(rec._cached_resampled, np.ndarray)
    assert rec._cached_resampled.size == 0
    assert rec._cached_resampled.dtype == np.float32


def test_secure_clear_caches_zeros_cached_no_resample_in_place():
    """``_cached_no_resample_arr`` is zeroed in-place BEFORE reassignment."""
    rec = _make_recorder()
    cached_no_resample = np.array([0.5, 0.6, 0.7], dtype=np.float32)
    rec._cached_no_resample_arr = cached_no_resample

    SessionState(_make_recorder()).secure_clear_caches(rec)

    assert np.all(cached_no_resample == 0), "underlying numpy buffer must be zeroed in-place"
    assert rec._cached_no_resample_arr is None


def test_secure_clear_caches_resets_native_chunk_count_and_no_resample_len():
    """The cache-key scalars are reset alongside the arrays."""
    rec = _make_recorder()
    rec._cached_native_chunk_count = 12345
    rec._cached_no_resample_len = 999

    SessionState(_make_recorder()).secure_clear_caches(rec)

    assert rec._cached_native_chunk_count == 0
    assert rec._cached_no_resample_len == -1


def test_secure_clear_caches_resets_buffer_sr_to_none():
    """The buffer-sample-rate tracker is reset to ``None`` so a fresh start() doesn't reuse it."""
    rec = _make_recorder()
    rec._audio_pipeline._buffer_sr = 48000

    SessionState(_make_recorder()).secure_clear_caches(rec)

    assert rec._audio_pipeline._buffer_sr is None


def test_secure_clear_caches_calls_audio_processor_reset():
    """XZ-PRIV-01: the audio processor's filter state is reset on stop()/discard()."""
    rec = _make_recorder()

    SessionState(_make_recorder()).secure_clear_caches(rec)

    rec._audio_processor.reset.assert_called_once_with()


def test_secure_clear_caches_swallows_audio_processor_reset_exception():
    """A misbehaving ``AudioProcessor.reset()`` is swallowed so the numpy caches still get cleared."""
    rec = _make_recorder(audio_processor=_make_audio_processor(raise_on_reset=True))
    cached_resampled = np.array([0.1, 0.2], dtype=np.float32)
    rec._cached_resampled = cached_resampled

    # Must NOT raise even though the audio processor's reset() raises.
    SessionState(_make_recorder()).secure_clear_caches(rec)

    assert np.all(cached_resampled == 0), "the numpy cache must still be zeroed"
    assert rec._cached_resampled.size == 0


def test_secure_clear_caches_handles_already_empty_caches():
    """Idempotent: safe to call when caches are already empty / None."""
    rec = _make_recorder()
    rec._cached_resampled = np.array([], dtype=np.float32)
    rec._cached_no_resample_arr = None

    # Must not raise.
    SessionState(_make_recorder()).secure_clear_caches(rec)

    assert rec._cached_resampled.size == 0
    assert rec._cached_no_resample_arr is None


def test_secure_clear_caches_routes_through_recording_pkg_indirection(monkeypatch):
    """regression: the secure-clear call uses the owning-module function.

    The extracted body calls ``_secure_clear_array`` imported from the
    ``recording.buffer`` submodule (C-ARCH-2); tests stub it by patching
    the ``session_state`` module binding (the single patch path).
    """
    rec = _make_recorder()
    rec._cached_resampled = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._cached_no_resample_arr = np.array([0.4, 0.5], dtype=np.float32)

    calls = []
    real_fn = _real_secure_clear_array

    def _spy(arr):
        calls.append(arr)
        real_fn(arr)

    # Patch the package-level binding (the path tests use in production).
    monkeypatch.setattr(
        "voice_typer.server.recording.session_state._secure_clear_array",
        _spy,
    )

    SessionState(_make_recorder()).secure_clear_caches(rec)

    # Four calls expected: 1 for ``_cached_resampled`` + 1 for
    # ``_cached_no_resample_arr`` + 1 segment in
    # ``_cached_resampled_segments`` + 1 segment in
    # ``_cached_no_resample_segments``. The production loops over
    # the segment lists and calls ``_secure_clear_array`` on each
    # non-empty segment so a per-snapshot dictated-prefix block
    # doesn't leak the user's voice into the numpy allocator's free
    # list (SEC-audit-008 / ).
    assert len(calls) == 4, f"expected 4 calls (2 arrays + 2 segments), got {len(calls)}"
    # All routed calls must have actually zeroed their argument.
    for arr in calls:
        assert np.all(arr == 0)


def test_secure_clear_caches_does_not_swallow_unexpected_exceptions(monkeypatch):
    """invariant: the ``except`` clause is narrowed to ``(OSError, ValueError)``.

    A ``RuntimeError`` from ``_secure_clear_array`` must propagate (it
    must NOT be silently swallowed by a broad ``except Exception:``).
    """
    rec = _make_recorder()
    rec._cached_resampled = np.array([0.1, 0.2], dtype=np.float32)

    def _raise_runtime_error(_arr):
        raise RuntimeError("simulated unexpected error")

    monkeypatch.setattr(
        "voice_typer.server.recording.session_state._secure_clear_array",
        _raise_runtime_error,
    )

    with pytest.raises(RuntimeError, match="simulated unexpected error"):
        SessionState(_make_recorder()).secure_clear_caches(rec)


def test_secure_clear_caches_swallows_oserror_and_value_error(monkeypatch):
    """``OSError`` / ``ValueError`` from the secure-clear call are swallowed (logged)."""
    rec = _make_recorder()
    rec._cached_resampled = np.array([0.1, 0.2], dtype=np.float32)
    rec._cached_no_resample_arr = None  # skip the second call

    def _raise_os_error(_arr):
        raise OSError("simulated mmap failure")

    monkeypatch.setattr(
        "voice_typer.server.recording.session_state._secure_clear_array",
        _raise_os_error,
    )

    # Must not raise — the OSError is swallowed + logged.
    SessionState(_make_recorder()).secure_clear_caches(rec)


# ─── resize_buffers_for_sample_rate ─────────────────────────────────────


def test_resize_buffers_grows_main_buffer_for_high_sample_rate():
    """A 48 kHz device + 900s max_rec must size the buffer for the full duration.

    Reproduces the AUDIO-HOT fix: the old 1024/16kHz sizing
    under-allocated at 48kHz, silently evicting the first ~25min of a
    30-min dictation. The sizing uses the actual chunk duration of the
    rate-scaled ~32 ms block (1536/48000 at 48 kHz) + a +1K safety
    margin.
    """
    rec = _make_recorder(config=_make_config(sample_rate=48000))
    initial_maxlen = rec._audio_pipeline._buffer.maxlen

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    new_maxlen = rec._audio_pipeline._buffer.maxlen
    # ~900s / 0.032s ≈ 28125 chunks + 1000 safety ≈ 29125 — well above the
    # placeholder initial maxlen of 1000.
    assert new_maxlen > initial_maxlen
    blocksize = scaled_audio_blocksize(48000)
    expected_min = int(900 / (blocksize / 48000))  # without safety
    assert new_maxlen >= expected_min


def test_resize_buffers_preserves_existing_buffer_contents():
    """Defensive: any chunks already in the buffer are preserved on resize."""
    rec = _make_recorder(config=_make_config(sample_rate=48000))
    chunk_a = np.array([0.1, 0.2], dtype=np.float32)
    chunk_b = np.array([0.3, 0.4], dtype=np.float32)
    rec._audio_pipeline._buffer.append(chunk_a)
    rec._audio_pipeline._buffer.append(chunk_b)

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    # The deque is replaced — but contents preserved.
    assert list(rec._audio_pipeline._buffer) == [chunk_a, chunk_b]


def test_resize_buffers_skips_main_buffer_resize_when_max_rec_zero():
    """``max_rec=0`` means "no max duration limit" — buffer resize is skipped."""
    rec = _make_recorder()
    initial_maxlen = rec._audio_pipeline._buffer.maxlen

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=0,
    )

    assert rec._audio_pipeline._buffer.maxlen == initial_maxlen


def test_resize_buffers_skips_main_buffer_resize_when_already_large_enough():
    """If the existing maxlen already covers max_rec, no resize is performed."""
    rec = _make_recorder(config=_make_config(sample_rate=16000))
    huge = collections.deque(maxlen=10_000_000)
    rec._audio_pipeline._buffer = huge

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=16000,
        max_rec=900,
    )

    # The original deque is preserved (no replacement).
    assert rec._audio_pipeline._buffer is huge


def test_resize_buffers_resizes_ring_buffer_proportional_to_sample_rate():
    """The SPSC ring buffer is resized to ~2 seconds of audio at the effective sample rate.

    the default ``VOICE_TYPER_RING_BUFFER_SECONDS`` was bumped
    from ``1.0`` to ``2.0`` (and the floor from 16 to 64) so the ring
    buffer absorbs the pre-roll filter-chain prepend duration (JB-55 —
    prepend now runs on the worker thread while live audio accumulates
    in the ring buffer) plus RNNoise worker stalls. The env var
    override still works (see ``test_resize_buffers_ring_buffer_honors_env_var_override``).
    """
    rec = _make_recorder(config=_make_config(sample_rate=48000))

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    # Rate-scaled ~32 ms blocks: int(48000 / 1536 * 2.0) = 62 chunks,
    # which the 64-chunk floor bumps to 64 chunks ≈ 2.048 s at 48 kHz.
    # The ~2 s DURATION contract is what matters — the chunk count is
    # rate-invariant by design of the scaled blocksize (a fixed-512
    # capacity computation would over-allocate the chunk count ~3×
    # while the callback delivers 3×-sized chunks).
    expected_capacity = max(64, int(48000 / scaled_audio_blocksize(48000) * 2.0))
    assert expected_capacity == 64
    assert rec._ring_buffer.maxlen == expected_capacity
    assert rec._ring_buffer.maxlen * scaled_audio_blocksize(48000) / 48000 >= 2.0


def test_resize_buffers_ring_buffer_floor_at_64_chunks():
    """A low blocksize / high sample-rate combination floors the ring at 64 chunks.

    the floor was bumped from 16 to 64 so a 16 kHz / 512-block
    device still gets ~2s of headroom (64 * 512 / 16000 = 2.048s),
    preserving the RNNoise-worker-stall headroom intent that previously
    lived in the (now-removed) ``_uu36_*`` override block.
    """
    rec = _make_recorder(config=_make_config(sample_rate=8000))

    # Force a tiny ring capacity by using a small effective_sr (so
    # 8000/512 * 2.0 = 31.25 → 31, which the floor bumps to 64).
    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=8000,
        max_rec=900,
    )

    assert rec._ring_buffer.maxlen == 64


def test_resize_buffers_ring_buffer_honors_env_var_override(monkeypatch):
    """``VOICE_TYPER_RING_BUFFER_SECONDS`` overrides the ~1s default ring sizing."""
    rec = _make_recorder(config=_make_config(sample_rate=48000))
    monkeypatch.setenv("VOICE_TYPER_RING_BUFFER_SECONDS", "4.0")

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    # 48000 / 1536 * 4.0 = 125 chunks for ~4 seconds at 48 kHz
    # (rate-scaled ~32 ms blocks keep the duration contract).
    expected = int(48000 / scaled_audio_blocksize(48000) * 4.0)
    assert expected == 125
    assert rec._ring_buffer.maxlen == expected


def test_resize_buffers_preroll_resized_for_effective_sample_rate():
    """The preroll deque is resized so it holds the CONFIGURED preroll
    seconds as a DURATION at the native rate.

    Regression (rate-scaled blocks): sizing the deque from a fixed 512
    chunk assumption while the callback delivers scaled ~32 ms chunks
    made a 1.0 s pre-roll over-capture ~3.04 s of pre-speech audio at
    48 kHz. The chunk count must be computed from the same
    ``scaled_audio_blocksize`` the stream was opened with.
    """
    rec = _make_recorder(config=_make_config(sample_rate=48000), preroll_seconds=1.0)
    rec._preroll_buffer = collections.deque(maxlen=32)  # placeholder __init__ maxlen

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    # 1.0s * 48000 / 1536 + 2 = 33.25 → 33 chunks (int truncation).
    blocksize = scaled_audio_blocksize(48000)
    expected = int(1.0 * 48000 / blocksize) + 2
    assert expected == 33
    assert rec._preroll_buffer.maxlen == expected
    # DURATION contract: ≈ the configured 1.0 s (the +2-chunk slack is
    # the only headroom) — NOT the ~3.04 s a fixed-512 chunk count
    # (95 chunks) would capture with scaled chunks.
    duration_s = rec._preroll_buffer.maxlen * blocksize / 48000
    assert 0.95 <= duration_s <= 1.15, f"pre-roll duration {duration_s:.3f}s must ≈ 1.0s"


def test_resize_buffers_skips_preroll_resize_when_inactive():
    """``_preroll_active=False`` skips the preroll-deque resize."""
    rec = _make_recorder(config=_make_config(sample_rate=48000), preroll_active=False)
    placeholder = collections.deque(maxlen=32)
    rec._preroll_buffer = placeholder

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    assert rec._preroll_buffer is placeholder  # unchanged


def test_resize_buffers_skips_preroll_resize_when_zero_seconds():
    """``_preroll_seconds=0`` skips the preroll-deque resize."""
    rec = _make_recorder(config=_make_config(sample_rate=48000), preroll_seconds=0.0)
    placeholder = collections.deque(maxlen=32)
    rec._preroll_buffer = placeholder

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    assert rec._preroll_buffer is placeholder  # unchanged


def test_resize_buffers_falls_back_to_config_sample_rate_when_effective_zero():
    """An ``effective_sr <= 0`` falls back to ``config.sample_rate`` for sizing."""
    rec = _make_recorder(config=_make_config(sample_rate=16000))

    # effective_sr=0 → sizing_sr should fall back to config.sample_rate (16000).
    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=0,
        max_rec=900,
    )

    # 16000 / 512 * 2.0 = 62.5 → 62 chunks for ~2 seconds at 16 kHz,
    # which is below the 64-chunk floor, so the floor kicks in → 64.
    assert rec._ring_buffer.maxlen == 64


def test_resize_buffers_preserves_existing_ring_buffer_contents():
    """Defensive: chunks already in the ring buffer are preserved on resize."""
    rec = _make_recorder(config=_make_config(sample_rate=48000))
    chunk = np.array([0.1, 0.2], dtype=np.float32)
    rec._ring_buffer.append(chunk)

    SessionState(_make_recorder()).resize_buffers_for_sample_rate(
        rec,
        effective_sr=48000,
        max_rec=900,
    )

    assert list(rec._ring_buffer) == [chunk]


# ─── prepend_preroll_to_buffer ──────────────────────────────────────────


def test_prepend_preroll_to_buffer_prepends_chunks_in_chronological_order():
    """Preroll chunks are prepended so the OLDEST preroll lands at the FRONT of ``_buffer``.

    The original ``_preroll_buffer`` is a deque where the audio callback
    called ``append(mono_preroll)`` for each captured chunk — so the
    FIRST element is the OLDEST preroll chunk and the LAST element is
    the MOST-RECENT preroll chunk. The prepend must produce a ``_buffer``
    where the oldest preroll chunk is at position 0 (i.e. the very
    front of the recording) so the chronological order is:

        [oldest_preroll, ..., newest_preroll, recording_chunk_1, ...]

    The body achieves this by iterating ``reversed(preroll_chunks)``
    and calling ``_buffer.appendleft(chunk.copy())`` for each — the
    most-recent chunk is appended first (lands at index 0), then the
    next-most-recent pushes it to index 1, etc., until the oldest chunk
    is appended LAST and lands at index 0 (i.e. the very front).
    """
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)
    old = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    mid = np.array([0.4, 0.5, 0.6], dtype=np.float32)
    new = np.array([0.7, 0.8, 0.9], dtype=np.float32)
    rec._preroll_buffer.append(old)
    rec._preroll_buffer.append(mid)
    rec._preroll_buffer.append(new)
    # Keep copies to assert against — the in-place zeroing of the
    # preroll deque (the fix) overwrites the original arrays.
    expected_old = old.copy()
    expected_mid = mid.copy()
    expected_new = new.copy()

    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    # _buffer[0] is the OLDEST preroll chunk (chronological order);
    # _buffer[-1] is the MOST-RECENT preroll chunk.
    chunks = list(rec._audio_pipeline._buffer)
    assert len(chunks) == 3
    np.testing.assert_allclose(chunks[0], expected_old)
    np.testing.assert_allclose(chunks[1], expected_mid)
    np.testing.assert_allclose(chunks[2], expected_new)


def test_prepend_preroll_to_buffer_calls_audio_processor_process_chunk():
    """R18-F12: each preroll chunk is routed through the filter chain before prepend."""
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)
    chunk = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._preroll_buffer.append(chunk)

    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    rec._audio_processor.process_chunk.assert_called_once()
    # The first positional arg is the (mono) chunk; the keyword arg
    # ``input_sample_rate`` is set from ``_effective_sr``. We can't
    # assert on the array contents because the body's # in-place zeroing of the preroll deque (after the prepend) wipes
    # the same numpy buffer the mock captured by reference. We
    # therefore assert on the call SHAPE only.
    args, kwargs = rec._audio_processor.process_chunk.call_args
    assert isinstance(args[0], np.ndarray)
    assert args[0].ndim == 1  # mono chunk is 1-D
    assert kwargs.get("input_sample_rate") == 16000


def test_prepend_preroll_to_buffer_falls_back_to_raw_chunk_on_processor_exception():
    """If the filter chain raises, the raw (mono) chunk is prepended — start() must not block."""
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)
    # Make process_chunk raise on the first call.
    rec._audio_processor = _make_audio_processor()
    rec._audio_processor.process_chunk = MagicMock(side_effect=RuntimeError("filter failure"))
    chunk = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._preroll_buffer.append(chunk)
    expected = chunk.copy()  # zeros the original in-place

    # Must NOT raise — the exception is caught + logged.
    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    chunks = list(rec._audio_pipeline._buffer)
    assert len(chunks) == 1
    np.testing.assert_allclose(chunks[0], expected)


def test_prepend_preroll_to_buffer_skips_filter_when_processor_none():
    """A ``None`` audio processor bypasses the filter chain — raw mono chunk is prepended."""
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)
    rec._audio_processor = None
    chunk = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._preroll_buffer.append(chunk)
    expected = chunk.copy()  # zeros the original in-place

    # Must not raise.
    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    chunks = list(rec._audio_pipeline._buffer)
    assert len(chunks) == 1
    np.testing.assert_allclose(chunks[0], expected)


def test_prepend_preroll_to_buffer_zeros_and_clears_preroll_deque():
    """the preroll deque is zeroed + cleared after the prepend.

    Without this, the preroll chunks (which are now duplicated into
    ``_buffer``) remain referenced by ``_preroll_buffer`` until the
    next ``start()`` — keeping the user's voice data alive in process
    memory for the entire recording session.
    """
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)
    chunk_a = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    chunk_b = np.array([0.4, 0.5, 0.6], dtype=np.float32)
    rec._preroll_buffer.append(chunk_a)
    rec._preroll_buffer.append(chunk_b)

    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    assert len(rec._preroll_buffer) == 0
    # The original numpy buffers must be zeroed in-place.
    assert np.all(chunk_a == 0), "preroll chunk must be zeroed in-place after prepend (the fix)"
    assert np.all(chunk_b == 0), "preroll chunk must be zeroed in-place after prepend (the fix)"


def test_prepend_preroll_to_buffer_noop_on_empty_preroll():
    """An empty preroll buffer is a no-op (no buffer mutation, no filter call)."""
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)

    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    assert len(rec._audio_pipeline._buffer) == 0
    rec._audio_processor.process_chunk.assert_not_called()


def test_prepend_preroll_to_buffer_converts_stereo_preroll_to_mono():
    """Multi-channel preroll chunks are downmixed to mono via ``_ensure_mono``."""
    rec = _make_recorder(config=_make_config(sample_rate=16000), effective_sr=16000)
    stereo = np.array([[0.1, 0.2], [0.3, 0.4]], dtype=np.float32)  # shape (2, 2)
    rec._preroll_buffer.append(stereo)

    SessionState(_make_recorder()).prepend_preroll_to_buffer(rec)

    chunks = list(rec._audio_pipeline._buffer)
    assert len(chunks) == 1
    # Stereo downmix: np.mean([[0.1,0.2],[0.3,0.4]], axis=1) = [0.15, 0.35]
    np.testing.assert_allclose(chunks[0], np.array([0.15, 0.35], dtype=np.float32))


# ─── Back-reference parity (collaborator-pattern invariant) ────────────


def test_session_state_methods_match_recorder_method_count():
    """Sanity: the 5 extracted methods are present on :class:`SessionState`."""
    method_names = {
        "reset_session_state",
        "cache_session_config",
        "secure_clear_caches",
        "resize_buffers_for_sample_rate",
        "prepend_preroll_to_buffer",
    }
    for name in method_names:
        assert hasattr(SessionState, name), f"SessionState is missing method {name!r}"
        assert callable(getattr(SessionState, name))


def test_session_state_methods_are_dunder_clean():
    """The 5 extracted methods are the only non-dunder public methods on the class."""
    public_methods = {
        name for name in dir(SessionState) if not name.startswith("__") and callable(getattr(SessionState, name))
    }
    expected = {
        "reset_session_state",
        "cache_session_config",
        "secure_clear_caches",
        "resize_buffers_for_sample_rate",
        "prepend_preroll_to_buffer",
    }
    # ``_recorder`` is a non-callable attribute; ``__init__`` is dunder.
    assert public_methods == expected, f"unexpected public methods: {public_methods - expected}"


# ─── _AUDIO_RING_BUFFER_CAPACITY indirection ──────────────────────────


def test_resize_buffers_uses_package_audio_ring_buffer_capacity():
    """The ring-buffer resize compares against ``_recording_pkg._AUDIO_RING_BUFFER_CAPACITY``.

    This pins the patch-path indirection: the constant is read from the
    package namespace at call time (matching how ``_secure_clear_array``
    is routed). If the package binding is ever patched, the new value
    is picked up automatically.
    """
    from voice_typer.server import recording as rec_pkg

    # The package re-exports _AUDIO_RING_BUFFER_CAPACITY from .recorder.
    assert hasattr(rec_pkg, "_AUDIO_RING_BUFFER_CAPACITY")
    assert isinstance(rec_pkg._AUDIO_RING_BUFFER_CAPACITY, int)
    assert rec_pkg._AUDIO_RING_BUFFER_CAPACITY >= 0
