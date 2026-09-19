"""Tests for the waveform bubble coordinator (server/waveform.py)."""

import sys
import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def bubble():
    from voice_typer.server.waveform import WaveformBubble

    return WaveformBubble()


@pytest.fixture(autouse=True)
def _reset_ipc_hook():
    """Make sure the module-level IPC push hook doesn't leak between tests."""
    from voice_typer.server import event_bus

    with event_bus._lock:
        original = set(event_bus._subscribers)
        event_bus._subscribers.clear()
    yield
    with event_bus._lock:
        event_bus._subscribers.clear()
        event_bus._subscribers.update(original)


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
        # (fix): replaced time.sleep(0.05) with a direct
        assert len(received) == 200


class TestWaveformBubbleSetState:
    """Test the ``set_state()`` method and ``on_set_state`` callback."""

    def test_set_state_fires_listener(self, bubble):
        """set_state() must invoke the on_set_state callback with the state."""
        states = []
        bubble.on_set_state = lambda s: states.append(s)
        bubble.set_state("transcribing")
        assert states == ["transcribing"]

    def test_set_state_idempotent(self, bubble):
        """Calling set_state with the same state repeatedly fires callback each time."""
        states = []
        bubble.on_set_state = lambda s: states.append(s)
        bubble.set_state("transcribing")
        bubble.set_state("transcribing")
        bubble.set_state("transcribing")
        assert states == ["transcribing", "transcribing", "transcribing"]

    def test_set_state_recording(self, bubble):
        """set_state('recording') fires the callback with 'recording'."""
        states = []
        bubble.on_set_state = lambda s: states.append(s)
        bubble.set_state("recording")
        assert states == ["recording"]

    def test_set_state_idle(self, bubble):
        """set_state('idle') fires the callback with 'idle'."""
        states = []
        bubble.on_set_state = lambda s: states.append(s)
        bubble.set_state("idle")
        assert states == ["idle"]

    def test_set_state_all_transitions(self, bubble):
        """set_state works for all three recognised states."""
        states = []
        bubble.on_set_state = lambda s: states.append(s)
        bubble.set_state("recording")
        bubble.set_state("transcribing")
        bubble.set_state("idle")
        assert states == ["recording", "transcribing", "idle"]

    def test_set_state_callback_swallows_exceptions(self, bubble):
        """A crashing on_set_state callback must not propagate."""

        def boom(state):
            raise RuntimeError("on_set_state boom")

        bubble.on_set_state = boom
        # Must not raise
        bubble.set_state("transcribing")

    def test_set_state_noop_when_no_callback(self, bubble):
        """set_state() must not crash when on_set_state is None."""
        assert bubble.on_set_state is None
        bubble.set_state("transcribing")  # Must not raise
        bubble.set_state("recording")  # Must not raise
        bubble.set_state("idle")  # Must not raise

    def test_set_state_does_not_affect_visibility(self, bubble):
        """set_state() must not change the bubble's visible property."""
        assert bubble.visible is False
        bubble.set_state("transcribing")
        assert bubble.visible is False  # Still hidden
        bubble.show()
        assert bubble.visible is True
        bubble.set_state("transcribing")
        assert bubble.visible is True  # Still visible

    def test_set_state_does_not_affect_levels(self, bubble):
        """set_state() must not reset RMS/peak/is_speaking."""
        bubble.update_level(0.2, 0.4)
        assert bubble.rms_level > 0
        assert bubble.peak_level > 0
        assert bubble.is_speaking is True
        bubble.set_state("transcribing")
        # Levels should be preserved
        assert bubble.rms_level > 0
        assert bubble.peak_level > 0
        assert bubble.is_speaking is True

    def test_set_state_thread_safety(self, bubble):
        """Concurrent set_state calls must not deadlock."""
        errors = []

        def worker():
            try:
                for _ in range(200):
                    bubble.set_state("transcribing")
                    bubble.set_state("recording")
                    bubble.set_state("idle")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)
        assert errors == []


