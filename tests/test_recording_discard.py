"""Regression tests for ``Recorder.discard()`` callback drain: Task 17-H-FIX-2."""

import sys
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_recorder():
    """Build a Recorder with a MagicMock stream (no real audio hardware)."""
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    r = make_fake_recorder()
    r._recording_event.set()
    r._effective_sr = 16000
    r._stream_lifecycle._stream = MagicMock()
    r._audio_pipeline._buffer = [np.array([[1.0]], dtype=np.float32)]
    return r


# 17-H-: discard() must drain the in-flight callback ────────────


class TestDiscardWaitsForCallback:
    """17-H-FIX-2: ``discard()`` must drain the in-flight audio callback"""

    def test_discard_calls_stream_stop_when_callback_in_flight(self, monkeypatch):
        """When ``_is_in_audio_callback`` is set (callback running),"""
        r = _make_recorder()
        r._is_in_audio_callback.set()

        # Capture the order of stream.stop() and stream.close() calls.
        call_order = []
        original_stop = r._stream_lifecycle._stream.stop
        original_close = r._stream_lifecycle._stream.close

        def tracking_stop():
            call_order.append("stop")
            return original_stop()

        def tracking_close():
            call_order.append("close")
            return original_close()

        r._stream_lifecycle._stream.stop = tracking_stop
        r._stream_lifecycle._stream.close = tracking_close

        r.discard()

        assert "stop" in call_order, "discard() did not call stream.stop(), needed to drain the callback"
        assert "close" in call_order, "discard() did not call stream.close()"
        assert call_order.index("stop") < call_order.index("close"), f"discard() called close before stop: {call_order}"
        assert r._stream_lifecycle._stream is None

    def test_discard_zero_ms_when_flag_already_clear(self, monkeypatch):
        """Symmetric with ``stop()``: when ``_is_in_audio_callback`` is NOT"""
        r = _make_recorder()
        # Flag is already clear (default state)

        stop_called = {"n": 0}
        original_stop = r._stream_lifecycle._stream.stop

        def tracking_stop():
            stop_called["n"] += 1
            return original_stop()

        r._stream_lifecycle._stream.stop = tracking_stop

        r.discard()

        assert stop_called["n"] >= 1, f"Expected stream.stop() called, got {stop_called['n']}"
        assert r._stream_lifecycle._stream is None

    def test_discard_delegates_to_stream_stop_for_callback(self, monkeypatch):
        """Simulates a callback that's 'in flight': ``discard()`` must"""
        import voice_typer.server.recording as rec_mod

        r = _make_recorder()
        r._is_in_audio_callback.set()

        # No fake sleep, the new contract doesn't poll.
        sleep_calls = []
        monkeypatch.setattr(rec_mod.time, "sleep", lambda s: sleep_calls.append(s))

        # performs in reality, so discard() must NOT need to poll.
        stop_called = {"n": 0}
        original_stop = r._stream_lifecycle._stream.stop

        def tracking_stop():
            stop_called["n"] += 1
            r._is_in_audio_callback.clear()  # callback drained → flag clear
            return original_stop()

        r._stream_lifecycle._stream.stop = tracking_stop

        start = time.perf_counter()
        r.discard()
        elapsed = time.perf_counter() - start

        assert stop_called["n"] >= 1, "discard() did not call stream.stop(), needed to drain callback"
        # No manual poll, stream.stop() (MagicMock) returns immediately.
        assert len(sleep_calls) == 0, f"Expected 0 sleep calls (no manual poll), got {len(sleep_calls)}"
        # Should complete promptly (stream.stop is mocked).
        assert elapsed < 0.300, f"discard() took {elapsed * 1000:.1f}ms, exceeded 300ms budget"
        assert r._stream_lifecycle._stream is None


# 17-H-: _teardown_stream() must be idempotent ──────────────────


class TestDiscardIdempotent:
    """17-H-FIX-2: ``_teardown_stream()`` must be idempotent —"""

    def test_discard_twice_does_not_crash(self, monkeypatch):
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep",
            lambda s: None,
        )

        # First discard tears down the stream
        r.discard()
        assert r._stream_lifecycle._stream is None

        # Second discard must be a no-op on the stream (already None)
        r.discard()  # must not raise
        assert r._stream_lifecycle._stream is None

    def test_discard_after_stop_does_not_crash(self, monkeypatch):
        """If ``stop()`` runs first and then ``discard()`` is called,"""
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep",
            lambda s: None,
        )

        r.stop()  # tears down the stream
        assert r._stream_lifecycle._stream is None

        r.discard()  # must not raise, idempotent _teardown_stream()
        assert r._stream_lifecycle._stream is None

    def test_teardown_stream_idempotent_directly(self):
        """Directly verify the helper handles a None stream."""
        r = _make_recorder()
        r._stream_lifecycle._stream = None

        # Must not raise even though there's no stream.
        r._teardown_stream()
        assert r._stream_lifecycle._stream is None

    def test_teardown_stream_idempotent_with_real_stream(self):
        """Verify that calling _teardown_stream() twice (with a real"""
        r = _make_recorder()
        # First call: stream is a MagicMock, stop()/close() are no-ops
        r._teardown_stream()
        assert r._stream_lifecycle._stream is None
        # Second call: stream is None, must early-return without raising
        r._teardown_stream()
        assert r._stream_lifecycle._stream is None


# 17-H-: discard() parity with stop() ───────────────────────────


class TestDiscardStopGeneration:
    """symmetry with ``stop()``, so any stale disconnect handler launched"""

    def test_discard_increments_stop_generation(self, monkeypatch):
        r = _make_recorder()
        monkeypatch.setattr("voice_typer.server.recording.time.sleep", lambda s: None)
        gen_before = r._stop_generation
        r.discard()
        assert r._stop_generation == gen_before + 1, (
            f"discard() must increment _stop_generation (got {r._stop_generation}, expected {gen_before + 1})"
        )

    def test_discard_sets_user_stop_pending_before_stream_stop(self, monkeypatch):
        """
        STREAM-FIX (Task 6) + 17-H-FIX-2: ``discard()`` must set
        ``_user_stop_pending`` BEFORE ``stream.stop()``, same contract
        """
        r = _make_recorder()
        monkeypatch.setattr("voice_typer.server.recording.time.sleep", lambda s: None)

        flag_at_stop = {"value": None}
        original_stop = r._stream_lifecycle._stream.stop

        def capturing_stop():
            flag_at_stop["value"] = r._user_stop_pending
            return original_stop()

        r._stream_lifecycle._stream.stop = capturing_stop

        assert r._user_stop_pending is False  # initial state
        r.discard()

        assert flag_at_stop["value"] is True, (
            "_user_stop_pending must be True when stream.stop() is called from discard()"
        )

    def test_discard_closes_stream_and_clears_buffer(self, monkeypatch):
        """End-to-end: discard() must close the stream, set _stream=None,"""
        r = _make_recorder()
        monkeypatch.setattr("voice_typer.server.recording.time.sleep", lambda s: None)

        close_called = {"n": 0}
        original_close = r._stream_lifecycle._stream.close

        def counting_close():
            close_called["n"] += 1
            return original_close()

        r._stream_lifecycle._stream.close = counting_close

        r.discard()

        assert close_called["n"] == 1, f"Expected stream.close() called once, got {close_called['n']}"
        assert r._stream_lifecycle._stream is None
        assert list(r._audio_pipeline._buffer) == [], "discard() must clear the audio buffer"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-cov", "-q"]))
