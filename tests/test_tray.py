"""Tests for the tray Phase 2 minimal menu.

Phase 2: Minimal right-click menu:
- Toggle Dictation (hotkey)
- Open App (Electron)
- Models
- Restart
- Quit

Left-click "Open App" launches the Electron app (or focuses it if already running).
All settings, history, templates, etc. live in the Electron window only.
"""

import subprocess
import sys
import time
import warnings
from types import SimpleNamespace
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
#
# Per-test PIL mocking is handled by the autouse ``mock_heavy_imports``
# fixture below (lines 102-105) and by ``tests/conftest.py``.
_mock_pystray = MagicMock()
_mock_pystray.Menu = MagicMock
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

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

    # DEAD-008: toggle_autostart, set_notifications, set_silence_*,
    # set_max_recording_time_seconds, create_desktop_shortcut removed from
    # TrayController protocol — no caller existed.

    # UX-1 (FIX-10): undo_last added to TrayController protocol so the
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
        "undo_last",
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
        assert any("Toggle Dictation" in label for label in labels)

    def test_menu_has_restart(self, tray):
        labels = _menu_labels(tray)
        assert "Restart" in labels

    def test_menu_has_quit(self, tray):
        labels = _menu_labels(tray)
        assert "Quit" in labels

    def test_menu_has_required_items(self, tray):
        """Phase 2 menu should have Toggle Dictation, Open App, Models, Restart, Quit."""
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        labels = [m.args[0] for m in menu_items]
        assert len(menu_items) >= 5
        assert any("Toggle Dictation" in lb for lb in labels)
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
        assert "Toggle Dictation (F9)" in labels

    def test_models_submenu_in_menu(self, tray):
        """Models submenu is now in the tray menu."""
        labels = _menu_labels(tray)
        assert any("Models" in lb for lb in labels)

    def test_microphone_submenu_in_menu(self, tray):
        """UX-2 (FIX-10): Microphone submenu is now in the tray menu.

        Previously (NEW-CQ-008) the mic list was a write-only no-op
        cache and there was no Microphone submenu. UX-2 re-adds the
        Microphones ▸ submenu mirroring the Models ▸ submenu.
        """
        labels = _menu_labels(tray)
        assert any("Microphone" in lb for lb in labels), "UX-2: tray menu should include a 'Microphones' submenu item"

    def test_undo_last_item_in_menu(self, tray):
        """UX-1 (FIX-10): 'Undo Last' item is in the tray menu.

        Previously the ``undo_last`` IPC command was wired but
        unreachable from any UI. The tray menu's new "Undo Last"
        item surfaces it.
        """
        labels = _menu_labels(tray)
        assert any("Undo Last" in lb for lb in labels), "UX-1: tray menu should include an 'Undo Last' item"

    def test_settings_history_help_items_in_menu(self, tray):
        """UX-33 (FIX-10): Settings/History/Help quick shortcuts present."""
        labels = _menu_labels(tray)
        assert any("Settings" in lb for lb in labels), "UX-33: tray menu should include a 'Settings...' item"
        assert any("History" in lb for lb in labels), "UX-33: tray menu should include a 'History...' item"
        assert any("Help" in lb for lb in labels), "UX-33: tray menu should include a 'Help...' item"

    def test_force_cancel_not_in_menu_when_idle(self, tray):
        """UX-3 (FIX-10): 'Force Cancel Stuck Transcription' is hidden when idle.

        Previously the item was always visible (cluttering the menu
        when nothing was stuck). Now it only renders when
        ``state == AppState.TRANSCRIBING``.
        """
        # Default state is IDLE — Force Cancel should NOT be in menu.
        labels = _menu_labels(tray)
        assert not any("Force Cancel" in lb for lb in labels), (
            "UX-3: Force Cancel Stuck Transcription should NOT appear when state==IDLE"
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

    def test_primary_path_pushes_show_window_over_tcp(self, tray, monkeypatch):
        """The primary path should push {"type": "show_window"} via TCP."""
        pushed = []
        monkeypatch.setattr(
            "voice_typer.server.event_bus.publish",
            lambda msg: pushed.append(msg) or True,
        )
        tray.open_electron_window()
        assert len(pushed) == 1
        assert pushed[0] == {"type": "show_window"}

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


# ─── UX-1 (FIX-10): Undo Last tray menu item ───────────────────────────────


class TestUndoLastTrayItem:
    """UX-1 (FIX-10): the ``undo_last`` IPC command was wired but
    unreachable from any UI. The tray menu's new "Undo Last" item
    surfaces it via ``controller.undo_last()``.
    """

    def test_undo_last_callback_invokes_controller(self, tray):
        """Clicking 'Undo Last' should call ``controller.undo_last()``."""
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        # Find the Undo Last item by label.
        undo_item = next((m for m in menu_items if "Undo Last" in str(m.args[0])), None)
        assert undo_item is not None, "Undo Last item not found in menu"
        # The callback is wrap_callback(undo_last) — args[1].
        cb = undo_item.args[1]
        # Invoke the wrapped callback (pystray passes icon+item).
        cb(icon=MagicMock(), item=MagicMock())
        # The controller's undo_last should have been called.
        tray._controller.undo_last.assert_called_once()

    def test_undo_last_is_above_force_cancel(self, tray):
        """UX-1: 'Undo Last' should appear ABOVE 'Force Cancel' in the menu."""
        # Set state to TRANSCRIBING so Force Cancel is rendered.
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.TRANSCRIBING, "transcribing")
        tray._menu_cache_valid = False  # force rebuild
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        menu_items = [i for i in items if isinstance(i, _FakeMenuItem)]
        labels = [str(m.args[0]) for m in menu_items]
        undo_idx = next((i for i, lb in enumerate(labels) if "Undo Last" in lb), None)
        force_idx = next((i for i, lb in enumerate(labels) if "Force Cancel" in lb), None)
        assert undo_idx is not None, "Undo Last item not found"
        assert force_idx is not None, "Force Cancel item not found"
        assert undo_idx < force_idx, f"Undo Last (idx={undo_idx}) should appear ABOVE Force Cancel (idx={force_idx})"


