"""Regression tests for ``Recorder.discard()`` callback drain — Task 17-H-FIX-2.

Background
----------
Task 17-H (reliability review) found that ``Recorder.discard()`` in
``voice_typer/server/recording.py`` closed the PortAudio stream WITHOUT
the 300ms ``_is_in_audio_callback`` poll that ``stop()`` performs. This
risks use-after-free or deadlock when ESC-cancel lands during a busy
audio callback (which fires ~16×/s). ``discard()`` is called from
``RecordingController.cancel()`` every time the user presses ESC.

17-H-FIX-2 extracts the "drain callback + stop + close" sequence into
``Recorder._teardown_stream()`` and updates both ``stop()`` and
``discard()`` to use it. These tests verify:

  1. ``discard()`` waits for the in-flight callback to complete (the
     previous implementation called ``stream.close()`` immediately, even
     with the callback flag set).
  2. ``discard()`` is idempotent — calling it twice (which can happen if
     the user mashes ESC) doesn't crash.
  3. ``discard()`` increments ``_stop_generation`` for symmetry with
     ``stop()``.
"""

import sys
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_recorder():
    """Build a Recorder with a MagicMock stream (no real audio hardware)."""
    # conftest's autouse mock_heavy_imports fixture has already installed
    # a MagicMock for sounddevice in sys.modules, so the Recorder can be
    # imported and constructed headless. Construction is delegated to the
    # shared canonical factory (helper dedup).
    from tests.fixtures.ipc_test_helpers import make_fake_recorder

    r = make_fake_recorder()
    r._recording_event.set()
    r._effective_sr = 16000
    r._stream = MagicMock()
    r._buffer = [np.array([[1.0]], dtype=np.float32)]
    return r


# 17-H-: discard() must drain the in-flight callback ────────────


class TestDiscardWaitsForCallback:
    """17-H-FIX-2: ``discard()`` must drain the in-flight audio callback
    before closing the stream, matching ``stop()``'s AUDIO-009/AUDIO-015
    contract (previously it skipped the poll entirely).

    Round 0 forward-port: the manual ``_is_in_audio_callback`` poll loop
    was removed because PortAudio's ``stream.stop()`` already blocks until
    the in-flight callback returns.  These tests were updated to verify
    ``discard()`` delegates to ``stream.stop()`` (which drains the
    callback) rather than polling ``_is_in_audio_callback`` manually."""

    def test_discard_calls_stream_stop_when_callback_in_flight(self, monkeypatch):
        """When ``_is_in_audio_callback`` is set (callback running),
        ``discard()`` must call ``stream.stop()`` (which blocks until the
        callback returns) before calling ``stream.close()``.  No manual
        poll loop — PortAudio handles the drain."""
        r = _make_recorder()
        r._is_in_audio_callback.set()

        # Capture the order of stream.stop() and stream.close() calls.
        call_order = []
        original_stop = r._stream.stop
        original_close = r._stream.close

        def tracking_stop():
            call_order.append("stop")
            return original_stop()

        def tracking_close():
            call_order.append("close")
            return original_close()

        r._stream.stop = tracking_stop
        r._stream.close = tracking_close

        r.discard()

        # stream.stop() must have been called BEFORE stream.close().
        assert "stop" in call_order, "discard() did not call stream.stop() — needed to drain the callback"
        assert "close" in call_order, "discard() did not call stream.close()"
        assert call_order.index("stop") < call_order.index("close"), f"discard() called close before stop: {call_order}"
        assert r._stream is None

    def test_discard_zero_ms_when_flag_already_clear(self, monkeypatch):
        """Symmetric with ``stop()``: when ``_is_in_audio_callback`` is NOT
        set, ``discard()`` still calls ``stream.stop()`` (idempotent when
        no callback is in flight) and then ``stream.close()``."""
        r = _make_recorder()
        # Flag is already clear (default state)

        stop_called = {"n": 0}
        original_stop = r._stream.stop

        def tracking_stop():
            stop_called["n"] += 1
            return original_stop()

        r._stream.stop = tracking_stop

        r.discard()

        # stream.stop() is still called (it's a no-op when no callback
        # is in flight, but it's part of the teardown contract).
        assert stop_called["n"] >= 1, f"Expected stream.stop() called, got {stop_called['n']}"
        assert r._stream is None

    def test_discard_delegates_to_stream_stop_for_callback(self, monkeypatch):
        """Simulates a callback that's 'in flight' — ``discard()`` must
        call ``stream.stop()`` (which blocks until the callback returns)
        rather than closing immediately.  No manual poll — PortAudio
        handles the drain inside ``stream.stop()``."""
        import voice_typer.server.recording as rec_mod

        r = _make_recorder()
        r._is_in_audio_callback.set()

        # No fake sleep — the new contract doesn't poll.
        sleep_calls = []
        monkeypatch.setattr(rec_mod.time, "sleep", lambda s: sleep_calls.append(s))

        # Track that stream.stop() is called. Model the REAL PortAudio
        # contract: stream.stop() blocks until the in-flight audio
        # callback returns, which clears _is_in_audio_callback on exit.
        # Without this, the mock stop() returns immediately while the flag
        # stays set, so _teardown_stream()'s 300ms safety poll spins (busy
        # loop calling time.sleep millions of times) until the deadline —
        # which is what produced the 1849135 "sleep calls" in the failure.
        # Clearing the flag here represents the drain that stream.stop()
        # performs in reality, so discard() must NOT need to poll.
        stop_called = {"n": 0}
        original_stop = r._stream.stop

        def tracking_stop():
            stop_called["n"] += 1
            r._is_in_audio_callback.clear()  # callback drained → flag clear
            return original_stop()

        r._stream.stop = tracking_stop

        start = time.perf_counter()
        r.discard()
        elapsed = time.perf_counter() - start

        # stream.stop() must have been called.
        assert stop_called["n"] >= 1, "discard() did not call stream.stop() — needed to drain callback"
        # No manual poll — stream.stop() (MagicMock) returns immediately.
        assert len(sleep_calls) == 0, f"Expected 0 sleep calls (no manual poll), got {len(sleep_calls)}"
        # Should complete promptly (stream.stop is mocked).
        assert elapsed < 0.300, f"discard() took {elapsed * 1000:.1f}ms — exceeded 300ms budget"
        assert r._stream is None


