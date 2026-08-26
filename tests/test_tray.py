"""Tests for the tray Phase 2 minimal menu.

Phase 2: Minimal right-click menu:
- Start Dictation (hotkey)
- Open App (Electron)
- Models
- Restart
- Quit

Left-click "Open App" launches the Electron app (or focuses it if already running).
All settings, history, templates, etc. live in the Electron window only.
"""

import subprocess
import sys
import threading
import time
import warnings
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

# Mock pystray at module level so the tray module can be imported
# without needing an X display (headless CI).
#
# NOTE: PIL is NOT mocked at module level. tray.py imports pystray
# directly but uses ``_make_icon`` from ``tray_icon.py``, which in turn
# uses *lazy* imports of PIL inside its drawing functions. So PIL is
# never imported at module load time, and mocking it here would
# permanently pollute ``sys.modules`` — breaking any later test that
# needs real PIL (e.g. tests/test_tray_icon.py, which is marked
# ``@pytest.mark.real_pil``).
from voice_typer.server.tray import TrayIcon  # noqa: E402


class _FakeMenu:
    """Lightweight stand-in for pystray.Menu that records construction args."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self._callable = args[0] if args and callable(args[0]) else None

    def __call__(self):
        """Materialize menu items by invoking the stored callable."""
        if self._callable is not None:
            return self._callable()
        return self.args

    SEPARATOR = "SEP"


class _FakeMenuItem:
    """Lightweight stand-in for pystray.MenuItem."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeIcon:
    """Record how pystray.Icon was constructed so we can assert on kwargs."""

    last_kwargs = {}

    def __init__(self, **kwargs):
        _FakeIcon.last_kwargs = kwargs
        self.menu = kwargs.get("menu")
        self.icon = kwargs.get("icon")
        self.title = kwargs.get("title", "")
        self._run_called = False

    def run(self):
        self._run_called = True

    def stop(self):
        pass

    def notify(self, *a, **kw):
        pass


# Mock heavy imports
@pytest.fixture(autouse=True)
def mock_heavy_imports(monkeypatch):
    mock_pystray = MagicMock()
    mock_pystray.Icon = _FakeIcon
    mock_pystray.Menu = _FakeMenu
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = _FakeMenuItem
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    import voice_typer.server.tray as tray_mod

    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)

    # #13: tray_menu.py is the new home for menu-building helpers.
    # It also imports pystray, so we need to mock it there too.
    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_menu_mod, "pystray", mock_pystray)

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())

    # Replace _make_icon with a stub that returns a sentinel object.
    # The original implementation called Image.open() on a real PNG, but
    # we don't need a real PIL image here — tray tests only verify that
    # _make_icon is invoked, not that the returned icon has pixels.
    # ``__import__("PIL.Image", ...)`` returns whatever is in
    # sys.modules["PIL.Image"] (the MagicMock set on line 112), so
    # ``_dummy_icon`` ends up being a MagicMock. That's fine because
    # the lambda on line 119 just hands it back to tray code, which
    # never inspects its contents.
    _real_image = __import__("PIL.Image", fromlist=["Image"])
    _dummy_icon = _real_image.new("RGBA", (64, 64), (0, 0, 0, 0))
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=0: _dummy_icon)


class _MockController:
    """Mock controller implementing the TrayController protocol."""

    def toggle_dictation(self) -> None:
        pass

    def change_microphone(self, mic_id: str | None) -> None:
        pass

    def change_model(self, model: str) -> None:
        pass

    def quit_app(self) -> None:
        pass

    # toggle_autostart, set_notifications, set_silence_*,
    # set_max_recording_time_seconds, create_desktop_shortcut removed from
    # TrayController protocol — no caller existed.

    # : undo_last added to TrayController protocol so the
    # tray menu's new "Undo Last" item can call it.
    def undo_last(self) -> None:
        pass

    def restart_app(self) -> None:
        pass


@pytest.fixture
def tray():
    _FakeIcon.last_kwargs = {}
    controller = _MockController()
    for method_name in [
        "toggle_dictation",
        "change_microphone",
        "change_model",
        "quit_app",
        "restart_app",
    ]:
        setattr(controller, method_name, MagicMock())
    t = TrayIcon(
        controller=controller,
        config=SimpleNamespace(
            hotkey="<f2>",
            model_size="small.en",
            autostart=True,
            show_notifications=True,
            microphone=None,
            silence_warning_seconds=20.0,
            stop_on_silence_seconds=120.0,
        ),
    )
    yield t
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnraisableExceptionWarning)
        del t


def _menu_labels(tray):
    """Helper to get menu item labels."""
    tray.start(bg_work=None)
    return [item.args[0] for item in _FakeIcon.last_kwargs["menu"]() if isinstance(item, _FakeMenuItem)]


# ─── Phase 2: Minimal menu tests ────────────────────────────────────────


class TestTrayMenuHasMinimalOptions:
    """Phase 2: Right-click menu has only Toggle, Restart, Quit."""

    def test_menu_has_toggle_dictation(self, tray):
        labels = _menu_labels(tray)
        assert any("Start Dictation" in label for label in labels)

    def test_menu_has_restart(self, tray):
        labels = _menu_labels(tray)
        assert "Restart" in labels

    def test_menu_has_quit(self, tray):
        labels = _menu_labels(tray)
        assert "Quit" in labels

    def test_menu_has_required_items(self, tray):
        """Phase 2 menu should have Start Dictation, Open App, Models, Restart, Quit."""
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        labels = [m.args[0] for m in menu_items]
        assert len(menu_items) >= 5
        assert any("Start Dictation" in lb for lb in labels)
        assert any("Open App" in lb for lb in labels)
        assert any("Restart" in lb for lb in labels)
        assert any("Quit" in lb for lb in labels)

    def test_toggle_label_includes_current_hotkey(self):
        controller = _MockController()
        tray = TrayIcon(
            controller=controller,
            config=SimpleNamespace(hotkey="<f9>", model_size="small.en", autostart=True, show_notifications=True),
        )
        labels = _menu_labels(tray)
        assert "Start Dictation (F9)" in labels

    def test_models_submenu_in_menu(self, tray):
        """Models submenu is now in the tray menu."""
        labels = _menu_labels(tray)
        assert any("Models" in lb for lb in labels)

    def test_microphone_submenu_in_menu(self, tray):
        """Microphone submenu is now in the tray menu.

        Previously (NEW-CQ-008) the mic list was a write-only no-op
        cache and there was no Microphone submenu. UX-2 re-adds the
        Microphones ▸ submenu mirroring the Models ▸ submenu.
        """
        labels = _menu_labels(tray)
        assert any("Microphone" in lb for lb in labels), "UX-2: tray menu should include a 'Microphones' submenu item"

    def test_undo_last_item_not_in_menu(self, tray):
        """C-TRAY-2: 'Undo Last' must NOT be in the tray menu."""
        labels = _menu_labels(tray)
        assert not any("Undo Last" in lb for lb in labels), (
            "C-TRAY-2 violation: 'Undo Last' must NOT appear in the tray menu"
        )

    def test_settings_history_help_items_in_menu(self, tray):
        """Settings/History/Help quick shortcuts present."""
        labels = _menu_labels(tray)
        assert any("Settings" in lb for lb in labels), "UX-33: tray menu should include a 'Settings' item"
        assert any("History" in lb for lb in labels), "UX-33: tray menu should include a 'History' item"
        assert any("Help" in lb for lb in labels), "UX-33: tray menu should include a 'Help' item"

    def test_force_cancel_not_in_menu_when_idle(self, tray):
        """the Force-cancel item is hidden when idle.

        Previously the item was always visible (cluttering the menu
        when nothing was stuck). Now it only renders when
        ``state == AppState.TRANSCRIBING``.

        the canonical tray label is now ``"Force cancel transcription"``
        (lowercase 'c') — the legacy ``force_cancel_stuck_transcription`` key
        has been removed. We substring-match on the canonical phrase.
        """
        # Default state is IDLE — Force cancel should NOT be in menu.
        labels = _menu_labels(tray)
        assert not any("Force cancel transcription" in lb for lb in labels), (
            "UX-3: Force cancel transcription should NOT appear when state==IDLE"
        )

    def test_no_advanced_submenu(self, tray):
        """Advanced settings are in Electron app, not tray menu."""
        labels = _menu_labels(tray)
        assert "Advanced" not in labels

    def test_no_hotkey_submenu(self, tray):
        """Hotkey config is in Electron app, not tray menu."""
        labels = _menu_labels(tray)
        assert "Hotkey" not in labels

    def test_no_start_on_login(self, tray):
        labels = _menu_labels(tray)
        assert "Start on Login" not in labels


# ─── Regression: menu= must be a pystray.Menu instance ──────────────────


class TestMenuIsPystrayMenuInstance:
    def test_menu_is_fake_menu_instance(self, tray):
        tray.start(bg_work=None)
        menu = _FakeIcon.last_kwargs.get("menu")
        assert isinstance(menu, _FakeMenu)

    def test_menu_callable_is_passed_to_menu_constructor(self, tray):
        tray.start(bg_work=None)
        menu = _FakeIcon.last_kwargs.get("menu")
        assert isinstance(menu, _FakeMenu)
        assert len(menu.args) >= 1
        assert callable(menu.args[0])


# ─── Threading model ────────────────────────────────────────────────────


class TestTrayStartIsNonBlocking:
    def test_start_returns_without_blocking(self, tray):
        bg_called = []

        def bg_work():
            bg_called.append(True)

        tray.start(bg_work=bg_work)
        assert not tray._icon._run_called
        time.sleep(0.1)
        assert len(bg_called) == 1

    def test_start_without_bg_work_does_not_crash(self, tray):
        tray.start(bg_work=None)
        assert tray._icon is not None


