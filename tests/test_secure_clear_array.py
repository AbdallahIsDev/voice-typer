"""CR-21 regression tests: ``_secure_clear_array`` is reachable from
``recorder.py`` and actually zeros cached audio arrays on session start.

Before the CR-21 fix, ``recorder.py`` called ``_secure_clear_array(...)``
as a bare name (no import).  The function is defined in
``voice_typer/server/recording/buffer.py`` and re-exported by the
package ``__init__.py``, but ``recorder.py`` never imported it.  The
surrounding ``try/except Exception: pass`` swallowed the resulting
``NameError``, so SEC-audit-008's secure-zeroing of cached audio arrays
(*``_cached_resampled``* and *``_cached_no_resample_arr``*) NEVER
executed — the previous session's audio lingered in process memory
until the next GC pass freed the numpy arrays.  That is exactly the
regression SEC-audit-008 was meant to fix.

These tests pin the fix:

1. ``_secure_clear_array`` is importable from
   ``voice_typer.server.recording`` (and bound in ``recorder.py``'s
   module namespace).
2. ``_secure_clear_array`` zeros a non-zero numpy array in-place.
3. ``Recorder.start()`` zeros the cached audio arrays before clearing
   them (integration test for the actual SEC-audit-008 path).
4. The ``except`` clause has been tightened so a future ``NameError``
   would surface instead of being silently swallowed (verified by
   source-string inspection of the tightened ``except`` clause).
"""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import numpy as np
import pytest

# ─── 1. Importability ────────────────────────────────────────────────────


def test_secure_clear_array_importable_from_recording_package():
    """``_secure_clear_array`` must be importable from the package.

    Pre-CR-21: ``recorder.py`` used a bare-name lookup that raised
    ``NameError``.  Post-CR-21: the name is imported at module top.
    """
    from voice_typer.server.recording import _secure_clear_array

    assert callable(_secure_clear_array)


def test_secure_clear_array_bound_in_recorder_module():
    """``recorder.py``'s module namespace must bind ``_secure_clear_array``.

    This is the direct regression check for the CR-21 ``NameError``: if
    the import is removed, ``getattr(recorder_module, ...)`` raises
    ``AttributeError`` (rather than the much-later ``NameError`` that
    used to be swallowed by ``except Exception: pass``).
    """
    from voice_typer.server.recording import recorder as recorder_mod

    assert hasattr(recorder_mod, "_secure_clear_array"), (
        "recorder.py must import _secure_clear_array at module top "
        "(CR-21 fix). Without the import, the SEC-audit-008 secure-clear "
        "path silently no-ops via the surrounding try/except."
    )
    assert callable(recorder_mod._secure_clear_array)


def test_secure_clear_array_source_file_lives_in_buffer_submodule():
    """Pin the actual definition location so future refactors don't
    re-introduce a bare-name lookup.

    Pre-CR-21 docstring claimed the function lived at ``recording.py:78``
    (a file that doesn't exist post-ARCH-045 split).  The actual
    definition lives in ``recording/buffer.py``.
    """
    from voice_typer.server.recording import _secure_clear_array

    assert _secure_clear_array.__module__ == "voice_typer.server.recording.buffer"
    assert _secure_clear_array.__name__ == "_secure_clear_array"


# ─── 2. Behaviour: actually zeros the array ──────────────────────────────


def test_secure_clear_array_zeros_non_zero_array():
    """Calling ``_secure_clear_array(arr)`` must zero ``arr`` in-place.

    Pre-CR-21: the call raised ``NameError`` before reaching this
    function, so the array was never zeroed.  Post-CR-21: the call
    succeeds and the array's contents are all 0.0 after the call.
    """
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
    """Empty arrays must not raise (the production code path guards with
    ``arr.size > 0`` but the helper itself should be robust)."""
    from voice_typer.server.recording import _secure_clear_array

    arr = np.array([], dtype=np.float32)
    _secure_clear_array(arr)  # must not raise
    assert arr.size == 0


# ─── 3. Integration: ``Recorder.start()`` zeros cached arrays ────────────


def _make_recorder() -> MagicMock:
    """Build a minimal Recorder with the fields the secure-clear path
    touches.  Avoids spawning real audio threads / sounddevice probes.

    The VAD availability check is mocked out because importing torch
    takes ~17s on this sandbox (see vad.is_available), which blows the
    per-test timeout.  The secure-clear path doesn't depend on VAD, so
    mocking the check is safe.
    """
    from unittest.mock import patch

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


