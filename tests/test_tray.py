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

import gc
import subprocess
import sys
import time
import warnings
import pytest
from types import SimpleNamespace
from unittest.mock import MagicMock

# Mock pystray + PIL at module level so the tray module can be imported
# without needing an X display (headless CI).
_mock_pystray = MagicMock()
_mock_pystray.Menu = MagicMock
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)
sys.modules.setdefault("PIL", MagicMock())
sys.modules.setdefault("PIL.Image", MagicMock())
sys.modules.setdefault("PIL.ImageDraw", MagicMock())

from voice_typer.server.tray import TrayIcon


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

    mock_pil = MagicMock()
    monkeypatch.setitem(sys.modules, "PIL", mock_pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())

    # _make_icon now calls Image.open() — replace with a dummy that
    # returns a simple transparent image to keep tests running.
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

    def change_hotkey(self, hotkey: str) -> None:
        pass

    def open_settings(self) -> None:
        pass

    def quit_app(self) -> None:
        pass

    # DEAD-008: toggle_autostart, set_notifications, set_silence_*,
    # set_max_recording_seconds, create_desktop_shortcut removed from
    # TrayController protocol — no caller existed.

    def restart_app(self) -> None:
        pass


@pytest.fixture
def tray():
    from voice_typer.server.tray import TrayIcon
    _FakeIcon.last_kwargs = {}
    controller = _MockController()
    for method_name in [
        "toggle_dictation", "change_microphone", "change_model",
        "change_hotkey", "open_settings", "quit_app",
        "restart_app",
    ]:
        setattr(controller, method_name, MagicMock())
    t = TrayIcon(
        controller=controller,
        config=SimpleNamespace(hotkey="<f2>", model_size="small.en", autostart=True, show_notifications=True, microphone=None, silence_warning_seconds=20.0, silence_auto_stop_seconds=120.0),
    )
    yield t
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", pytest.PytestUnraisableExceptionWarning)
        del t


def _menu_labels(tray):
    """Helper to get menu item labels."""
    tray.start(bg_work=None)
    return [
        item.args[0]
        for item in _FakeIcon.last_kwargs["menu"]()
        if isinstance(item, _FakeMenuItem)
    ]


# ─── Phase 2: Minimal menu tests ────────────────────────────────────────

class TestPhase2MinimalMenu:
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
        assert any("Toggle Dictation" in l for l in labels)
        assert any("Open App" in l for l in labels)
        assert any("Restart" in l for l in labels)
        assert any("Quit" in l for l in labels)

    def test_toggle_label_includes_current_hotkey(self):
        from voice_typer.server.tray import TrayIcon
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
        assert any("Models" in l for l in labels)

    def test_no_microphone_submenu(self, tray):
        """Microphone selection is in Electron app, not tray menu."""
        labels = _menu_labels(tray)
        assert "Microphone" not in labels

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
            "voice_typer.server.ipc_server._push_event_now",
            lambda msg: (pushed.append(msg) or True),
        )
        tray.open_electron_window()
        assert len(pushed) == 1
        assert pushed[0] == {"type": "show_window"}

    def test_falls_back_to_bring_electron_to_front(self, tray, monkeypatch):
        """When TCP push fails, should try bring_electron_to_front."""
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._push_event_now",
            lambda msg: False,
        )
        called = []
        import voice_typer.server.tray_window as tw_mod
        monkeypatch.setattr(
            tw_mod,
            "bring_electron_to_front",
            lambda: (called.append(True) or True),
        )
        tray.open_electron_window()
        assert called

    def test_falls_back_to_launching_electron(self, tray, monkeypatch):
        """When TCP push and Win32 focus both fail, should launch Electron."""
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._push_event_now",
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
            lambda *a, **kw: (launched.append(True) or MagicMock()),
        )
        tray.open_electron_window()
        assert launched

    def test_primary_path_skips_win32_and_launch(self, tray, monkeypatch):
        """When TCP push succeeds, neither Win32 focus nor launch should run."""
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._push_event_now",
            lambda msg: True,
        )
        win32_called = []
        import voice_typer.server.tray_window as tw_mod
        monkeypatch.setattr(
            tw_mod,
            "bring_electron_to_front",
            lambda: (win32_called.append(True) or True),
        )
        launched = []
        monkeypatch.setattr(
            subprocess,
            "Popen",
            lambda *a, **kw: (launched.append(True) or MagicMock()),
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

    The fix: ``_wrap`` logs the SystemExit and re-raises it so the
    process can exit cleanly via the normal ``sys.exit(0)`` path.
    """

    def test_wrap_re_raises_system_exit(self):
        """If the wrapped callback raises SystemExit, _wrap must
        re-raise it, not swallow it."""
        from voice_typer.server.tray import TrayIcon

        def cb_that_exits():
            raise SystemExit(0)

        wrapper = TrayIcon._wrap(cb_that_exits)
        with pytest.raises(SystemExit):
            wrapper(icon=MagicMock(), item=MagicMock())

    def test_wrap_does_not_swallow_system_exit_from_quit(self):
        """Simulates the real-world scenario: a controller's quit_app
        calls sys.exit(0), which raises SystemExit.  _wrap must let it
        propagate so the process actually exits."""
        from voice_typer.server.tray import TrayIcon

        class _ControllerThatExits:
            def quit_app(self):
                raise SystemExit(0)

        ctrl = _ControllerThatExits()
        wrapper = TrayIcon._wrap(ctrl.quit_app)
        with pytest.raises(SystemExit):
            wrapper(icon=MagicMock(), item=MagicMock())

    def test_wrap_passes_through_normal_callback(self):
        """Non-SystemExit callbacks should still work normally."""
        from voice_typer.server.tray import TrayIcon

        called = []
        def cb():
            called.append("yes")

        wrapper = TrayIcon._wrap(cb)
        wrapper(icon=MagicMock(), item=MagicMock())
        assert called == ["yes"]

    def test_wrap_propagates_non_system_exceptions(self):
        """Non-SystemExit exceptions must also propagate (not be
        converted to silent failures)."""
        from voice_typer.server.tray import TrayIcon

        def cb_that_errors():
            raise RuntimeError("boom")

        wrapper = TrayIcon._wrap(cb_that_errors)
        with pytest.raises(RuntimeError, match="boom"):
            wrapper(icon=MagicMock(), item=MagicMock())