class TestTrayRunBlocksMainThread:
    def test_run_calls_icon_run(self, tray):
        tray.start(bg_work=None)
        assert not tray._icon._run_called
        tray.run()
        assert tray._icon._run_called


class TestTrayPendingState:
    def test_state_before_run_is_queued(self, tray):
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.LOADING, "Loading model...")
        assert len(tray._pending_states) == 1
        assert tray._pending_states[0] == (AppState.LOADING, "Loading model...")

    def test_pending_state_flushed_on_run(self, tray):
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.LOADING, "Starting...")
        tray.start(bg_work=None)
        tray.run()
        assert len(tray._pending_states) == 0
        assert tray._state == AppState.LOADING

    def test_notification_before_run_is_queued(self, tray):
        tray.notify("Title", "Message")
        assert len(tray._pending_notifications) == 1

    def test_pending_notification_flushed_on_run(self, tray):
        tray.notify("Test", "Hello")
        tray.start(bg_work=None)
        tray.run()
        assert len(tray._pending_notifications) == 0


# ─── Menu callable signature ────────────────────────────────────────────


class TestMenuCallableSignature:
    def test_menu_callable_takes_zero_positional_args(self, tray):
        tray.start(bg_work=None)
        menu = _FakeIcon.last_kwargs.get("menu")
        assert isinstance(menu, _FakeMenu)
        result = menu()
        assert result is not None

    def test_menu_materialization_works(self, tray):
        tray.start(bg_work=None)
        menu = _FakeIcon.last_kwargs.get("menu")
        assert isinstance(menu, _FakeMenu)
        items = menu()
        assert isinstance(items, tuple)
        assert len(items) > 0


# ─── Integration: full start + run cycle ────────────────────────────────


class TestFullStartRunCycle:
    def test_full_start_run_cycle_no_crash(self, tray):
        try:
            tray.start(bg_work=None)
            tray.run()
        except Exception as exc:
            pytest.fail(f"start() + run() cycle raised unexpectedly: {exc}")


# tray-unavailable fallback (Wayland / VOICE_TYPER_NO_TRAY) ──


class TestTrayUnavailableFallback:
    """PVT-G5-001: when the tray is unavailable (Wayland-without-SNI,
    headless session, or ``VOICE_TYPER_NO_TRAY=1``), ``run()`` MUST
    block the main thread on ``threading.Event`` instead of raising
    RuntimeError. ``stop()`` MUST release the event so the main thread
    can exit cleanly. Previously ``run()`` raised RuntimeError, which
    propagated to ``ipc_server.main()``'s except handler →
    ``sys.exit(EXIT_CRASH)`` — the app crashed immediately on
    Wayland/no-dbus Linux."""

    def test_run_does_not_raise_when_tray_unavailable(self, tray, monkeypatch):
        """When ``_tray_unavailable`` is True and ``_icon`` is None,
        ``run()`` MUST NOT raise RuntimeError. Instead it blocks on
        ``_run_event`` (released by ``stop()``)."""
        tray._tray_unavailable = True
        tray._icon = None
        # Release the event from a separate thread so run() returns
        # instead of blocking the test forever.
        import threading as _threading

        def _release_after():
            import time

            time.sleep(0.05)
            tray.stop()

        # capture the thread handle and join it after run()
        # returns so we don't leak a daemon Thread-without-join (the
        # thread has already fired tray.stop() by the time run()
        # returns, so the join is near-instant).
        _release_thread = _threading.Thread(target=_release_after, daemon=True)
        _release_thread.start()
        # Must not raise.
        tray.run()
        _release_thread.join(timeout=1.0)

    def test_stop_releases_blocked_run(self, tray):
        """``stop()`` sets ``_run_event`` so a ``run()`` blocked on
        the event returns promptly."""
        tray._tray_unavailable = True
        tray._icon = None
        import threading as _threading

        run_returned = _threading.Event()

        def _run_thread():
            tray.run()
            run_returned.set()

        t = _threading.Thread(target=_run_thread, daemon=True)
        t.start()
        # Give run() time to enter the _run_event.wait() call.
        time.sleep(0.05)
        tray.stop()
        assert run_returned.wait(timeout=1.0), "run() did not return within 1s after stop() — _run_event was not set"
        # Best-effort join so the daemon thread doesn't linger past the
        # test (run() has already returned — _run_thread only sets
        # run_returned after run() exits — so the join is near-instant).
        t.join(timeout=1.0)

    def test_voice_typer_no_tray_env_var_skips_icon_creation(self, tray, monkeypatch):
        """``VOICE_TYPER_NO_TRAY=1`` env var forces the tray-unavailable
        path: ``_icon`` stays None, ``_tray_unavailable`` is True, and
        ``bg_work`` is started on a daemon thread."""
        monkeypatch.setenv("VOICE_TYPER_NO_TRAY", "1")
        bg_called = []
        tray.start(bg_work=lambda: bg_called.append(True))
        assert tray._icon is None
        assert tray._tray_unavailable is True
        # bg_work was launched on a daemon thread — give it a moment
        # to fire.
        time.sleep(0.1)
        assert bg_called == [True], f"bg_work should have been called once on the daemon thread; got {bg_called}"

    def test_voice_typer_no_tray_env_var_other_value_does_not_skip(self, tray, monkeypatch):
        """Only the literal value ``1`` triggers the skip — ``0``,
        ``false``, ``no``, etc. fall through to normal tray creation."""
        monkeypatch.setenv("VOICE_TYPER_NO_TRAY", "0")
        tray.start(bg_work=None)
        # Normal path: icon was created, _tray_unavailable is False.
        assert tray._icon is not None
        assert tray._tray_unavailable is False

    def test_run_raises_when_start_never_called(self, tray):
        """PVT-G5-001: the RuntimeError path is retained when ``start()``
        was never called (``_icon`` is None AND
        ``_tray_unavailable`` is False). This signals a programming
        error rather than an unsupported environment."""
        assert tray._icon is None
        assert tray._tray_unavailable is False
        with pytest.raises(RuntimeError, match=r"call start\(\) before run\(\)"):
            tray.run()


# ─── _drain_pending fallback notification path  ────────────────


class TestDrainPending:
    """``_drain_pending`` is the fallback notification path
    invoked from ``run()`` every 60s when the tray is unavailable
    (Linux Wayland without SNI, Windows-Server headless,
    ``VOICE_TYPER_NO_TRAY=1``, or pystray.Icon() OSError fallback).

    Pre-fix symptom: queued ``notify_safety`` / ``notify`` calls were
    silently dropped on the tray-unavailable path — a 60s drain
    cleared the queue without surfacing the notification, so a
    critical notification (crash recovery failure, model load error)
    would never reach the user.

    The fixed path:
    1. Logs each notification at WARNING level (Python rotating file
       logger is always available — separate process from pystray).
    2. Publishes a ``tray_fallback_notification`` event via the event
       bus so the Electron renderer can surface it as a toast.
    3. Clears the queue (notification preserved via logs + Tauri
       channel — cannot be lost).
    4. Wraps the publish in ``contextlib.suppress(Exception)`` so a
       logging or event-bus failure cannot crash the tray's main loop.

    These tests pin behavior directly on ``_drain_pending`` rather
    than waiting 60s for ``run()`` to invoke it.
    """

    def test_drain_pending_empty_queue_is_noop(self, tray, monkeypatch):
        """an empty ``_pending_notifications`` queue is a noop —
        no log, no event-bus publish, no exception."""
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg, *a, **kw: published.append(msg) or True,
        )
        # Ensure queue is empty (fixture yields a fresh tray).
        assert tray._pending_notifications == []
        # Must not raise and must not publish anything.
        tray._drain_pending()
        assert published == [], f"_drain_pending with empty queue must not publish events; got: {published}"

    def test_drain_pending_publishes_each_notification(self, tray, monkeypatch):
        """each queued notification is published as a
        ``tray_fallback_notification`` event with the original title +
        message preserved."""
        published: list[dict] = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg, *a, **kw: published.append(msg) or True,
        )
        # Seed the queue with two notifications (the production path
        # appends (title, message) tuples under _queue_lock).
        with tray._queue_lock:
            tray._pending_notifications.append(("Crash Recovery", "Failed to recover"))
            tray._pending_notifications.append(("Model Load", "Could not load small.en"))

        tray._drain_pending()

        assert len(published) == 2, (
            f"_drain_pending must publish one event per queued notification; got {len(published)} events"
        )
        # Each event has the canonical envelope shape.
        assert published[0] == {
            "type": "tray_fallback_notification",
            "title": "Crash Recovery",
            "message": "Failed to recover",
        }, f"first event envelope mismatch; got: {published[0]!r}"
        assert published[1] == {
            "type": "tray_fallback_notification",
            "title": "Model Load",
            "message": "Could not load small.en",
        }, f"second event envelope mismatch; got: {published[1]!r}"
        # The queue was cleared as part of the drain.
        assert tray._pending_notifications == [], "_drain_pending must clear the queue after publishing"

    def test_drain_pending_swallows_event_bus_failure(self, tray, monkeypatch):
        """a failure inside ``event_bus.publish`` (or the log
        call) must NOT crash the tray's main loop — the publish is
        wrapped in ``contextlib.suppress(Exception)`` so the loop
        continues to the next notification.

        This is fail-safe: even if the event bus is broken, the
        notification has already been preserved via the WARNING log
        above (and the queue is cleared either way).
        """
        call_count = {"n": 0}

        def _flaky_publish(msg, *a, **kw):
            call_count["n"] += 1
            raise RuntimeError("event bus exploded")

        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            _flaky_publish,
        )
        # Seed the queue with multiple notifications so we can verify
        # the loop continues past the first failure.
        with tray._queue_lock:
            tray._pending_notifications.append(("First", "msg1"))
            tray._pending_notifications.append(("Second", "msg2"))
            tray._pending_notifications.append(("Third", "msg3"))

        # Must not raise — the suppress swallows every RuntimeError.
        tray._drain_pending()
        # Every notification was attempted (the loop didn't break
        # after the first failure).
        assert call_count["n"] == 3, (
            f"_drain_pending must attempt to publish every notification "
            f"even when event_bus.publish raises; only attempted "
            f"{call_count['n']} of 3"
        )
        # Queue was still cleared (drain is fail-safe — the dropped
        # notification has been preserved via the WARNING log).
        assert tray._pending_notifications == []

    def test_drain_pending_logs_warning_with_title_and_message(self, tray, monkeypatch):
        """each notification is logged at WARNING level with
        the full title + message so the user can grep their log file
        for the notification after the fact (the Python rotating file
        logger is always available — separate process from pystray)."""
        # Replace event_bus.publish with a noop so the test only
        # exercises the log path.
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg, *a, **kw: True,
        )
        # Capture log.warning calls on the tray module's logger.
        import voice_typer.server.tray as tray_mod

        warning_calls: list[tuple[str, object, object]] = []

        def _capturing_warning(msg, *args, **kwargs):
            warning_calls.append((msg, args, kwargs))
            # Don't actually call the real logger — keep the test
            # output clean.

        monkeypatch.setattr(tray_mod.log, "warning", _capturing_warning)

        with tray._queue_lock:
            tray._pending_notifications.append(("Model Load Error", "tiny.en not found"))

        tray._drain_pending()

        assert len(warning_calls) == 1, (
            f"_drain_pending must log one WARNING per notification; got {len(warning_calls)}"
        )
        msg_template, args, _kwargs = warning_calls[0]
        # The log message template mentions the tray fallback path.
        assert "TRAY" in msg_template or "tray" in msg_template.lower(), (
            f"warning log template should mention the tray fallback path; got: {msg_template!r}"
        )
        # The title + message are passed as positional args so they
        # appear in the formatted log line.
        assert "Model Load Error" in args, f"warning log args should include the notification title; got: {args!r}"
        assert "tiny.en not found" in args, f"warning log args should include the notification message; got: {args!r}"


