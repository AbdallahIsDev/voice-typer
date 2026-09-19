"""Tests for WindowsNativeHotkey readiness handshake."""

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.wait_for import wait_for


def _flip(backend):
    """Helper: make the next is_set() return True so the polling loop exits."""
    backend._stop_event.is_set.return_value = True


def _wait_until(predicate, timeout: float = 3.0, msg: str = "condition not met"):
    """Poll ``predicate`` until truthy or ``timeout`` elapses."""
    if not wait_for(predicate, timeout=timeout):
        raise AssertionError(f"{msg} (waited {timeout}s)")


@pytest.fixture()
def mock_win32(monkeypatch):
    """Provide mocked user32 and kernel32 DLLs."""
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()

    # Default: success for all Win32 calls
    mock_user32.RegisterHotKey.return_value = 1  # BOOL TRUE
    mock_user32.UnregisterHotKey.return_value = 1
    mock_user32.PostThreadMessageW.return_value = 1
    mock_user32.GetAsyncKeyState.return_value = 0  # key not pressed

    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.Sleep = MagicMock()

    # Patch ctypes.windll (Linux has no windll attribute by default)
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)

    return mock_user32, mock_kernel32


class TestRegisterHotKeyFailure:
    """When RegisterHotKey fails, start() falls back to the WH_KEYBOARD_LL"""

    def test_fallback_on_register_failure(self, mock_win32):
        """RegisterHotKey returns 0 -> falls back to low-level hook (not raise)."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0  # BOOL FALSE
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            # Preferred fallback is the low-level hook (not polling).
            _wait_until(
                lambda: backend._hook_handle is not None,
                msg="LL hook handle never installed",
            )
            assert not backend._using_polling
            assert backend._hook_handle is not None
            assert backend._last_error == 1409
        finally:
            backend.stop()

    def test_polling_only_when_hook_also_fails(self, mock_win32):
        """If BOTH RegisterHotKey and the low-level hook fail, poll via"""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409
        # Force the hook install to fail too.
        mock_user32.SetWindowsHookExW.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            _wait_until(
                lambda: backend._using_polling,
                msg="polling fallback never engaged",
            )
            assert backend._using_polling
            assert backend._hook_handle is None
        finally:
            backend.stop()

    def test_fallback_completes_quickly(self, mock_win32):
        """start() returns quickly on RegisterHotKey failure (polling fallback)."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            import time

            start_time = time.monotonic()
            backend.start(MagicMock())
            elapsed = time.monotonic() - start_time
            assert elapsed < 7.0, f"Took too long: {elapsed:.1f}s"
        finally:
            backend.stop()

    def test_error_code_captured(self, mock_win32):
        """RegisterHotKey failure should capture the Win32 error code."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._last_error == 1409
        finally:
            backend.stop()


class TestSuccessScenario:
    """On success, is_alive() returns True and _ready_event is set."""

    def test_ready_event_set_on_success(self, mock_win32):
        """After successful start(), _ready_event should be set."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        assert backend._ready_event.is_set()
        assert backend._success is True
        backend.stop()

    def test_is_alive_returns_true(self, mock_win32):
        """After successful start(), is_alive() returns True while thread runs."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        assert backend.is_alive() is True
        backend.stop()

    def test_is_alive_false_after_stop(self, mock_win32):
        """After stop(), is_alive() returns False."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())
        backend.stop()

        assert backend.is_alive() is False

    def test_registered_flag_true(self, mock_win32):
        """On success, _success should be True (survives thread cleanup)."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        assert backend._success is True
        backend.stop()


class TestDiagnoseMethod:
    """Test diagnose() reports success/failure state correctly."""

    def test_diagnose_before_start(self):
        """Before start(), diagnose() should say 'no thread started'."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        info = backend.diagnose()
        assert "no thread" in info.lower()

    def test_diagnose_on_success(self, mock_win32):
        """After successful start(), diagnose() includes key info."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        info = backend.diagnose()
        assert "WindowsNativeHotkey" in info
        assert "<f2>" in info
        assert "0x71" in info  # VK code for F2
        backend.stop()

    def test_diagnose_on_register_failure(self, mock_win32):
        """After RegisterHotKey failure, falls back to the low-level hook"""
        mock_user32, _ = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._ready_event.is_set()
            assert backend._success is True  # hook fallback, not an error
            # Preferred fallback is the low-level hook, not polling.
            _wait_until(
                lambda: backend._hook_handle is not None,
                msg="LL hook handle never installed",
            )
            assert not backend._using_polling
            assert backend._hook_handle is not None
        finally:
            backend.stop()


class TestMockVerification:
    """Verify that our mocking actually hits the right code paths."""

    def test_register_hotkey_called(self, mock_win32):
        """RegisterHotKey should be called during start()."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())

        mock_user32.RegisterHotKey.assert_called_once()
        backend.stop()

    def test_register_hotkey_uses_ctrl_modifier_for_ctrl_digit(self, mock_win32):
        """Ctrl+1 should register Ctrl as a modifier and 1 as the main key."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<ctrl>+1")
        backend.start(MagicMock())

        args = mock_user32.RegisterHotKey.call_args[0]
        assert args[2] == 0x4000 | 0x0002
        assert args[3] == ord("1")
        backend.stop()

    def test_stop_calls_cleanup(self, mock_win32):
        """stop() should call UnregisterHotKey."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.start(MagicMock())
        backend.stop()

        mock_user32.UnregisterHotKey.assert_called()


