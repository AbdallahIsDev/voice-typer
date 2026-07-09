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
import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


def _make_recorder():
    """Build a Recorder with a MagicMock stream (no real audio hardware)."""
    # conftest's autouse mock_heavy_imports fixture has already installed
    # a MagicMock for sounddevice in sys.modules, so the Recorder can be
    # imported and constructed headless.
    from voice_typer.server.recording import Recorder

    config = MagicMock(sample_rate=16000, microphone=None)
    r = Recorder(config)
    r._recording_event.set()
    r._effective_sr = 16000
    r._stream = MagicMock()
    r._buffer = [np.array([[1.0]], dtype=np.float32)]
    return r


# ── 17-H-FIX-2: discard() must drain the in-flight callback ────────────


class TestDiscardWaitsForCallback:
    """17-H-FIX-2: ``discard()`` must drain the in-flight audio callback
    before closing the stream, matching ``stop()``'s AUDIO-009/AUDIO-015
    contract (previously it skipped the poll entirely)."""

    def test_discard_polls_when_callback_in_flight(self, monkeypatch):
        """When ``_is_in_audio_callback`` is set (callback running),
        ``discard()`` must poll until it clears — not call
        ``stream.close()`` immediately."""
        r = _make_recorder()
        r._is_in_audio_callback.set()

        # Simulate the callback completing after a few polls.
        poll_count = {"n": 0}

        def fake_sleep(s):
            poll_count["n"] += 1
            # After 3 polls, simulate the callback returning
            if poll_count["n"] >= 3:
                r._is_in_audio_callback.clear()

        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep", fake_sleep
        )

        # Capture the callback-flag state at the moment stream.close() runs.
        flag_at_close = {"value": None}
        original_close = r._stream.close

        def capturing_close():
            flag_at_close["value"] = r._is_in_audio_callback.is_set()
            return original_close()

        r._stream.close = capturing_close

        r.discard()

        # The poll loop must have run at least 3 times before close().
        assert poll_count["n"] >= 3, (
            f"Expected >= 3 polls before stream.close(), got {poll_count['n']}"
        )
        # stream.close() must have been called AFTER the flag cleared.
        assert flag_at_close["value"] is False, (
            "discard() called stream.close() while the callback was still "
            "in flight — _teardown_stream() should poll first"
        )
        assert r._stream is None

    def test_discard_zero_ms_when_flag_already_clear(self, monkeypatch):
        """Symmetric with ``stop()``: when ``_is_in_audio_callback`` is NOT
        set, ``discard()``'s poll loop exits on the first check — 0ms wait."""
        r = _make_recorder()
        # Flag is already clear (default state)

        sleep_calls = []
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep",
            lambda s: sleep_calls.append(s),
        )

        r.discard()

        assert len(sleep_calls) == 0, (
            f"Expected 0 sleep calls (flag clear), got {len(sleep_calls)}"
        )
        assert r._stream is None

    def test_discard_waits_50ms_for_callback(self, monkeypatch):
        """Simulates a callback that stays 'in flight' for ~50ms —
        ``discard()`` must wait for it (matching ``stop()``'s contract)
        instead of closing immediately, and must not blow past the 300ms
        hard deadline."""
        import voice_typer.server.recording as rec_mod

        r = _make_recorder()
        r._is_in_audio_callback.set()

        # Use the real time.sleep so we get real wall-clock timing. A
        # background thread clears the flag after ~50ms (simulating the
        # callback returning).
        original_sleep = time.sleep
        sleep_calls = []

        def tracked_sleep(s):
            sleep_calls.append(s)
            original_sleep(s)

        monkeypatch.setattr(rec_mod.time, "sleep", tracked_sleep)

        def release_after_50ms():
            original_sleep(0.050)
            r._is_in_audio_callback.clear()

        t = threading.Thread(target=release_after_50ms, daemon=True)
        start = time.perf_counter()
        t.start()

        r.discard()
        elapsed = time.perf_counter() - start

        # Must have waited at least ~50ms for the callback to clear.
        # (45ms lower bound gives slack for scheduler jitter.)
        assert elapsed >= 0.045, (
            f"discard() returned in {elapsed*1000:.1f}ms — should have "
            f"waited ~50ms for the callback to clear"
        )
        # Must not have blown past the 300ms hard deadline.
        assert elapsed < 0.300, (
            f"discard() took {elapsed*1000:.1f}ms — exceeded 300ms budget"
        )
        # Must have polled at least once.
        assert len(sleep_calls) >= 1, (
            "Expected at least one poll while waiting for the callback"
        )
        assert r._stream is None


# ── 17-H-FIX-2: _teardown_stream() must be idempotent ──────────────────


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

        r.stop()       # tears down the stream
        assert r._stream is None

        r.discard()    # must not raise — idempotent _teardown_stream()
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


# ── 17-H-FIX-2: discard() parity with stop() ───────────────────────────


class TestDiscardStopGeneration:
    """17-H-FIX-2: ``discard()`` must increment ``_stop_generation`` for
    symmetry with ``stop()``, so any stale disconnect handler launched
    from the audio callback bails out instead of racing with teardown."""

    def test_discard_increments_stop_generation(self, monkeypatch):
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep", lambda s: None
        )
        gen_before = r._stop_generation
        r.discard()
        assert r._stop_generation == gen_before + 1, (
            f"discard() must increment _stop_generation (got "
            f"{r._stop_generation}, expected {gen_before + 1})"
        )

    def test_discard_sets_user_stop_pending_before_stream_stop(
        self, monkeypatch
    ):
        """STREAM-FIX (Task 6) + 17-H-FIX-2: ``discard()`` must set
        ``_user_stop_pending`` BEFORE ``stream.stop()`` — same contract
        as ``stop()`` — so the audio callback's early-return guard
        suppresses the false 'Stream finished unexpectedly' warning."""
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep", lambda s: None
        )

        flag_at_stop = {"value": None}
        original_stop = r._stream.stop

        def capturing_stop():
            flag_at_stop["value"] = r._user_stop_pending
            return original_stop()

        r._stream.stop = capturing_stop

        assert r._user_stop_pending is False  # initial state
        r.discard()

        assert flag_at_stop["value"] is True, (
            "_user_stop_pending must be True when stream.stop() is called "
            "from discard()"
        )

    def test_discard_closes_stream_and_clears_buffer(self, monkeypatch):
        """End-to-end: discard() must close the stream, set _stream=None,
        and zero+clear the audio buffer (SEC-audit-008)."""
        r = _make_recorder()
        monkeypatch.setattr(
            "voice_typer.server.recording.time.sleep", lambda s: None
        )

        close_called = {"n": 0}
        original_close = r._stream.close

        def counting_close():
            close_called["n"] += 1
            return original_close()

        r._stream.close = counting_close

        r.discard()

        assert close_called["n"] == 1, (
            f"Expected stream.close() called once, got {close_called['n']}"
        )
        assert r._stream is None
        assert r._buffer == [], "discard() must clear the audio buffer"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "--no-cov", "-q"]))