# ─── Notification safety ────────────────────────────────────────────────


class TestNotifySafety:
    def test_notify_safety_bypasses_toggle(self, tray):
        """notify_safety() should send notification even when notifications disabled."""
        tray.set_notifications_enabled(False)
        tray.start(bg_work=None)
        tray.run()
        # Should not raise
        tray.notify_safety("Test", "Safety message")

    def test_notify_respects_toggle(self, tray):
        """notify() should be suppressed when notifications disabled."""
        tray.set_notifications_enabled(False)
        tray.start(bg_work=None)
        tray.run()
        # notify should not add to pending when disabled and icon exists
        tray.notify("Test", "Normal message")


# ─── open_electron_window: TCP push + fallback ─────────────────────────


class TestOpenElectronWindow:
    """open_electron_window() pushes show_window over TCP first, then
    falls back to Win32 focus, then to launching Electron.

    #13: The actual implementation now lives in tray_window.py.
    Tests mock at the tray_window module level.
    """

    def test_primary_path_pushes_show_window_over_tcp(self, tray, monkeypatch, caplog):
        """The primary path should push {"type": "show_window"} via TCP."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg) or True,
        )
        monkeypatch.setattr(
            "voice_typer.server.event_bus.has_live_transport",
            lambda: True,
        )
        with caplog.at_level("INFO", logger="voice_typer.server.tray_window"):
            tray.open_electron_window()
        assert len(pushed) == 1
        assert pushed[0] == {"type": "show_window"}
        # The delivery log must be specific about what happened (not a
        # vague "pushed" line): it names the trigger (tray left-click),
        # the transport (IPC), and what Electron will do (show + raise
        # + focus the dashboard).
        assert any(
            "Tray icon left-click" in rec.message and "show_window" in rec.message and "raise and focus" in rec.message
            for rec in caplog.records
        ), f"expected specific show_window delivery log, got: {[r.message for r in caplog.records]}"

    def test_falls_back_to_bring_electron_to_front(self, tray, monkeypatch):
        """When TCP push fails, should try bring_electron_to_front."""
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: False,
        )
        called = []
        import voice_typer.server.tray_window as tw_mod

        monkeypatch.setattr(
            tw_mod,
            "bring_electron_to_front",
            lambda: called.append(True) or True,
        )
        tray.open_electron_window()
        assert called

    def test_falls_back_to_launching_electron(self, tray, monkeypatch):
        """When TCP push and Win32 focus both fail, should launch Electron."""
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: False,
        )
        import voice_typer.server.tray_window as tw_mod

        monkeypatch.setattr(
            tw_mod,
            "bring_electron_to_front",
            lambda: False,
        )
        launched = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: launched.append(True) or MagicMock(),
        )
        tray.open_electron_window()
        assert launched

    def test_primary_path_skips_win32_and_launch(self, tray, monkeypatch):
        """When TCP push succeeds, neither Win32 focus nor launch should run."""
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: True,
        )
        win32_called = []
        import voice_typer.server.tray_window as tw_mod

        monkeypatch.setattr(
            tw_mod,
            "bring_electron_to_front",
            lambda: win32_called.append(True) or True,
        )
        launched = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: launched.append(True) or MagicMock(),
        )
        tray.open_electron_window()
        assert not win32_called
        assert not launched

    def test_publish_true_without_live_transport_falls_back_to_win32(self, tray, monkeypatch):
        """publish() returning True is NOT proof of delivery: the TCP push
        swallows write failures and unrelated subscribers accept every
        event, so the transport-liveness probe is the only truthful
        signal. When no probe reports a live host client (the exact
        production state when the Electron TCP connection is down), the
        push must NOT be treated as delivered and the Win32 focus
        fallback must run — regression: the tray "Open App" silently
        did nothing while the window stayed hidden/minimized."""
        from voice_typer.server.event_bus import (
            register_transport_probe,
            unregister_transport_probe,
        )

        # publish() reports success (as it always does — the IPC push
        # buffers silently when disconnected).
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: True,
        )

        # A registered transport whose client is NOT connected.
        def probe() -> bool:
            return False

        register_transport_probe(probe)
        try:
            called = []
            import voice_typer.server.tray_window as tw_mod

            monkeypatch.setattr(
                tw_mod,
                "bring_electron_to_front",
                lambda: called.append(True) or True,
            )
            tray.open_electron_window()
            assert called, "publish()==True with no live transport client must fall through to bring_electron_to_front"
        finally:
            unregister_transport_probe(probe)

    def test_publish_true_with_live_transport_skips_fallbacks(self, tray, monkeypatch):
        """When a transport probe reports a live host client, the push is
        genuinely delivered and neither fallback runs."""
        from voice_typer.server.event_bus import (
            register_transport_probe,
            unregister_transport_probe,
        )

        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: True,
        )

        def probe() -> bool:
            return True

        register_transport_probe(probe)
        try:
            win32_called = []
            import voice_typer.server.tray_window as tw_mod

            monkeypatch.setattr(
                tw_mod,
                "bring_electron_to_front",
                lambda: win32_called.append(True) or True,
            )
            launched = []
            monkeypatch.setattr(
                subprocess,
                "Popen",
                lambda *a, **kw: launched.append(True) or MagicMock(),
            )
            tray.open_electron_window()
            assert not win32_called
            assert not launched
        finally:
            unregister_transport_probe(probe)


class TestBringElectronToFront:
    """bring_electron_to_front() is a Win32-only fallback that handles
    hidden (close-to-tray) and minimized windows.

    #13: Now lives in tray_window.py as a standalone function.
    """

    def test_returns_false_on_non_windows(self, monkeypatch):
        """On non-Windows platforms, should return False immediately."""
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.tray_window import bring_electron_to_front

        result = bring_electron_to_front()
        assert result is False


# ── EO-16: duplicate-Electron-launch gate on macOS/Linux ─────────────────


class TestElectronDuplicateLaunchGate:
    """EO-16: when the TCP push AND the platform focus helper both fail
    but an Electron process is still alive, open_electron_window() must
    NOT spawn a second Electron — on macOS/Linux the focus fallback was
    previously a no-op (Win32-only) so a transient TCP blip launched a
    DUPLICATE process.
    """

    def _setup_dead_end(self, tray, monkeypatch):
        """TCP push fails + focus fails + launch would be observed."""
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: False,
        )
        import voice_typer.server.tray_window as tw_mod

        monkeypatch.setattr(
            tw_mod,
            "bring_electron_to_front",
            lambda: False,
        )
        launched = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: launched.append(True) or MagicMock(),
        )
        return launched

    def test_live_tracked_pid_blocks_duplicate_launch(self, tray, monkeypatch):
        """A tracked + alive Electron PID must suppress the launch even
        when focus failed (the window exists; it just couldn't be
        brought to front)."""
        import voice_typer.server.tray_window as tw_mod

        launched = self._setup_dead_end(tray, monkeypatch)
        monkeypatch.setattr(tw_mod, "_electron_pid", 12345)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._is_pid_alive",
            lambda pid: True,
        )
        tray.open_electron_window()
        assert not launched, "EO-16: a live Electron PID must block the duplicate launch"

    def test_dead_tracked_pid_allows_launch(self, tray, monkeypatch):
        """A tracked PID that is no longer alive must NOT block the
        launch — Electron genuinely isn't running, so the last-resort
        launch is correct."""
        import voice_typer.server.tray_window as tw_mod

        launched = self._setup_dead_end(tray, monkeypatch)
        monkeypatch.setattr(tw_mod, "_electron_pid", 99999)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._is_pid_alive",
            lambda pid: False,
        )
        tray.open_electron_window()
        assert launched, "EO-16: a dead Electron PID must NOT block the last-resort launch"

    def test_untracked_pid_allows_launch(self, tray, monkeypatch):
        """With no tracked PID (and no pgrep match), the launch proceeds
        — the pre-EO-16 behavior is preserved for the genuine
        not-running case."""
        import voice_typer.server.tray_window as tw_mod

        launched = self._setup_dead_end(tray, monkeypatch)
        monkeypatch.setattr(tw_mod, "_electron_pid", None)
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **kw: MagicMock(returncode=1, stderr=b""),
        )
        tray.open_electron_window()
        assert launched


# ── RELIABILITY-001: _wrap must not silently swallow SystemExit ──────────


class TestWrapSystemExitHandling:
    """RELIABILITY-001: the tray callback wrapper used to silently
    swallow ``SystemExit``, which forced ``quit_app`` and
    ``restart_app`` to use ``os._exit(0)`` to actually terminate the
    process.  That bypassed Python cleanup (atexit, __del__, finally),
    leaving the Win32 mutex unreleased, mic handles open, and hotkey
    registrations leaked.

    ERR-QUIT-002 (fix): ``_wrap`` now SUPPRESSES ``SystemExit`` instead
    of re-raising it. Since ``quit()`` and ``restart_app()`` both call
    ``self.tray.stop()`` before raising ``SystemExit``, the pystray
    event loop is already broken — re-raising caused pystray to print
    a confusing "error" traceback. Suppressing lets pystray see a clean
    return; its loop exits because ``stop()`` was called.
    """

    def test_wrap_suppresses_system_exit(self):
        """ERR-QUIT-002: SystemExit must be suppressed (not re-raised)
        so pystray doesn't print a traceback."""

        def cb_that_exits():
            raise SystemExit(0)

        wrapper = TrayIcon._wrap(cb_that_exits)
        # Should NOT raise — SystemExit is caught and suppressed.
        wrapper(icon=MagicMock(), item=MagicMock())

    def test_wrap_suppresses_system_exit_from_quit(self):
        """Simulates the real-world scenario: a controller's quit_app
        calls sys.exit(0), which raises SystemExit.  _wrap must suppress
        it so pystray doesn't print a traceback."""

        class _ControllerThatExits:
            def quit_app(self):
                raise SystemExit(0)

        ctrl = _ControllerThatExits()
        wrapper = TrayIcon._wrap(ctrl.quit_app)
        # Should NOT raise — SystemExit is caught and suppressed.
        wrapper(icon=MagicMock(), item=MagicMock())

    def test_wrap_passes_through_normal_callback(self):
        """Non-SystemExit callbacks should still work normally."""

        called = []

        def cb():
            called.append("yes")

        wrapper = TrayIcon._wrap(cb)
        wrapper(icon=MagicMock(), item=MagicMock())
        assert called == ["yes"]

    def test_wrap_propagates_non_system_exceptions(self):
        """Non-SystemExit exceptions must also propagate (not be
        converted to silent failures)."""

        def cb_that_errors():
            raise RuntimeError("boom")

        wrapper = TrayIcon._wrap(cb_that_errors)
        with pytest.raises(RuntimeError, match="boom"):
            wrapper(icon=MagicMock(), item=MagicMock())


