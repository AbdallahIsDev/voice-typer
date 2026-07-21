"""CR-15 regression tests: HotkeyDispatcher.restart() atomicity.

Verifies that ``HotkeyDispatcher.restart()`` is atomic — it brings up
the NEW backend BEFORE stopping the OLD one. If the new backend fails
to start (e.g. the hotkey spec is invalid, or the OS rejects it because
another app already claimed it), the OLD backend is kept running so
the user is never left without a working dictation hotkey.

Three scenarios are covered:

1. **Success path** — ``create_hotkey_backend`` returns a new backend
   whose ``start()`` succeeds. Assert: NEW backend assigned to
   ``_hotkey_backend``, OLD ``stop()`` called, tray ``set_hotkey``
   called.

2. **Failure path A** — ``create_hotkey_backend`` raises (e.g. spec
   parse error or missing native binary). Assert: OLD backend still
   assigned to ``_hotkey_backend``, OLD ``stop()`` NOT called, tray
   ``notify`` shown.

3. **Failure path B** — ``create_hotkey_backend`` returns a new backend
   whose ``start()`` raises (e.g. Win32 ``RegisterHotKey`` rejected
   because another app claimed the hotkey). Assert: OLD backend still
   assigned to ``_hotkey_backend``, OLD ``stop()`` NOT called, tray
   ``notify`` shown.

These tests use a minimal mock app — they do NOT construct a real
``VoiceTyperApp`` (which would pull in sounddevice / faster_whisper /
pynput / pystray / PIL / pyperclip and the cross-process config lock).
The mock app exposes just the surface area ``HotkeyDispatcher`` reads:
``config.hotkey``, ``config.save()``, ``config.recording_mode``,
``config.esc_cancel_enabled``, ``config.repaste_hotkey``, ``tray.notify``,
``tray.set_hotkey``, and ``_stop_dictation``.
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
    """Build a minimal mock app satisfying the HotkeyDispatcher contract.

    ``config.save`` is a MagicMock so individual tests can override its
    return value (default: ``True``) — the real ``Config.save`` acquires
    a cross-process ``fcntl``/``msvcrt`` lock that we don't want to
    exercise in these atomicity tests.
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
    """Build a HotkeyDispatcher backed by a minimal mock app.

    The mock app is also attached as ``dispatcher._app`` for in-test
    customization (e.g. overriding ``config.save`` return value).
    """
    app = _make_mock_app()
    return HotkeyDispatcher(app)


# ─── Success path ────────────────────────────────────────────────────────


def test_restart_success_installs_new_backend_and_stops_old(dispatcher: HotkeyDispatcher, monkeypatch):
    """Success path: new backend created+started; OLD backend stopped."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f3>")

    # Config was updated and saved
    assert dispatcher._app.config.hotkey == "<f3>"
    dispatcher._app.config.save.assert_called_once()

    # Factory was called with the new hotkey spec
    factory.assert_called_once_with("<f3>")

    # NEW backend was started and assigned
    new_backend.start.assert_called_once()
    assert dispatcher._hotkey_backend is new_backend

    # OLD backend was stopped
    old_backend.stop.assert_called_once()

    # Tray was notified about the new hotkey
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f3>")


def test_restart_success_with_no_old_backend_does_not_crash(dispatcher: HotkeyDispatcher, monkeypatch):
    """First-time restart (no old backend) — new backend installed, no
    stop call needed (nothing to stop)."""
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


# ─── Failure path A: create_hotkey_backend raises ────────────────────────


def test_restart_failure_in_factory_keeps_old_backend_alive(dispatcher: HotkeyDispatcher, monkeypatch):
    """Failure path A: ``create_hotkey_backend`` raises (e.g. spec parse
    error, missing native binary). OLD backend must keep running."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    factory = MagicMock(side_effect=RuntimeError("invalid hotkey spec"))
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<bad>")

    # CR-15 (a): OLD backend is still assigned to _hotkey_backend
    assert dispatcher._hotkey_backend is old_backend, (
        "OLD backend must remain installed when restart() fails so the "
        "user is never left without a working dictation hotkey."
    )

    # CR-15 (b): OLD backend's stop() was NOT called
    old_backend.stop.assert_not_called()

    # CR-15 (c): tray notification was shown (UX-002: names the hotkey)
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<bad>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<bad>', got: {notify_calls}"
    )

    # Tray hotkey label is still updated (config was set, even if registration failed)
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<bad>")