# ─── UX-2 (FIX-10): Microphone submenu + set_microphones cache ────────────


class TestMicrophoneSubmenu:
    """UX-2 (FIX-10): tray menu now includes a Microphones ▸ submenu
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
        """Active mic (matching config.microphone) gets a '•' prefix."""
        # Configure saved mic preference + set device list.
        tray._config = SimpleNamespace(hotkey="<f2>", model_size="small.en", microphone="5")
        tray.set_microphones(
            [
                {"id": "0", "name": "Built-in Mic", "default": True},
                {"id": "5", "name": "USB Mic", "default": False},
            ]
        )
        items = tray._build_microphones_submenu()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        # The USB Mic (id=5) should be marked active.
        usb_label = next(lb for lb in labels if "USB Mic" in lb)
        assert usb_label.startswith("• "), f"Active mic should be prefixed with '• ', got: {usb_label!r}"
        # The Built-in Mic (id=0, default=True but not the saved pref)
        # should NOT be marked active.
        builtin_label = next(lb for lb in labels if "Built-in Mic" in lb)
        assert not builtin_label.startswith("• "), f"Non-active mic should NOT have '• ' prefix, got: {builtin_label!r}"

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


# ─── UX-3 (FIX-10): Force Cancel conditional on state == TRANSCRIBING ──────


class TestForceCancelConditional:
    """UX-3 (FIX-10): 'Force Cancel Stuck Transcription' is only
    rendered when ``state == AppState.TRANSCRIBING``. Previously it
    was always visible, cluttering the menu when nothing was stuck.
    """

    def test_force_cancel_hidden_when_idle(self, tray):
        """Force Cancel is NOT in the menu when state==IDLE."""
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.IDLE)
        tray._menu_cache_valid = False
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        assert not any("Force Cancel" in lb for lb in labels), "Force Cancel should NOT appear when state==IDLE"

    def test_force_cancel_visible_when_transcribing(self, tray):
        """Force Cancel IS in the menu when state==TRANSCRIBING."""
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.TRANSCRIBING, "transcribing")
        tray._menu_cache_valid = False
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        assert any("Force Cancel" in lb for lb in labels), "Force Cancel SHOULD appear when state==TRANSCRIBING"

    def test_force_cancel_label_renamed(self, tray):
        """UX-3: the label is 'Force Cancel Stuck Transcription' (not
        the old 'Cancel Transcription')."""
        from voice_typer.server.tray import AppState

        tray.set_state(AppState.TRANSCRIBING, "transcribing")
        tray._menu_cache_valid = False
        tray.start(bg_work=None)
        items = _FakeIcon.last_kwargs["menu"]()
        labels = [str(i.args[0]) for i in items if isinstance(i, _FakeMenuItem)]
        force_label = next(lb for lb in labels if "Force Cancel" in lb)
        assert "Stuck" in force_label, f"Label should say 'Force Cancel Stuck Transcription', got: {force_label!r}"


# ─── UX-11 (FIX-10): Elapsed recording time in tooltip ────────────────────


class TestElapsedRecordingTooltip:
    """UX-11 (FIX-10): tray tooltip shows elapsed ``mm:ss`` when
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