# : Undo Last ABSENT from tray menu (C-TRAY-2) ─────────────


class TestUndoLastAbsentFromTrayMenu:
    """C-TRAY-2: no 'Undo Last' button in the tray menu.

    The ``undo_last`` IPC command remains wired (the renderer's Undo
    button + the undo hotkey still call it), but AGENTS.md forbids an
    'Undo Last' entry in the tray menu.
    """

    def test_undo_last_item_not_in_menu(self, tray):
        """No menu item is labelled 'Undo Last'."""
        labels = _menu_labels(tray)
        assert not any("Undo Last" in lb for lb in labels), (
            "C-TRAY-2 violation: 'Undo Last' must NOT appear in the tray menu"
        )

    def test_undo_last_not_above_force_cancel(self, tray):
        """With the undo item gone, the Force-cancel item still renders
        and there is no undo item above it."""
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.TRANSCRIBING, "transcribing")
        tray._menu_cache_valid = False  # force rebuild
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        labels = [str(m.args[0]) for m in menu_items]
        assert not any("Undo Last" in lb for lb in labels), "C-TRAY-2: undo_last must not be in the menu"
        assert any("Force cancel transcription" in lb for lb in labels), (
            "Force cancel transcription item must still render while transcribing"
        )


# : Microphone submenu + set_microphones cache ────────────


class TestMicrophoneSubmenu:
    """tray menu now includes a Microphones ▸ submenu
    that mirrors the Models ▸ submenu. ``set_microphones`` populates
    the cache and invalidates the menu cache so the next right-click
    reflects the current device set.
    """

    def test_set_microphones_caches_list(self, tray):
        """set_microphones stores the device list for the submenu builder."""
        mics = [
            {"id": "0", "name": "Built-in Mic", "default": True},
            {"id": "5", "name": "USB Mic", "default": False},
        ]
        tray.set_microphones(mics)
        assert tray._microphones == mics

    def test_set_microphones_invalidates_menu_cache(self, tray):
        """set_microphones sets _menu_cache_valid=False so the next
        right-click rebuilds the menu with the new device list."""
        tray._menu_cache_valid = True
        tray.set_microphones([{"id": "0", "name": "Mic"}])
        assert tray._menu_cache_valid is False

    def test_set_microphones_empty_list_safe(self, tray):
        """set_microphones with None or empty list should not crash."""
        tray.set_microphones(None)
        assert tray._microphones == []
        tray.set_microphones([])
        assert tray._microphones == []

    def test_mic_submenu_lists_devices(self, tray):
        """The Microphones ▸ submenu enumerates the cached devices."""
        tray.set_microphones(
            [
                {"id": "0", "name": "Built-in Mic", "default": True},
                {"id": "5", "name": "USB Mic", "default": False},
            ]
        )
        items = tray._build_microphones_submenu()
        # Each mic becomes a MenuItem + 1 separator + 1 "More microphones...".
        # _FakeMenuItem stores args; the label is args[0].
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        assert any("Built-in Mic" in lb for lb in labels)
        assert any("USB Mic" in lb for lb in labels)
        assert any("More microphones" in lb for lb in labels)

    def test_mic_submenu_marks_active_with_bullet(self, tray):
        """Active mic (matching config.microphone) is marked via
        ``checked`` (the platform-standard checkmark via pystray's
        ``checked=`` parameter — Win32 MF_CHECKED / macOS
        NSControlStateValueOn / GTK radio active). Pre- the active
        mic was prefixed with ``• `` in the label string, which
        bypassed the platform checkmark, broke screen-reader
        semantics, and misaligned with the Models submenu.

        pystray's ``checked`` must be a CALLABLE (it is invoked as
        ``checked(item)`` at render time; a raw bool raises
        ``ValueError`` at MenuItem construction, crashing the tray at
        startup). The callable is evaluated to the active-mic bool.
        """
        # Configure saved mic preference + set device list.
        tray._config = SimpleNamespace(hotkey="<f2>", model_size="small.en", microphone="5")
        tray.set_microphones(
            [
                {"id": "0", "name": "Built-in Mic", "default": True},
                {"id": "5", "name": "USB Mic", "default": False},
            ]
        )
        items = tray._build_microphones_submenu()
        # Find the USB Mic (id=5) item and assert it's checked.
        usb_items = [i for i in items if isinstance(i, _FakeMenuItem) and "USB Mic" in str(i.args[0])]
        assert usb_items, f"USB Mic menu item not found in {items!r}"
        usb_item = usb_items[0]
        checked_cb = usb_item.kwargs.get("checked")
        assert callable(checked_cb), (
            f"pystray checked= must be a callable (raw bool crashes the tray); got {checked_cb!r}"
        )
        assert checked_cb(None) is True, f"Active mic (USB Mic) checked() must return True; got {checked_cb(None)!r}"
        # The Built-in Mic (id=0, default=True but not the saved
        # pref) should NOT be marked active.
        builtin_items = [i for i in items if isinstance(i, _FakeMenuItem) and "Built-in Mic" in str(i.args[0])]
        assert builtin_items
        builtin_item = builtin_items[0]
        builtin_checked_cb = builtin_item.kwargs.get("checked")
        assert callable(builtin_checked_cb), f"pystray checked= must be a callable; got {builtin_checked_cb!r}"
        assert builtin_checked_cb(None) is False, (
            f"Non-active mic (Built-in Mic) checked() must return False; got {builtin_checked_cb(None)!r}"
        )

    def test_mic_submenu_click_calls_change_microphone(self, tray):
        """Clicking a mic in the submenu calls change_microphone(id)."""
        tray.set_microphones(
            [
                {"id": "0", "name": "Built-in Mic", "default": True},
                {"id": "7", "name": "Bluetooth", "default": False},
            ]
        )
        items = tray._build_microphones_submenu()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        # Find the Bluetooth mic item.
        bt_item = next((m for m in menu_items if "Bluetooth" in str(m.args[0])), None)
        assert bt_item is not None
        # Click it.
        cb = bt_item.args[1]
        cb(icon=MagicMock(), item=MagicMock())
        tray._controller.change_microphone.assert_called_once_with("7")


# : Force Cancel conditional on state == TRANSCRIBING ──────


