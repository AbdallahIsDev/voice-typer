"""PVT-G5-027 regression tests: HotkeyDispatcher.restart() ordering.

Verifies that ``HotkeyDispatcher.restart()`` stops the OLD backend
BEFORE starting the NEW one (eliminating the double-backend window
where both old and new backends fire on the same keypress → double-
toggle on platforms that permit multiple global-hotkey registrations
like pynput on Linux/X11 and Wayland).

If the new backend fails to start (e.g. the hotkey spec is invalid,
or the OS rejects it because another app already claimed it), the
OLD hotkey spec is restored to ``app.config.hotkey`` and a fresh
backend is created with the OLD spec so the user is never left
without a working dictation hotkey. This preserves the CR-15 user-
facing contract ("restart failure keeps the previous hotkey working")
while eliminating the double-backend window.

Three scenarios are covered:

1. **Success path** — ``create_hotkey_backend`` returns a new backend
   whose ``start()`` succeeds. Assert: NEW backend assigned to
   ``_hotkey_backend``, OLD ``stop()`` called BEFORE the new backend
   is started (no overlap), tray ``set_hotkey`` called with the new
   spec.

2. **Failure path A** — ``create_hotkey_backend`` raises on the FIRST
   call (for the new hotkey spec). Assert: OLD backend's ``stop()``
   WAS called (we stop before register), the factory is called a
   SECOND time to restore the OLD hotkey spec, the restored backend
   is installed, ``app.config.hotkey`` is reverted to the OLD spec,
   tray ``set_hotkey`` called with the OLD spec, tray ``notify``
   shown naming the rejected hotkey.

3. **Failure path B** — ``create_hotkey_backend`` returns a new
   backend whose ``start()`` raises on the FIRST call. Same
   assertions as Failure path A (restore path is identical).

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
    """Success path: new backend created+started; OLD backend stopped
    BEFORE the new one is started (PVT-G5-027: no double-backend window)."""
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
    # kwarg, since the role-aware factory refactor).
    factory.assert_called_once_with("<f3>", role="dictation")

    # NEW backend was started and assigned
    new_backend.start.assert_called_once()
    assert dispatcher._hotkey_backend is new_backend

    # OLD backend was stopped
    old_backend.stop.assert_called_once()

    # OLD backend's stop() was called BEFORE NEW backend's
    # start() — no window where both backends are simultaneously
    # listening on the same hotkey (would cause double-toggle on Linux
    # pynput/Wayland where multiple registrations are permitted).
    assert call_order == ["old_backend.stop", "new_backend.start"], (
        f"Expected old_backend.stop BEFORE new_backend.start; got {call_order}"
    )

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


def test_restart_failure_in_factory_restores_old_hotkey_spec(dispatcher: HotkeyDispatcher, monkeypatch):
    """PVT-G5-027 Failure path A: ``create_hotkey_backend`` raises on
    the FIRST call (for the new hotkey spec). The OLD backend's
    ``stop()`` MUST be called (we stop before register), and the
    factory is called a SECOND time to restore a backend with the OLD
    hotkey spec. ``app.config.hotkey`` is reverted to the OLD spec so
    the user is never left without a working dictation hotkey."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    restored_backend = MagicMock()
    restored_backend.is_alive.return_value = True
    # First call (for "<bad>") raises; second call (for "<f2>", the
    # old hotkey) returns the restored backend.
    factory = MagicMock(
        side_effect=[RuntimeError("invalid hotkey spec"), restored_backend],
    )
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<bad>")

    # OLD backend's stop() WAS called (before register)
    old_backend.stop.assert_called_once()

    # Factory was called twice: once for the new (failing) hotkey,
    # once for the restore (with the OLD hotkey spec). Each call
    # passes the hotkey spec + role kwarg (canonical signature of
    # ``create_hotkey_backend`` since the role-aware refactor).
    assert factory.call_count == 2
    factory.assert_any_call("<bad>", role="dictation")
    factory.assert_any_call("<f2>", role="dictation")  # old hotkey spec

    # Restored backend is installed (a NEW instance — NOT old_backend,
    # which was already stopped).
    assert dispatcher._hotkey_backend is restored_backend
    restored_backend.start.assert_called_once()

    # Config was reverted to the OLD hotkey spec
    assert dispatcher._app.config.hotkey == "<f2>"

    # Tray hotkey label is set to the OLD (restored) spec, NOT "<bad>"
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f2>")

    # + : tray notification was shown naming the
    # rejected hotkey (register() shows this notification).
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<bad>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<bad>', got: {notify_calls}"
    )


