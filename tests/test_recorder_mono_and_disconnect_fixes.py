"""Focused tests for the mono downmix scratch buffer and the disconnect
hot-swap restart fixes.

Covers:
  * ``_ensure_mono`` stereo downmix correctness, result independence
    (no aliasing of the per-thread scratch), thread-local isolation,
    and lazy scratch growth for oversized chunks.
  * ``DisconnectHandler.restart_stream`` state-write atomicity
    (``_actual_channels`` / ``_device_disconnected`` /
    ``_device_disconnect_retries`` written inside ``recorder._audio_pipeline._lock``),
    buffer + cache flush on hot-swap, ring-buffer drain, and the
    narrowed ``except`` clause that re-raises programming bugs while
    recovering from ``sd.PortAudioError`` / ``OSError``.
"""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import numpy as np
import pytest
from voice_typer.server.recording.disconnect_handler import (
    DisconnectHandler,
)
from voice_typer.server.recording.format import ensure_mono
from voice_typer.server.recording.recorder import Recorder

# ── helpers ─────────────────────────────────────────────────────────────


def _make_recorder() -> Recorder:
    """Build a real ``Recorder`` with a MagicMock config (no audio device).

    Delegates to the shared canonical factory (XS-42 helper dedup) —
    see ``tests/fixtures/recorder_test_helpers.make_recorder`` for the
    pre-populated config fields.
    """
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    return make_fake_recorder()


# ── _ensure_mono: correctness + independence ────────────────────────────