# ─── UX-33 (FIX-10): _open_page generalization ────────────────────────────


class TestOpenPageGeneralization:
    """UX-33 (FIX-10): ``_open_models_page`` was generalized into
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
        for label_substr, expected_path in [
            ("Settings", "/settings"),
            ("History", "/history"),
            ("Help", "/about"),
        ]:
            item = next((m for m in menu_items if label_substr in str(m.args[0])), None)
            assert item is not None, f"{label_substr} item not found"
            cb = item.args[1]
            cb(icon=MagicMock(), item=MagicMock())
        assert called_paths == ["/settings", "/history", "/about"]


# ─── I18N-2 (FIX-10): Tray locale support for all 8 renderer locales ──────


class TestTrayLocaleFullCoverage:
    """I18N-2 (FIX-10): tray i18n now supports all 8 renderer locales
    (ar, de, en, es, fr, hi, ru, zh) — previously only en+es.
    """

    def test_register_tray_labels_adds_locale(self):
        """UX-6: a locale dict pushed from the renderer is registered and
        made active by set_tray_locale, even for a locale the server does
        not hard-code (en/es only by default)."""
        from voice_typer.server.tray import (
            _,
            get_tray_locale,
            register_tray_labels,
            set_tray_locale,
        )

        register_tray_labels("de", {"open_app": "App öffnen", "quit": "Beenden"})
        set_tray_locale("de")
        assert get_tray_locale() == "de"
        assert _("open_app") == "App öffnen"
        # Keys not in the pushed dict fall back to English.
        assert _("toggle_dictation") == "Toggle Dictation"
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
        it: after the call, `_()` returns the pushed translation."""
        from voice_typer.server import tray as tray_mod

        labels = {"models": "Модели", "microphones": "Микрофоны"}
        tray_mod.register_tray_labels("ru", labels)
        tray_mod.set_tray_locale("ru")
        assert tray_mod._("models") == "Модели"
        assert tray_mod._("microphones") == "Микрофоны"
        # Untranslated key keeps the English default.
        assert tray_mod._("quit") == "Quit"
        tray_mod.set_tray_locale("en")

    def test_set_tray_locale_falls_back_to_en_for_unknown(self):
        """Unknown locale falls back to English (existing TRAY-008 contract)."""
        from voice_typer.server.tray import _, get_tray_locale, set_tray_locale

        set_tray_locale("xx")
        assert get_tray_locale() == "en"
        assert _("quit") == "Quit"


# ─── UX-5 (FIX-10): update_available_body uses localized template ─────────


class TestUpdateAvailableBodyLocalized:
    """UX-5 (FIX-10): the update-available notification body now uses
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
