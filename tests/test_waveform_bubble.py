"""Tests for the waveform bubble coordinator (server/waveform.py)."""

import threading
import time
from unittest.mock import MagicMock

import numpy as np
import pytest


@pytest.fixture
def bubble():
    from voice_typer.server.waveform import WaveformBubble
    return WaveformBubble()


@pytest.fixture(autouse=True)
def _reset_ipc_hook():
    """Make sure the module-level IPC push hook doesn't leak between tests."""
    from voice_typer.server import ipc_server
    ipc_server._push_event = None
    yield
    ipc_server._push_event = None


class TestWaveformBubbleState:
    def test_starts_hidden(self, bubble):
        assert bubble.visible is False
        assert bubble.is_speaking is False
        assert bubble.rms_level == 0.0
        assert bubble.peak_level == 0.0

    def test_show_marks_visible_and_fires_listener(self, bubble):
        calls = []
        bubble.on_show = lambda: calls.append("show")
        bubble.show()
        assert bubble.visible is True
        assert calls == ["show"]

    def test_show_idempotent(self, bubble):
        calls = []
        bubble.on_show = lambda: calls.append("show")
        bubble.show()
        bubble.show()
        assert calls == ["show"]

    def test_hide_clears_state_and_fires_listener(self, bubble):
        calls = []
        bubble.on_hide = lambda: calls.append("hide")
        bubble.show()
        bubble.update_level(0.1, 0.3)
        bubble.hide()
        assert bubble.visible is False
        assert bubble.rms_level == 0.0
        assert bubble.peak_level == 0.0
        assert calls == ["hide"]

    def test_hide_idempotent(self, bubble):
        calls = []
        bubble.on_hide = lambda: calls.append("hide")
        bubble.hide()
        bubble.hide()
        assert calls == []


class TestWaveformBubbleLevel:
    def test_level_smoothing(self, bubble):
        # Drive a single high sample; smoothed value should be below the
        # input but non-zero (low-pass averaging).
        bubble.update_level(1.0, 1.0)
        assert 0.0 < bubble.rms_level < 1.0
        # Subsequent equal samples should asymptote near the input.
        for _ in range(50):
            bubble.update_level(1.0, 1.0)
        assert bubble.rms_level > 0.95

    def test_silence_means_not_speaking(self, bubble):
        bubble.update_level(0.0, 0.0)
        assert bubble.is_speaking is False

    def test_loud_means_speaking(self, bubble):
        bubble.update_level(0.05, 0.1)
        for _ in range(10):
            bubble.update_level(0.05, 0.1)
        assert bubble.is_speaking is True

    def test_peak_clamps_to_max(self, bubble):
        bubble.update_level(0.05, 0.5)
        # Peak decays each sample; pushing a higher peak should bump it
        bubble.update_level(0.05, 0.9)
        assert bubble.peak_level >= 0.5

    def test_callback_swallows_exceptions(self, bubble):
        def boom(_rms, _peak):
            raise RuntimeError("nope")
        bubble.on_level = boom
        # Should not raise
        bubble.update_level(0.1, 0.1)


class TestWaveformBubbleThreadSafety:
    def test_concurrent_show_hide_does_not_deadlock(self, bubble):
        errors = []

        def worker():
            try:
                for _ in range(500):
                    bubble.show()
                    bubble.hide()
                    bubble.update_level(0.05, 0.1)
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []
        # Final state must be a valid boolean (visible or not)
        assert bubble.visible in (True, False)

    def test_listener_receives_levels_under_load(self, bubble):
        received = []
        lock = threading.Lock()

        def cb(rms, peak):
            with lock:
                received.append((rms, peak))

        bubble.on_level = cb
        for _ in range(200):
            bubble.update_level(0.05, 0.1)
        # Allow the call-queue to drain (synchronous, but defensive)
        time.sleep(0.05)
        assert len(received) == 200


# ── Module-level IPC push hook (regression: was app._ipc_server) ─────


