"""FR-22 / FR-23 regression tests for the tray menu + icon locks."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.tray import TrayIcon  # noqa: E402
from voice_typer.server.tray_types import AppState  # noqa: E402


class _FakeMenu:
    """Lightweight stand-in for pystray.Menu (mirrors tests/test_tray.py)."""

    SEPARATOR = "SEP"

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeMenuItem:
    """Lightweight stand-in for pystray.MenuItem."""

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class _FakeIcon:
    """Stand-in pystray.Icon whose ``stop()`` simulates the teardown"""

    def __init__(self, **kwargs):
        self.menu = kwargs.get("menu")
        self.icon = kwargs.get("icon")
        self.title = kwargs.get("title", "")
        self._run_called = False
        self.stop_was_called = False
        self._torn_down = False

    def run(self):
        self._run_called = True

    def stop(self):
        self.stop_was_called = True
        self._torn_down = True

    def notify(self, *a, **kw):
        pass

    def _update_menu(self):
        # If the icon is torn down, _update_menu is a no-op (the real
        if self._torn_down:
            return

    def __setattr__(self, name, value):
        if getattr(self, "_torn_down", False) and name in ("icon", "title"):
            raise OSError("WinError 1402: invalid cursor handle (torn-down Icon)")
        super().__setattr__(name, value)


@pytest.fixture(autouse=True)
def _mock_pystray_for_tray(monkeypatch):
    """Install the fake pystray for both tray.py and tray_menu.py."""
    mock_pystray = MagicMock()
    mock_pystray.Icon = _FakeIcon
    mock_pystray.Menu = _FakeMenu
    mock_pystray.Menu.SEPARATOR = "SEP"
    mock_pystray.MenuItem = _FakeMenuItem
    monkeypatch.setitem(sys.modules, "pystray", mock_pystray)

    import voice_typer.server.tray as tray_mod
    import voice_typer.server.tray_menu as tray_menu_mod

    monkeypatch.setattr(tray_mod, "pystray", mock_pystray)
    monkeypatch.setattr(tray_menu_mod, "pystray", mock_pystray)

    # Stub _make_icon so it doesn't need real PIL.
    _dummy = MagicMock()
    monkeypatch.setattr(tray_mod, "_make_icon", lambda state, size=0: _dummy)


class _MockController:
    """Minimal TrayController for the tray menu build."""

    def __init__(self):
        self.toggle_dictation = MagicMock()
        self.change_microphone = MagicMock()
        self.change_model = MagicMock()
        self.quit_app = MagicMock()
        self.restart_app = MagicMock()
        self.undo_last = MagicMock()


def _make_tray() -> TrayIcon:
    """Build a real TrayIcon (full __init__) with a mock controller/config."""
    controller = _MockController()
    config = SimpleNamespace(
        hotkey="<f2>",
        model_size="small.en",
        autostart=True,
        show_notifications=True,
        microphone=None,
        silence_warning_seconds=20.0,
        stop_on_silence_seconds=120.0,
    )
    return TrayIcon(controller=controller, config=config)


class TestMenuLockDeclared:
    """FR-22: TrayIcon.__init__ must declare ``_menu_lock``."""

    def test_menu_lock_is_reentrant_rlock(self):
        """FR-22: ``_menu_lock`` must be re-entrant (RLock)."""
        tray = _make_tray()
        with tray._menu_lock, tray._menu_lock:
            pass  # RLock allows re-entrant acquisition from one thread
        # If we get here without deadlock, _menu_lock is re-entrant.

    def test_menu_lock_is_distinct_from_queue_lock(self):
        tray = _make_tray()
        assert tray._menu_lock is not tray._queue_lock, (
            "_menu_lock must be a separate Lock instance from _queue_lock "
            "(FR-22), sharing would serialize unrelated queue + menu paths."
        )


class _CallableMenuIcon:
    """Fake pystray icon whose ``_update_menu`` iterates a callable menu."""

    def __init__(self, tray):
        self._tray = tray

    def _update_menu(self):
        for _descriptor in self._tray._build_menu():
            pass


class TestConcurrentBuildAndInvalidateNoException:
    """FR-22: spawn N threads through build_menu_for_tray +"""

    def test_invalidate_menu_cache_with_live_icon_does_not_self_deadlock(self):
        """Regression: ``invalidate_menu_cache`` on a LIVE icon must not"""
        from voice_typer.server.tray_menu import invalidate_menu_cache

        tray = _make_tray()
        tray._microphones = [{"id": "mic1", "name": "Mic 1"}]
        tray._icon = _CallableMenuIcon(tray)

        done: list[bool] = []

        def invalidate():
            try:
                invalidate_menu_cache(tray)
                done.append(True)
            except Exception:  # noqa: BLE001, test surface for the regression
                done.append(False)

        t = threading.Thread(target=invalidate, daemon=True)
        t.start()
        t.join(timeout=5.0)
        assert not t.is_alive(), (
            "invalidate_menu_cache with a live callable-menu icon self-deadlocked "
            "on _menu_lock (>5s), the 15s command_timeout storm root cause (regression)."
        )
        assert done == [True], "invalidate_menu_cache raised or did not complete on a live icon."

    def test_concurrent_build_and_invalidate_no_exceptions(self):
        from voice_typer.server.tray_menu import (
            build_menu_for_tray,
            invalidate_menu_cache,
        )

        tray = _make_tray()
        # Set up enough state for build_menu_for_tray to succeed without
        tray._microphones = [{"id": "mic1", "name": "Mic 1"}]
        errors: list[Exception] = []
        stop = threading.Event()
        barrier = threading.Barrier(8)

        def builder():
            try:
                barrier.wait(timeout=5.0)
                iterations = 0
                while not stop.is_set() and iterations < 200:
                    build_menu_for_tray(tray)
                    iterations += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def invalidator():
            try:
                barrier.wait(timeout=5.0)
                iterations = 0
                while not stop.is_set() and iterations < 200:
                    invalidate_menu_cache(tray)
                    iterations += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=builder, name="menu-builder-1") for _ in range(4)] + [
            threading.Thread(target=invalidator, name="menu-invalidator-1") for _ in range(4)
        ]
        for t in threads:
            t.start()
        time.sleep(0.5)  # intentional fixed delay (stress-test duration)
        stop.set()
        for t in threads:
            t.join(timeout=3.0)
            assert not t.is_alive(), (
                f"Thread {t.name!r} still alive after 3s join, likely deadlocked on _menu_lock (FR-22 regression)."
            )
        assert not errors, f"concurrent build_menu_for_tray + invalidate_menu_cache raised: {errors}"

    def test_concurrent_builds_produce_consistent_menu(self):
        """Two concurrent builds must not corrupt _cached_menu, both"""
        from voice_typer.server.tray_menu import build_menu_for_tray

        tray = _make_tray()
        tray._microphones = []
        results: list = []
        errors: list[Exception] = []
        barrier = threading.Barrier(6)

        def builder():
            try:
                barrier.wait(timeout=5.0)
                for _ in range(50):
                    menu = build_menu_for_tray(tray)
                    results.append(menu)
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=builder, name=f"consistency-{i}") for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), f"Thread {t.name!r} deadlocked on _menu_lock."
        assert not errors, f"concurrent builds raised: {errors}"
        # Every result must be a tuple (the cache write is `tuple(items)`).
        assert all(isinstance(r, tuple) for r in results), (
            "build_menu_for_tray returned a non-tuple, cache was corrupted by a concurrent build (FR-22 regression)."
        )
        # After the storm, the cache must be valid + point at a tuple.
        assert tray._menu_cache_valid is True
        assert isinstance(tray._cached_menu, tuple)


class TestIconLockDeclared:
    """FR-23: TrayIcon.__init__ must declare ``_icon_lock`` as an RLock."""

    def test_icon_lock_is_rlock(self):
        tray = _make_tray()
        with tray._icon_lock, tray._icon_lock:
            pass  # RLock allows re-entrant acquisition
        # If we get here without deadlock, _icon_lock is re-entrant.

    def test_icon_lock_is_distinct_from_menu_lock(self):
        tray = _make_tray()
        assert tray._icon_lock is not tray._menu_lock, "_icon_lock must be a separate lock from _menu_lock (FR-23)."


class TestApplyStateStopRaceNoTornDownIconWrite:
    """torn-down Icon during ``stop()``."""

    def test_concurrent_apply_state_and_stop_no_torn_down_write(self):
        tray = _make_tray()
        tray.start(bg_work=None)
        tray.run()  # sets _icon._run_called; _icon is a _FakeIcon
        assert tray._icon is not None

        errors: list[Exception] = []
        stop_event = threading.Event()
        barrier = threading.Barrier(6)

        def applier():
            try:
                barrier.wait(timeout=5.0)
                iterations = 0
                while not stop_event.is_set() and iterations < 500:
                    tray._apply_state(AppState.RECORDING, "recording")
                    tray._apply_state(AppState.IDLE, "")
                    iterations += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def stopper():
            try:
                barrier.wait(timeout=5.0)
                for _ in range(50):
                    tray.stop()
                    tray._icon = _FakeIcon(
                        menu=_FakeMenu(lambda: ()),
                        icon=MagicMock(),
                        title="Lausu",
                    )
                    tray._last_applied_state = None
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=applier, name=f"applier-{i}") for i in range(5)] + [
            threading.Thread(target=stopper, name="stopper")
        ]
        for t in threads:
            t.start()
        time.sleep(1.0)  # intentional fixed delay (stress-test duration)
        stop_event.set()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), (
                f"Thread {t.name!r} still alive after 5s join, likely deadlocked on _icon_lock (FR-23 regression)."
            )
        torn_down_errors = [e for e in errors if isinstance(e, OSError)]
        assert not torn_down_errors, (
            f"_apply_state wrote to a torn-down Icon during stop(), "
            f"FR-23 race NOT fixed. OSError(s): {torn_down_errors}"
        )
        # Other exceptions (e.g. from re-arming _icon) are acceptable

    def test_stop_sets_icon_none_under_lock(self):
        """FR-23: after stop(), _icon must be None (the lock doesn't"""
        tray = _make_tray()
        tray.start(bg_work=None)
        tray.run()
        assert tray._icon is not None
        tray.stop()
        assert tray._icon is None, "stop() must set _icon = None (FR-23)."

    def test_apply_state_noop_when_icon_none(self):
        """FR-23: _apply_state must return early (no exception) when"""
        tray = _make_tray()
        # _icon is None before start(), _apply_state must no-op.
        tray._apply_state(AppState.RECORDING, "recording")
        # No exception raised = pass.


# Ensure no stale sys.modules entries leak between test modules.
