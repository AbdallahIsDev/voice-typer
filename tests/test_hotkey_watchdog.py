"""Tests for the  pynput watchdog restart logic, the  PTT
safety timeout, the  ``_registration_degraded`` property, and the
 LL hook queue maxsize increase.

These tests run on Linux without pynput/Windows deps — the listener is
mocked so the watchdog's restart path is exercised in isolation.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher
from voice_typer.server.keyboard_ownership import keyboard_ownership

# ─── Fixtures ────────────────────────────────────────────────────────────


def _make_mock_app() -> SimpleNamespace:
    """Build a minimal mock app satisfying the HotkeyDispatcher contract."""
    app = SimpleNamespace()
    app.config = SimpleNamespace(
        hotkey="<f2>",
        recording_mode="toggle",
        esc_cancel_enabled=False,
        repaste_hotkey=None,
    )
    app.tray = MagicMock()
    app._stop_dictation = MagicMock()
    app.toggle_dictation = MagicMock()
    app._cancel_dictation = MagicMock()
    app.repaste_last = MagicMock()
    app._esc_cancel_paused = False
    app._shutting_down = False
    return app


@pytest.fixture
def dispatcher() -> HotkeyDispatcher:
    app = _make_mock_app()
    return HotkeyDispatcher(app)


@pytest.fixture(autouse=True)
def _reset_keyboard_ownership():
    keyboard_ownership().reset()
    yield
    keyboard_ownership().reset()


# ─── PynputHotkey liveness watchdog ──────────────────────────────


class TestPynputWatchdog:
    """the pynput backend's watchdog thread must detect a dead
    listener and attempt a restart, surfacing a tray notification after
    5 consecutive failures."""

    def test_watchdog_restarts_dead_listener(self, monkeypatch):
        """When ``self._listener.is_alive()`` returns False, the watchdog
        calls ``_start_listener`` (which re-runs the GlobalHotKeys →
        fallback chain) and resets the failure counter on success."""
        from voice_typer.server.hotkeys.pynput_backend import PynputHotkey

        backend = PynputHotkey("<f2>")
        # Short poll interval so the test runs fast.
        backend._WATCHDOG_POLL_INTERVAL_SECONDS = 0.05
        backend._WATCHDOG_MAX_FAILURES = 5

        # Track restart calls. The watchdog calls ``_start_listener``
        # when it detects a dead listener.
        call_count = {"restarts": 0}

        def fake_start_listener(callback):
            call_count["restarts"] += 1
            # Simulate a successful restart: set up a healthy fake listener.
            healthy = MagicMock()
            healthy.is_alive.return_value = True
            backend._listener = healthy
            return True

        backend._start_listener = fake_start_listener

        # Seed an initial DEAD listener so the watchdog's first poll
        # detects death and triggers a restart.
        dead = MagicMock()
        dead.is_alive.return_value = False
        backend._listener = dead
        backend._user_callback = lambda: None
        backend._watchdog_stop_event.clear()
        backend._watchdog_failure_count = 0

        backend._start_watchdog()
        try:
            # Wait for the watchdog to notice the dead listener and restart.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if call_count["restarts"] >= 1:
                    break
                time.sleep(0.02)
            assert call_count["restarts"] >= 1, (
                f"Watchdog should have restarted the listener at least once "
                f"after it died; got {call_count['restarts']} _start_listener calls"
            )
            # After a successful restart, the failure counter resets.
            assert backend._watchdog_failure_count == 0, "Failure counter should reset to 0 after a successful restart"
        finally:
            backend.stop()

    def test_watchdog_surfaces_notification_after_max_failures(self, monkeypatch):
        """After ``_WATCHDOG_MAX_FAILURES`` consecutive restart failures,
        the watchdog surfaces a tray notification via ``self._tray`` and
        stops retrying."""
        from voice_typer.server.hotkeys.pynput_backend import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend._WATCHDOG_POLL_INTERVAL_SECONDS = 0.02
        backend._WATCHDOG_MAX_FAILURES = 3

        # Attach a mock tray so we can capture the notification.
        tray = MagicMock()
        backend._tray = tray

        # Make _start_listener always fail (returns False, no listener set).
        backend._start_listener = MagicMock(return_value=False)

        # _stop_listener should be safe to call when _listener is None.
        # (already the case)

        try:
            # Manually arm the watchdog without calling start() (we
            # don't want a real listener). Set the callback so the
            # watchdog attempts restart.
            backend._user_callback = lambda: None
            backend._watchdog_stop_event.clear()
            backend._watchdog_failure_count = 0
            backend._start_watchdog()
            # Wait for the watchdog to hit max failures and surface the
            # notification.
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if tray.notify_safety.called or tray.notify.called:
                    break
                time.sleep(0.02)
            # The tray notification must have been called.
            assert tray.notify_safety.called or tray.notify.called, (
                "Watchdog should have surfaced a tray notification after "
                f"{backend._WATCHDOG_MAX_FAILURES} consecutive restart failures"
            )
            # The failure counter must be at or above max.
            assert backend._watchdog_failure_count >= backend._WATCHDOG_MAX_FAILURES
        finally:
            backend.stop()

    def test_watchdog_resets_failure_count_on_recovery(self, monkeypatch):
        """When the listener recovers (is_alive returns True), the
        watchdog resets the failure counter to 0."""
        from voice_typer.server.hotkeys.pynput_backend import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend._WATCHDOG_POLL_INTERVAL_SECONDS = 0.02
        backend._WATCHDOG_MAX_FAILURES = 5

        # Start with a healthy listener.
        healthy_listener = MagicMock()
        healthy_listener.is_alive.return_value = True
        backend._listener = healthy_listener
        backend._user_callback = lambda: None
        backend._watchdog_failure_count = 3  # simulate prior failures

        backend._watchdog_stop_event.clear()
        backend._start_watchdog()
        try:
            # Wait one poll cycle.
            time.sleep(0.1)
            assert backend._watchdog_failure_count == 0, (
                "Watchdog should reset failure count to 0 when listener is healthy"
            )
        finally:
            backend.stop()

    def test_stop_signals_watchdog_to_exit(self):
        """``stop()`` must set ``_watchdog_stop_event`` so the watchdog
        thread exits promptly (not after the full 30s poll interval)."""
        from voice_typer.server.hotkeys.pynput_backend import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend._WATCHDOG_POLL_INTERVAL_SECONDS = 30.0  # long interval
        backend._user_callback = lambda: None
        # Don't call start() — we only want to test the watchdog exit.
        backend._watchdog_stop_event.clear()
        backend._start_watchdog()
        assert backend._watchdog_thread is not None
        assert backend._watchdog_thread.is_alive()
        # stop() should signal the watchdog and join within 2s.
        start = time.monotonic()
        backend.stop()
        elapsed = time.monotonic() - start
        assert elapsed < 2.5, f"stop() should join the watchdog within 2s; took {elapsed:.1f}s"
        # The watchdog thread should no longer be alive (or None after
        # stop() clears it).
        assert backend._watchdog_thread is None or not backend._watchdog_thread.is_alive()


# ─── PTT safety timeout ──────────────────────────────────────────


class TestPTTSafetyTimeout:
    """the dispatcher arms a 60s PTT safety timer that auto-stops
    dictation and surfaces a tray notification if the release event is
    missed."""

    def test_ptt_timer_armed_in_push_to_talk_mode(self, dispatcher: HotkeyDispatcher):
        """In push_to_talk mode, ``_create_and_start_main_backend`` arms
        the PTT safety timer."""
        dispatcher._app.config.recording_mode = "push_to_talk"
        # Use a very short timeout so the test doesn't wait 60s.
        dispatcher._PTT_SAFETY_TIMEOUT_SECONDS = 100.0  # won't fire in test
        # Mock create_hotkey_backend so no real backend is created.
        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True
        dispatcher._app.config.hotkey = "<f2>"
        # Patch the module-level create_hotkey_backend reference.
        import voice_typer.server.hotkey_dispatcher as hd_mod

        orig_factory = hd_mod.create_hotkey_backend
        hd_mod.create_hotkey_backend = lambda spec, role=None: fake_backend
        try:
            dispatcher._create_and_start_main_backend("<f2>")
            assert dispatcher._ptt_safety_timer is not None, "PTT safety timer should be armed in push_to_talk mode"
        finally:
            hd_mod.create_hotkey_backend = orig_factory
            dispatcher._cancel_ptt_safety_timer()

    def test_ptt_timer_not_armed_in_toggle_mode(self, dispatcher: HotkeyDispatcher):
        """In toggle mode, no PTT safety timer is armed."""
        dispatcher._app.config.recording_mode = "toggle"
        fake_backend = MagicMock()
        fake_backend.is_alive.return_value = True
        import voice_typer.server.hotkey_dispatcher as hd_mod

        orig_factory = hd_mod.create_hotkey_backend
        hd_mod.create_hotkey_backend = lambda spec, role=None: fake_backend
        try:
            dispatcher._create_and_start_main_backend("<f2>")
            assert dispatcher._ptt_safety_timer is None, "PTT safety timer should NOT be armed in toggle mode"
        finally:
            hd_mod.create_hotkey_backend = orig_factory

    def test_ptt_timeout_auto_stops_and_notifies(self, dispatcher: HotkeyDispatcher):
        """When the PTT safety timer fires, it calls
        ``app._stop_dictation`` and ``tray.notify_safety``."""
        dispatcher._PTT_SAFETY_TIMEOUT_SECONDS = 0.05  # fire quickly
        dispatcher._start_ptt_safety_timer()
        # Wait for the timer to fire.
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if dispatcher._app._stop_dictation.called:
                break
            time.sleep(0.02)
        assert dispatcher._app._stop_dictation.called, "PTT safety timeout should call app._stop_dictation"
        assert dispatcher._app.tray.notify_safety.called, "PTT safety timeout should surface a tray notification"
        # The notification message should mention the 60s timeout (the
        # message hardcodes "60s" — the test uses a shorter timeout but
        # the message is the same).
        call_args = dispatcher._app.tray.notify_safety.call_args
        message = call_args[0][1] if call_args[0] else call_args[1].get("message", "")
        assert "PTT" in message or "60s" in message, f"Tray notification should mention PTT/60s; got: {message!r}"

    def test_cancel_ptt_timer_is_noop_when_not_armed(self, dispatcher: HotkeyDispatcher):
        """``_cancel_ptt_safety_timer`` must be safe to call when no
        timer is armed (no AttributeError)."""
        # _ptt_safety_timer is None (initialized in __init__).
        dispatcher._cancel_ptt_safety_timer()  # must not raise
        assert dispatcher._ptt_safety_timer is None

    def test_stop_all_cancels_ptt_timer(self, dispatcher: HotkeyDispatcher):
        """``stop_all`` must cancel any armed PTT safety timer."""
        dispatcher._PTT_SAFETY_TIMEOUT_SECONDS = 100.0  # won't fire
        dispatcher._start_ptt_safety_timer()
        assert dispatcher._ptt_safety_timer is not None
        dispatcher.stop_all()
        assert dispatcher._ptt_safety_timer is None, "stop_all should cancel the PTT safety timer"


# ─── WindowsNativeHotkey _registration_degraded ──────────────────


class TestRegistrationDegradedProperty:
    """``WindowsNativeHotkey`` must expose a
    ``_registration_degraded`` property that returns True when
    RegisterHotKey failed but a fallback (LL hook or polling) kept the
    hotkey functional."""

    def test_property_returns_false_before_start(self):
        """Before ``start()``, the property must return False (no
        registration has been attempted yet)."""
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        assert backend._registration_degraded is False

    def test_degraded_flag_attribute_exists(self):
        """The ``_degraded_registration`` attribute must exist after
        ``__init__`` so the property doesn't raise."""
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        assert hasattr(backend, "_degraded_registration")
        assert backend._degraded_registration is False