class TestModuleLevelPushHook:
    """The bubble listeners must reach the IPC server through a
    module-level global, not via ``app._ipc_server``.

    The original implementation used a closure that did
    ``getattr(self, "_ipc_server", None)``.  In production the
    closure's ``self`` and the app that had ``_ipc_server`` set
    turned out to be the same instance — but the lookup still
    returned ``None`` for reasons we could not pin down from the
    logs (suspected: stale app instance, or the hook firing before
    ``IPCServer.start()`` finished).  The module-level hook is
    bypass-proof and is what production now uses.
    """

    def test_show_pushes_event_when_hook_registered(self, bubble):
        from voice_typer.server import ipc_server
        from voice_typer.server.app import VoiceTyperApp

        # Simulate an app and call _wire_waveform_bubble.  We use a
        # real VoiceTyperApp-shaped stand-in: just the methods we
        # need.  The wire function is a method, so we can't easily
        # call it on a MagicMock; instead exercise the actual code
        # path by creating a real VoiceTyperApp and stubbing the
        # heavy bits.
        from voice_typer.server.waveform import WaveformBubble as RealBubble

        real_bubble = RealBubble()

        # Build a minimal app with the same __init__ that
        # _wire_waveform_bubble needs: just the bubble attribute.
        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        # Bypass the real __init__ and call only the wire method.
        VoiceTyperApp._wire_waveform_bubble(app)

        # Register a fake push function at the module level — the
        # exact same hook that IPCServer.start() sets in production.
        sent: list = []
        ipc_server._set_push_event(sent.append)

        real_bubble.show()
        assert len(sent) == 1
        assert sent[0] == {"type": "bubble_show"}

    def test_show_drops_event_when_no_hook_registered(self, bubble):
        from voice_typer.server import ipc_server
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.waveform import WaveformBubble as RealBubble

        real_bubble = RealBubble()
        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        VoiceTyperApp._wire_waveform_bubble(app)

        # No hook registered.  This is the state before
        # IPCServer.start() has run, or after it stopped.
        assert ipc_server._push_event is None
        real_bubble.show()  # must not raise
        real_bubble.hide()  # must not raise

    def test_level_pushes_via_hook(self, bubble):
        from voice_typer.server import ipc_server
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.waveform import WaveformBubble as RealBubble
        import queue as _queue
        import threading as _threading

        real_bubble = RealBubble()
        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        # PERF-NEW-001: _wire_waveform_bubble now sets up a background
        # queue + worker thread.  We need to provide the queue, the
        # threading event, AND a None worker (so the wiring code's
        # "is the worker already alive?" check doesn't get fooled by
        # MagicMock's auto-attribute behavior).
        app._bubble_level_queue = _queue.Queue(maxsize=64)
        app._bubble_level_worker_stop = _threading.Event()
        app._bubble_level_worker = None
        # Reset the throttle timestamp so the first update_level call
        # isn't dropped by the 33ms throttle (other tests in the suite
        # may have set it recently).
        app._last_bubble_level_push_ts = 0.0
        VoiceTyperApp._wire_waveform_bubble(app)

        sent: list = []
        ipc_server._set_push_event(sent.append)

        # Force the throttle to allow the first push.
        app._last_bubble_level_push_ts = 0.0
        real_bubble.update_level(0.05, 0.12)
        # PERF-NEW-001: pushes are now async (drained by a background
        # worker thread).  Wait briefly for the worker to drain.
        # The queue has maxsize=64 so a single item drains in well
        # under 100 ms.
        import time as _time
        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline:
            if any(m.get("type") == "bubble_level" for m in sent):
                break
            _time.sleep(0.02)
        # Find the bubble_level event in the sent stream
        levels = [m for m in sent if m.get("type") == "bubble_level"]
        assert len(levels) >= 1, (
            f"expected >= 1 bubble_level event, got {len(levels)}; "
            f"sent={sent}"
        )
        last = levels[-1]["data"]
        assert "rms" in last and "peak" in last
        assert 0.0 < last["rms"] <= 1.0

        # Cleanup: stop the worker thread
        app._bubble_level_worker_stop.set()
        try:
            app._bubble_level_queue.put_nowait(None)
        except _queue.Full:
            pass

    def test_push_event_now_returns_false_when_no_hook(self):
        from voice_typer.server import ipc_server
        assert ipc_server._push_event_now({"type": "test"}) is False

    def test_push_event_now_returns_true_when_hook_set(self):
        from voice_typer.server import ipc_server
        sent: list = []
        ipc_server._set_push_event(sent.append)
        try:
            assert ipc_server._push_event_now({"type": "x", "data": 1}) is True
            assert sent == [{"type": "x", "data": 1}]
        finally:
            ipc_server._set_push_event(None)

    def test_push_event_now_swallows_hook_exceptions(self):
        from voice_typer.server import ipc_server

        def bad_hook(_msg):
            raise RuntimeError("boom")
        ipc_server._set_push_event(bad_hook)
        try:
            # Must not raise
            assert ipc_server._push_event_now({"type": "x"}) is False
        finally:
            ipc_server._set_push_event(None)


