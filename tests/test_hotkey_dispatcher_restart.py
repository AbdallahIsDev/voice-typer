"""
PVT-G5-027 regression tests: HotkeyDispatcher.restart() ordering.
These tests use a minimal mock app, they do NOT construct a real
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher


def _make_mock_app(
    *,
    hotkey: str = "<f2>",
    recording_mode: str = "toggle",
    esc_cancel_enabled: bool = False,
    repaste_hotkey: str | None = None,
) -> SimpleNamespace:
    """Build a minimal mock app satisfying the HotkeyDispatcher contract."""
    app = SimpleNamespace()
    app.config = SimpleNamespace(
        hotkey=hotkey,
        recording_mode=recording_mode,
        esc_cancel_enabled=esc_cancel_enabled,
        repaste_hotkey=repaste_hotkey,
        save=MagicMock(return_value=True),
    )
    app.tray = MagicMock()
    app._stop_dictation = MagicMock()
    app.toggle_dictation = MagicMock()
    app._cancel_dictation = MagicMock()
    app.repaste_last = MagicMock()
    return app


@pytest.fixture
def dispatcher() -> HotkeyDispatcher:
    """Build a HotkeyDispatcher backed by a minimal mock app."""
    app = _make_mock_app()
    return HotkeyDispatcher(app)


def test_restart_success_installs_new_backend_and_stops_old(dispatcher: HotkeyDispatcher, monkeypatch):
    """Success path: new backend created+started; OLD backend stopped"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    # Record the order of stop() vs start() calls to assert no overlap.
    call_order: list[str] = []
    old_backend.stop.side_effect = lambda: call_order.append("old_backend.stop")
    new_backend.start.side_effect = lambda cb: call_order.append("new_backend.start")

    dispatcher.restart("<f3>")

    # Config was updated and saved
    assert dispatcher._app.config.hotkey == "<f3>"
    dispatcher._app.config.save.assert_called_once()

    # Factory was called with the new hotkey spec (and the role
    factory.assert_called_once_with("<f3>", role="dictation")

    # NEW backend was started and assigned
    new_backend.start.assert_called_once()
    assert dispatcher._hotkey_backend is new_backend

    # OLD backend was stopped
    old_backend.stop.assert_called_once()

    # OLD backend's stop() was called BEFORE NEW backend's
    assert call_order == ["old_backend.stop", "new_backend.start"], (
        f"Expected old_backend.stop BEFORE new_backend.start; got {call_order}"
    )

    # Tray was notified about the new hotkey
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f3>")


def test_restart_success_with_no_old_backend_does_not_crash(dispatcher: HotkeyDispatcher, monkeypatch):
    """First-time restart (no old backend), new backend installed, no"""
    assert dispatcher._hotkey_backend is None

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    # Must not raise even though there's no old backend to stop.
    dispatcher.restart("<f4>")

    assert dispatcher._hotkey_backend is new_backend
    new_backend.start.assert_called_once()
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f4>")


def test_restart_failure_in_factory_restores_old_hotkey_spec(dispatcher: HotkeyDispatcher, monkeypatch):
    """factory is called a SECOND time to restore a backend with the OLD"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    restored_backend = MagicMock()
    restored_backend.is_alive.return_value = True
    factory = MagicMock(
        side_effect=[RuntimeError("invalid hotkey spec"), restored_backend],
    )
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<bad>")

    # OLD backend's stop() WAS called (before register)
    old_backend.stop.assert_called_once()

    # Factory was called twice: once for the new (failing) hotkey,
    assert factory.call_count == 2
    factory.assert_any_call("<bad>", role="dictation")
    factory.assert_any_call("<f2>", role="dictation")  # old hotkey spec

    # Restored backend is installed (a NEW instance, NOT old_backend,
    assert dispatcher._hotkey_backend is restored_backend
    restored_backend.start.assert_called_once()

    assert dispatcher._app.config.hotkey == "<f2>"

    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f2>")

    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<bad>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<bad>', got: {notify_calls}"
    )


def test_restart_failure_in_factory_with_failed_restore_leaves_no_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """PVT-G5-027 Failure path A (restore also fails): if the factory"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    factory = MagicMock(side_effect=RuntimeError("display gone"))
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<bad>")

    # OLD backend's stop() WAS called (we stop before register).
    old_backend.stop.assert_called_once()

    # Factory was called twice (once for "<bad>", once for "<f2>")
    assert factory.call_count == 2

    # No backend installed, restore also failed.
    assert dispatcher._hotkey_backend is None

    assert dispatcher._app.config.hotkey == "<f2>"

    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f2>")

    # A tray notification was shown naming the rejected hotkey
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<bad>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<bad>', got: {notify_calls}"
    )


