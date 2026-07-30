"""AB-34 regression tests: ``HotkeyDispatcher.restart()`` must NOT
re-create the ESC and repaste backends.

Before AB-34, ``register()`` unconditionally called ``register_esc()``
and ``register_repaste()`` on every invocation. Because ``restart()``
calls ``register()`` to swap the MAIN dictation hotkey, a user changing
only the main hotkey caused the ESC and repaste backends to be torn
down and re-created even though their specs hadn't changed — costing
subprocess spawns / thread creation / Win32 hook installs and briefly
leaving ESC/repaste dead during the stop→start window.

The fix (AB-34): ``register()`` accepts a ``skip_aux: bool = False``
parameter. The FIRST call at startup passes ``skip_aux=False`` (default)
so all 3 backends are installed. ``restart()`` passes ``skip_aux=True``
so only the MAIN dictation hotkey is swapped.

These tests pin the contract by mocking ``register_esc()`` and
``register_repaste()`` and counting calls.
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
    esc_cancel_enabled: bool = True,
    repaste_hotkey: str | None = "<f6>",
) -> SimpleNamespace:
    """Build a minimal mock app satisfying the HotkeyDispatcher contract.

    Defaults set ESC and repaste ON so the test exercises the skip path
    (otherwise ``register_esc`` / ``register_repaste`` are no-ops anyway
    and the test wouldn't catch an AB-34 regression).
    """
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
    """AB-34: the FIRST call to ``register()`` (at startup) MUST install
    all 3 backends — ``register_esc()`` and ``register_repaste()`` are
    each called once. This pins the "first-time setup" contract so a
    future refactor doesn't accidentally pass ``skip_aux=True`` to the
    first call."""
    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(return_value=new_backend),
    )

    # Spy on register_esc / register_repaste (don't replace — we want to
    # verify they're called with their default behavior).
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
    """AB-34: ``restart()`` swaps ONLY the main dictation hotkey. It
    MUST NOT call ``register_esc()`` — the ESC backend's spec hasn't
    changed, so re-creating it would waste subprocess spawns / thread
    creation / Win32 hook installs and briefly leave ESC dead during
    the stop→start window."""
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
    """AB-34: ``restart()`` MUST NOT call ``register_repaste()`` — same
    rationale as ``register_esc`` (the repaste spec hasn't changed)."""
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
    """AB-34 (stronger): ``restart()`` MUST NOT stop or replace the
    existing ESC and repaste backends. They keep running untouched
    across a main-hotkey swap so the user can press ESC to cancel or
    the repaste hotkey throughout the swap window."""
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
    """AB-34 unit: ``register(skip_aux=True)`` skips ``register_esc()``
    and ``register_repaste()`` while still installing the main backend.
    This is the building block ``restart()`` relies on."""
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
    """AB-34 negative: ``register(skip_aux=False)`` (the default) still
    calls both aux backends. This pins the first-time-setup contract
    so a future change can't accidentally make ``skip_aux`` always True."""
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
    """AB-34: even on the failure/restore path, ``restart()`` must NOT
    call ``register_esc()`` / ``register_repaste()``. The restore path
    calls ``_create_and_start_main_backend(old_hotkey_str)`` directly
    (not ``register()``), so aux backends are untouched."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    restored_backend = MagicMock()
    restored_backend.is_alive.return_value = True
    # First call (for "<bad>") raises; second call (for "<f2>") returns
    # the restored backend.
    factory = MagicMock(side_effect=[RuntimeError("invalid"), restored_backend])
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    register_esc_calls: list[int] = []
    register_repaste_calls: list[int] = []
    dispatcher.register_esc = lambda: register_esc_calls.append(1)  # type: ignore[assignment]
    dispatcher.register_repaste = lambda: register_repaste_calls.append(1)  # type: ignore[assignment]

    dispatcher.restart("<bad>")

    # Restore happened with the OLD spec.
    assert dispatcher._hotkey_backend is restored_backend
    assert dispatcher._app.config.hotkey == "<f2>"
    # Aux backends untouched.
    assert register_esc_calls == [], "restart() failure path must NOT call register_esc()"
    assert register_repaste_calls == [], "restart() failure path must NOT call register_repaste()"
