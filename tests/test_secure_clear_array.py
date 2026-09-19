"""
``recorder.py`` and actually zeros cached audio arrays on session start.
``NameError``, so SEC-audit-008's secure-zeroing of cached audio arrays
"""

from __future__ import annotations

import contextlib
import inspect
from unittest.mock import MagicMock

import numpy as np
import pytest

from tests.fixtures.ipc_test_helpers import make_fake_recorder


def test_secure_clear_array_importable_from_recording_package():
    """``_secure_clear_array`` must be importable from the package."""
    from voice_typer.server.recording import _secure_clear_array

    assert callable(_secure_clear_array)


def test_secure_clear_array_bound_in_recorder_module():
    """``recorder.py``'s module namespace must bind ``_secure_clear_array``."""
    from voice_typer.server.recording import recorder as recorder_mod

    assert hasattr(recorder_mod, "_secure_clear_array"), (
        "recorder.py must import _secure_clear_array at module top "
        "( fix). Without the import, the SEC-audit-008 secure-clear "
        "path silently no-ops via the surrounding try/except."
    )
    assert callable(recorder_mod._secure_clear_array)


def test_secure_clear_array_source_file_lives_in_buffer_submodule():
    """Pin the actual definition location so future refactors don't"""
    from voice_typer.server.recording import _secure_clear_array

    assert _secure_clear_array.__module__ == "voice_typer.server.recording.buffer"
    assert _secure_clear_array.__name__ == "_secure_clear_array"


def test_secure_clear_array_zeros_non_zero_array():
    """Calling ``_secure_clear_array(arr)`` must zero ``arr`` in-place."""
    from voice_typer.server.recording import _secure_clear_array

    arr = np.array([0.5, -0.3, 0.8, 0.0, -1.0], dtype=np.float32)
    assert np.any(arr != 0), "test setup: array must start non-zero"

    _secure_clear_array(arr)

    assert np.all(arr == 0), (
        "_secure_clear_array must zero the array in-place (SEC-audit-008: prevents forensic recovery of audio data)"
    )
    # Shape and dtype are preserved (zeroing must not reshape or promote).
    assert arr.shape == (5,)
    assert arr.dtype == np.float32


def test_secure_clear_array_zeros_2d_array():
    """2-D arrays (multi-channel audio) must also be zeroed in-place."""
    from voice_typer.server.recording import _secure_clear_array

    arr = np.array([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6]], dtype=np.float32)
    _secure_clear_array(arr)
    assert np.all(arr == 0)
    assert arr.shape == (3, 2)


def test_secure_clear_array_on_empty_array_is_noop():
    """Empty arrays must not raise (the production code path guards with"""
    from voice_typer.server.recording import _secure_clear_array

    arr = np.array([], dtype=np.float32)
    _secure_clear_array(arr)  # must not raise
    assert arr.size == 0


def _make_recorder() -> MagicMock:
    """Backwards-compatible alias over the shared factory (XS-42)."""
    return make_fake_recorder()


def test_recorder_start_zeros_cached_resampled_array():
    """``Recorder.start()`` must zero ``_cached_resampled`` before"""
    rec = _make_recorder()

    # Simulate the previous session's cached audio: a non-zero array
    previous_audio = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    rec._cached_resampled = previous_audio
    assert np.any(previous_audio != 0), "test setup: must start non-zero"

    rec.start()

    # SEC-audit-008: the original buffer must be zeroed in-place.
    assert np.all(previous_audio == 0), (
        "Recorder.start() must zero _cached_resampled in-place via "
        "_secure_clear_array before replacing it ( / SEC-audit-008). "
        "Pre-fix: the NameError was swallowed by except Exception: pass, "
        "leaving the previous session's audio in process memory."
    )


