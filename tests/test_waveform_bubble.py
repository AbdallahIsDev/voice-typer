"""Tests for the waveform bubble coordinator (server/waveform.py)."""

import threading
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def bubble():
    from voice_typer.server.waveform import WaveformBubble

    return WaveformBubble()


@pytest.fixture(autouse=True)
def _reset_ipc_hook():
    """Make sure the module-level IPC push hook doesn't leak between tests.

    the global ``_push_event`` was replaced by the
    in-process ``event_bus`` (``_subscribers`` set + ``_lock`` RLock).
    Each test that wants a clean slate must clear the registry; we
    snapshot/restore it here so other tests aren't affected.
    """
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
        # (fix): replaced time.sleep(0.05) with a direct
        # assertion. The call-queue is synchronous (update_level calls
        # the callback inline), so no waiting is needed — all 200
        # callbacks have already fired by the time we reach the assert.
        assert len(received) == 200


# ── WaveformBubble.set_state (NEW-BUBBLE-TRANSCRIBING) ─────────────────


class TestWaveformBubbleSetState:
    """Test the ``set_state()`` method and ``on_set_state`` callback.

    NEW-BUBBLE-TRANSCRIBING: ``set_state()`` is called from
    ``RecordingController.stop()`` to switch the bubble from recording
    visualizer to "Transcribing…" text, and from ``DictationPipeline``
    when transcription completes (back to idle or hidden).
    """

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
        from voice_typer.server.app import VoiceTyperApp

        # Simulate an app and call _wire_waveform_bubble.  We use a
        # real VoiceTyperApp-shaped stand-in: just the methods we
        # need.  The wire function is a method, so we can't easily
        # call it on a MagicMock; instead exercise the actual code
        # path by creating a real WaveformBubbleWiring instance.
        from voice_typer.server.waveform import WaveformBubble as RealBubble
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        real_bubble = RealBubble()

        # Build a minimal app with the same __init__ that
        # _wire_waveform_bubble needs: just the bubble attribute.
        app = MagicMock(spec=VoiceTyperApp)
        app._waveform_bubble = real_bubble
        # THREAD-REGISTRY: _wire_waveform_bubble registers the
        # bubble-level worker on self._thread_registry, which is an
        # instance attribute set in __init__ (not part of the class
        # spec), so Mock(spec=...) doesn't expose it.  Provide one.
        app._thread_registry = MagicMock()
        # Phase 7: the wiring code now lives on WaveformBubbleWiring.
        # Install a real WaveformBubbleWiring so the delegate reaches real code.
        app.waveform_wiring = WaveformBubbleWiring(app)
        # Bypass the real __init__ and call only the wire method.
        app.waveform_wiring._wire_waveform_bubble()

        # Register a fake push function at the module level — the
        # exact same hook that IPCServer.start() sets in production.
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
        # IPCServer.start() has run, or after it stopped.
        # the registry is a set, not a single Optional.
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
        # Phase 7: install a real WaveformBubbleWiring. Its
        # __init__ initializes the worker state to None, so the wiring
        # code's "is the worker already alive?" check doesn't get
        # fooled by MagicMock's auto-attribute behavior.
        app.waveform_wiring = WaveformBubbleWiring(app)
        # Reset the throttle timestamp so the first update_level call
        # isn't dropped by the 16ms throttle (BUBBLE- changed it
        # from 33ms to 16ms = ~60Hz; other tests in the suite may have
        # set it recently).
        app.waveform_wiring._last_bubble_level_push_ts = 0.0
        app.waveform_wiring._wire_waveform_bubble()

        sent: list = []
        from voice_typer.server import event_bus

        event_bus.subscribe(sent.append)

        # Force the throttle to allow the first push.
        app.waveform_wiring._last_bubble_level_push_ts = 0.0
        real_bubble.update_level(0.05, 0.12)
        # PERF-: pushes are now async (drained by a background
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
        assert len(levels) >= 1, f"expected >= 1 bubble_level event, got {len(levels)}; sent={sent}"
        last = levels[-1]["data"]
        assert "rms" in last and "peak" in last
        assert 0.0 < last["rms"] <= 1.0

        # Cleanup: stop the worker thread ( Phase 7: via the
        # WaveformBubbleWiring.stop() helper).
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
            # unregister via the registry helper.
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
        from voice_typer.server import app as app_module
        from voice_typer.server import event_bus, ipc_server

        # Reset hook
        # clear the registry instead of setting a single None.
        with event_bus._lock:
            event_bus._subscribers.clear()

        # Stub the heavy bits: setup_logging, single-instance, app
        # construction, app.start.  We only care that app.main
        # instantiates IPCServer and calls start() on it.
        calls = {"ipc_started": 0, "app_started": 0}

        monkeypatch.setattr(app_module, "_setup_logging", lambda: None)
        monkeypatch.setattr(
            app_module,
            "_ensure_single_instance",
            lambda **kw: object(),
        )

        class FakeApp:
            def __init__(self):
                self._waveform_bubble = None

            def start(self):
                calls["app_started"] += 1
                # Don't actually block on the tray event loop

        monkeypatch.setattr(app_module, "VoiceTyperApp", FakeApp)

        # previously this FakeServer class was defined twice
        # in the same test function — the second definition silently
        # shadowed the first. Deleted the duplicate; kept the second
        # (it's the one that was actually used by the monkeypatch
        # below, so behavior is unchanged).
        class FakeServer:
            # ``event_bus._SubscriberSet`` stores bound-method
            # subscribers via WEAK references (a leak fix) — if the
            # server instance is garbage-collected after ``main()``
            # returns (nothing else holds a ref to the local ``server``
            # inside ``main``), its subscription is evicted before the
            # assertion below runs. Keep every constructed instance
            # alive on the class so the weak ref survives.
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
                # stub it so the test doesn't AttributeError.
                pass

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
            app_module,
            "_ensure_single_instance",
            lambda **kw: object(),
        )

        try:
            ipc_server.main()
            assert calls["ipc_started"] == 1, "IPCServer.start was not called by ipc_server.main()"
            assert calls["app_started"] == 1, "VoiceTyperApp.start was not called"
            # Module-level hook must be set (the whole point of the fix).
            # the registry is a set — non-empty means at
            # least one server registered its push callable.
            with event_bus._lock:
                assert len(event_bus._subscribers) > 0, "ipc_server.main() did not register the IPC push hook"
        finally:
            # clear the registry on teardown.
            with event_bus._lock:
                event_bus._subscribers.clear()