# 17-H-: _teardown_stream() must be idempotent ──────────────────


class TestDiscardIdempotent:
    """17-H-FIX-2: ``_teardown_stream()`` must be idempotent —
    ``discard()`` can be called twice (user mashing ESC) without crashing
    on the already-None stream."""

    def test_discard_twice_does_not_crash(self, monkeypatch):
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep",
            lambda s: None,
        )

        # First discard tears down the stream
        r.discard()
        assert r._stream is None

        # Second discard must be a no-op on the stream (already None)
        r.discard()  # must not raise
        assert r._stream is None

    def test_discard_after_stop_does_not_crash(self, monkeypatch):
        """If ``stop()`` runs first and then ``discard()`` is called,
        the discard path must not crash on the already-None stream."""
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep",
            lambda s: None,
        )

        r.stop()  # tears down the stream
        assert r._stream is None

        r.discard()  # must not raise — idempotent _teardown_stream()
        assert r._stream is None

    def test_teardown_stream_idempotent_directly(self):
        """Directly verify the helper handles a None stream."""
        r = _make_recorder()
        r._stream = None

        # Must not raise even though there's no stream.
        r._teardown_stream()
        assert r._stream is None

    def test_teardown_stream_idempotent_with_real_stream(self):
        """Verify that calling _teardown_stream() twice (with a real
        stream the first time) doesn't crash on the second call."""
        r = _make_recorder()
        # First call: stream is a MagicMock — stop()/close() are no-ops
        r._teardown_stream()
        assert r._stream is None
        # Second call: stream is None — must early-return without raising
        r._teardown_stream()
        assert r._stream is None


# 17-H-: discard() parity with stop() ───────────────────────────


class TestDiscardStopGeneration:
    """17-H-FIX-2: ``discard()`` must increment ``_stop_generation`` for
    symmetry with ``stop()``, so any stale disconnect handler launched
    from the audio callback bails out instead of racing with teardown."""

    def test_discard_increments_stop_generation(self, monkeypatch):
        r = _make_recorder()
        monkeypatch.setattr("voice_typer.server.recording.time.sleep", lambda s: None)
        gen_before = r._stop_generation
        r.discard()
        assert r._stop_generation == gen_before + 1, (
            f"discard() must increment _stop_generation (got {r._stop_generation}, expected {gen_before + 1})"
        )

    def test_discard_sets_user_stop_pending_before_stream_stop(self, monkeypatch):
        """STREAM-FIX (Task 6) + 17-H-FIX-2: ``discard()`` must set
        ``_user_stop_pending`` BEFORE ``stream.stop()`` — same contract
        as ``stop()`` — so the audio callback's early-return guard
        suppresses the false 'Stream finished unexpectedly' warning."""
        r = _make_recorder()
        monkeypatch.setattr("voice_typer.server.recording.time.sleep", lambda s: None)

        flag_at_stop = {"value": None}
        original_stop = r._stream.stop

        def capturing_stop():
            flag_at_stop["value"] = r._user_stop_pending
            return original_stop()

        r._stream.stop = capturing_stop

        assert r._user_stop_pending is False  # initial state
        r.discard()

        assert flag_at_stop["value"] is True, (
            "_user_stop_pending must be True when stream.stop() is called from discard()"
        )

    def test_discard_closes_stream_and_clears_buffer(self, monkeypatch):
        """End-to-end: discard() must close the stream, set _stream=None,
        and zero+clear the audio buffer (SEC-audit-008)."""
        r = _make_recorder()
        monkeypatch.setattr("voice_typer.server.recording.time.sleep", lambda s: None)

        close_called = {"n": 0}
        original_close = r._stream.close

        def counting_close():
            close_called["n"] += 1
            return original_close()

        r._stream.close = counting_close

        r.discard()

        assert close_called["n"] == 1, f"Expected stream.close() called once, got {close_called['n']}"
        assert r._stream is None
        assert list(r._buffer) == [], "discard() must clear the audio buffer"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-cov", "-q"]))
