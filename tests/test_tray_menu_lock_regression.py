"""FR-22 / FR-23 regression tests for the tray menu + icon locks.

These tests guard the thread-safety fixes added for:

  * **FR-22** (Medium): ``tray_menu.py:build_menu_for_tray`` and
    ``invalidate_menu_cache`` read+write ``tray._cached_menu`` /
    ``tray._menu_cache_valid`` / ``tray._microphones`` without any
    lock. ``invalidate_menu_cache`` is called from background threads
    (e.g. ``set_microphones`` from the device watcher). On Windows,
    ``pystray.Icon._update_menu()`` calls ``DestroyMenu`` /
    ``CreatePopupMenu`` — not guaranteed thread-safe.
    Fix: ``tray._menu_lock`` (``threading.Lock``) serializes the
    check-then-build-then-cache sequence in ``build_menu_for_tray``
    AND the flag-clear + ``_update_menu()`` pair in
    ``invalidate_menu_cache``.

  * **FR-23** (Medium): ``tray.py:_apply_state`` + ``stop`` had no
    lock around ``self._icon`` access. Between ``self._icon.stop()``
    returning and ``self._icon = None`` executing, a concurrent
    ``_apply_state`` could read ``self._icon`` as non-None, then call
    ``self._icon.icon = ...`` on a torn-down Icon — the documented
    WinError 1402 trigger.
    Fix: ``tray._icon_lock`` (``threading.RLock``) serializes the
    ``if not self._icon: return`` + writes in ``_apply_state`` AND
    the ``stop()`` + ``= None`` pair in ``stop()``. ``_apply_state``
    re-checks ``self._icon`` INSIDE the lock so the caller's racy
    ``if self._icon:`` check (in ``set_state``) is not authoritative.

The concurrency stress tests below spawn N threads through the
production lock-using paths and assert no thread raises (no torn
reads, no AttributeError on a torn-down Icon) and no thread hangs
within the timeout (no deadlock).
"""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Mock pystray at module level so the tray module imports without an X
# display (headless CI). Mirrors tests/test_tray.py's approach.
_mock_pystray = MagicMock()
_mock_pystray.Menu = MagicMock
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

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
    """Stand-in pystray.Icon whose ``stop()`` simulates the teardown
    race window that FR-23 guards against.

    ``stop_was_called`` lets the test assert that a concurrent
    ``_apply_state`` did NOT touch the icon after ``stop()`` (which
    would be the WinError 1402 torn-down-Icon write).
    """

    def __init__(self, **kwargs):
        self.menu = kwargs.get("menu")
        self.icon = kwargs.get("icon")
        self.title = kwargs.get("title", "")
        self._run_called = False
        self.stop_was_called = False
        # race amplifier: when stop_was_called is True, any
        # subsequent attribute write raises (simulating a torn-down
        # Icon on Windows). Without the lock, _apply_state would hit
        # this after stop() returns but before _icon = None lands.
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
        # pystray would raise / log). The lock in invalidate_menu_cache
        # serializes this against build_menu_for_tray.
        if self._torn_down:
            return

    def __setattr__(self, name, value):
        # After stop(), writes to .icon / .title raise to simulate the
        # WinError 1402 torn-down-Icon symptom. This is the exact race
        # prevents: _apply_state must not write to a stopped Icon.
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


# menu cache lock ─────────────────────────────────────────────


class TestMenuLockDeclared:
    """FR-22: TrayIcon.__init__ must declare ``_menu_lock``."""

    def test_menu_lock_is_threading_lock(self):
        import threading

        tray = _make_tray()
        assert isinstance(tray._menu_lock, type(threading.Lock())), (
            f"tray._menu_lock must be a threading.Lock (FR-22). Got {type(tray._menu_lock)!r}."
        )

    def test_menu_lock_is_distinct_from_queue_lock(self):
        tray = _make_tray()
        assert tray._menu_lock is not tray._queue_lock, (
            "_menu_lock must be a separate Lock instance from _queue_lock "
            "(FR-22) — sharing would serialize unrelated queue + menu paths."
        )


