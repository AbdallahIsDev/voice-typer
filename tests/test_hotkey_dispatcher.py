"""direct unit tests for ``voice_typer/server/hotkey_dispatcher.py``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server import event_bus
from voice_typer.server.branding import APP_NAME
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
from voice_typer.server.keyboard_ownership import keyboard_ownership


def _make_mock_app() -> SimpleNamespace:
    """Build a minimal mock app satisfying the HotkeyDispatcher contract."""
    app = SimpleNamespace()
    app.config = SimpleNamespace(
        hotkey="<f2>",
        recording_mode="toggle",
        esc_cancel_enabled=False,
        repaste_hotkey=None,
    )
    app.tray = MagicMock()
    app._stop_dictation = MagicMock()
    app.toggle_dictation = MagicMock()
    app._cancel_dictation = MagicMock()
    app.repaste_last = MagicMock()
    app._esc_cancel_paused = False
    app._shutting_down = False
    return app


@pytest.fixture
def dispatcher() -> HotkeyDispatcher:
    """Build a ``HotkeyDispatcher`` backed by a minimal mock app."""
    app = _make_mock_app()
    return HotkeyDispatcher(app)


@pytest.fixture(autouse=True)
def _reset_keyboard_ownership():
    """Ensure each test starts with ``keyboard_ownership`` in the"""
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


class TestOnEscRelease:
    """Direct unit tests for ``HotkeyDispatcher._on_esc_release``."""

    def test_noop_when_pending_event_not_set(self, dispatcher: HotkeyDispatcher):
        """If the ESC key-down never set the pending flag (e.g. release"""
        dispatcher._esc_pending_capture_exit_event.clear()
        backend = MagicMock()
        dispatcher._esc_backend = backend

        # Capture event_bus state to ensure NO publication.
        published: list[dict] = []
        orig_publish = event_bus.publish
        event_bus.publish = lambda event: published.append(event)  # type: ignore[assignment]
        try:
            dispatcher._on_esc_release()
        finally:
            event_bus.publish = orig_publish  # type: ignore[assignment]

        # No side effects.
        assert keyboard_ownership().current_owner() == "normal"
        assert published == []
        # Backend's set_on_release must NOT have been called.
        backend.set_on_release.assert_not_called()

    def test_publishes_cancel_event_and_resets_ownership(self, dispatcher: HotkeyDispatcher):
        """``normal``, ``hotkey_capture_cancel`` published, legacy alias"""
        # Pre-conditions: ownership is hotkey_capture, pending flag is
        keyboard_ownership().set_owner("hotkey_capture", reason="test setup")
        dispatcher._esc_pending_capture_exit_event.set()
        dispatcher._app._esc_cancel_paused = True

        backend = MagicMock()
        dispatcher._esc_backend = backend

        published: list[dict] = []
        orig_publish = event_bus.publish
        event_bus.publish = lambda event: published.append(event)  # type: ignore[assignment]
        try:
            dispatcher._on_esc_release()
        finally:
            event_bus.publish = orig_publish  # type: ignore[assignment]

        # (1) Ownership back to normal.
        assert keyboard_ownership().current_owner() == "normal"
        # (2) Cancel event published exactly once with the right shape.
        assert len(published) == 1
        assert published[0]["type"] == "hotkey_capture_cancel"
        # (3) Legacy alias cleared.
        assert dispatcher._app._esc_cancel_paused is False
        # (4) Pending flag cleared (so a second release is a no-op).
        assert dispatcher._esc_pending_capture_exit_event.is_set() is False
        # (5) Release callback uninstalled from the backend.
        backend.set_on_release.assert_called_once_with(None)

    def test_tolerates_missing_backend(self, dispatcher: HotkeyDispatcher):
        """If ``_esc_backend`` is None (e.g. ESC backend stopped"""
        keyboard_ownership().set_owner("hotkey_capture", reason="test setup")
        dispatcher._esc_pending_capture_exit_event.set()
        dispatcher._esc_backend = None

        published: list[dict] = []
        orig_publish = event_bus.publish
        event_bus.publish = lambda event: published.append(event)  # type: ignore[assignment]
        try:
            dispatcher._on_esc_release()  # must not raise
        finally:
            event_bus.publish = orig_publish  # type: ignore[assignment]

        assert keyboard_ownership().current_owner() == "normal"
        assert len(published) == 1
        assert published[0]["type"] == "hotkey_capture_cancel"

    def test_release_callback_uninstall_failure_is_swallowed(self, dispatcher: HotkeyDispatcher):
        """If ``backend.set_on_release(None)`` raises (e.g. the backend"""
        keyboard_ownership().set_owner("hotkey_capture", reason="test setup")
        dispatcher._esc_pending_capture_exit_event.set()

        backend = MagicMock()
        backend.set_on_release.side_effect = RuntimeError("backend torn down")
        dispatcher._esc_backend = backend

        # Must not raise.
        dispatcher._on_esc_release()

        # Side effects still applied.
        assert keyboard_ownership().current_owner() == "normal"
        assert dispatcher._app._esc_cancel_paused is False


# Shutdown guards () ────────────────────────────────────────


class TestShutdownGuards:
    """XZ-R17-02: all three hotkey callbacks must short-circuit when"""

    def test_dictation_callback_noop_during_shutdown(self, dispatcher: HotkeyDispatcher):
        callback = dispatcher._make_dictation_callback()
        dispatcher._app._shutting_down = True
        # Ownership is normal, without the shutdown guard this would
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.toggle_dictation.assert_not_called()

    def test_dictation_callback_fires_when_not_shutting_down(self, dispatcher: HotkeyDispatcher):
        """Sanity: the guard does not false-positive."""
        callback = dispatcher._make_dictation_callback()
        dispatcher._app._shutting_down = False
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.toggle_dictation.assert_called_once()

    def test_repaste_callback_noop_during_shutdown(self, dispatcher: HotkeyDispatcher):
        callback = dispatcher._make_repaste_callback()
        dispatcher._app._shutting_down = True
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.repaste_last.assert_not_called()

    def test_repaste_callback_fires_when_not_shutting_down(self, dispatcher: HotkeyDispatcher):
        callback = dispatcher._make_repaste_callback()
        dispatcher._app._shutting_down = False
        keyboard_ownership().set_owner("normal", reason="test")

        callback()

        dispatcher._app.repaste_last.assert_called_once()

    def test_esc_callback_noop_during_shutdown(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """The ESC key-down callback (built in ``register_esc``) must"""
        mock_backend = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec, **kwargs: mock_backend,
        )

        dispatcher._app._shutting_down = True
        dispatcher.register_esc()

        # The callback registered with backend.start() is the inner
        assert mock_backend.start.called
        esc_callback = mock_backend.start.call_args.args[0]
        assert callable(esc_callback)

        keyboard_ownership().set_owner("normal", reason="test")
        esc_callback()

        dispatcher._app._cancel_dictation.assert_not_called()


class TestStopAll:
    """``stop_all`` is the shutdown entry point, must clear all three"""

    def test_stop_all_clears_all_backends(self, dispatcher: HotkeyDispatcher):
        main = MagicMock()
        esc = MagicMock()
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        dispatcher.stop_all()

        main.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None

    def test_stop_all_swallows_stop_failures(self, dispatcher: HotkeyDispatcher):
        """If one backend's ``stop()`` raises, ``stop_all`` must still"""
        main = MagicMock()
        main.stop.side_effect = RuntimeError("join timed out")
        esc = MagicMock()
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        dispatcher.stop_all()  # must not raise

        main.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None

    def test_stop_all_noop_when_no_backends(self, dispatcher: HotkeyDispatcher):
        """If nothing was ever registered, ``stop_all`` is a no-op."""
        dispatcher._hotkey_backend = None
        dispatcher._esc_backend = None
        dispatcher._repaste_backend = None

        dispatcher.stop_all()  # must not raise

        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None


class TestRegistrationFailureSurfacesToTray:
    """FR-20: when ``register_esc`` / ``register_repaste`` fail (e.g."""

    def test_register_esc_calls_notify_safety_on_factory_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """ESC: ``create_hotkey_backend(\"<esc>\")`` raises →"""

        def _raise(spec):
            raise RuntimeError("RegisterHotKey failed: atom already claimed")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            _raise,
        )

        dispatcher.register_esc()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "ESC" in args[1]
        assert dispatcher._esc_backend is None
        assert dispatcher._esc_spec is None
        # The non-safety ``notify`` channel must NOT be used (
        dispatcher._app.tray.notify.assert_not_called()

    def test_register_esc_calls_notify_safety_on_start_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """ESC: ``create_hotkey_backend`` succeeds but ``backend.start()``"""
        mock_backend = MagicMock()
        mock_backend.start.side_effect = RuntimeError("listener thread died")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec, **kwargs: mock_backend,
        )

        dispatcher.register_esc()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "ESC" in args[1]
        assert dispatcher._esc_backend is None
        assert dispatcher._esc_spec is None

    def test_register_esc_does_not_notify_on_success(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Sanity: when ESC registration succeeds, ``notify_safety`` is"""
        mock_backend = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec, **kwargs: mock_backend,
        )

        dispatcher.register_esc()

        dispatcher._app.tray.notify_safety.assert_not_called()
        assert dispatcher._esc_backend is mock_backend
        assert dispatcher._esc_spec == "<esc>"

    def test_register_repaste_calls_notify_safety_on_factory_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Repaste: ``create_hotkey_backend`` raises →"""
        # ``<f8>`` is a single non-alphanumeric function key, passes
        dispatcher._app.config.repaste_hotkey = "<f8>"

        def _raise(spec):
            raise RuntimeError("RegisterHotKey failed: F8 already claimed")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            _raise,
        )

        dispatcher.register_repaste()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "Repaste" in args[1] or "repaste" in args[1].lower()
        assert dispatcher._repaste_backend is None
        assert dispatcher._repaste_spec is None
        dispatcher._app.tray.notify.assert_not_called()

    def test_register_repaste_calls_notify_safety_on_start_failure(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Repaste: ``backend.start()`` raises → same ``notify_safety``"""
        dispatcher._app.config.repaste_hotkey = "<f8>"

        mock_backend = MagicMock()
        mock_backend.start.side_effect = RuntimeError("start failed")

        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec, **kwargs: mock_backend,
        )

        dispatcher.register_repaste()  # must not raise

        dispatcher._app.tray.notify_safety.assert_called_once()
        args = dispatcher._app.tray.notify_safety.call_args.args
        assert args[0] == APP_NAME
        assert "Repaste" in args[1] or "repaste" in args[1].lower()
        assert dispatcher._repaste_backend is None
        assert dispatcher._repaste_spec is None

    def test_register_repaste_does_not_notify_on_success(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Sanity: when repaste registration succeeds, ``notify_safety``"""
        dispatcher._app.config.repaste_hotkey = "<f8>"

        mock_backend = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda spec, **kwargs: mock_backend,
        )

        dispatcher.register_repaste()

        dispatcher._app.tray.notify_safety.assert_not_called()
        assert dispatcher._repaste_backend is mock_backend
        assert dispatcher._repaste_spec == "<f8>"


class TestStopAllTimeoutBudget:
    """``concurrent.futures.ThreadPoolExecutor`` worker with a hard 3s"""

    def test_stop_all_3s_budget_leaks_hung_backend(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """A ``backend.stop()`` that blocks for longer than the 3s budget"""
        import time

        import voice_typer.server.hotkey_dispatcher as hd_mod

        # Patch the 3.0 budget down to 0.2s so the test is fast.
        original_wait = hd_mod.concurrent.futures.wait

        def _fast_wait(futures, timeout=None):
            return original_wait(futures, timeout=0.2)

        monkeypatch.setattr(hd_mod.concurrent.futures, "wait", _fast_wait)

        # Simulated slow duration. The elapsed budget below is a ratio
        # of this value so it scales instead of flaking on slow runners.
        slow_stop_s = 2.0

        main = MagicMock()
        # Sleep far longer than the patched 0.2s budget.
        main.stop.side_effect = lambda: time.sleep(slow_stop_s)
        esc = MagicMock()
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        start = time.monotonic()
        dispatcher.stop_all()  # must return in ~0.2s, NOT ~2s
        elapsed = time.monotonic() - start

        # Budget is 0.2s; allow generous slack for CI scheduling jitter,
        # expressed as a ratio of the simulated slow duration.
        assert elapsed < 0.5 * slow_stop_s, f"stop_all took {elapsed:.2f}s, 0.2s budget not enforced"
        # The two fast backends stopped normally.
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()

    def test_stop_all_swallows_stop_failures_under_pool(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """preserves the prior contract: a ``stop()`` that raises"""
        main = MagicMock()
        main.stop.side_effect = RuntimeError("join timed out")
        esc = MagicMock()
        esc.stop.side_effect = OSError("EBADF")
        repaste = MagicMock()
        dispatcher._hotkey_backend = main
        dispatcher._esc_backend = esc
        dispatcher._repaste_backend = repaste

        dispatcher.stop_all()  # must not raise

        main.stop.assert_called_once()
        esc.stop.assert_called_once()
        repaste.stop.assert_called_once()
        assert dispatcher._hotkey_backend is None
        assert dispatcher._esc_backend is None
        assert dispatcher._repaste_backend is None