# ── app.main() must also wire the IPC server hook ────────────────────
#
# Regression: voice_typer.server.app:main is the entry point for the
# ``voice-typer`` console script (per pyproject.toml).  It used to
# create VoiceTyperApp + start the tray, but never start the
# IPCServer, so the bubble push hook stayed None.  Users running the
# console script (or any path that landed on app.main) got a working
# app but no IPC bridge.  This test pins the corrected behavior: if
# app.main reaches the IPC server start, the module-level hook must
# be set.


class TestAppMainWiresIpcHook:
    def test_app_main_sets_ipc_push_hook(self, monkeypatch):
        from voice_typer.server import ipc_server
        from voice_typer.server import app as app_module

        # Reset hook
        ipc_server._set_push_event(None)

        # Stub the heavy bits: setup_logging, single-instance, app
        # construction, app.start.  We only care that app.main
        # instantiates IPCServer and calls start() on it.
        calls = {"ipc_started": 0, "app_started": 0}

        monkeypatch.setattr(app_module, "_setup_logging", lambda: None)
        monkeypatch.setattr(
            app_module, "_ensure_single_instance", lambda **kw: object(),
        )

        class FakeApp:
            def __init__(self):
                self._waveform_bubble = None

            def start(self):
                calls["app_started"] += 1
                # Don't actually block on the tray event loop

        monkeypatch.setattr(app_module, "VoiceTyperApp", FakeApp)

        # TEST-007: previously this FakeServer class was defined twice
        # in the same test function — the second definition silently
        # shadowed the first. Deleted the duplicate; kept the second
        # (it's the one that was actually used by the monkeypatch
        # below, so behavior is unchanged).
        class FakeServer:
            def __init__(self, app):
                self.app = app

            def start(self):
                calls["ipc_started"] += 1
                # Simulate the real IPCServer.start setting the hook
                ipc_server._set_push_event(self.push)

            def push(self, msg):
                pass

        # Patch the IPCServer class.  app.main() imports it
        # inside the function body via
        # ``from voice_typer.server.ipc_server import IPCServer``, so
        # patching the symbol on ipc_server is what gets picked up.
        monkeypatch.setattr(ipc_server, "IPCServer", FakeServer)

        # BUILD-002: the console script entry point was moved from
        # app.main() to ipc_server.main().  This test was updated to
        # call ipc_server.main() instead of app.main().
        # We need to stub the same things ipc_server.main() calls:
        # _setup_logging, _ensure_single_instance (from app), VoiceTyperApp.
        monkeypatch.setattr(app_module, "_setup_logging", lambda: None)
        monkeypatch.setattr(
            app_module, "_ensure_single_instance", lambda **kw: object(),
        )

        try:
            ipc_server.main()
            assert calls["ipc_started"] == 1, "IPCServer.start was not called by ipc_server.main()"
            assert calls["app_started"] == 1, "VoiceTyperApp.start was not called"
            # Module-level hook must be set (the whole point of the fix)
            assert ipc_server._push_event is not None, (
                "ipc_server.main() did not register the IPC push hook"
            )
        finally:
            ipc_server._set_push_event(None)


# ── T021: Silero VAD integration tests ──────────────────────────────