class TestConcurrentBuildAndInvalidateNoException:
    """FR-22: spawn N threads through build_menu_for_tray +
    invalidate_menu_cache and assert no thread raises."""

    def test_concurrent_build_and_invalidate_no_exceptions(self):
        from voice_typer.server.tray_menu import (
            build_menu_for_tray,
            invalidate_menu_cache,
        )

        tray = _make_tray()
        # Set up enough state for build_menu_for_tray to succeed without
        # a real icon (it reads _microphones / _hotkey / _config / _state).
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
                    # invalidate_menu_cache reads tray._icon (None here)
                    # and calls maybe_publish_tray_menu — both safe headless.
                    invalidate_menu_cache(tray)
                    iterations += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=builder, name="menu-builder-1") for _ in range(4)] + [
            threading.Thread(target=invalidator, name="menu-invalidator-1") for _ in range(4)
        ]
        for t in threads:
            t.start()
        # Let them run for 0.5s; if any thread is stuck in a deadlock the
        # join(timeout=3) below will fail.
        time.sleep(0.5)  # intentional fixed delay (stress-test duration)
        stop.set()
        for t in threads:
            t.join(timeout=3.0)
            assert not t.is_alive(), (
                f"Thread {t.name!r} still alive after 3s join — likely deadlocked on _menu_lock (FR-22 regression)."
            )
        assert not errors, f"concurrent build_menu_for_tray + invalidate_menu_cache raised: {errors}"

    def test_concurrent_builds_produce_consistent_menu(self):
        """Two concurrent builds must not corrupt _cached_menu — both
        must return a tuple of MenuItems (no half-written state)."""
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
            "build_menu_for_tray returned a non-tuple — cache was corrupted by a concurrent build (FR-22 regression)."
        )
        # After the storm, the cache must be valid + point at a tuple.
        assert tray._menu_cache_valid is True
        assert isinstance(tray._cached_menu, tuple)


# icon lock ───────────────────────────────────────────────────


class TestIconLockDeclared:
    """FR-23: TrayIcon.__init__ must declare ``_icon_lock`` as an RLock."""

    def test_icon_lock_is_rlock(self):
        tray = _make_tray()
        # threading.RLock instances are not the RLock class directly
        # (factory returns a _RLock C object). Verify re-entrancy:
        # acquiring twice from the same thread must not deadlock.
        with tray._icon_lock, tray._icon_lock:
            pass  # RLock allows re-entrant acquisition
        # If we get here without deadlock, _icon_lock is re-entrant.

    def test_icon_lock_is_distinct_from_menu_lock(self):
        tray = _make_tray()
        assert tray._icon_lock is not tray._menu_lock, "_icon_lock must be a separate lock from _menu_lock (FR-23)."


class TestApplyStateStopRaceNoTornDownIconWrite:
    """FR-23: a concurrent ``_apply_state`` must NOT write to a
    torn-down Icon during ``stop()``.

    The ``_FakeIcon`` raises ``OSError`` (simulating WinError 1402) on
    any ``.icon`` / ``.title`` write AFTER ``stop()`` was called. If
    the lock is missing or the re-check inside the lock is removed,
    this test will surface the OSError.
    """

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
                    # _apply_state must either write to a LIVE icon or
                    # no-op after stop() — it must NEVER write to a
                    # torn-down icon (which raises OSError in _FakeIcon).
                    tray._apply_state(AppState.RECORDING, "recording")
                    tray._apply_state(AppState.IDLE, "")
                    iterations += 1
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def stopper():
            try:
                barrier.wait(timeout=5.0)
                for _ in range(50):
                    # stop() sets _icon = None under the lock. A
                    # concurrent _apply_state that reads _icon BEFORE
                    # the lock but writes AFTER must re-check inside
                    # the lock and bail out (no torn-down write).
                    tray.stop()
                    # Re-arm: restart the tray so the next stop() has a
                    # live icon to tear down. This maximizes the race
                    # window between _apply_state and stop().
                    tray._icon = _FakeIcon(
                        menu=_FakeMenu(lambda: ()),
                        icon=MagicMock(),
                        title="Voice Typer",
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
                f"Thread {t.name!r} still alive after 5s join — likely deadlocked on _icon_lock (FR-23 regression)."
            )
        torn_down_errors = [e for e in errors if isinstance(e, OSError)]
        assert not torn_down_errors, (
            f"_apply_state wrote to a torn-down Icon during stop() — "
            f"FR-23 race NOT fixed. OSError(s): {torn_down_errors}"
        )
        # Other exceptions (e.g. from re-arming _icon) are acceptable
        # for this test's purpose — the key assertion is no OSError
        # from a torn-down Icon write.

    def test_stop_sets_icon_none_under_lock(self):
        """FR-23: after stop(), _icon must be None (the lock doesn't
        change the observable post-condition, just the race-safety)."""
        tray = _make_tray()
        tray.start(bg_work=None)
        tray.run()
        assert tray._icon is not None
        tray.stop()
        assert tray._icon is None, "stop() must set _icon = None (FR-23)."

    def test_apply_state_noop_when_icon_none(self):
        """FR-23: _apply_state must return early (no exception) when
        _icon is None — the re-check inside the lock is the guard."""
        tray = _make_tray()
        # _icon is None before start() — _apply_state must no-op.
        tray._apply_state(AppState.RECORDING, "recording")
        # No exception raised = pass.


# Ensure no stale sys.modules entries leak between test modules.