def test_restart_failure_in_factory_with_failed_restore_leaves_no_backend(dispatcher: HotkeyDispatcher, monkeypatch):
    """PVT-G5-027 Failure path A (restore also fails): if the factory
    raises on BOTH calls (the new hotkey AND the restore attempt), the
    user is left without a working dictation hotkey. ``app.config.hotkey``
    is still reverted to the OLD spec so the tray label and any future
    restart attempt use the OLD (known-good) spec. A separate ERROR
    notification is shown so the user knows to rebind in Settings."""
    old_backend = MagicMock()
    old_backend.is_alive.return_value = True
    dispatcher._hotkey_backend = old_backend

    # Factory raises on EVERY call — both the initial register and the
    # restore attempt fail.
    factory = MagicMock(side_effect=RuntimeError("display gone"))
    monkeypatch.setattr("voice_typer.server.hotkey_dispatcher.create_hotkey_backend", factory)

    dispatcher.restart("<bad>")

    # OLD backend's stop() WAS called (we stop before register).
    old_backend.stop.assert_called_once()

    # Factory was called twice (once for "<bad>", once for "<f2>")
    assert factory.call_count == 2

    # No backend installed — restore also failed.
    assert dispatcher._hotkey_backend is None

    # Config was reverted to the OLD hotkey spec
    assert dispatcher._app.config.hotkey == "<f2>"

    # Tray hotkey label is set to the OLD (restored) spec
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f2>")

    # A tray notification was shown naming the rejected hotkey
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<bad>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<bad>', got: {notify_calls}"
    )


# ─── Failure path B: backend.start() raises ──────────────────────────────


def test_restart_failure_in_start_restores_old_hotkey_spec(dispatcher: HotkeyDispatcher, monkeypatch):
    """PVT-G5-027 Failure path B: ``create_hotkey_backend`` returns a
    new backend whose ``start()`` raises on the FIRST call. The OLD
    backend's ``stop()`` MUST be called (we stop before register), and
    the factory is called a SECOND time to restore a backend with the
    OLD hotkey spec."""
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
    # new_backend), once for "<f2>" (the OLD hotkey, returns the
    # restored backend). Each call passes the hotkey spec + role kwarg
    # (the canonical signature of ``create_hotkey_backend`` since
    # the role-aware refactor).
    assert factory.call_count == 2
    factory.assert_any_call("<f5>", role="dictation")
    factory.assert_any_call("<f2>", role="dictation")

    # The broken new backend's start() was attempted (and raised)
    new_backend.start.assert_called_once()

    # Restored backend IS installed and its start() was called.
    assert dispatcher._hotkey_backend is restored_backend
    restored_backend.start.assert_called_once()

    # Config was reverted to the OLD hotkey spec
    assert dispatcher._app.config.hotkey == "<f2>"

    # Tray hotkey label is set to the OLD (restored) spec
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f2>")

    # Tray notification was shown naming the rejected hotkey
    notify_calls = dispatcher._app.tray.notify.call_args_list
    assert any("<f5>" in str(call) for call in notify_calls), (
        f"Expected tray notification naming the rejected hotkey '<f5>', got: {notify_calls}"
    )


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


# negative: restart() must NOT restore on success path ──────
#
# This test was contributed by session-4 () and is retained in the
# merged tree because it is COMPATIBLE with session-5's  behavior
# (both agree: on the success path, no restoration occurs, only one save
# is made, and the OLD backend is stopped). The other two  tests
# (``test_restart_restores_hotkey_on_register_failure`` and
# ``test_restart_restores_hotkey_on_start_failure``) were DROPPED because
# they assert ``old_backend.stop.assert_not_called()`` and
# ``dispatcher._hotkey_backend is old_backend``, which directly
# contradict 's stop-before-start + restore-with-old-spec
# behavior. The merged ``hotkey_dispatcher.py`` follows  (the
# more robust strategy that eliminates the double-backend window on
# Linux pynput/Wayland). See sub-agent SO report for the full conflict
# analysis.


def test_restart_does_not_restore_hotkey_on_success(dispatcher: HotkeyDispatcher, monkeypatch):
    """G4-H-17 (negative test): when ``register()`` SUCCEEDS, the new
    hotkey is kept — no restoration occurs, and ``config.save()`` is
    called exactly once (the pre-register save). This guards against
    the restoration logic firing spuriously on the success path.

    This negative assertion is compatible with both the G4-H-17 (keep
    OLD backend alive on failure) and PVT-G5-027 (stop OLD + restore
    with old spec on failure) strategies, because on the SUCCESS path
    neither strategy attempts a restoration."""
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

    # Only ONE save (the pre-register save) — no restoration save.
    assert dispatcher._app.config.save.call_count == 1

    # New backend installed, OLD stopped.
    assert dispatcher._hotkey_backend is new_backend
    old_backend.stop.assert_called_once()

    # Tray label shows the new hotkey.
    dispatcher._app.tray.set_hotkey.assert_called_once_with("<f8>")
