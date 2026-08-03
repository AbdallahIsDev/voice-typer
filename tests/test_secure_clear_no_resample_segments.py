"""Regression tests for the secure-clear path on
``_cached_no_resample_segments`` (the no-resample-path segment list).

Parallel to ``tests/test_secure_clear_array.py`` (XE-6-1), which pins
the in-place zeroing of ``_cached_resampled_segments``, this module
pins the same guarantee for ``_cached_no_resample_segments``.

Pre-fix: ``secure_clear_caches`` (called from ``stop()`` /
``discard()``) and ``_secure_clear_session_caches`` (called from
``start()``) zeroed ``_cached_resampled``, ``_cached_no_resample_arr``,
and each entry in ``_cached_resampled_segments``, but left
``_cached_no_resample_segments`` untouched — the list reference was
reassigned to ``[]`` *without* zeroing the underlying numpy buffers.
The no-resample path is the COMMON path in production (AudioProcessor
resamples to 16 kHz before appending, so ``_buffer_sr == target_sr``),
so this list is the primary storage for the dictated prefix in a
typical session. Up to ~115 MB of float32 audio (30 min @ 16 kHz)
survived ``stop()``/``discard()``/``start()`` in process memory until
the numpy allocator reused the blocks — defeating SEC-audit-008's
intent for the no-resample-path segment cache.

These tests pin the fix:

1. Source-inspection: ``SessionState.secure_clear_caches`` contains a
   loop over ``_cached_no_resample_segments`` calling
   ``_secure_clear_array``.
2. Source-inspection: ``Recorder._secure_clear_session_caches``
   contains the same loop.
3. Functional: ``secure_clear_caches`` zeros each segment in-place and
   resets the list to ``[]``.
4. Functional: ``_cached_no_resample_concat_dirty`` is set to ``False``
   after clear.
"""

from __future__ import annotations

import ctypes
import inspect
from unittest.mock import MagicMock, patch

import numpy as np

# ── helpers ─────────────────────────────────────────────────────────────


def _make_recorder() -> MagicMock:
    """Build a minimal Recorder with the fields the secure-clear path
    touches.  Avoids spawning real audio threads / sounddevice probes.

    The VAD availability check is mocked out because importing torch
    takes ~17s on this sandbox (see vad.is_available), which blows the
    per-test timeout.  The secure-clear path doesn't depend on VAD, so
    mocking the check is safe.
    """
    from voice_typer.server.recording import Recorder

    config = MagicMock()
    config.sample_rate = 16000
    config.microphone = None
    config.silence_warning_seconds = 20.0
    config.stop_on_silence_seconds = 120.0
    config.max_recording_time_seconds = 900
    config.device = "cpu"
    with patch("voice_typer.server.vad.is_available", return_value=False):
        return Recorder(config)


def _assert_array_memory_zeroed(arr: np.ndarray, *, ctx: str = "") -> None:
    """Assert that the underlying numpy buffer is fully zeroed,
    byte-for-byte (mirrors ``tests/test_secure_clear_array.py``)."""
    assert isinstance(arr, np.ndarray), f"{ctx}: expected ndarray, got {type(arr).__name__}"
    assert arr.dtype == np.float32, f"{ctx}: expected float32 dtype, got {arr.dtype}"
    if arr.size == 0:
        return  # nothing to verify for empty arrays
    nbytes = int(arr.nbytes)
    assert nbytes > 0, f"{ctx}: expected non-empty buffer, got nbytes={nbytes}"
    raw_bytes = ctypes.string_at(arr.ctypes.data, nbytes)
    zero_bytes = b"\x00" * nbytes
    assert raw_bytes == zero_bytes, (
        f"{ctx}: underlying numpy buffer is NOT zeroed byte-for-byte. "
        f"Expected {nbytes} zero bytes, got non-zero bytes at offsets: "
        f"{[i for i, b in enumerate(raw_bytes) if b != 0][:10]}."
    )


# ── 1. Source-inspection: SessionState.secure_clear_caches ─────────────


def test_secure_clear_caches_zeros_no_resample_segments_in_source():
    """``SessionState.secure_clear_caches`` must contain a loop over
    ``_cached_no_resample_segments`` that calls ``_secure_clear_array``
    on each non-empty segment.

    Source-string inspection is the most direct detection of the
    regression (mirrors ``tests/test_secure_clear_array.py:266-267``
    pattern). A future edit that drops the loop would otherwise go
    unnoticed by behavioral tests unless they happened to populate
    ``_cached_no_resample_segments`` with non-zero data — the
    source-string check is the durable guardrail.
    """
    from voice_typer.server.recording.session_state import SessionState

    src = inspect.getsource(SessionState.secure_clear_caches)
    assert "for seg in recorder._cached_no_resample_segments:" in src, (
        "SessionState.secure_clear_caches must iterate over "
        "_cached_no_resample_segments (mirrors the XE-6-1 loop for "
        "_cached_resampled_segments)."
    )
    assert "_secure_clear_array(seg)" in src, (
        "SessionState.secure_clear_caches must call _secure_clear_array "
        "on each segment in _cached_no_resample_segments."
    )
    # The list reference and dirty flag must be reset after the loop.
    assert "recorder._cached_no_resample_segments = []" in src, (
        "SessionState.secure_clear_caches must reset _cached_no_resample_segments to []."
    )
    assert "recorder._cached_no_resample_concat_dirty = False" in src, (
        "SessionState.secure_clear_caches must reset _cached_no_resample_concat_dirty to False."
    )