def test_recorder_start_zeros_cached_resampled_array():
    """``Recorder.start()`` must zero ``_cached_resampled`` before
    clearing it.

    Pre-CR-21: the secure-clear call raised ``NameError`` and was
    swallowed by ``except Exception: pass``.  The cached array
    reference was then replaced with a fresh empty array, but the
    underlying numpy buffer (with the previous session's audio) was
    not zeroed — it lingered in process memory until GC.
    Post-CR-21: the array is zeroed in-place before being replaced.
    """
    rec = _make_recorder()

    # Simulate the previous session's cached audio: a non-zero array
    # that we keep a separate reference to so we can inspect it after
    # ``start()`` replaces ``_cached_resampled`` with a fresh empty one.
    previous_audio = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    rec._cached_resampled = previous_audio
    assert np.any(previous_audio != 0), "test setup: must start non-zero"

    rec.start()

    # SEC-audit-008: the original buffer must be zeroed in-place.
    assert np.all(previous_audio == 0), (
        "Recorder.start() must zero _cached_resampled in-place via "
        "_secure_clear_array before replacing it (CR-21 / SEC-audit-008). "
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
        "_secure_clear_array before replacing it (CR-21 / SEC-audit-008)."
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


# ─── 4. ``except`` clause tightened (no longer swallows NameError) ──────


def test_recorder_start_except_clause_does_not_swallow_nameerror():
    """Source-string check: the secure-clear ``except`` clause must be
    tightened to ``(OSError, ValueError)`` (or a similarly narrow tuple).

    Pre-CR-21: ``except Exception: pass`` swallowed the ``NameError``
    from the missing import, leaving the secure-clear path silently
    broken.  Post-CR-21: the ``except`` is narrowed so a future
    ``NameError``-class import bug surfaces immediately.

    RW-8 / ARCH-12: this is a source-string check (not a behavioral
    test) because the only way to deterministically catch a regression
    that re-broadens the ``except`` is to inspect the source.  A
    behavioral test would need to delete ``_secure_clear_array`` from
    the module namespace and confirm the call raises — but that's
    exactly the regression this test pins, so the source-string check
    is the most direct detection.
    """
    from voice_typer.server.recording import Recorder

    src = inspect.getsource(Recorder.start)
    # The secure-clear call sites must be present (regression check for
    # CR-21 itself: the import fix means the call no longer raises
    # NameError, so the call sites must still be there).
    assert "_secure_clear_array(self._cached_resampled)" in src, (
        "Recorder.start must call _secure_clear_array on _cached_resampled"
    )
    assert "_secure_clear_array(self._cached_no_resample_arr)" in src, (
        "Recorder.start must call _secure_clear_array on _cached_no_resample_arr"
    )
    # Extract the secure-clear try/except block (the lines around the
    # ``_secure_clear_array`` call sites).  We can't just check that
    # ``except Exception:`` is absent from the whole ``start()`` source
    # because ``start()`` has several other ``except Exception:`` clauses
    # for unrelated concerns (device probing, audio stream teardown, etc.)
    # that are out of scope for CR-21.
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
            # clause (one for _cached_resampled, one for
            # _cached_no_resample_arr).  The body may be ``pass`` (the
            # original CR-21 form) or — per the project's
            # "no ``except: pass``" rule — a real handler such as
            # ``log.warning(...)``.  Either way, the first non-empty,
            # non-``except`` line after the 2nd ``except`` marks the end
            # of the secure-clear block.
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
        "Recorder.start must NOT use bare ``except Exception:`` around the "
        "_secure_clear_array calls — that swallows NameError-class import "
        "bugs (CR-21 regression). Use a narrowed clause like "
        "``except (OSError, ValueError):``.\n"
        f"secure-clear block:\n{block_src}"
    )
    assert "except (OSError, ValueError):" in block_src, (
        "Recorder.start must narrow the secure-clear except clause to "
        "``(OSError, ValueError)`` (CR-21 fix).\n"
        f"secure-clear block:\n{block_src}"
    )


def test_secure_clear_array_import_statement_present_in_recorder_source():
    """Source-string check: ``recorder.py`` must import ``_secure_clear_array``
    at module top (CR-21 fix).

    RW-8 / ARCH-12: source-string check is the most direct way to catch
    a future regression where the import is removed (which would
    reintroduce the silent NameError-swallowing bug).
    """
    from voice_typer.server.recording import recorder as recorder_mod

    src = inspect.getsource(recorder_mod)
    assert "from voice_typer.server.recording import _secure_clear_array" in src, (
        "recorder.py must import _secure_clear_array at module top "
        "(CR-21 fix). Without this import, the SEC-audit-008 secure-clear "
        "path silently no-ops via the surrounding try/except."
    )


# ─── 5. Run the secure-clear path twice (no lingering state) ────────────


@pytest.mark.parametrize("iteration", range(3))
def test_secure_clear_array_idempotent_across_sessions(iteration: int):
    """The secure-clear path must work across multiple sessions.

    Pre-CR-21: the silent NameError meant the secure-clear NEVER ran,
    so the second session's cached array pointed to the same underlying
    buffer as the first (no zeroing happened).  Post-CR-21: each call
    to ``start()`` zeros the previous cache before replacing it.
    """
    rec = _make_recorder()

    for i in range(iteration + 1):
        previous_audio = np.array([0.9, 0.8, 0.7], dtype=np.float32)
        rec._cached_resampled = previous_audio
        rec._cached_no_resample_arr = previous_audio.copy()
        # ``start()`` short-circuits with ``return`` if
        # ``_recording_event`` is already set (i.e. we're already
        # recording).  Clear it before each iteration so the
        # secure-clear path actually runs on every iteration.
        rec._recording_event.clear()
        rec.start()
        # After start(), the previous buffer must be zeroed.
        assert np.all(previous_audio == 0), f"iteration {i}: previous_audio must be zeroed after start()"