class TestModuleLevelPushHook:
    """module-level global, not via ``app._ipc_server``."""

    def test_show_pushes_event_when_hook_registered(self, bubble):
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.waveform import WaveformBubble as RealBubble
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        real_bubble = RealBubble()

        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        app._thread_registry = MagicMock()
        app.waveform_wiring = WaveformBubbleWiring(app)
        # Bypass the real __init__ and call only the wire method.
        app.waveform_wiring._wire_waveform_bubble()

        sent: list = []
        from voice_typer.server import event_bus

        event_bus.subscribe(sent.append)

        real_bubble.show()
        assert len(sent) == 1
        assert sent[0] == {"type": "bubble_show"}

    def test_show_drops_event_when_no_hook_registered(self, bubble):
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.waveform import WaveformBubble as RealBubble
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        real_bubble = RealBubble()
        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        app._thread_registry = MagicMock()
        app.waveform_wiring = WaveformBubbleWiring(app)
        app.waveform_wiring._wire_waveform_bubble()

        # No hook registered.  This is the state before
        from voice_typer.server import event_bus

        with event_bus._lock:
            assert len(event_bus._subscribers) == 0
        real_bubble.show()  # must not raise
        real_bubble.hide()  # must not raise

    def test_level_pushes_via_hook(self, bubble):
        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.waveform import WaveformBubble as RealBubble
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        real_bubble = RealBubble()
        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        app._thread_registry = MagicMock()
        app.waveform_wiring = WaveformBubbleWiring(app)
        # Reset the throttle timestamp so the first update_level call
        app.waveform_wiring._last_bubble_level_push_ts = 0.0
        app.waveform_wiring._wire_waveform_bubble()

        sent: list = []
        from voice_typer.server import event_bus

        event_bus.subscribe(sent.append)

        # Force the throttle to allow the first push.
        app.waveform_wiring._last_bubble_level_push_ts = 0.0
        real_bubble.update_level(0.05, 0.12)
        # PERF-: pushes are now async (drained by a background
        import time as _time

        deadline = _time.monotonic() + 2.0
        while _time.monotonic() < deadline:
            if any(m.get("type") == "bubble_level" for m in sent):
                break
            _time.sleep(0.02)
        # Find the bubble_level event in the sent stream
        levels = [m for m in sent if m.get("type") == "bubble_level"]
        assert len(levels) >= 1, f"expected >= 1 bubble_level event, got {len(levels)}; sent={sent}"
        last = levels[-1]["data"]
        assert "rms" in last and "peak" in last
        assert 0.0 < last["rms"] <= 1.0

        app.waveform_wiring.stop()

    def test_push_event_now_returns_false_when_no_hook(self):
        from voice_typer.server import ipc_server

        assert ipc_server._push_event_now({"type": "test"}) is False

    def test_push_event_now_returns_true_when_hook_set(self):
        from voice_typer.server import event_bus, ipc_server

        sent: list = []
        event_bus.subscribe(sent.append)
        try:
            assert ipc_server._push_event_now({"type": "x", "data": 1}) is True
            assert sent == [{"type": "x", "data": 1}]
        finally:
            event_bus.unsubscribe(sent.append)

    def test_push_event_now_swallows_hook_exceptions(self):
        from voice_typer.server import event_bus, ipc_server

        def bad_hook(_msg):
            raise RuntimeError("boom")

        event_bus.subscribe(bad_hook)
        try:
            # Must not raise
            assert ipc_server._push_event_now({"type": "x"}) is False
        finally:
            event_bus.unsubscribe(bad_hook)


class TestAppMainWiresIpcHook:
    def test_app_main_sets_ipc_push_hook(self, monkeypatch):
        from voice_typer.server import app as app_module, event_bus, ipc_server

        with event_bus._lock:
            event_bus._subscribers.clear()

        # Stub the heavy bits: setup_logging, single-instance, app
        calls = {"ipc_started": 0, "app_started": 0}

        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda **kw: object(),
        )

        class FakeApp:
            def __init__(self):
                self._waveform_bubble = None

            def start(self):
                calls["app_started"] += 1
                # Don't actually block on the tray event loop

        monkeypatch.setattr(app_module, "VoiceTyperApp", FakeApp)

        class FakeServer:
            # ``event_bus._SubscriberSet`` stores bound-method
            instances: list = []

            def __init__(self, app):
                self.app = app
                FakeServer.instances.append(self)

            def start(self):
                calls["ipc_started"] += 1
                # Simulate the real IPCServer.start setting the hook
                from voice_typer.server import event_bus

                event_bus.subscribe(self.push)

            def start_tcp(self, port=None):
                # P1-: standalone mode calls start_tcp(port);
                pass

            def push(self, msg):
                pass

        # Patch the IPCServer class.  app.main() imports it
        monkeypatch.setattr(ipc_server, "IPCServer", FakeServer)
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", FakeApp)
        monkeypatch.setattr("voice_typer.server.sidecar_ws.run", lambda server: 0)
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        import threading as _threading

        class _ImmediateThread:
            def __init__(self, target=None, args=(), kwargs=None, **_kw):
                self._target = target
                self._args = args or ()
                self._kwargs = kwargs or {}

            def start(self):
                if self._target is not None:
                    self._target(*self._args, **self._kwargs)

        monkeypatch.setattr(_threading, "Thread", _ImmediateThread)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
            lambda **kw: object(),
        )

        try:
            with pytest.raises(SystemExit) as exc_info:
                ipc_server.main()
            assert exc_info.value.code in (0, None)
            assert calls["ipc_started"] == 1, "IPCServer.start was not called by ipc_server.main()"
            assert calls["app_started"] == 1, "VoiceTyperApp.start was not called"
            with event_bus._lock:
                assert len(event_bus._subscribers) > 0, "ipc_server.main() did not register the IPC push hook"
        finally:
            with event_bus._lock:
                event_bus._subscribers.clear()