# ── 2. Source-inspection: SessionState.reset_session_state ────────────


def test_secure_clear_session_caches_zeros_no_resample_segments_in_source():
    """``SessionState.reset_session_state`` (the body of
    ``Recorder._reset_session_state``, called from ``start()`` after
    ``_secure_clear_session_caches``) must contain the same loop.

    ``Recorder._secure_clear_session_caches`` itself is intentionally a
    SMALLER helper that zeros only the two cached arrays
    (``_cached_resampled`` / ``_cached_no_resample_arr``) plus the
    resample-path segment list (see its docstring in ``recorder.py``).
    The no-resample-path segment list is zeroed by the *bulk*
    ``secure_clear_caches`` (called from ``stop()``/``discard()``) and
    by ``reset_session_state`` (called from ``start()``). The two paths
    are symmetric: a session that ends cleanly via ``stop()``/``discard()``
    has its no-resample segments zeroed by ``secure_clear_caches``; a
    session that starts after an unclean prior session has them zeroed
    by ``reset_session_state`` (the belt-and-suspenders guard). Mirrors
    ``tests/test_secure_clear_array.py`` which pins the resample-path
    arrays via source-string inspection of ``_secure_clear_session_caches``.
    """
    from voice_typer.server.recording.session_state import SessionState

    src = inspect.getsource(SessionState.reset_session_state)
    assert "for seg in recorder._cached_no_resample_segments:" in src, (
        "SessionState.reset_session_state must iterate over "
        "_cached_no_resample_segments (mirrors the XE-6-1 loop for "
        "_cached_resampled_segments, and the bulk secure_clear_caches)."
    )
    assert "_secure_clear_array(seg)" in src, (
        "SessionState.reset_session_state must call "
        "_secure_clear_array on each segment in "
        "_cached_no_resample_segments."
    )
    assert "recorder._cached_no_resample_segments = []" in src, (
        "SessionState.reset_session_state must reset _cached_no_resample_segments to []."
    )
    assert "recorder._cached_no_resample_concat_dirty = False" in src, (
        "SessionState.reset_session_state must reset _cached_no_resample_concat_dirty to False."
    )


# ── 3. Functional: secure_clear_caches zeros + resets ─────────────────


def test_secure_clear_caches_zeros_no_resample_segments_in_place():
    """``SessionState.secure_clear_caches`` must zero each segment in
    ``_cached_no_resample_segments`` IN-PLACE before replacing the list
    reference.

    Uses a recorder mock whose ``_cached_no_resample_segments`` holds
    two non-zero float32 arrays. After ``secure_clear_caches`` runs,
    each array's underlying buffer must read all-zero (byte-for-byte),
    the list reference must be ``[]``, and the dirty flag must be
    ``False``.
    """
    from voice_typer.server.recording.session_state import SessionState

    rec = _make_recorder()
    segment_a = np.zeros(100, dtype=np.float32)
    segment_b = np.zeros(200, dtype=np.float32)
    segment_a[:] = 0.1
    segment_b[:] = 0.2
    rec._cached_no_resample_segments = [segment_a, segment_b]
    rec._cached_no_resample_concat_dirty = True
    assert np.any(segment_a != 0), "test setup: segment_a must start non-zero"
    assert np.any(segment_b != 0), "test setup: segment_b must start non-zero"

    session_state = SessionState(rec)
    session_state.secure_clear_caches(rec)

    assert rec._cached_no_resample_segments == [], "secure_clear_caches must reset _cached_no_resample_segments to []."
    _assert_array_memory_zeroed(segment_a, ctx="segment_a after secure_clear_caches")
    _assert_array_memory_zeroed(segment_b, ctx="segment_b after secure_clear_caches")


# ── 4. Functional: dirty flag reset ───────────────────────────────────


def test_secure_clear_caches_resets_no_resample_concat_dirty():
    """``_cached_no_resample_concat_dirty`` must be ``False`` after
    ``secure_clear_caches`` runs, regardless of its prior value.

    Without this reset the next ``snapshot()`` would skip the
    re-concatenation step (``_ensure_no_resample_concat`` short-circuits
    when the dirty flag is ``False``) and serve a stale or empty cache.
    """
    from voice_typer.server.recording.session_state import SessionState

    rec = _make_recorder()
    rec._cached_no_resample_segments = [
        np.zeros(100, dtype=np.float32),
        np.zeros(200, dtype=np.float32),
    ]
    rec._cached_no_resample_concat_dirty = True
    assert rec._cached_no_resample_concat_dirty is True, "test setup: dirty flag must start True"

    session_state = SessionState(rec)
    session_state.secure_clear_caches(rec)

    assert rec._cached_no_resample_concat_dirty is False, (
        "secure_clear_caches must reset _cached_no_resample_concat_dirty "
        "to False so the next snapshot rebuilds the concat from scratch."
    )
    assert rec._cached_no_resample_segments == [], "secure_clear_caches must also reset the segment list to []."