# ─── Failure path B: backend.start() raises ──────────────────────────────


def test_restart_failure_in_start_keeps_old_backend_alive(dispatcher: HotkeyDispatcher, monkeypatch):
    """Failure path B: ``create_hotkey_backend`` returns a new backend
    whose ``start()`` raises (e.g. Win32 RegisterHotKey rejected because
    another app claimed the hotkey). OLD backend must keep running."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    new_backend.start.side_effect = OSError("hotkey already claimed by another app")
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<f5>")

    # CR-15 (a): OLD backend is still assigned (NOT the broken new one)
    assert dispatcher._hotkey_backend is old_backend, (
        "OLD backend must remain installed when new backend's start() "
        "fails. The new (broken) backend must NOT replace it."
    )

    # CR-15 (b): OLD backend's stop() was NOT called
    old_backend.stop.assert_not_called()

    # The new (broken) backend WAS constructed and start was attempted,
    # but it should NOT have been installed as _hotkey_backend.
    new_backend.start.assert_called_once()

    # CR-15 (c): tray notification was shown
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<f5>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<f5>', got: {notify_calls}"
    )

    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f5>")


# ─── Failure path C: config.save() returns False ─────────────────────────


def test_restart_with_failed_config_save_still_attempts_registration(dispatcher: HotkeyDispatcher, monkeypatch):
    """If ``config.save()`` returns False (e.g. disk full), restart
    shows a tray notification about the save failure but still
    attempts to register the new hotkey (in-memory config was already
    updated). This is the pre-existing behavior; CR-15 doesn't change
    it, but the test pins it so future refactors don't silently drop
    the notification or the registration attempt."""
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

    # Registration still attempted — new backend installed, old stopped
    assert dispatcher._hotkey_backend is new_backend
    new_backend.start.assert_called_once()
    old_backend.stop.assert_called_once()


# ─── stop() failure on old backend doesn't break the new one ─────────────


def test_restart_swallows_old_backend_stop_failure(dispatcher: HotkeyDispatcher, monkeypatch):
    """If OLD backend's stop() raises (e.g. listener thread already
    dead), the NEW backend stays installed — we don't roll back a
    successful swap just because cleanup of the old backend failed."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    old_backend.stop.side_effect = RuntimeError("listener thread already dead")
    dispatcher._hotkey_backend = old_backend

    new_backend = MagicMock()
    new_backend.is_alive.return_value = True
    factory = MagicMock(return_value=new_backend)
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    # Must not raise — old-backend stop failure is logged, not propagated.
    dispatcher.restart("<f7>")

    # NEW backend still installed (the swap succeeded before stop was called)
    assert dispatcher._hotkey_backend is new_backend
    new_backend.start.assert_called_once()
    # OLD backend's stop WAS attempted (and raised, but was caught)
    old_backend.stop.assert_called_once()


# ─── register() atomicity (the building block restart relies on) ─────────


def test_register_failure_does_not_overwrite_existing_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """CR-15 building block: register() must NOT overwrite
    self._hotkey_backend with a broken new backend if start() fails.
    The OLD backend (if any) must remain in place so a subsequent
    restart() can detect failure via the return value."""
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

    # register() returns False on failure
    assert result is False

    # OLD backend still in place — NOT overwritten with the broken new one
    assert dispatcher._hotkey_backend is old_backend

    # New backend WAS constructed and start was attempted
    new_backend.start.assert_called_once()
    # OLD backend was NOT touched
    old_backend.stop.assert_not_called()


def test_register_success_returns_true_and_installs_new_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """CR-15 building block: register() returns True on success and
    assigns the new backend to _hotkey_backend."""
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
    """First-time register() failure leaves _hotkey_backend as None
    (no old backend to keep). This is the original pre-CR-15 behavior
    for first-time registration; the CR-15 fix only changes the
    behavior when an OLD backend exists."""
    assert dispatcher._hotkey_backend is None

    monkeypatch.setattr(
        "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
        MagicMock(side_effect=RuntimeError("no display")),
    )

    result = dispatcher.register()

    assert result is False
    assert dispatcher._hotkey_backend is None