# ─── 6. G4-H-06: stop() / discard() zero cached arrays in-place ────────


def test_stop_clears_cached_arrays():
    """G4-H-06: ``stop()`` must securely zero ``_cached_resampled`` and
    ``_cached_no_resample_arr`` IN-PLACE before replacing them.

    Pre-G4-H-06: ``stop()`` reassigned both caches to fresh empty arrays
    / ``None`` without first zeroing the underlying numpy buffers.  The
    cached arrays can hold up to ~30 min of 16 kHz float32 audio (~115 MB)
    of the user's voice, so simply dropping the reference left that data
    in process memory until the numpy allocator reused the block —
    defeating SEC-audit-008's intent.

    Post-G4-H-06: ``stop()`` calls ``_secure_clear_caches()`` which
    invokes ``_secure_clear_array`` (``arr.fill(0)``) on each non-empty
    cache BEFORE reassignment, so the underlying numpy buffer is zeroed
    in-place.  This test keeps a separate reference to each cached
    array (so we can inspect its contents after ``stop()`` replaces the
    cache attribute) and asserts both are all-zeros post-stop.
    """
    rec = _make_recorder()

    # Populate the caches with non-zero data and keep separate refs so
    # we can inspect the underlying buffers after stop() reassigns the
    # cache attributes.
    cached_resampled = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    cached_no_resample = np.array([0.5, 0.6, 0.7], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample
    assert np.any(cached_resampled != 0), "test setup: _cached_resampled must start non-zero"
    assert np.any(cached_no_resample != 0), "test setup: _cached_no_resample_arr must start non-zero"

    # Pretend a recording is in progress so stop() doesn't early-return
    # at the ``if not self._recording_event.is_set()`` guard.  No real
    # audio stream / worker thread is started, so _teardown_stream /
    # _stop_audio_worker / _stop_event_worker are no-ops (the stream is
    # None and the workers were never started).
    rec._recording_event.set()

    # Empty buffer → stop() takes the early-return path at
    # ``if not self._buffer`` (the first of the two stop() code paths).
    # ``_secure_clear_caches()`` must be called either way.
    rec.stop()

    # G4-H-06: the underlying numpy buffers must be zeroed in-place
    # (not just dereferenced).  If ``stop()`` had only reassigned the
    # cache attributes, these separate refs would still hold the
    # original non-zero data.
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
    # empty/None defaults.
    assert rec._cached_resampled.size == 0
    assert rec._cached_no_resample_arr is None


def test_stop_clears_cached_arrays_on_main_path():
    """G4-H-06 (companion): the MAIN stop() path (when ``_buffer`` is
    non-empty and audio is concatenated) must also zero the cached
    arrays in-place.

    The first test (``test_stop_clears_cached_arrays``) exercises the
    empty-buffer early-return path.  This test exercises the main path
    by populating ``_buffer`` with real chunks so ``np.concatenate`` runs.
    """
    rec = _make_recorder()

    cached_resampled = np.array([0.1, 0.2], dtype=np.float32)
    cached_no_resample = np.array([0.3, 0.4, 0.5], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample
    rec._recording_event.set()

    # Populate the buffer so stop() takes the main path (concatenate +
    # secure_clear_array_background + _secure_clear_caches).
    rec._buffer.append(np.array([[1.0], [2.0]], dtype=np.float32))
    rec._buffer.append(np.array([[3.0]], dtype=np.float32))

    rec.stop()

    assert np.all(cached_resampled == 0), "Recorder.stop() main path must zero _cached_resampled in-place (G4-H-06)."
    assert np.all(cached_no_resample == 0), (
        "Recorder.stop() main path must zero _cached_no_resample_arr in-place (G4-H-06)."
    )


def test_discard_clears_cached_arrays():
    """G4-H-06 (companion): ``discard()`` must also zero the cached
    arrays in-place via ``_secure_clear_caches()``."""
    rec = _make_recorder()

    cached_resampled = np.array([0.8, 0.9], dtype=np.float32)
    cached_no_resample = np.array([0.1, 0.2, 0.3], dtype=np.float32)
    rec._cached_resampled = cached_resampled
    rec._cached_no_resample_arr = cached_no_resample

    # discard() doesn't check _recording_event — it just clears it.
    rec.discard()

    assert np.all(cached_resampled == 0), "Recorder.discard() must zero _cached_resampled in-place (G4-H-06)."
    assert np.all(cached_no_resample == 0), "Recorder.discard() must zero _cached_no_resample_arr in-place (G4-H-06)."
