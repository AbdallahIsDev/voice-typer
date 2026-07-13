"""Tests for WindowsNativeHotkey readiness handshake.

These tests mock ctypes.windll.user32 and kernel32 to simulate specific
failure modes and success scenarios without requiring a Windows host.
"""

import ctypes
import ctypes.wintypes
import time
from unittest.mock import MagicMock

import pytest

# ─── Fixture ─────────────────────────────────────────────────────────────────


@pytest.fixture()
def mock_win32(monkeypatch):
    """Provide mocked user32 and kernel32 DLLs.

    Default behavior: all Win32 calls succeed.  The polling loop exits
    quickly so tests don't hang.
    """
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


# ─── RegisterHotKey failure ──────────────────────────────────────────────────


class TestRegisterHotKeyFailure:
    """When RegisterHotKey fails, start() falls back to polling."""

    def test_fallback_on_register_failure(self, mock_win32):
        """RegisterHotKey returns 0 -> polling fallback, no raise."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0  # BOOL FALSE
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._using_polling
            assert backend._last_error == 1409
        finally:
            backend.stop()

    def test_fallback_completes_quickly(self, mock_win32):
        """start() returns quickly on RegisterHotKey failure (polling fallback)."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
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


# ─── Success scenario ────────────────────────────────────────────────────────


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


# ─── diagnose() method ───────────────────────────────────────────────────────


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
        """After RegisterHotKey failure, falls back to polling (no raise)."""
        mock_user32, _ = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._ready_event.is_set()
            assert backend._success is True  # polling fallback, not an error
            assert backend._using_polling
        finally:
            backend.stop()


# ─── Mocking verification ────────────────────────────────────────────────────


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


# ─── FIX-HOTKEY-ARCHITECTURE: modifier-only hotkeys ─────────────────────────