class TestModifierOnlyHotkeys:
    """FIX-HOTKEY-ARCHITECTURE: <alt>, <ctrl>, <shift>, <win> alone"""

    def test_alt_only_hotkey_starts_without_error(self, mock_win32):
        """
        <alt> no longer raises ValueError at start() time.
        This test pins both paths: hook succeeds by default (hook mode),
        """
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        try:
            backend.start(MagicMock())
            assert backend._is_modifier_only is True
            assert backend._vk is None
            assert backend._modifiers & 0x0001  # _MOD_ALT
            # Hook installs by default → hook mode (not polling).
            _wait_until(
                lambda: backend._hook_handle is not None,
                msg="LL hook handle never installed for modifier-only spec",
            )
            assert backend._using_polling is False
        finally:
            backend.stop()

        mock_user32.SetWindowsHookExW.return_value = 0
        backend2 = WindowsNativeHotkey("<alt>")
        try:
            backend2.start(MagicMock())
            _wait_until(
                lambda: backend2._using_polling,
                msg="modifier-only polling fallback never engaged",
            )
            assert backend2._using_polling is True
            assert backend2._hook_handle is None
        finally:
            backend2.stop()

    def test_modifier_only_hotkey_skips_register_hotkey(self, mock_win32):
        """RegisterHotKey must NOT be called for modifier-only hotkeys"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<ctrl>")
        try:
            backend.start(MagicMock())
            mock_user32.RegisterHotKey.assert_not_called()
            assert backend._registered is False
        finally:
            backend.stop()

    def test_modifier_only_hotkey_diagnose_does_not_crash(self, mock_win32):
        """diagnose() must not crash on modifier-only hotkeys where"""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<shift>")
        try:
            backend.start(MagicMock())
            info = backend.diagnose()
            assert "modifier-only" in info.lower()
            assert "<shift>" in info
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_detects_press(self, mock_win32):
        """FIX-HOTKEY-AND-NOTIFICATION: when the configured modifier is"""
        mock_user32, _ = mock_win32
        # Force the LL hook to fail so the modifier-only POLLING loop
        mock_user32.SetWindowsHookExW.return_value = 0
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # State machine: 0 = nothing pressed, 1 = Alt pressed (held),
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk == 0x12:  # VK_MENU (Alt)
                return 0x8000 if state["value"] == 1 else 0
            return 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            assert not wait_for(lambda: callback.call_count > 0, timeout=0.03), "Callback fired before Alt was pressed"
            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.05), (
                "Callback fired while modifier held (toggle mode defers to release)"
            )
            # Phase 3: release Alt. Now the callback should fire exactly once.
            state["value"] = 2
            _wait_until(
                lambda: callback.call_count >= 1,
                timeout=2.0,
                msg="Callback did not fire on release-alone",
            )
            assert callback.call_count == 1, (
                f"Expected callback to fire exactly once on release-alone, got {callback.call_count}"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_does_not_fire_while_held(
        self,
        mock_win32,
    ):
        """FIX-HOTKEY-AND-NOTIFICATION (b): press-and-hold must NOT"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")

        # Alt held for the entire test, never released.
        def fake_get_async_key_state(vk):
            return 0x8000 if vk == 0x12 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            # callback must NOT fire while the key is held).
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.2), (
                f"Callback fired {callback.call_count} times while Alt held, toggle mode must defer to release"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_when_other_held(
        self,
        mock_win32,
    ):
        """If another modifier is held alongside the configured one,"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # State: 0 = nothing, 1 = Alt+Ctrl pressed, 2 = Alt released
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if state["value"] == 0:
                return 0
            if state["value"] == 1:
                # Both Alt and Ctrl held.
                return 0x8000 if vk in (0x11, 0x12) else 0
            return 0x8000 if vk == 0x11 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.05), "Callback fired while Alt+Ctrl held"
            # Phase 2: release Alt but keep Ctrl held.
            state["value"] = 2
            # Per the FIX-HOTKEY-AND-NOTIFICATION behavior, toggle mode
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.05), (
                "Callback fired on Alt release while Ctrl still held, should be suppressed (combo)"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_on_non_modifier_combo(
        self,
        mock_win32,
    ):
        """FIX-HOTKEY-AND-NOTIFICATION (a): if a non-modifier key (like"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # State: 0 = nothing, 1 = Alt held, 2 = Alt+C held,
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if state["value"] == 0:
                return 0
            if state["value"] == 1:
                return 0x8000 if vk == 0x12 else 0  # Alt only
            if state["value"] == 2:
                # Alt + C (VK_C = 0x43)
                return 0x8000 if vk in (0x12, 0x43) else 0
            return 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.08)
            state["value"] = 2
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.08)
            state["value"] = 3
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.12), (
                f"Callback fired {callback.call_count} times after Alt+C "
                f"combo, should be suppressed (user was doing Alt+C, not "
                f"invoking bare Alt hotkey)"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_ptt_fires_on_press_and_release(
        self,
        mock_win32,
    ):
        """FIX-HOTKEY-AND-NOTIFICATION (d): for push-to-talk mode (has"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk == 0x12:  # VK_MENU (Alt)
                return 0x8000 if state["value"] == 1 else 0
            return 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        press_callback = MagicMock()
        release_callback = MagicMock()
        backend.set_on_release(release_callback)
        try:
            backend.start(press_callback)

            # Phase 1: nothing pressed. Wait 30ms and verify no fire.
            assert not wait_for(lambda: press_callback.call_count > 0, timeout=0.03)
            assert not wait_for(lambda: release_callback.call_count > 0, timeout=0.03)
            # Phase 2: press Alt. PTT mode fires press immediately.
            state["value"] = 1
            _wait_until(
                lambda: press_callback.call_count >= 1,
                timeout=2.0,
                msg="PTT press callback did not fire on press",
            )
            assert press_callback.call_count == 1, (
                f"PTT press callback should fire once on press, got {press_callback.call_count}"
            )
            assert release_callback.call_count == 0
            # Hold for an extended period, must NOT fire press repeatedly.
            _press_count_after_hold_start = press_callback.call_count
            assert not wait_for(
                lambda: press_callback.call_count > _press_count_after_hold_start,
                timeout=0.1,
            ), f"PTT press callback fired {press_callback.call_count} times during hold, must fire exactly once"
            # Phase 3: release Alt. PTT fires on_release.
            state["value"] = 0
            _wait_until(
                lambda: release_callback.call_count >= 1,
                timeout=2.0,
                msg="PTT on_release did not fire on release",
            )
            assert release_callback.call_count == 1, (
                f"PTT on_release should fire once on release, got {release_callback.call_count}"
            )
            assert press_callback.call_count == 1  # unchanged
        finally:
            backend.stop()


class TestToggleFiresOnKeyUp:
    """USER-REQUESTED FIX: in toggle mode with ``set_toggle_on_keyup(True)``,"""

    def test_toggle_fires_on_key_up_not_while_held(self, mock_win32):
        """must fire exactly once on release."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.set_toggle_on_keyup(True)
        mock_user32.RegisterHotKey.return_value = 0
        mock_user32.SetWindowsHookExW.return_value = 0
        # State: 0 = up, 1 = held down.
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != 0x71:  # VK_F2
                return 0
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.15), (
                "Toggle callback fired while the key was held, must defer to key-up (release)"
            )
            # Phase 2: release. Must fire exactly once.
            state["value"] = 0
            _wait_until(
                lambda: callback.call_count >= 1,
                timeout=2.0,
                msg="Toggle callback did not fire on release",
            )
            assert callback.call_count == 1, (
                f"Expected toggle callback to fire exactly once on release, got {callback.call_count}"
            )
        finally:
            backend.stop()

    def test_toggle_does_not_fire_twice_on_repeated_holds(self, mock_win32):
        """Two independent press/release cycles must produce exactly two"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.set_toggle_on_keyup(True)
        # Force the GetAsyncKeyState polling path (see other test).
        mock_user32.RegisterHotKey.return_value = 0
        mock_user32.SetWindowsHookExW.return_value = 0
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != 0x71:
                return 0
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            for i in range(1, 3):
                state["value"] = 1
                # Wait for the polling loop to observe the press (no fire
                _calls_before_press = mock_user32.GetAsyncKeyState.call_count
                _wait_until(
                    lambda _calls=_calls_before_press: mock_user32.GetAsyncKeyState.call_count > _calls,
                    timeout=0.5,
                    msg="Polling loop did not observe the press",
                )
                state["value"] = 0
                # Wait for the callback to fire on release.
                _wait_until(
                    lambda _i=i: callback.call_count == _i,
                    timeout=2.0,
                    msg=f"Callback did not fire on release (cycle {i})",
                )
            assert callback.call_count == 2, f"Expected 2 fires (one per release), got {callback.call_count}"
        finally:
            backend.stop()


class TestCapsLockSuppression:
    """sending a synthetic Caps Lock keypress via SendInput (modern Win32"""

    def test_caps_lock_hotkey_calls_sendinput_on_press(self, mock_win32):
        """When Caps Lock (VK=0x14) is pressed, _suppress_caps_lock_toggle"""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<caps_lock>")
        import itertools

        call_counter = itertools.count()

        def fake_get_async_key_state(vk):
            if vk != 0x14:
                return 0
            # First call (seeding) returns "not pressed"; all subsequent
            if next(call_counter) == 0:
                return 0
            return 0x8000

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state
        mock_user32.GetKeyState.return_value = 1  # toggle bit set
        # SendInput returns 1 (single event inserted), the modern
        mock_user32.SendInput.return_value = 1

        callback = MagicMock()
        try:
            backend.start(callback)

            # Wait for the polling loop to observe the press and call
            _wait_until(
                lambda: mock_user32.SendInput.call_count >= 2,
                timeout=2.0,
                msg="SendInput was not called for caps-lock suppression",
            )
        finally:
            backend.stop()


# ─── PERF-01 / CPU-01: polling-fallback timer hardening ───────────
# (not 1ms).  These assertions pin the battery-drain fix so a future


class TestPollingFallbackTimerHardening:
    """PERF-01 / CPU-01: the GetAsyncKeyState fallback must not spin at"""

    def _force_polling_fallback(self, mock_win32):
        """Make start() land on the GetAsyncKeyState polling path."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409
        mock_user32.SetWindowsHookExW.return_value = 0  # hook install fails
        return mock_user32, mock_kernel32

    def _drive_one_iteration(self, backend, mock_kernel32):
        """Run the polling loop for exactly one iteration, then exit."""

        def _sleep(_ms):
            backend._stop_event.is_set.return_value = True

        mock_kernel32.Sleep.side_effect = _sleep
        backend._stop_event = MagicMock()
        backend._stop_event.is_set.return_value = False

    def _new_backend_and_winmm(self, mock_win32):
        mock_user32, mock_kernel32 = self._force_polling_fallback(mock_win32)
        mock_winmm = MagicMock()
        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32
        mock_windll.winmm = mock_winmm
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        return backend, mock_user32, mock_kernel32, mock_winmm, mock_windll

    def test_time_begin_and_end_period_called(self, mock_win32):
        """winmm.timeBeginPeriod(8) is called on entry and timeEndPeriod(8)"""
        backend, _u, mock_kernel32, mock_winmm, mock_windll = self._new_backend_and_winmm(mock_win32)
        self._drive_one_iteration(backend, mock_kernel32)
        try:
            with patch.object(ctypes, "windll", mock_windll, create=True):
                backend.start(MagicMock())
                if backend._thread is not None:
                    backend._thread.join(timeout=2.0)
            assert mock_winmm.timeBeginPeriod.called
            assert mock_winmm.timeBeginPeriod.call_args == ((8,),)
            assert mock_winmm.timeEndPeriod.called
            assert mock_winmm.timeEndPeriod.call_args == ((8,),)
        finally:
            backend.stop()

    def test_sleep_uses_8ms_not_1ms(self, mock_win32):
        """The polling loop sleeps 8ms (125 Hz), not 1ms (1000 Hz)."""
        backend, _u, mock_kernel32, mock_winmm, mock_windll = self._new_backend_and_winmm(mock_win32)
        self._drive_one_iteration(backend, mock_kernel32)
        try:
            with patch.object(ctypes, "windll", mock_windll, create=True):
                backend.start(MagicMock())
                if backend._thread is not None:
                    backend._thread.join(timeout=2.0)
            sleep_calls = [c.args[0] for c in mock_kernel32.Sleep.call_args_list]
            assert 8 in sleep_calls, f"expected Sleep(8), got {sleep_calls}"
            assert 1 not in sleep_calls, f"Sleep(1) regression: {sleep_calls}"
        finally:
            backend.stop()

    def test_timer_restored_even_on_exception(self, mock_win32):
        """If the polling loop body raises, timeEndPeriod(8) still runs."""
        backend, mock_user32, mock_kernel32, mock_winmm, mock_windll = self._new_backend_and_winmm(mock_win32)
        # Force the loop body to raise on the first GetAsyncKeyState call.
        mock_user32.GetAsyncKeyState.side_effect = RuntimeError("simulated crash")
        backend._stop_event = MagicMock()
        backend._stop_event.is_set.return_value = False
        try:
            with patch.object(ctypes, "windll", mock_windll, create=True):
                backend.start(MagicMock())
                if backend._thread is not None:
                    backend._thread.join(timeout=2.0)
            assert mock_winmm.timeEndPeriod.called
            assert mock_winmm.timeEndPeriod.call_args == ((8,),)
        finally:
            backend.stop()

    def test_modifier_only_loop_timer_hardened(self, mock_win32):
        """PERF-01 / CPU-01: the modifier-only polling fallback"""
        # <alt> is modifier-only, so start() enters the modifier loop.
        mock_user32, mock_kernel32 = self._force_polling_fallback(mock_win32)
        mock_winmm = MagicMock()
        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32
        mock_windll.kernel32 = mock_kernel32
        mock_windll.winmm = mock_winmm

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        self._drive_one_iteration(backend, mock_kernel32)
        try:
            with patch.object(ctypes, "windll", mock_windll, create=True):
                backend.start(MagicMock())
                if backend._thread is not None:
                    backend._thread.join(timeout=2.0)
            assert mock_winmm.timeBeginPeriod.called
            assert mock_winmm.timeBeginPeriod.call_args == ((8,),)
            assert mock_winmm.timeEndPeriod.called
            assert mock_winmm.timeEndPeriod.call_args == ((8,),)
            sleep_calls = [c.args[0] for c in mock_kernel32.Sleep.call_args_list]
            assert 8 in sleep_calls, f"expected Sleep(8), got {sleep_calls}"
        finally:
            backend.stop()