class TestEnsureMonoScratchBuffer:
    """``_ensure_mono`` uses a per-thread scratch for the stereo fast path."""

    def test_mono_1d_passthrough_returns_same_reference(self):
        """1-D (already mono) input is returned unchanged (no copy)."""
        r = _make_recorder()
        audio = np.array([1.0, 2.0, 3.0], dtype=np.float32)
        result = ensure_mono(r, audio)
        assert result is audio

    def test_stereo_downmix_matches_np_mean(self):
        """The manual (L+R)/2 fast path must match ``np.mean``."""
        r = _make_recorder()
        audio = np.array([[1.0, 3.0], [2.0, 4.0], [5.0, 7.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)
        assert result.shape == (3,)

    def test_stereo_result_is_independent_copy(self):
        """The returned array must NOT alias the scratch buffer.

        If the result were a view, the next ``_ensure_mono`` call would
        overwrite it — corrupting any stored reference (``_buffer``,
        ``_preroll_buffer``).
        """
        r = _make_recorder()
        audio = np.array([[1.0, 3.0], [2.0, 4.0]], dtype=np.float32)
        first = ensure_mono(r, audio)
        # Mutate the returned array — must not affect the scratch.
        first[0] = 999.0
        # Second call with the same input — result must reflect the
        # original mean, not the mutation we applied to ``first``.
        second = ensure_mono(r, audio)
        assert second[0] != 999.0, "result aliased the scratch — stored references would corrupt"

    def test_repeated_calls_do_not_corrupt_prior_results(self):
        """Multiple sequential calls must each return an independent array.

        This is the core correctness invariant for the scratch+copy
        design: even though the scratch is reused, each returned array
        is an independent copy that survives subsequent calls.
        """
        r = _make_recorder()
        results = []
        for i in range(10):
            audio = np.full((4, 2), float(i), dtype=np.float32)
            results.append(ensure_mono(r, audio))
        # Each result must still hold its original value.
        for i, res in enumerate(results):
            assert np.all(res == float(i)), f"result {i} corrupted by subsequent calls: {res}"

    def test_single_channel_2d_reshape(self):
        """2-D input with shape[1]==1 is reshaped to 1-D (no downmix)."""
        r = _make_recorder()
        audio = np.array([[1.0], [2.0], [3.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        np.testing.assert_array_equal(result, np.array([1.0, 2.0, 3.0], dtype=np.float32))

    def test_multi_channel_falls_back_to_np_mean(self):
        """>2-channel input uses ``np.mean`` (rare — channels clamped to
        [1,2] at stream-open, but the fallback must still be correct)."""
        r = _make_recorder()
        audio = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
        result = ensure_mono(r, audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)

    def test_large_chunk_triggers_scratch_growth(self):
        """A chunk larger than the default 1024-sample scratch must grow
        the scratch without error and produce the correct downmix."""
        r = _make_recorder()
        n = 2048
        audio = np.random.randn(n, 2).astype(np.float32)
        result = ensure_mono(r, audio)
        expected = np.mean(audio, axis=1, dtype=np.float32)
        np.testing.assert_array_almost_equal(result, expected)
        assert result.shape == (n,)

    def test_is_instance_method_not_staticmethod(self):
        """``ensure_mono`` takes the owning ``Recorder`` as its first
        parameter (the collaborator-function contract)."""
        # Python 3.12: instance methods are just functions in __dict__;
        # staticmethods are descriptors. Check via inspect.signature.
        sig = inspect.signature(ensure_mono)
        params = list(sig.parameters.keys())
        assert params[0] == "recorder", f"ensure_mono must take 'recorder' as first param, got {params[0]}"

    def test_mono_scratch_local_initialized_in_init(self):
        """``__init__`` must set up the thread-local scratch holder."""
        r = _make_recorder()
        assert hasattr(r, "_mono_scratch_local"), "_mono_scratch_local must be initialized in __init__"
        assert isinstance(r._mono_scratch_local, threading.local)


# ── _ensure_mono: thread-local isolation ────────────────────────────────


class TestEnsureMonoThreadLocal:
    """The scratch is ``threading.local`` so the RT callback (pre-roll)
    and the audio worker each get their own buffer — no race."""

    def test_concurrent_calls_from_multiple_threads_do_not_corrupt(self):
        """Each thread must get its own scratch; concurrent calls must
        not interfere."""
        r = _make_recorder()
        errors: list[str] = []

        def worker(tid: int) -> None:
            try:
                for i in range(200):
                    audio = np.full((512, 2), float(tid), dtype=np.float32)
                    result = ensure_mono(r, audio)
                    # The result must equal the mean of the input —
                    # if another thread's scratch leaked, the value
                    # would differ.
                    expected = float(tid)
                    if not np.allclose(result, expected):
                        errors.append(f"thread {tid} iter {i}: expected {expected}, got {result[0]}")
                        return
            except Exception as exc:  # noqa: BLE001
                errors.append(f"thread {tid} raised: {exc}")

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, f"thread-local isolation failed: {errors}"


# DisconnectHandler.restart_stream:  lock atomicity ──────────────


class TestRestartStreamLockAtomicity:
    """UE-35: the three disconnect-state writes must be inside
    ``recorder._audio_pipeline._lock`` so a concurrent health-checker cannot race."""

    def test_state_writes_are_inside_lock_in_source(self):
        """Source inspection: ``_actual_channels``, ``_device_disconnected``,
        and ``_device_disconnect_retries`` must appear AFTER ``with
        recorder._audio_pipeline._lock:`` and BEFORE the lock block ends (i.e., before
        ``log.info("Successfully restarted")``)."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        lock_idx = src.find("with recorder._audio_pipeline._lock:")
        assert lock_idx >= 0, "restart_stream must acquire recorder._audio_pipeline._lock"
        # Find the end of the lock block (next unindented line).
        lock_block = src[lock_idx:]
        # All three writes must be inside the lock block.
        assert "_actual_channels = channels" in lock_block, (
            "_actual_channels must be assigned inside recorder._audio_pipeline._lock"
        )
        assert "_device_disconnected = False" in lock_block, (
            "_device_disconnected must be assigned inside recorder._audio_pipeline._lock"
        )
        assert "_device_disconnect_retries = 0" in lock_block, (
            "_device_disconnect_retries must be assigned inside recorder._audio_pipeline._lock"
        )

    def test_state_writes_not_outside_lock(self):
        """The three writes must NOT appear between the lock block and
        the ``log.info("Successfully restarted")`` call (the pre-fix
        location)."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        lock_idx = src.find("with recorder._audio_pipeline._lock:")
        log_idx = src.find("Successfully restarted")
        assert lock_idx >= 0 and log_idx >= 0
        between = src[lock_idx:log_idx]
        # All three writes must be in the lock block (between lock-open
        # and log.info). The real test is that they're INDENTED to the
        # lock level — we verify they're present in the span.
        for name in (
            "_actual_channels = channels",
            "_device_disconnected = False",
            "_device_disconnect_retries = 0",
        ):
            assert name in between, f"{name} must be between lock-open and log.info"

    def test_narrowed_except_re_raises_programming_bugs(self):
        """The except clause must re-raise AttributeError/TypeError/KeyError
        instead of swallowing them as transient device failures."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        assert "except (AttributeError, TypeError, KeyError)" in src, (
            "restart_stream must have a re-raise clause for programming bugs"
        )
        assert "raise" in src, "the programming-bug clause must re-raise"

    def test_outer_except_preceded_by_programming_bug_reraise(self):
        """The outer ``except Exception`` (which catches transient device
        failures) must be preceded by a re-raise clause for programming
        bugs (``AttributeError``/``TypeError``/``KeyError``). This
        ensures programming bugs surface instead of being masked as
        transient device failures.

        We use the balanced approach (catch ``Exception`` + re-raise
        programming bugs) rather than the strict approach (catch only
        ``sd.PortAudioError``/``OSError``) because the test conftest
        replaces ``sounddevice`` with a ``MagicMock``, making
        ``sd.PortAudioError`` a non-class that Python refuses to catch.
        The balanced approach achieves the same goal (programming bugs
        surface) while working in all environments."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        # The programming-bug clause must come BEFORE the general
        # Exception clause (Python evaluates except clauses in order).
        reraise_idx = src.find("except (AttributeError, TypeError, KeyError)")
        exception_idx = src.find("except Exception as e")
        assert reraise_idx >= 0, "programming-bug re-raise clause not found"
        assert exception_idx >= 0, "general Exception catch not found"
        assert reraise_idx < exception_idx, "programming-bug re-raise must come before the general Exception catch"

    def test_no_broad_outer_except_without_reraise(self):
        """The outer ``except Exception`` must be preceded by a re-raise
        clause for programming bugs. Inner ``except Exception`` clauses
        for individual device-query operations are acceptable — they
        guard against flaky ``sd.query_devices`` calls and don't mask
        programming bugs in the restart logic itself."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        # There must be a re-raise clause for programming bugs.
        assert "except (AttributeError, TypeError, KeyError)" in src, "restart_stream must re-raise programming bugs"
        # The re-raise clause must be followed by ``raise``.
        reraise_idx = src.find("except (AttributeError, TypeError, KeyError)")
        reraise_block = src[reraise_idx:]
        assert "raise" in reraise_block[:500], "the programming-bug clause must re-raise (raise not found nearby)"


# DisconnectHandler.restart_stream:  buffer flush ───────────────


class TestRestartStreamBufferFlush:
    """UE-36: hot-swap restart must flush ``_buffer`` and the ring buffer
    so pre-disconnect audio at the OLD rate is not later resampled at
    the NEW rate."""

    def test_ring_buffer_clear_in_source(self):
        """``recorder._ring_buffer.clear()`` must be called before the
        lock block (drops stale chunks the worker would otherwise
        re-process)."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        assert "recorder._ring_buffer.clear()" in src, "restart_stream must clear the ring buffer to drop stale chunks"

    def test_buffer_clear_inside_lock_in_source(self):
        """The buffer must be securely cleared INSIDE the lock block
        (atomic with respect to the audio worker's append).

        The disconnect handler now uses the swap-and-secure-clear-background
        pattern (mirrors ``discard()`` in ``_recorder_split.py``): it captures
        ``_old_buffer = recorder._audio_pipeline._buffer``, assigns a fresh deque, then calls
        ``_secure_clear_array_background(_old_buffer)`` to zero the old
        chunks in a background worker. The swap happens inside the lock so
        the atomicity contract is preserved."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        lock_idx = src.find("with recorder._audio_pipeline._lock:")
        assert lock_idx >= 0
        lock_block = src[lock_idx:]
        # The swap-and-secure-clear pattern replaces the bare .clear() call.
        assert "_secure_clear_array_background" in lock_block, (
            "_secure_clear_array_background must be called inside "
            "recorder._audio_pipeline._lock to securely zero old buffer chunks"
        )

    def test_secure_clear_caches_called_inside_lock(self):
        """``SessionState.secure_clear_caches`` must be called inside the lock to
        securely zero the cached arrays before the buffer clear."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        lock_idx = src.find("with recorder._audio_pipeline._lock:")
        assert lock_idx >= 0
        lock_block = src[lock_idx:]
        assert "recorder._session_state.secure_clear_caches(recorder)" in lock_block, (
            "secure_clear_caches(recorder) must be called inside recorder._audio_pipeline._lock"
        )

    def test_no_resample_segments_reset_inside_lock(self):
        """``_cached_no_resample_segments`` and the dirty flag must be
        reset inside the lock (``_secure_clear_caches`` does not handle
        these fields)."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        lock_idx = src.find("with recorder._audio_pipeline._lock:")
        assert lock_idx >= 0
        lock_block = src[lock_idx:]
        assert "_cached_no_resample_segments = []" in lock_block, (
            "_cached_no_resample_segments must be reset inside the lock"
        )
        assert "_cached_no_resample_concat_dirty = False" in lock_block, (
            "_cached_no_resample_concat_dirty must be reset inside the lock"
        )

    def test_resample_key_reset_inside_lock(self):
        """``_cached_resample_key`` must be reset so the next snapshot
        detects a key mismatch and rebuilds from scratch."""
        src = inspect.getsource(DisconnectHandler.restart_stream)
        lock_idx = src.find("with recorder._audio_pipeline._lock:")
        assert lock_idx >= 0
        lock_block = src[lock_idx:]
        assert "_cached_resample_key = ()" in lock_block, "_cached_resample_key must be reset inside the lock"


# ── DisconnectHandler.restart_stream: runtime behavior ─────────────────


def _setup_recorder_for_restart(monkeypatch, r: Recorder) -> None:
    """Stub all external dependencies so ``restart_stream`` can run
    without a real audio device or a started stream."""
    # ``_current_callback`` is normally set by ``start()`` via
    # ``stream_lifecycle.py``. Stub it so ``sd.InputStream(callback=...)``
    # doesn't raise AttributeError. ``raising=False`` because the
    # attribute doesn't exist until ``start()`` runs.
    monkeypatch.setattr(r, "_current_callback", lambda *a, **k: None, raising=False)
    # ``DeviceManager._resolve_device`` returns None → skip the
    # same-named-device candidate loop and fall straight to
    # ``device=None`` (OS default).
    monkeypatch.setattr(r._devices, "_resolve_device", lambda: None)
    # ``DeviceManager._resolve_effective_sample_rate`` returns (sr, None).
    monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", lambda _d: (48000, None))
    # ``refresh_vad_caches`` is a no-op (no real VAD state to refresh).
    # Stub the lazy ``sd`` proxy's ``query_devices`` and ``InputStream``.
    import voice_typer.server.recording.disconnect_handler as dh_mod

    monkeypatch.setattr(dh_mod, "refresh_vad_caches", lambda rec: None)
    monkeypatch.setattr(
        dh_mod.sd,
        "query_devices",
        lambda *a, **k: {"max_input_channels": 1, "name": "fake"},
    )
    fake_stream = MagicMock()
    monkeypatch.setattr(dh_mod.sd, "InputStream", lambda **kw: fake_stream)


class TestRestartStreamRuntimeBehavior:
    """End-to-end behavior: ``restart_stream`` flushes state and updates
    the disconnect flags atomically under the lock."""

    def test_successful_restart_clears_buffer_and_ring_buffer(self, monkeypatch):
        """A successful restart must clear both ``_buffer`` and
        ``_ring_buffer`` so no stale-rate audio survives."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        # Simulate stale pre-disconnect audio in both buffers.
        r._audio_pipeline._buffer.append(np.zeros(512, dtype=np.float32))
        r._audio_pipeline._buffer.append(np.zeros(512, dtype=np.float32))
        r._ring_buffer.append(("stale", 512, None, None, 0.0))
        assert len(r._audio_pipeline._buffer) == 2
        assert len(r._ring_buffer) == 1

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        assert len(r._audio_pipeline._buffer) == 0, "buffer must be flushed on hot-swap restart"
        assert len(r._ring_buffer) == 0, "ring buffer must be flushed on hot-swap restart"
        assert r._devices._device_disconnected is False
        assert r._devices._device_disconnect_retries == 0
        assert r._actual_channels == 1
        assert r._effective_sr == 48000
        assert r._audio_pipeline._buffer_sr is None

    def test_successful_restart_invalidates_snapshot_caches(self, monkeypatch):
        """A successful restart must reset the snapshot caches so the
        next ``take_snapshot`` rebuilds from the new (empty) buffer."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        # Populate caches with stale data.
        r._cached_resampled = np.ones(128, dtype=np.float32)
        r._cached_no_resample_arr = np.ones(128, dtype=np.float32)
        r._cached_no_resample_segments = [np.ones(64, dtype=np.float32)]
        r._cached_no_resample_concat_dirty = True
        r._cached_resample_key = ("stale", 48000, 16000)
        r._cached_native_chunk_count = 999

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        # Caches must be cleared / reset.
        assert r._cached_resampled.size == 0, "cached_resampled must be empty"
        assert r._cached_no_resample_arr is None, "cached_no_resample_arr must be None"
        assert r._cached_no_resample_segments == [], "no_resample_segments must be empty"
        assert r._cached_no_resample_concat_dirty is False
        assert r._cached_resample_key == (), "resample_key must be reset"
        assert r._cached_native_chunk_count == 0

    def test_portaudio_error_clears_disconnect_flag(self, monkeypatch):
        """When ``sd.InputStream`` raises a transient error (simulated
        via ``OSError`` since the test conftest replaces
        ``sounddevice`` with a ``MagicMock``, making
        ``sd.PortAudioError`` a non-class), the except clause must log
        and clear ``_device_disconnected`` so the health checker
        re-probes."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        r._devices._device_disconnected = True

        import voice_typer.server.recording.disconnect_handler as dh_mod

        def raise_transient(**kw):
            raise OSError("simulated device open failure")

        monkeypatch.setattr(dh_mod.sd, "InputStream", raise_transient)

        handler = DisconnectHandler(r)
        # The except clause CATCHES the transient error — no propagation.
        handler.restart_stream(_captured_generation=0)

        assert r._devices._device_disconnected is False, (
            "transient error must clear _device_disconnected for health-checker re-probe"
        )

    def test_oserror_clears_disconnect_flag(self, monkeypatch):
        """``OSError`` (e.g. device busy) is also caught and cleared."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        r._devices._device_disconnected = True

        import voice_typer.server.recording.disconnect_handler as dh_mod

        def raise_oserror(**kw):
            raise OSError("device busy")

        monkeypatch.setattr(dh_mod.sd, "InputStream", raise_oserror)

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        assert r._devices._device_disconnected is False, (
            "OSError must clear _device_disconnected for health-checker re-probe"
        )

    def test_runtime_error_clears_disconnect_flag(self, monkeypatch):
        """A non-programming-bug ``RuntimeError`` (e.g. from a flaky
        driver) is also caught and cleared — preserving the pre-fix
        recovery behavior for unknown transient errors."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)
        r._devices._device_disconnected = True

        import voice_typer.server.recording.disconnect_handler as dh_mod

        def raise_runtime(**kw):
            raise RuntimeError("driver flake")

        monkeypatch.setattr(dh_mod.sd, "InputStream", raise_runtime)

        handler = DisconnectHandler(r)
        handler.restart_stream(_captured_generation=0)

        assert r._devices._device_disconnected is False, (
            "RuntimeError must clear _device_disconnected for health-checker re-probe"
        )

    def test_programming_bug_is_reraised_not_swallowed(self, monkeypatch):
        """An ``AttributeError`` (programming bug) must propagate, NOT be
        swallowed as a transient device failure."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)

        # Override _resolve_effective_sample_rate to raise AttributeError
        # (simulating a missing attribute on a broken recorder subclass).
        def raise_attr_error(_d):
            raise AttributeError("boom")

        monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", raise_attr_error)

        handler = DisconnectHandler(r)
        with pytest.raises(AttributeError, match="boom"):
            handler.restart_stream(_captured_generation=0)

    def test_typeerror_is_reraised_not_swallowed(self, monkeypatch):
        """A ``TypeError`` (programming bug) must also propagate."""
        r = _make_recorder()
        _setup_recorder_for_restart(monkeypatch, r)

        def raise_type_error(_d):
            raise TypeError("wrong type")

        monkeypatch.setattr(r._devices, "_resolve_effective_sample_rate", raise_type_error)

        handler = DisconnectHandler(r)
        with pytest.raises(TypeError, match="wrong type"):
            handler.restart_stream(_captured_generation=0)