class TestForceCancelConditional:
    """the Force-cancel transcription item is only
    rendered when ``state == AppState.TRANSCRIBING``. Previously it
    was always visible, cluttering the menu when nothing was stuck.

    the canonical tray label is now ``"Force cancel transcription"``
    (lowercase 'c'); the legacy ``force_cancel_stuck_transcription`` key
    (``"Force Cancel Stuck Transcription"``) was removed from
    ``tray_i18n.py`` so the tray menu and the renderer's
    ``home.forceCancelHint`` use the same canonical wording.
    """

    def test_force_cancel_hidden_when_idle(self, tray):
        """Force cancel is NOT in the menu when state==IDLE."""
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.IDLE)
        tray._menu_cache_valid = False
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        assert not any("Force cancel transcription" in lb for lb in labels), (
            "Force cancel transcription should NOT appear when state==IDLE"
        )

    def test_force_cancel_visible_when_transcribing(self, tray):
        """Force cancel IS in the menu when state==TRANSCRIBING."""
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.TRANSCRIBING, "transcribing")
        tray._menu_cache_valid = False
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        assert any("Force cancel transcription" in lb for lb in labels), (
            "Force cancel transcription SHOULD appear when state==TRANSCRIBING"
        )

    def test_force_cancel_label_renamed(self, tray):
        """the canonical tray label is 'Force cancel transcription'.

        The legacy ``force_cancel_stuck_transcription`` key ("Force Cancel
        Stuck Transcription") was removed from ``tray_i18n.py`` so the
        tray menu and the renderer's ``home.forceCancelHint`` use the
        same canonical wording.
        """
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.TRANSCRIBING, "transcribing")
        tray._menu_cache_valid = False
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        # Canonical label uses lowercase 'c' in "cancel".
        force_label = next(lb for lb in labels if "Force cancel transcription" in lb)
        assert "Stuck" not in force_label, (
            f"Label should NOT contain 'Stuck' (NH-17 canonical wording), got: {force_label!r}"
        )


# : Elapsed recording time in tooltip ────────────────────


class TestElapsedRecordingTooltip:
    """tray tooltip shows elapsed ``mm:ss`` when
    recording. A 1-second ``threading.Timer`` updates the tooltip.
    """

    def test_format_elapsed_zero(self):
        """0 seconds → '00:00'."""
        assert TrayIcon._format_elapsed(0) == "00:00"

    def test_format_elapsed_under_minute(self):
        """45 seconds → '00:45'."""
        assert TrayIcon._format_elapsed(45) == "00:45"

    def test_format_elapsed_over_minute(self):
        """125 seconds → '02:05'."""
        assert TrayIcon._format_elapsed(125) == "02:05"

    def test_format_elapsed_over_hour(self):
        """3725 seconds → '1:02:05'."""
        assert TrayIcon._format_elapsed(3725) == "1:02:05"

    def test_format_elapsed_negative_clamped_to_zero(self):
        """Negative input is clamped to 0 → '00:00'."""
        assert TrayIcon._format_elapsed(-5) == "00:00"

    def test_set_state_recording_starts_timer(self, tray):
        """Transitioning IDLE→RECORDING stores _recording_started_at
        and starts the elapsed timer."""
        from voice_typer.server.tray import AppState

        # Start the tray so _icon exists.
        tray.start(bg_work=None)
        tray.run()
        assert tray._elapsed_timer is None
        tray.set_state(AppState.RECORDING, "recording")
        assert tray._recording_started_at is not None
        assert tray._elapsed_timer is not None
        # Cleanup.
        tray.set_state(AppState.IDLE)

    def test_set_state_idle_stops_timer(self, tray):
        """Transitioning RECORDING→IDLE cancels the timer."""
        from voice_typer.server.tray import AppState

        tray.start(bg_work=None)
        tray.run()
        tray.set_state(AppState.RECORDING, "recording")
        assert tray._elapsed_timer is not None
        tray.set_state(AppState.IDLE)
        assert tray._elapsed_timer is None
        assert tray._recording_started_at is None

    def test_apply_state_includes_elapsed_when_recording(self, tray):
        """When state==RECORDING, _apply_state appends 'mm:ss' to title."""
        from voice_typer.server.tray import AppState

        tray.start(bg_work=None)
        tray.run()
        tray.set_state(AppState.RECORDING, "recording")
        title = tray._icon.title
        # Title should contain a '(' followed by mm:ss pattern.
        # The elapsed-time suffix is the LAST parenthesized group.
        assert "(" in title and ":" in title, f"Recording tooltip should include mm:ss, got: {title!r}"

    def test_apply_state_excludes_elapsed_when_idle(self, tray):
        """When state==IDLE, _apply_state does NOT append elapsed time."""
        from voice_typer.server.tray import AppState

        tray.start(bg_work=None)
        tray.run()
        tray.set_state(AppState.IDLE)
        title = tray._icon.title
        # The hotkey is in parens, so we can't just check for '(' —
        # we check that the title doesn't have a SECOND parenthesized
        # group (the elapsed mm:ss would be the second one).
        # Simple check: title should NOT contain a ':' from elapsed time.
        # The model name in brackets and hotkey in parens don't have ':'.
        # The state.value 'recording' doesn't have ':' either.
        # So if there's a ':', it must be from elapsed time.
        assert ":" not in title, f"IDLE tooltip should NOT include elapsed mm:ss, got: {title!r}"


# : _open_page generalization ────────────────────────────


