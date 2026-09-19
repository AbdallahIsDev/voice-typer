"""AB-34 regression tests: ``HotkeyDispatcher.restart()`` must NOT"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher


def _make_mock_app(
    *,
    hotkey: str = "<f2>",
    recording_mode: str = "toggle",
    esc_cancel_enabled: bool = True,
    repaste_hotkey: str | None = "<f6>",
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
    """Build a HotkeyDispatcher with ESC + repaste both enabled."""
    return HotkeyDispatcher(_make_mock_app())


def test_first_time_register_calls_aux_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """AB-34: the FIRST call to ``register()`` (at startup) MUST install"""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    register_esc_calls: list[int] = []
    register_repaste_calls: list[int] = []
    orig_esc = dispatcher.register_esc
    orig_repaste = dispatcher.register_repaste

    def spy_esc():
        register_esc_calls.append(1)
        return orig_esc()

    def spy_repaste():
        register_repaste_calls.append(1)
        return orig_repaste()

    dispatcher.register_esc = spy_esc  # type: ignore[assignment]
    dispatcher.register_repaste = spy_repaste  # type: ignore[assignment]

    try:
        result = dispatcher.register()  # default: skip_aux=False
    finally:
        dispatcher.register_esc = orig_esc  # type: ignore[assignment]
        dispatcher.register_repaste = orig_repaste  # type: ignore[assignment]

    assert result is True
    # First-time setup MUST register ESC and repaste.
    assert len(register_esc_calls) == 1, (
        f"First-time register() must call register_esc() once; got {register_esc_calls}"
    )
    assert len(register_repaste_calls) == 1, (
        f"First-time register() must call register_repaste() once; got {register_repaste_calls}"
    )


def test_restart_does_not_call_register_esc(dispatcher: HotkeyDispatcher, monkeypatch):
    """
    AB-34: ``restart()`` swaps ONLY the main dictation hotkey. It
    MUST NOT call ``register_esc()``, the ESC backend's spec hasn't
    """
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    register_esc_calls: list[int] = []
    dispatcher.register_esc = lambda: register_esc_calls.append(1)  # type: ignore[assignment]

    dispatcher.restart("<f3>")

    assert register_esc_calls == [], f"restart() must NOT call register_esc() (AB-34); got {register_esc_calls}"
    # Main hotkey was swapped.
    assert dispatcher._hotkey_backend is new_backend
    assert dispatcher._app.config.hotkey == "<f3>"


def test_restart_does_not_call_register_repaste(dispatcher: HotkeyDispatcher, monkeypatch):
    """AB-34: ``restart()`` MUST NOT call ``register_repaste()``, same"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    register_repaste_calls: list[int] = []
    dispatcher.register_repaste = lambda: register_repaste_calls.append(1)  # type: ignore[assignment]

    dispatcher.restart("<f4>")

    assert register_repaste_calls == [], (
        f"restart() must NOT call register_repaste() (AB-34); got {register_repaste_calls}"
    )


def test_restart_does_not_touch_existing_esc_or_repaste_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """existing ESC and repaste backends. They keep running untouched"""
    existing_esc = MagicMock()
    existing_repaste = MagicMock()
    dispatcher._hotkey_backend = MagicMock()
    dispatcher._esc_backend = existing_esc
    dispatcher._repaste_backend = existing_repaste

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    dispatcher.restart("<f9>")

    # Existing ESC / repaste backends are UNTOUCHED.
    assert dispatcher._esc_backend is existing_esc
    assert dispatcher._repaste_backend is existing_repaste
    existing_esc.stop.assert_not_called()
    existing_repaste.stop.assert_not_called()
    # Main was swapped.
    assert dispatcher._hotkey_backend is new_backend


def test_register_with_skip_aux_true_skips_aux_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """AB-34 unit: ``register(skip_aux=True)`` skips ``register_esc()``"""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    register_esc_calls: list[int] = []
    register_repaste_calls: list[int] = []
    dispatcher.register_esc = lambda: register_esc_calls.append(1)  # type: ignore[assignment]
    dispatcher.register_repaste = lambda: register_repaste_calls.append(1)  # type: ignore[assignment]

    result = dispatcher.register(skip_aux=True)

    assert result is True
    assert dispatcher._hotkey_backend is new_backend
    assert register_esc_calls == [], "skip_aux=True must skip register_esc()"
    assert register_repaste_calls == [], "skip_aux=True must skip register_repaste()"


def test_register_with_skip_aux_false_calls_aux_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """AB-34 negative: ``register(skip_aux=False)`` (the default) still"""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    register_esc_calls: list[int] = []
    register_repaste_calls: list[int] = []
    dispatcher.register_esc = lambda: register_esc_calls.append(1)  # type: ignore[assignment]
    dispatcher.register_repaste = lambda: register_repaste_calls.append(1)  # type: ignore[assignment]

    result = dispatcher.register(skip_aux=False)

    assert result is True
    assert dispatcher._hotkey_backend is new_backend
    assert len(register_esc_calls) == 1
    assert len(register_repaste_calls) == 1


def test_restart_failure_path_does_not_call_aux_backends(dispatcher: HotkeyDispatcher, monkeypatch):
    """AB-34: even on the failure/restore path, ``restart()`` must NOT"""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    restored_backend = MagicMock()
    restored_backend.is_alive.return_value = True
    # First call (for "<bad>") raises; second call (for "<f2>") returns
    factory = MagicMock(side_effect=[RuntimeError("invalid"), restored_backend])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    register_esc_calls: list[int] = []
    register_repaste_calls: list[int] = []
    dispatcher.register_esc = lambda: register_esc_calls.append(1)  # type: ignore[assignment]
    dispatcher.register_repaste = lambda: register_repaste_calls.append(1)  # type: ignore[assignment]

    dispatcher.restart("<bad>")

    assert dispatcher._hotkey_backend is restored_backend
    assert dispatcher._app.config.hotkey == "<f2>"
    # Aux backends untouched.
    assert register_esc_calls == [], "restart() failure path must NOT call register_esc()"
    assert register_repaste_calls == [], "restart() failure path must NOT call register_repaste()"