class TestModifierOnlyHotkeys:
    """FIX-HOTKEY-ARCHITECTURE: <alt>, <ctrl>, <shift>, <win> alone
    (no main key) should be accepted by the polling backend and use
    ``_run_modifier_only_polling_loop`` instead of raising ValueError.
    """

    def test_alt_only_hotkey_starts_without_error(self, mock_win32):
        """<alt> no longer raises ValueError at start() time."""
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        try:
            backend.start(MagicMock())
            assert backend._is_modifier_only is True
            assert backend._vk is None
            assert backend._modifiers & 0x0001  # _MOD_ALT
            assert backend._using_polling is True
        finally:
            backend.stop()

    def test_modifier_only_hotkey_skips_register_hotkey(self, mock_win32):
        """RegisterHotKey must NOT be called for modifier-only hotkeys
        (it would fail with ERROR_INVALID_PARAMETER since there's no
        main VK to register)."""
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
        """diagnose() must not crash on modifier-only hotkeys where
        ``self._vk`` is None (previously the f-string ``0x{None:X}``
        would raise TypeError)."""
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
        """FIX-HOTKEY-AND-NOTIFICATION: when the configured modifier is
        pressed AND released alone (no other modifiers, no non-modifier
        keys during the hold), the press callback must fire exactly once.

        Toggle mode (no on_release callback set) defers the fire to the
        release transition so we can verify the modifier was released
        alone — this is the fix for the "Alt+C fires the dictation"
        problem. The test simulates press → hold → release and asserts
        the callback fires exactly once (not zero, not repeatedly).
        """
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # State machine: 0 = nothing pressed, 1 = Alt pressed (held),
        # 2 = Alt released. The polling loop polls at ~1ms, so we can
        # use a mutable dict to drive the mock through the press →
        # release cycle.
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk == 0x12:  # VK_MENU (Alt)
                return 0x8000 if state["value"] == 1 else 0
            return 0
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)
            import time as _time
            # Phase 1: nothing pressed — callback must not fire.
            _time.sleep(0.03)
            assert callback.call_count == 0, (
                "Callback fired before Alt was pressed"
            )
            # Phase 2: press Alt (held). Toggle mode defers the fire to
            # release, so the callback still must not fire while held.
            state["value"] = 1
            _time.sleep(0.05)
            assert callback.call_count == 0, (
                "Callback fired while modifier held (toggle mode defers to release)"
            )
            # Phase 3: release Alt. Now the callback should fire exactly once.
            state["value"] = 2
            _time.sleep(0.05)
            assert callback.call_count == 1, (
                f"Expected callback to fire exactly once on release-alone, "
                f"got {callback.call_count}"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_does_not_fire_while_held(
        self, mock_win32,
    ):
        """FIX-HOTKEY-AND-NOTIFICATION (b): press-and-hold must NOT
        fire the callback repeatedly. The callback fires at most once
        per press-release cycle. This test holds Alt for an extended
        period (without releasing) and verifies the callback never
        fires in toggle mode (which defers to release)."""
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
            import time as _time
            # Hold for 200ms — far longer than the polling interval.
            _time.sleep(0.2)
            # Toggle mode defers to release, so callback must NOT fire
            # while the key is held.
            assert callback.call_count == 0, (
                f"Callback fired {callback.call_count} times while Alt held "
                f"— toggle mode must defer to release"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_when_other_held(
        self, mock_win32,
    ):
        """If another modifier is held alongside the configured one,
        the press callback must NOT fire (user intent is a combo).

        FIX-HOTKEY-AND-NOTIFICATION: the test simulates press+release
        with another modifier held the entire time. Toggle mode must
        not fire because the release was not "alone" (other modifiers
        were still held at release time).
        """
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # State: 0 = nothing, 1 = Alt+Ctrl pressed, 2 = Alt released
        # (Ctrl still held).
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if state["value"] == 0:
                return 0
            if state["value"] == 1:
                # Both Alt and Ctrl held.
                return 0x8000 if vk in (0x11, 0x12) else 0
            # state == 2: only Ctrl held (Alt released).
            return 0x8000 if vk == 0x11 else 0
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)
            import time as _time
            # Phase 1: press Alt+Ctrl (held).
            state["value"] = 1
            _time.sleep(0.05)
            assert callback.call_count == 0, (
                "Callback fired while Alt+Ctrl held"
            )
            # Phase 2: release Alt but keep Ctrl held.
            state["value"] = 2
            _time.sleep(0.05)
            # Per the FIX-HOTKEY-AND-NOTIFICATION behavior, toggle mode
            # checks _other_modifiers_pressed() at release time. Since
            # Ctrl is still held, the toggle fire is suppressed.
            assert callback.call_count == 0, (
                "Callback fired on Alt release while Ctrl still held "
                "— should be suppressed (combo)"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_on_non_modifier_combo(
        self, mock_win32,
    ):
        """FIX-HOTKEY-AND-NOTIFICATION (a): if a non-modifier key (like
        'C') is pressed between the modifier press and release, the
        press callback must NOT fire on release — the user was doing a
        combo like Alt+C, not invoking the bare Alt hotkey.

        FLAKY-FIX: previously used 30/30/50ms sleeps which could be too
        short under CI load — the polling loop (1ms interval) needs at
        least ~10 cycles per phase to reliably observe each state
        transition. Bumped to 80/80/120ms to give a comfortable margin
        even on slow CI runners. Also added a final state verification
        so a failure gives a clearer diagnostic.
        """
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<alt>")
        # State: 0 = nothing, 1 = Alt held, 2 = Alt+C held,
        # 3 = Alt released (C also released).
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if state["value"] == 0:
                return 0
            if state["value"] == 1:
                return 0x8000 if vk == 0x12 else 0  # Alt only
            if state["value"] == 2:
                # Alt + C (VK_C = 0x43)
                return 0x8000 if vk in (0x12, 0x43) else 0
            # state == 3: nothing pressed.
            return 0
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)
            import time as _time
            # Phase 1: press Alt.
            state["value"] = 1
            _time.sleep(0.08)
            assert callback.call_count == 0  # toggle mode defers
            # Phase 2: press C while Alt held (Alt+C combo).
            state["value"] = 2
            _time.sleep(0.08)
            assert callback.call_count == 0  # still no fire while held
            # Phase 3: release everything.
            state["value"] = 3
            _time.sleep(0.12)
            # The non-modifier key (C) was pressed during the hold, so
            # the toggle fire on release must be suppressed.
            assert callback.call_count == 0, (
                f"Callback fired {callback.call_count} times after Alt+C "
                f"combo — should be suppressed (user was doing Alt+C, not "
                f"invoking bare Alt hotkey)"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_ptt_fires_on_press_and_release(
        self, mock_win32,
    ):
        """FIX-HOTKEY-AND-NOTIFICATION (d): for push-to-talk mode (has
        on_release callback), the press callback fires once on press
        (if no other modifiers held) and the on_release callback fires
        once on release. Press-and-hold does NOT fire repeatedly.
        """
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
            import time as _time
            # Phase 1: nothing pressed.
            _time.sleep(0.03)
            assert press_callback.call_count == 0
            assert release_callback.call_count == 0
            # Phase 2: press Alt. PTT mode fires press immediately.
            state["value"] = 1
            _time.sleep(0.05)
            assert press_callback.call_count == 1, (
                f"PTT press callback should fire once on press, got "
                f"{press_callback.call_count}"
            )
            assert release_callback.call_count == 0
            # Hold for an extended period — must NOT fire press repeatedly.
            _time.sleep(0.1)
            assert press_callback.call_count == 1, (
                f"PTT press callback fired {press_callback.call_count} "
                f"times during hold — must fire exactly once"
            )
            # Phase 3: release Alt. PTT fires on_release.
            state["value"] = 0
            _time.sleep(0.05)
            assert release_callback.call_count == 1, (
                f"PTT on_release should fire once on release, got "
                f"{release_callback.call_count}"
            )
            assert press_callback.call_count == 1  # unchanged
        finally:
            backend.stop()


# ─── FIX-HOTKEY-ARCHITECTURE: Caps Lock toggle suppression ─────────────────


class TestCapsLockSuppression:
    """FIX-HOTKEY-ARCHITECTURE: when the hotkey is <caps_lock>, the
    polling backend should suppress the OS-level caps-state toggle by
    sending a synthetic Caps Lock keypress via keybd_event.
    """

    def test_caps_lock_hotkey_calls_keybd_event_on_press(self, mock_win32):
        """When Caps Lock (VK=0x14) is pressed, _suppress_caps_lock_toggle
        should call keybd_event to undo the OS-level toggle."""
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<caps_lock>")
        # HOTKEY-DEFER-001: simulate a realistic keypress cycle. The
        # polling loop now seeds was_pressed from the current key state
        # at registration time (defense-in-depth against the
        # capture-triggers-recording race). If we return 0x8000 from
        # the very first GetAsyncKeyState call, the seeding sets
        # was_pressed=True and the callback never fires (the key is
        # treated as "already held"). To test the actual keypress→fire
        # →suppress cycle, we return 0 (not pressed) for the first
        # call (seeding), then 0x8000 (pressed) for subsequent calls.
        import itertools
        call_counter = itertools.count()
        def fake_get_async_key_state(vk):
            if vk != 0x14:
                return 0
            # First call (seeding) returns "not pressed"; all subsequent
            # calls return "pressed" to simulate the user pressing the key.
            if next(call_counter) == 0:
                return 0
            return 0x8000
        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state
        mock_user32.GetKeyState.return_value = 1  # toggle bit set

        callback = MagicMock()
        try:
            backend.start(callback)
            import time as _time
            _time.sleep(0.05)
            # keybd_event should have been called for the synthetic
            # keydown + keyup (2 calls per suppression cycle).
            assert mock_user32.keybd_event.call_count >= 2
        finally:
            backend.stop()