class TestOpenPageGeneralization:
    """``_open_models_page`` was generalized into
    ``_open_page(path)`` so any in-app route can be opened from the
    tray menu. Used by the new Settings/History/Help shortcuts.
    """

    def test_open_page_exists(self, tray):
        """TrayIcon has an _open_page method."""
        assert hasattr(tray, "_open_page")
        assert callable(tray._open_page)

    def test_open_page_publishes_navigate_event(self, tray, monkeypatch):
        """_open_page pushes a navigate event with the given path."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg) or True,
        )
        tray._open_page("/settings")
        assert len(pushed) == 1
        assert pushed[0] == {"type": "navigate", "data": {"path": "/settings"}}

    def test_open_models_page_calls_open_page(self, tray, monkeypatch):
        """The legacy _open_models_page delegates to _open_page('/models')."""
        called_paths = []

        def fake_open_page(path):
            called_paths.append(path)

        monkeypatch.setattr(tray, "_open_page", fake_open_page)
        tray._open_models_page()
        assert called_paths == ["/models"]

    def test_open_settings_history_help_use_open_page(self, tray, monkeypatch):
        """The Settings/History/Help tray items route through _open_page."""
        called_paths = []

        def fake_open_page(path):
            called_paths.append(path)

        monkeypatch.setattr(tray, "_open_page", fake_open_page)
        # Build the menu so the lambdas are captured.
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        # Find Settings, History, Help items and click each.
        for label_substr, _expected_path in [
            ("Settings", "/settings"),
            ("History", "/history"),
            ("Help", "/about"),
        ]:
            item = next((m for m in menu_items if label_substr in str(m.args[0])), None)
            assert item is not None, f"{label_substr} item not found"
            cb = item.args[1]
            cb(icon=MagicMock(), item=MagicMock())
        assert called_paths == ["/settings", "/history", "/about"]


# I18N-2 : Tray locale support for all 8 renderer locales ──────


class TestTrayStateMessagesLocalized:
    """Every server tray STATE message follows the renderer locale.

    The renderer pushes ``trayState.*`` translations for ALL ``state.*``
    server keys via ``set_tray_locale`` (``trayLabelsForLocale`` in
    push.ts). ``merge_labels`` + ``set_locale`` (the handler's HU-17
    bridge) must make ``i18n.t`` resolve those keys — for the
    no-model-selected message AND the rest (recording_failed,
    model_not_downloaded, dictation-pipeline states, AppState labels).
    """

    def test_pipeline_state_keys_have_english_fallback(self):
        """The dictation-pipeline state keys ship English fallbacks so
        the tooltip is never a raw key before the first renderer push."""
        from voice_typer.server import i18n

        assert i18n.t("state.dictation_pipeline.clipboard_unavailable") == ("Done -- clipboard unavailable")
        assert i18n.t("state.dictation_pipeline.no_speech_detected") == ("No speech detected")
        assert i18n.t("state.dictation_pipeline.no_speech_check_mic") == ("No speech -- check microphone")
        assert i18n.t("state.dictation_pipeline.transcription_empty") == ("Transcription returned empty")

    def test_pushed_state_messages_resolve_via_merge(self):
        """A renderer push (merge_labels + set_locale, the exact HU-17
        bridge the set_tray_locale handler runs) makes i18n.t return the
        pushed localized text for the state messages."""
        from voice_typer.server import i18n

        labels = {
            # recording_controller
            "state.recording_controller.recording_failed": "Aufnahme fehlgeschlagen",
            "state.recording_controller.too_short": "Zu kurz \u2013 ignoriert",
            # model_manager (placeholder preserved for call-time format)
            "state.model_manager.model_not_downloaded": (
                "Keine Modelle verf\u00fcgbar. \u00d6ffnen Sie die Modelle-Seite."
            ),
            "state.model_manager.ready_whisper": "Bereit \u2013 {device_info}",
            # dictation pipeline
            "state.dictation_pipeline.transcription_empty": "Transkription leer",
        }
        try:
            i18n.merge_labels("zz", labels)
            i18n.set_locale("zz")
            assert i18n.t("state.recording_controller.recording_failed") == ("Aufnahme fehlgeschlagen")
            assert i18n.t("state.recording_controller.too_short") == ("Zu kurz \u2013 ignoriert")
            assert i18n.t("state.model_manager.model_not_downloaded") == (
                "Keine Modelle verf\u00fcgbar. \u00d6ffnen Sie die Modelle-Seite."
            )
            # placeholder still interpolated at call time
            assert i18n.t("state.model_manager.ready_whisper", device_info="CUDA") == "Bereit \u2013 CUDA"
            assert i18n.t("state.dictation_pipeline.transcription_empty") == ("Transkription leer")
            # untranslated key falls back to English (the F2 reference
            # was removed 2026-08-16 — the default hotkey is Caps Lock,
            # so the message is generic: "your hotkey")
            assert i18n.t("state.model_manager.loading") == ("Loading model -- press your hotkey to queue...")
        finally:
            i18n.set_locale("en")

    def test_tooltip_appstate_fallback_follows_locale(self, tray):
        """The AppState fallback suffix (``— <state.value>`` when no
        message is set) localizes via ``state.<value>`` keys pushed from
        the renderer — not just the per-call messages."""
        from voice_typer.server import i18n
        from voice_typer.server.tray import AppState

        try:
            i18n.merge_labels("zz", {"state.recording": "\u0631\u0642\u0645"})
            i18n.set_locale("zz")
            title = tray._compute_tooltip(AppState.RECORDING, "")
            assert "\u0631\u0642\u0645" in title, title
            # message wins over the fallback label when present
            i18n.merge_labels("zz", {"state.recording_controller.recording": "Aufnahme..."})
            title2 = tray._compute_tooltip(AppState.RECORDING, "Aufnahme...")
            assert "Aufnahme..." in title2
        finally:
            i18n.set_locale("en")


class TestTrayLocaleFullCoverage:
    """tray i18n now supports all 8 renderer locales
    (ar, de, en, es, fr, hi, ru, zh) — previously only en+es.
    """

    def test_register_tray_labels_adds_locale(self):
        """UX-6: a locale dict pushed from the renderer is registered and
        made active by set_tray_locale, even for a locale the server does
        not hard-code (en/es only by default).

        S1-de/fr/ru/zh/ar/hi are now pre-registered with full
        translations, so this test uses a locale code ("xx") that is
        NOT in _TRAY_LABELS_LOCALES to verify the push-from-renderer
        path still works for locales the server doesn't know about.
        """
        from voice_typer.server.tray import (
            _,
            get_tray_locale,
            register_tray_labels,
            set_tray_locale,
        )

        register_tray_labels("xx", {"open_app": "Open App XX", "quit": "Quit XX"})
        set_tray_locale("xx")
        assert get_tray_locale() == "xx"
        assert _("open_app") == "Open App XX"
        # Keys not in the pushed dict fall back to English.
        assert _("toggle_dictation") == "Start Dictation"
        # Restore default.
        set_tray_locale("en")

    def test_register_tray_labels_merges_over_existing(self):
        """Re-registering a locale merges new keys over the prior dict."""
        from voice_typer.server.tray import (
            _,
            register_tray_labels,
            set_tray_locale,
        )

        register_tray_labels("fr", {"open_app": "Ouvrir"})
        register_tray_labels("fr", {"quit": "Quitter"})
        set_tray_locale("fr")
        assert _("open_app") == "Ouvrir"
        assert _("quit") == "Quitter"
        set_tray_locale("en")

    def test_set_tray_locale_with_labels_dict(self):
        """The set_tray_locale IPC path accepts a `labels` dict and applies
        it: after the call, `_()` returns the pushed translation.

        S1-uses locale "yy" (not pre-registered) so the test
        verifies the push path without interference from the 8
        pre-registered locales (en/es/ar/de/fr/hi/ru/zh).
        """
        from voice_typer.server import tray as tray_mod

        labels = {"models": "Модели", "microphones": "Микрофоны"}
        tray_mod.register_tray_labels("yy", labels)
        tray_mod.set_tray_locale("yy")
        assert tray_mod._("models") == "Модели"
        assert tray_mod._("microphones") == "Микрофоны"
        # Untranslated key keeps the English default.
        assert tray_mod._("quit") == "Quit"
        tray_mod.set_tray_locale("en")

    def test_set_tray_locale_falls_back_to_en_for_unknown(self):
        """Unknown locale falls back to English (existing contract).

        S1-uses "zz" which is guaranteed not in the pre-registered
        locale set (en/es/ar/de/fr/hi/ru/zh) nor in any test-pushed locale.
        """
        from voice_typer.server.tray import _, get_tray_locale, set_tray_locale

        set_tray_locale("zz")
        assert get_tray_locale() == "en"
        assert _("quit") == "Quit"


# : update_available_body uses localized template ─────────


class TestUpdateAvailableBodyLocalized:
    """the update-available notification body now uses
    the ``update_available_body`` localized template instead of an
    inline f-string. This makes the body translatable.
    """

    def test_update_available_body_key_in_en_dict(self):
        """The EN locale dict has an 'update_available_body' template."""
        from voice_typer.server.tray import _TRAY_LABELS_EN

        assert "update_available_body" in _TRAY_LABELS_EN
        body = _TRAY_LABELS_EN["update_available_body"]
        assert "{app}" in body
        assert "{version}" in body
        assert "{current}" in body

    def test_update_available_body_localizes_for_es(self):
        """The ES locale dict has a translated update_available_body."""
        from voice_typer.server.tray import _TRAY_LABELS_ES, _, set_tray_locale

        assert "disponible" in _TRAY_LABELS_ES["update_available_body"]
        set_tray_locale("es")
        body = _("update_available_body").format(app="Voice Typer", version="2.0.0", current="1.0.0")
        assert "disponible" in body
        assert "2.0.0" in body
        set_tray_locale("en")

    def test_update_available_body_template_supports_reordering(self):
        """Translators can reorder placeholders for grammar (e.g. JA-style)."""
        # The template uses str.format() so {app}, {version}, {current}
        # can appear in any order.
        template = "{version} of {app} is available (current: {current})"
        body = template.format(app="Voice Typer", version="2.0.0", current="1.0.0")
        assert body == "2.0.0 of Voice Typer is available (current: 1.0.0)"


# --- VT-1: run() must degrade gracefully when the tray event loop
# fails at RUNTIME (e.g. pystray PermissionError creating the tray
# window in a restricted / non-interactive session). Pre-fix the
# exception propagated up through app.start() and crashed the WHOLE
# backend. ---


class TestRunDegradesOnRuntimeFailure:
    """``run()`` must catch a runtime failure of ``_icon.run()`` and
    degrade to the tray-unavailable blocking path instead of
    propagating the exception (which crashed the entire backend via
    ``app.start()`` -> ``[FATAL] app.start() raised``).

    Observed in the ``voice-typer`` terminal run (VT-1): pystray
    raised ``PermissionError: [WinError 5] Access is denied`` from
    ``_create_window``; ``start()`` only catches construction-time
    ``OSError``, so the runtime failure was unhandled.
    """

    def test_run_degrades_when_icon_run_raises(self, tray, monkeypatch):
        """When ``_icon.run()`` raises, ``run()`` must NOT propagate;
        it degrades to the tray-unavailable path (blocks on
        ``_run_event``, drained every 60s) and ``stop()`` releases it.
        """
        tray.start(bg_work=None)
        assert tray._icon is not None

        def _boom():
            raise PermissionError("[WinError 5] Access is denied")

        monkeypatch.setattr(tray._icon, "run", _boom)

        import threading as _threading

        run_returned = _threading.Event()

        def _run_thread():
            tray.run()
            run_returned.set()

        t = _threading.Thread(target=_run_thread, daemon=True)
        t.start()
        # Give run() time to hit the exception + enter the wait loop.
        time.sleep(0.1)
        tray.stop()
        assert run_returned.wait(timeout=1.0), (
            "run() did not return within 1s after stop() - the runtime "
            "failure must degrade to the _run_event blocking path"
        )
        # Best-effort join so the daemon thread doesn't linger past the
        # test (run() has already returned — _run_thread only sets
        # run_returned after run() exits — so the join is near-instant).
        t.join(timeout=1.0)
        # The tray must now be marked unavailable so downstream code
        # treats it as headless (hotkey + IPC still active).
        assert tray._tray_unavailable is True
        assert tray._icon is None

    def test_run_sets_unavailable_state_on_failure(self, tray, monkeypatch):
        """After a runtime failure, ``_tray_unavailable`` must be True
        and ``_icon`` None so a later ``stop()`` does not call
        ``icon.stop()`` on a torn-down icon."""
        tray.start(bg_work=None)

        def _boom():
            raise RuntimeError("event loop broke")

        monkeypatch.setattr(tray._icon, "run", _boom)
        tray.stop()  # stop() is idempotent and must not raise


# --- VT-1: notification truncation. pystray's Win32
# NOTIFYICONDATAW struct has szInfo=WCHAR*256 and szInfoTitle=WCHAR*64;
# an over-long message raised "string too long (466, maximum length
# 256)" and the toast was silently dropped. ---


class TestNotificationTruncation:
    """``_truncate_notification`` must cap the title at 64 chars and
    the message at 256 chars (the pystray Win32 NOTIFYICONDATAW
    limits), preserving the informative tail with a leading ellipsis.
    """

    def test_short_messages_unchanged(self):
        from voice_typer.server.tray_notifications import _truncate_notification

        title, message = _truncate_notification("Short title", "Short message")
        assert title == "Short title"
        assert message == "Short message"

    def test_long_message_truncated_to_256(self):
        from voice_typer.server.tray_notifications import _truncate_notification

        long_message = "x" * 466  # the exact length seen in the wild
        _, message = _truncate_notification("t", long_message)
        assert len(message) <= 256, f"message must be capped at 256; got {len(message)}"
        # The tail is preserved (the informative part) + ellipsis.
        assert message.endswith("x")
        assert message.startswith("...")

    def test_long_title_truncated_to_64(self):
        from voice_typer.server.tray_notifications import _truncate_notification

        long_title = "y" * 200
        title, _ = _truncate_notification(long_title, "m")
        assert len(title) <= 64, f"title must be capped at 64; got {len(title)}"
        assert title.endswith("y")
        assert title.startswith("...")

    def test_do_notify_passes_truncated_values_to_icon(self, tray, monkeypatch):
        """``do_notify`` must truncate BEFORE calling
        ``tray._icon.notify`` so a 466-char message no longer raises
        inside pystray and gets silently dropped.
        """
        tray.start(bg_work=None)
        captured = {}

        def _record(message, title):
            captured["message"] = message
            captured["title"] = title

        monkeypatch.setattr(tray._icon, "notify", _record)

        from voice_typer.server.tray_notifications import do_notify

        do_notify(tray, "Some title", "x" * 466)
        assert captured["message"], "icon.notify must have been called"
        assert len(captured["message"]) <= 256, (
            f"message passed to icon.notify must be <= 256 chars; got {len(captured['message'])}"
        )
        assert len(captured["title"]) <= 64


# ==============================================================================
# Merged from tests/test_tray_misc_perf_fixes.py —
#   tray/misc perf regression pins (CPU-fallback publishes tray state to Tauri, ElapsedTimer generation counter vs
#   stop/restart race, _push_bubble_config None-field defaults)
# ==============================================================================
# Regression tests for the three tray + misc server perf findings.
#
# Covers:
#
# 1. **tray_notifications.on_parakeet_cpu_fallback** must call
# ``tray._publish_tray_state()`` after ``_apply_state`` so the
# Tauri/Electron renderer's tray indicator picks up the
# "(CPU fallback)" tooltip suffix immediately. Pre-fix, only the
# pystray Icon got the suffix (via ``_apply_state``); the Tauri
# host stayed stale until the next ``_on_elapsed_tick`` (1 s later)
# or the next ``set_state`` call. On the tray-unavailable path
# (no pystray Icon), the suffix never reached the renderer at all.
#
# 2. **tray_elapsed_timer.ElapsedTimer** must use a generation counter
# so a rapid ``start()`` (stop/restart race) invalidates any
# in-flight ``_tick`` from a prior ``start()`` — preventing the
# stale tick from rescheduling a NEW Timer that overwrites the
# ``_timer`` reference set by the new ``start()``. Pre-fix, the
# stale tick would leak the just-scheduled Timer and break
# ``cancel()``'s join semantics.
#
# 3. **waveform_bubble_wiring._push_bubble_config** must use
# ``getattr(cfg, name, None) or default`` for non-None defaults so
# a Config with explicit ``None`` fields (e.g. partial / corrupt
# load) falls back to the documented default instead of
# propagating ``None`` to the bubble renderer. ``custom_theme``
# is exempt because ``None`` is its valid default.
#
# These tests are HEADLESS and SIDE-EFFECT-FREE: they perform no IPC,
# spawn no process, touch no sockets. They mock pystray at module
# level so the tray module imports cleanly without an X display.
#


class TestCpuFallbackPublishesToTauri:
    """``on_parakeet_cpu_fallback`` must publish state to Tauri after
    applying it to the pystray Icon.

    Pre-fix: only ``_apply_state`` was called, so the Tauri host's
    tray_state event stayed stale (the pystray Icon got the
    "(CPU fallback)" suffix but the renderer didn't). Post-fix:
    ``_publish_tray_state`` is also called, mirroring the pattern in
    ``_on_elapsed_tick``.
    """

    def test_publish_tray_state_called_after_apply_state(self, monkeypatch):
        """The fix calls ``_publish_tray_state`` after ``_apply_state``
        inside the same try/except — so the Tauri host receives the
        updated tooltip immediately."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._state = "RECORDING"
        tray._message = "recording"

        apply_calls: list[tuple] = []
        publish_calls: list[None] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: publish_calls.append(None))

        on_parakeet_cpu_fallback(
            tray,
            {"type": "parakeet_cpu_fallback", "data": {"device": "cpu", "reason": "cuda oom"}},
        )

        # _apply_state must have been called with the current state.
        assert len(apply_calls) == 1, f"Expected 1 _apply_state call, got {apply_calls}"
        assert apply_calls[0] == ("RECORDING", "recording")

        # _publish_tray_state must ALSO have been called (the fix).
        assert len(publish_calls) == 1, (
            f"Expected 1 _publish_tray_state call after the fix, got {publish_calls}. "
            "Pre-fix behavior was to only call _apply_state, leaving the Tauri host stale."
        )

        # _cpu_fallback_active flag must have been set.
        assert tray._cpu_fallback_active is True

    def test_publish_tray_state_failure_does_not_mask_apply_state(self, monkeypatch):
        """If ``_publish_tray_state`` raises, ``_apply_state`` must
        still have been called first (both calls are in the same
        try/except, so a publish failure is caught + logged, not
        propagated — but the apply already ran)."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._state = "IDLE"
        tray._message = ""

        apply_calls: list[tuple] = []
        monkeypatch.setattr(tray, "_apply_state", lambda s, m: apply_calls.append((s, m)))
        # _publish_tray_state raises — must be swallowed.
        monkeypatch.setattr(tray, "_publish_tray_state", lambda: (_ for _ in ()).throw(RuntimeError("publish boom")))

        # Must NOT raise — the try/except in on_parakeet_cpu_fallback
        # catches the publish failure.
        on_parakeet_cpu_fallback(
            tray,
            {"type": "parakeet_cpu_fallback", "data": {"device": "cpu"}},
        )

        # _apply_state ran first (before the publish that raised).
        assert len(apply_calls) == 1, (
            f"_apply_state must have been called even though _publish_tray_state raised; got {apply_calls}"
        )

    def test_ignores_non_dict_event(self, monkeypatch):
        """Malformed payloads (non-dict) are ignored — no state change."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._cpu_fallback_active = False

        on_parakeet_cpu_fallback(tray, "not a dict")  # type: ignore[arg-type]

        # _cpu_fallback_active must NOT have been set.
        assert tray._cpu_fallback_active is False
        tray._apply_state.assert_not_called()
        tray._publish_tray_state.assert_not_called()

    def test_ignores_wrong_event_type(self, monkeypatch):
        """Events with the wrong ``type`` are ignored."""
        from voice_typer.server.tray_notifications import on_parakeet_cpu_fallback

        tray = MagicMock()
        tray._cpu_fallback_active = False

        on_parakeet_cpu_fallback(tray, {"type": "some_other_event"})

        assert tray._cpu_fallback_active is False
        tray._apply_state.assert_not_called()
        tray._publish_tray_state.assert_not_called()