# ─── LL hook queue maxsize increase ──────────────────────────────


class TestLLHookQueueMaxsize:
    """the LL hook callback queue maxsize must be 256 (up from
    64) so a brief worker stall doesn't drop callbacks."""

    def test_queue_maxsize_is_256(self):
        from voice_typer.server.hotkeys.windows_native import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        assert backend._hook_callback_queue.maxsize == 256, (
            f"LL hook queue maxsize should be 256 ; got {backend._hook_callback_queue.maxsize}"
        )


# ─── Wayland caps_lock tray notification ─────────────────────────


class TestWaylandCapsLockWarning:
    """the dispatcher surfaces a tray notification when the user
    binds Caps Lock on Wayland."""

    def test_no_warning_on_non_wayland(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """On non-Wayland platforms, no tray notification is fired."""
        import voice_typer.server.platform_utils as platform_utils

        monkeypatch.setattr(platform_utils, "is_wayland_session", lambda: False)
        dispatcher._maybe_warn_wayland_caps_lock("<caps_lock>")
        dispatcher._app.tray.notify_safety.assert_not_called()

    def test_no_warning_for_non_caps_lock_hotkey(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """Even on Wayland, a non-caps_lock hotkey does not fire the
        warning."""
        import voice_typer.server.platform_utils as platform_utils

        monkeypatch.setattr(platform_utils, "is_wayland_session", lambda: True)
        dispatcher._maybe_warn_wayland_caps_lock("<f2>")
        dispatcher._app.tray.notify_safety.assert_not_called()

    def test_warning_fired_on_wayland_with_caps_lock(self, dispatcher: HotkeyDispatcher, monkeypatch):
        """On Wayland with a caps_lock hotkey, the tray notification
        must fire."""
        import voice_typer.server.platform_utils as platform_utils

        monkeypatch.setattr(platform_utils, "is_wayland_session", lambda: True)
        dispatcher._maybe_warn_wayland_caps_lock("<caps_lock>")
        dispatcher._app.tray.notify_safety.assert_called_once()
        call_args = dispatcher._app.tray.notify_safety.call_args
        message = call_args[0][1] if call_args[0] else ""
        assert "Caps Lock" in message or "caps" in message.lower(), (
            f"Notification should mention Caps Lock; got: {message!r}"
        )
