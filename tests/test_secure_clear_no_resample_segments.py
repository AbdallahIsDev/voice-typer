"""``_cached_no_resample_segments`` (the no-resample-path segment list)."""

from __future__ import annotations

import ctypes
import inspect

import numpy as np

from tests.fixtures.ipc_test_helpers import make_fake_recorder


def _assert_array_memory_zeroed(arr: np.ndarray, *, ctx: str = "") -> None:
    """Assert that the underlying numpy buffer is fully zeroed,"""
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


def test_secure_clear_caches_zeros_no_resample_segments_in_source():
    """``SessionState.secure_clear_caches`` must contain a loop over"""
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


def test_secure_clear_session_caches_zeros_no_resample_segments_in_source():
    """``Recorder._reset_session_state``, called from ``start()`` after"""
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


def test_secure_clear_caches_zeros_no_resample_segments_in_place():
    """``_cached_no_resample_segments`` IN-PLACE before replacing the list"""
    from voice_typer.server.recording.session_state import SessionState

    rec = make_fake_recorder()
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


def test_secure_clear_caches_resets_no_resample_concat_dirty():
    """``_cached_no_resample_concat_dirty`` must be ``False`` after"""
    from voice_typer.server.recording.session_state import SessionState

    rec = make_fake_recorder()
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