# ─── tray_elapsed_timer: generation counter prevents leak ────


class TestElapsedTimerGenerationCounter:
    """The generation counter prevents timer leaks on rapid stop/restart.

    Pre-fix: a ``_tick`` running concurrently with a rapid ``start()``
    would reschedule a NEW Timer that overwrites the ``_timer``
    reference set by the new ``start()`` — leaking the just-scheduled
    Timer and breaking ``cancel()``'s join semantics.

    Post-fix: ``start()`` increments ``_generation``; the ``_tick``
    closure captures the generation at entry and only reschedules if
    ``self._generation == my_gen``. A rapid ``start()`` invalidates
    prior ticks so they exit without rescheduling.
    """

    def test_generation_counter_starts_at_zero(self):
        """``_generation`` is initialized to 0 in ``__init__``."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=lambda: True,
            set_timer_ref=lambda t: None,
        )
        assert timer._generation == 0

    def test_start_increments_generation(self):
        """Each ``start()`` call increments ``_generation`` (cancel +1
        then the explicit +1, so a single ``start()`` is +2 — the
        generation is monotonically increasing, which is the only
        property the generation guard relies on)."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        assert timer._generation == 0
        try:
            timer.start()
            # start() calls cancel() (+1) then increments (+1) = +2.
            assert timer._generation > 0, f"start() must increment _generation (got {timer._generation})"
            gen_after_first_start = timer._generation
            timer.start()
            assert timer._generation > gen_after_first_start, (
                f"second start() must further increment _generation "
                f"(was {gen_after_first_start}, now {timer._generation})"
            )
        finally:
            timer.cancel()

    def test_cancel_increments_generation(self):
        """``cancel()`` increments ``_generation`` so any in-flight
        ``_tick`` from a prior ``start()`` exits without rescheduling."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        gen_after_start = timer._generation
        timer.cancel()
        assert timer._generation > gen_after_start, (
            f"cancel() must increment _generation (was {gen_after_start}, now {timer._generation})"
        )

    def test_rapid_restart_does_not_leak_timer_ref(self):
        """Rapid ``start()`` calls don't leave a stale ``_worker``
        reference — the new ``start()``'s worker is the sole owner.

        DJ-37 single-worker design: each ``start()`` cancels + joins
        the prior worker (via ``cancel()``) and increments the
        generation counter so any in-flight worker from a prior
        ``start()`` exits on its next ``is_active()`` / generation
        check. Pre-DJ-37, a concurrent ``_tick`` from the prior
        ``start()`` could overwrite ``self._timer`` with a freshly
        scheduled ``threading.Timer`` that the new ``start()`` didn't
        know about. The generation guard (combined with the explicit
        ``cancel()`` join) closes that leak.
        """
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        refs: list[threading.Thread | None] = []
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=refs.append,
        )

        timer.start()
        first_worker = timer._worker
        assert first_worker is not None
        assert isinstance(first_worker, threading.Thread)

        # Rapid restart — cancels + joins the first worker, then
        # increments generation before starting the new worker.
        timer.start()
        second_worker = timer._worker
        assert second_worker is not None
        assert second_worker is not first_worker, "Restart should create a NEW worker, not reuse the prior one"

        # ``cancel()`` (called inside the second ``start()``) joined
        # the first worker. The generation guard is a belt-and-suspenders
        # against any worker that escapes the join (e.g. is mid-tick).
        time.sleep(0.05)

        # _worker must still point at the second worker (not overwritten
        # by a stale tick from the first worker).
        assert timer._worker is second_worker, (
            "Stale worker from the first start() must NOT overwrite _worker "
            "(generation guard should have made it exit without re-entering the loop)"
        )

        timer.cancel()
        # After cancel, the worker ref is cleared.
        assert timer._worker is None

    def test_stale_tick_does_not_reschedule(self):
        """A ``_tick`` whose generation no longer matches exits without
        rescheduling — verified by checking the generation counter
        invalidated the prior tick's closure.

        Rather than try to deterministically reproduce the race
        (Timer fires concurrently with start()), we verify the
        GENERATION GUARD LOGIC by directly invoking a captured _tick
        closure after a newer start() has incremented _generation."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        refs: list[threading.Timer | None] = []
        timer = ElapsedTimer(
            tick_callback=lambda: None,
            is_active=active.is_set,
            set_timer_ref=refs.append,
        )

        # We can't easily capture the _tick closure from outside the
        # class (it's a local inside start()). Instead, verify the
        # invariant the generation guard relies on: each start() +
        # cancel() cycle monotonically increases _generation, so any
        # _tick captured at an earlier generation will see
        # ``self._generation != my_gen`` and exit without rescheduling.
        timer.start()
        gen_after_first_start = timer._generation
        # The first _tick closure captured my_gen = gen_after_first_start.
        # A subsequent cancel + start invalidates that closure.
        timer.cancel()
        timer.start()
        gen_after_second_start = timer._generation
        assert gen_after_second_start > gen_after_first_start, (
            "second start() must produce a strictly larger generation than "
            "the first — this is the invariant the generation guard relies on"
        )
        # The first _tick's my_gen (= gen_after_first_start) is now stale.
        # When it fires, ``self._generation != my_gen`` evaluates True
        # (gen_after_second_start > gen_after_first_start), so it exits
        # without rescheduling. This is the generation guard's contract.
        assert gen_after_second_start != gen_after_first_start

        timer.cancel()

    def test_normal_reschedule_still_works(self):
        """The generation guard doesn't break normal 1s rescheduling —
        a single ``start()`` continues ticking until ``cancel()``."""
        from voice_typer.server.tray_elapsed_timer import ElapsedTimer

        active = threading.Event()
        active.set()
        ticks: list[float] = []
        timer = ElapsedTimer(
            tick_callback=lambda: ticks.append(time.time()),
            is_active=active.is_set,
            set_timer_ref=lambda t: None,
        )
        timer.start()
        try:
            # Wait for at least 2 ticks (>= 2 seconds).
            deadline = time.monotonic() + 4.0
            while len(ticks) < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
            assert len(ticks) >= 2, f"Expected >= 2 ticks in 4s (normal reschedule path), got {len(ticks)}"
        finally:
            timer.cancel()