def test_restart_failure_in_start_restores_old_hotkey_spec(dispatcher: HotkeyDispatcher, monkeypatch):
    """new backend whose ``start()`` raises on the FIRST call. The OLD"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    new_backend.start.side_effect = OSError("hotkey already claimed by another app")
    restored_backend = MagicMock()
    restored_backend.is_alive.return_value = True
    factory = MagicMock(side_effect=[new_backend, restored_backend])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f5>")

    # OLD backend's stop() WAS called
    old_backend.stop.assert_called_once()

    # Factory was called twice: once for "<f5>" (returned the broken
    assert factory.call_count == 2
    factory.assert_any_call("<f5>", role="dictation")
    factory.assert_any_call("<f2>", role="dictation")

    # The broken new backend's start() was attempted (and raised)
    new_backend.start.assert_called_once()

    # Restored backend IS installed and its start() was called.
    assert dispatcher._hotkey_backend is restored_backend
    restored_backend.start.assert_called_once()

    assert dispatcher._app.config.hotkey == "<f2>"

    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f2>")

    # Tray notification was shown naming the rejected hotkey
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<f5>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<f5>', got: {notify_calls}"
    )


def test_restart_with_failed_config_save_still_attempts_registration(dispatcher: HotkeyDispatcher, monkeypatch):
    """If ``config.save()`` returns False (e.g. disk full), restart"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    dispatcher._app.config.save.return_value = False

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f6>")

    # Save-failure notification shown
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("save" in str(call).lower() or "disk" in str(call).lower() for call in notify_calls), (
        f"Expected save-failure tray notification, got: {notify_calls}"
    )

    # Registration still attempted, new backend installed, old stopped
    assert dispatcher._hotkey_backend is new_backend
    new_backend.start.assert_called_once()
    old_backend.stop.assert_called_once()


def test_restart_swallows_old_backend_stop_failure(dispatcher: HotkeyDispatcher, monkeypatch):
    """If OLD backend's stop() raises (e.g. listener thread already"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    old_backend.stop.side_effect = RuntimeError("listener thread already dead")
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    # Must not raise, old-backend stop failure is logged, not propagated.
    dispatcher.restart("<f7>")

    # NEW backend still installed (the swap succeeded before stop was called)
    assert dispatcher._hotkey_backend is new_backend
    new_backend.start.assert_called_once()
    # OLD backend's stop WAS attempted (and raised, but was caught)
    old_backend.stop.assert_called_once()


def test_register_failure_does_not_overwrite_existing_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """building block: register() must NOT overwrite"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    # New backend's start() raises
    new_backend = MagicMock()
    new_backend.start.side_effect = OSError("hotkey already claimed")
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    result = dispatcher.register()

    assert result is False

    # OLD backend still in place, NOT overwritten with the broken new one
    assert dispatcher._hotkey_backend is old_backend

    # New backend WAS constructed and start was attempted
    new_backend.start.assert_called_once()
    # OLD backend was NOT touched
    old_backend.stop.assert_not_called()


def test_register_success_returns_true_and_installs_new_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """building block: register() returns True on success and"""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    result = dispatcher.register()

    assert result is True
    assert dispatcher._hotkey_backend is new_backend
    new_backend.start.assert_called_once()


def test_register_failure_with_no_existing_backend_leaves_field_none(dispatcher: HotkeyDispatcher, monkeypatch):
    """First-time register() failure leaves _hotkey_backend as None"""
    assert dispatcher._hotkey_backend is None

    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(side_effect=RuntimeError("no display")),
    )

    result = dispatcher.register()

    assert result is False
    assert dispatcher._hotkey_backend is None


# negative: restart() must NOT restore on success path ──────


def test_restart_does_not_restore_hotkey_on_success(dispatcher: HotkeyDispatcher, monkeypatch):
    """G4-H-17 (negative test): when ``register()`` SUCCEEDS, the new"""
    assert dispatcher._app.config.hotkey == "<f2>"
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    dispatcher._app.config.save.reset_mock()
    dispatcher._app.config.save.return_value = True

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f8>")

    # New hotkey is kept (not restored to <f2>).
    assert dispatcher._app.config.hotkey == "<f8>"

    # Only ONE save (the pre-register save), no restoration save.
    assert dispatcher._app.config.save.call_count == 1

    # New backend installed, OLD stopped.
    assert dispatcher._hotkey_backend is new_backend
    old_backend.stop.assert_called_once()

    # Tray label shows the new hotkey.
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f8>")