class TestVADModule:
    """Test the VAD wrapper module (voice_typer.server.vad)."""

    def test_is_available_returns_bool(self):
        """is_available() should return True or False, not raise."""
        from voice_typer.server.vad import is_available
        result = is_available()
        assert isinstance(result, bool)

    def test_compute_vad_prob_without_torch(self, monkeypatch):
        """When torch is not available, compute_vad_prob returns None."""
        from voice_typer.server import vad
        monkeypatch.setitem(__import__("sys").modules, "torch", None)
        vad.reset()
        result = vad.compute_vad_prob(np.zeros(16000, dtype=np.float32))
        assert result is None

    def test_is_speech_fallback_rms(self, monkeypatch):
        """Without VAD, is_speech falls back to RMS energy check."""
        from voice_typer.server import vad
        vad.reset()
        # Silence
        assert vad.is_speech(np.zeros(16000, dtype=np.float32)) is False
        # Loud audio
        assert vad.is_speech(np.full(16000, 0.1, dtype=np.float32)) is True

    def test_is_speech_empty_audio(self):
        """Empty audio chunk should return False."""
        from voice_typer.server.vad import is_speech
        assert is_speech(np.array([], dtype=np.float32)) is False

    def test_reset_clears_model(self):
        """reset() should clear the cached model."""
        from voice_typer.server import vad
        vad.reset()
        assert vad._model is None
        assert vad._utils is None


class TestWaveformVADGate:
    """Test that WaveformBubble.update_level gates on VAD."""

    def test_update_level_without_audio_chunk(self, bubble):
        """When no audio_chunk is passed, update_level works as before (RMS-only)."""
        bubble.update_level(0.1, 0.2)
        assert abs(bubble.rms_level - 0.045) < 0.01  # smoothed
        assert bubble.is_speaking is True  # 0.045 > 0.01 threshold

    def test_update_level_with_silent_audio_chunk(self, bubble, monkeypatch):
        """With a silent audio chunk, VAD gates the visualizer (decays)."""
        from voice_typer.server import vad
        # Force VAD to report non-speech
        monkeypatch.setattr(vad, "is_speech", lambda chunk, sr=16000: False)
        bubble.update_level(0.15, 0.3, audio_chunk=np.zeros(16000, dtype=np.float32))
        # Level should decay, not increase
        assert bubble.rms_level < 0.15
        assert bubble.is_speaking is False

    def test_update_level_with_speech_audio_chunk(self, bubble, monkeypatch):
        """With speech audio chunk, VAD allows the normal update path."""
        from voice_typer.server import vad
        # Force VAD to report speech
        monkeypatch.setattr(vad, "is_speech", lambda chunk, sr=16000: True)
        bubble.update_level(0.15, 0.3, audio_chunk=np.full(16000, 0.1, dtype=np.float32))
        assert bubble.rms_level > 0
        assert bubble.is_speaking is True


class TestT021ProductionWiring:
    """T021: verify the audio_chunk path is wired end-to-end.

    The VAD gate existed in waveform.py but was inert in production
    because app._on_recorder_rms(rms, peak) didn't pass audio_chunk
    to WaveformBubble.update_level. These tests verify the wiring is
    now in place.
    """

    def test_app_on_recorder_rms_accepts_audio_chunk(self, monkeypatch):
        """app._on_recorder_rms signature must accept audio_chunk kwarg."""
        import inspect
        from voice_typer.server.app import VoiceTyperApp
        sig = inspect.signature(VoiceTyperApp._on_recorder_rms)
        assert "audio_chunk" in sig.parameters, (
            "_on_recorder_rms must accept audio_chunk kwarg to forward "
            "audio to WaveformBubble.update_level for VAD gating"
        )

    def test_recorder_callback_passes_three_args(self):
        """Recorder.on_rms_level callback receives 3 args: rms, peak, audio_chunk.

        Reads the source of the recording module to confirm the callback
        is invoked with 3 positional arguments (not 2). The callback
        is a nested function inside Recorder.start(), so we read the
        whole module source as a static check.
        """
        import inspect
        from voice_typer.server import recording
        src = inspect.getsource(recording)
        assert "rms_callback(chunk_rms, chunk_peak, filtered)" in src, (
            "Recorder's audio callback must pass the filtered audio chunk "
            "as the 3rd argument to rms_callback so VAD can run on it"
        )

    def test_update_level_signature_accepts_audio_chunk(self):
        """WaveformBubble.update_level must accept audio_chunk kwarg."""
        import inspect
        from voice_typer.server.waveform import WaveformBubble
        sig = inspect.signature(WaveformBubble.update_level)
        assert "audio_chunk" in sig.parameters, (
            "WaveformBubble.update_level must accept audio_chunk kwarg "
            "to run VAD on the incoming audio"
        )