# ─── waveform_bubble_wiring: getattr returns default for None ──


class _CapturingWiring:
    """Minimal WaveformBubbleWiring stand-in that captures the
    ``bubble_config`` event published by ``_push_bubble_config``.

    We can't easily instantiate the real ``WaveformBubbleWiring``
    without a full app mock (it needs ``app._waveform_bubble``,
    ``app._thread_registry``, etc.). Instead we extract the
    ``_push_bubble_config`` closure from the wiring's source by
    calling ``_wire_waveform_bubble`` on a mock app and then
    invoking the installed ``on_config`` callback.
    """

    def __init__(self) -> None:
        from voice_typer.server.waveform import WaveformBubble
        from voice_typer.server.waveform_bubble_wiring import WaveformBubbleWiring

        self.bubble = WaveformBubble()
        app = MagicMock()
        app._waveform_bubble = self.bubble
        app._thread_registry = MagicMock()
        self.wiring = WaveformBubbleWiring(app)
        self.wiring._wire_waveform_bubble()

    def push_config(self, cfg: Any) -> dict | None:
        """Invoke the installed ``on_config`` callback with ``cfg``,
        capturing the published ``bubble_config`` event."""
        from voice_typer.server import event_bus

        captured: list[dict] = []

        def capture(msg: dict) -> None:
            if isinstance(msg, dict) and msg.get("type") == "bubble_config":
                captured.append(msg)

        # Snapshot + clear the subscriber set so we only see our capture.
        with event_bus._lock:
            original = set(event_bus._subscribers)
            event_bus._subscribers.clear()
            event_bus._subscribers.add(capture)
        try:
            assert self.bubble.on_config is not None
            self.bubble.on_config(cfg)
        finally:
            with event_bus._lock:
                event_bus._subscribers.clear()
                event_bus._subscribers.update(original)

        return captured[0] if captured else None

    def stop(self) -> None:
        self.wiring.stop()


class TestPushBubbleConfigGetattrDefault:
    """``_push_bubble_config`` must fall back to defaults when config
    fields are explicitly ``None``.

    Pre-fix: ``getattr(cfg, name, default)`` returned the attribute
    value even when it was ``None`` — so a Config with explicit
    ``None`` fields propagated ``None`` to the bubble renderer.

    Post-fix: ``getattr(cfg, name, None) or default`` falls back to
    the default for missing / null values.
    """

    def test_none_fields_fall_back_to_defaults(self):
        """When all non-None-default fields are ``None``, the
        ``bubble_config`` event carries the documented defaults."""
        wiring = _CapturingWiring()
        try:
            # A cfg where every field is explicitly None (e.g. a
            # partial / corrupt Config load).
            class _CfgWithNone:
                bubble_behavior = None  # type: ignore[assignment]
                bubble_click_to_toggle = None  # type: ignore[assignment]
                bubble_mic_button = None  # type: ignore[assignment]
                theme_mode = None  # type: ignore[assignment]
                theme_preset = None  # type: ignore[assignment]
                custom_theme = None

            event = wiring.push_config(_CfgWithNone())
            assert event is not None, "bubble_config event was not published"
            data = event["data"]

            # Each None field must fall back to its documented default.
            assert data["bubble_behavior"] == "show_on_record", (
                f"None bubble_behavior should fall back to 'show_on_record', got {data['bubble_behavior']!r}"
            )
            assert data["bubble_click_to_toggle"] is True, (
                f"None bubble_click_to_toggle should fall back to True, got {data['bubble_click_to_toggle']!r}"
            )
            assert data["bubble_mic_button"] is True, (
                f"None bubble_mic_button should fall back to True, got {data['bubble_mic_button']!r}"
            )
            assert data["theme_mode"] == "system", (
                f"None theme_mode should fall back to 'system', got {data['theme_mode']!r}"
            )
            assert data["theme_preset"] == "default", (
                f"None theme_preset should fall back to 'default', got {data['theme_preset']!r}"
            )
            # custom_theme: None IS the valid default — no fallback.
            assert data["custom_theme"] is None, (
                f"custom_theme=None should be preserved (None is its valid default), got {data['custom_theme']!r}"
            )
        finally:
            wiring.stop()

    def test_missing_attributes_fall_back_to_defaults(self):
        """When the cfg object lacks the attributes entirely (e.g. a
        MinimalMock), the defaults are used."""
        wiring = _CapturingWiring()
        try:
            # A cfg with NONE of the expected attributes.
            event = wiring.push_config(SimpleNamespace())
            assert event is not None
            data = event["data"]

            assert data["bubble_behavior"] == "show_on_record"
            assert data["bubble_click_to_toggle"] is True
            assert data["bubble_mic_button"] is True
            assert data["theme_mode"] == "system"
            assert data["theme_preset"] == "default"
            assert data["custom_theme"] is None
        finally:
            wiring.stop()

    def test_real_values_are_preserved(self):
        """When the cfg has real (non-None) values, they're preserved —
        the ``or default`` fallback only kicks in for None / missing."""
        wiring = _CapturingWiring()
        try:

            class _CfgWithValues:
                bubble_behavior = "always_visible"
                bubble_click_to_toggle = True
                bubble_mic_button = True
                theme_mode = "dark"
                theme_preset = "nord"
                custom_theme = {"light": {"--bg": "#fff"}, "dark": {"--bg": "#000"}}

            event = wiring.push_config(_CfgWithValues())
            assert event is not None
            data = event["data"]

            assert data["bubble_behavior"] == "always_visible"
            assert data["bubble_click_to_toggle"] is True
            assert data["bubble_mic_button"] is True
            assert data["theme_mode"] == "dark"
            assert data["theme_preset"] == "nord"
            assert data["custom_theme"] == {"light": {"--bg": "#fff"}, "dark": {"--bg": "#000"}}

        finally:
            wiring.stop()

    def test_empty_string_theme_preset_falls_back_to_default(self):
        """An empty-string ``theme_preset`` (which is falsy) falls back
        to ``"default"`` — the ``or default`` pattern treats empty
        string as a missing value, which is the intended behavior
        (an empty theme preset is not a valid value)."""
        wiring = _CapturingWiring()
        try:

            class _CfgWithEmpty:
                bubble_behavior = ""  # empty string → fallback
                bubble_click_to_toggle = True
                bubble_mic_button = True
                theme_mode = ""  # empty string → fallback
                theme_preset = ""  # empty string → fallback
                custom_theme = None

            event = wiring.push_config(_CfgWithEmpty())
            assert event is not None
            data = event["data"]

            # Empty strings fall back to defaults (``or`` treats "" as falsy).
            assert data["bubble_behavior"] == "show_on_record"
            assert data["theme_mode"] == "system"
            assert data["theme_preset"] == "default"
        finally:
            wiring.stop()