def test_recorder_start_zeros_cached_no_resample_array():
    """Same as above for the second cached array (``_cached_no_resample_arr``)."""
    rec = _make_recorder()

    previous_audio = np.array([0.5, 0.6, 0.7], dtype=np.float32)
    rec._cached_no_resample_arr = previous_audio
    assert np.any(previous_audio != 0), "test setup: must start non-zero"

    rec.start()

    assert np.all(previous_audio == 0), (
        "Recorder.start() must zero _cached_no_resample_arr in-place via "
        "_secure_clear_array before replacing it ( / SEC-audit-008)."
    )


def test_recorder_start_zeros_both_cached_arrays_when_both_present():
    """When both caches are populated, both must be zeroed."""
    rec = _make_recorder()

    cached_resampled = np.array([0.1, 0.2], dtype=np.float32)
    cached_no_resample = np.array([0.3, 0.4, 0.5], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample

    rec.start()

    assert np.all(cached_resampled == 0)
    assert np.all(cached_no_resample == 0)


def test_recorder_start_skips_zeroing_when_caches_are_empty():
    """Empty / None caches must not raise (the size > 0 guard)."""
    rec = _make_recorder()
    rec._cached_resampled = np.array([], dtype=np.float32)
    rec._cached_no_resample_arr = None

    # Must not raise.
    rec.start()


def test_recorder_start_except_clause_does_not_swallow_nameerror():
    """Source-string check: the secure-clear ``except`` clause must be"""
    from voice_typer.server.recording import Recorder

    src = inspect.getsource(Recorder._secure_clear_session_caches)
    assert "_secure_clear_array(self._cached_resampled)" in src, (
        "Recorder._secure_clear_session_caches must call _secure_clear_array on _cached_resampled"
    )
    assert "_secure_clear_array(self._cached_no_resample_arr)" in src, (
        "Recorder._secure_clear_session_caches must call _secure_clear_array on _cached_no_resample_arr"
    )
    lines = src.split("\n")
    secure_clear_block: list[str] = []
    in_block = False
    seen_except_count = 0
    seen_body_after_second_except = False
    for line in lines:
        if "_secure_clear_array(" in line:
            in_block = True
            secure_clear_block.append(line)
            continue
        if in_block:
            secure_clear_block.append(line)
            # Stop after we've seen the body of the second ``except ...:``
            if line.strip().startswith("except "):
                seen_except_count += 1
            elif seen_except_count == 2 and line.strip():
                seen_body_after_second_except = True
                break
    block_src = "\n".join(secure_clear_block)
    assert seen_body_after_second_except, (
        "Could not locate the end of the secure-clear block (expected to "
        "find the body line after the second ``except`` clause). "
        f"Collected block:\n{block_src}"
    )
    assert block_src.count("except ") == 2, (
        f"expected exactly two `except` clauses in the secure-clear block, got:\n{block_src}"
    )
    assert "except Exception:" not in block_src, (
        "Recorder._secure_clear_session_caches must NOT use bare ``except Exception:`` around the "
        "_secure_clear_array calls, that swallows NameError-class import "
        "bugs ( regression). Use a narrowed clause like "
        "``except (OSError, ValueError):``.\n"
        f"secure-clear block:\n{block_src}"
    )
    assert "except (OSError, ValueError):" in block_src, (
        "Recorder._secure_clear_session_caches must narrow the secure-clear except clause to "
        "``(OSError, ValueError)`` ( fix).\n"
        f"secure-clear block:\n{block_src}"
    )


def test_secure_clear_array_import_statement_present_in_recorder_source():
    """Source-string check: ``recorder.py`` must import ``_secure_clear_array``"""
    from voice_typer.server.recording import recorder as recorder_mod

    src = inspect.getsource(recorder_mod)
    assert "from voice_typer.server.recording import _secure_clear_array" in src, (
        "recorder.py must import _secure_clear_array at module top "
        "( fix). Without this import, the SEC-audit-008 secure-clear "
        "path silently no-ops via the surrounding try/except."
    )


@pytest.mark.parametrize("iteration", range(3))
def test_secure_clear_array_idempotent_across_sessions(iteration: int):
    """The secure-clear path must work across multiple sessions."""
    rec = _make_recorder()

    for i in range(iteration + 1):
        previous_audio = np.array([0.9, 0.8, 0.7], dtype=np.float32)
        rec._cached_resampled = previous_audio
        rec._cached_no_resample_arr = previous_audio.copy()
        rec._recording_event.clear()
        rec.start()
        # After start(), the previous buffer must be zeroed.
        assert np.all(previous_audio == 0), f"iteration {i}: previous_audio must be zeroed after start()"


# 6. : stop() / discard() zero cached arrays in-place ────────


def test_stop_clears_cached_arrays():
    """``_cached_no_resample_arr`` IN-PLACE before replacing them."""
    rec = _make_recorder()

    cached_resampled = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    cached_no_resample = np.array([0.5, 0.6, 0.7], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample
    assert np.any(cached_resampled != 0), "test setup: _cached_resampled must start non-zero"
    assert np.any(cached_no_resample != 0), "test setup: _cached_no_resample_arr must start non-zero"

    # Pretend a recording is in progress so stop() doesn't early-return
    rec._recording_event.set()

    # Empty buffer → stop() takes the early-return path at
    rec.stop()

    assert np.all(cached_resampled == 0), (
        "Recorder.stop() must zero _cached_resampled in-place via "
        "_secure_clear_array before replacing it (G4-H-06 / "
        "SEC-audit-008). Pre-fix: the cache was reassigned to a fresh "
        "empty array but the previous session's audio lingered in the "
        "old numpy buffer until the allocator reused it."
    )
    assert np.all(cached_no_resample == 0), (
        "Recorder.stop() must zero _cached_no_resample_arr in-place via "
        "_secure_clear_array before replacing it (G4-H-06 / "
        "SEC-audit-008)."
    )
    # And the cache attributes themselves must be reset to their
    assert rec._cached_resampled.size == 0
    assert rec._cached_no_resample_arr is None


def test_stop_clears_cached_arrays_on_main_path():
    """the MAIN stop() path (when ``_buffer`` is"""
    rec = _make_recorder()

    cached_resampled = np.array([0.1, 0.2], dtype=np.float32)
    cached_no_resample = np.array([0.3, 0.4, 0.5], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample
    rec._recording_event.set()

    # Populate the buffer so stop() takes the main path (concatenate +
    rec._audio_pipeline._buffer.append(np.array([[1.0], [2.0]], dtype=np.float32))
    rec._audio_pipeline._buffer.append(np.array([[3.0]], dtype=np.float32))

    rec.stop()

    assert np.all(cached_resampled == 0), "Recorder.stop() main path must zero _cached_resampled in-place (G4-H-06)."
    assert np.all(cached_no_resample == 0), (
        "Recorder.stop() main path must zero _cached_no_resample_arr in-place (G4-H-06)."
    )


def test_discard_clears_cached_arrays():
    """``discard()`` must also zero the cached"""
    rec = _make_recorder()

    cached_resampled = np.array([0.8, 0.9], dtype=np.float32)
    cached_no_resample = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample

    rec._recording_event.set()
    rec.discard()

    assert np.all(cached_resampled == 0), "Recorder.discard() must zero _cached_resampled in-place (G4-H-06)."
    assert np.all(cached_no_resample == 0), "Recorder.discard() must zero _cached_no_resample_arr in-place (G4-H-06)."


# 7. : stop()/discard()/start() zero the segments list in-place ──


def _assert_array_memory_zeroed(arr: np.ndarray, *, ctx: str = "") -> None:
    """Assert that the underlying numpy buffer (``arr.ctypes.data`` region)"""
    import ctypes

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
        f"{[i for i, b in enumerate(raw_bytes) if b != 0][:10]}. "
        "regression: the segment array's memory must be zeroed "
        "in-place via _secure_clear_array before the list reference is "
        "dropped, otherwise up to ~115 MB of dictated float32 audio "
        "lingers in process memory until the numpy allocator reuses the block."
    )


def test_stop_zeros_cached_resampled_segments_in_place():
    """``_cached_resampled_segments`` IN-PLACE before replacing the list"""
    rec = _make_recorder()

    segment_a = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    segment_b = np.array([0.5, 0.6, 0.7], dtype=np.float32)
    segment_c = np.array([0.8, 0.9, 1.0, 1.1, 1.2], dtype=np.float32)
    rec._cached_resampled_segments = [segment_a, segment_b, segment_c]
    rec._cached_resampled_concat_dirty = True
    assert np.any(segment_a != 0), "test setup: segment_a must start non-zero"

    rec._recording_event.set()

    rec.stop()

    assert rec._cached_resampled_segments == [], "Recorder.stop() must reset _cached_resampled_segments to []."
    assert rec._cached_resampled_concat_dirty is False
    _assert_array_memory_zeroed(segment_a, ctx="segment_a after stop()")
    _assert_array_memory_zeroed(segment_b, ctx="segment_b after stop()")
    _assert_array_memory_zeroed(segment_c, ctx="segment_c after stop()")


def test_discard_zeros_cached_resampled_segments_in_place(monkeypatch):
    """(companion): ``discard()`` must also zero the segment list"""
    import voice_typer.server.recording as rec_pkg

    monkeypatch.setattr(rec_pkg, "_secure_clear_array_background", lambda _buf: None)

    rec = _make_recorder()

    segment_a = np.array([0.8, 0.9], dtype=np.float32)
    segment_b = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._cached_resampled_segments = [segment_a, segment_b]
    rec._cached_resampled_concat_dirty = True

    rec._recording_event.set()
    rec.discard()

    assert rec._cached_resampled_segments == [], "Recorder.discard() must reset _cached_resampled_segments to []."
    assert rec._cached_resampled_concat_dirty is False
    _assert_array_memory_zeroed(segment_a, ctx="segment_a after discard()")
    _assert_array_memory_zeroed(segment_b, ctx="segment_b after discard()")


def test_start_zeros_cached_resampled_segments_in_place():
    """(companion): ``start()`` must zero the segment list in-place"""
    rec = _make_recorder()

    segment_a = np.array([0.4, 0.5, 0.6], dtype=np.float32)
    segment_b = np.array([0.7, 0.8], dtype=np.float32)
    rec._cached_resampled_segments = [segment_a, segment_b]
    rec._cached_resampled_concat_dirty = True
    assert np.any(segment_a != 0), "test setup: segment_a must start non-zero"

    with contextlib.suppress(Exception):
        rec.start()

    assert rec._cached_resampled_segments == [], "Recorder.start() must reset _cached_resampled_segments to []."
    assert rec._cached_resampled_concat_dirty is False
    _assert_array_memory_zeroed(segment_a, ctx="segment_a after start()")
    _assert_array_memory_zeroed(segment_b, ctx="segment_b after start()")


def test_secure_clear_caches_handles_empty_segment_list_without_raising():
    """``secure_clear_caches`` must handle an empty segment list"""
    rec = _make_recorder()
    rec._cached_resampled_segments = []
    rec._cached_resampled_concat_dirty = False
    rec._recording_event.set()

    rec.stop()

    assert rec._cached_resampled_segments == []
    assert rec._cached_resampled_concat_dirty is False


def test_secure_clear_caches_handles_none_entries_in_segment_list():
    """segment list (defensive, the production code path never appends"""
    rec = _make_recorder()

    segment_a = np.array([0.1, 0.2], dtype=np.float32)
    rec._cached_resampled_segments = [segment_a, None]
    rec._cached_resampled_concat_dirty = True
    rec._recording_event.set()

    rec.stop()

    assert rec._cached_resampled_segments == []
    _assert_array_memory_zeroed(segment_a, ctx="segment_a with None sibling in list")
