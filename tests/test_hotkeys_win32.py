"""Tests for WindowsNativeHotkey readiness handshake.

These tests mock ctypes.windll.user32 and kernel32 to simulate specific
failure modes and success scenarios without requiring a Windows host.
"""

import ctypes
import ctypes.wintypes
from unittest.mock import MagicMock, patch

import pytest

from tests.fixtures.wait_for import wait_for


def _flip(backend):
    """Helper: make the next is_set() return True so the polling loop exits."""
    backend._stop_event.is_set.return_value = True


def _wait_until(predicate, timeout: float = 3.0, msg: str = "condition not met"):
    """Poll ``predicate`` until truthy or ``timeout`` elapses.

    Production ``start()`` sets ``_ready_event`` BEFORE the detection
    branch runs on the worker thread, so ``_hook_handle`` /
    ``_using_polling`` are assigned asynchronously after ``start()``
    returns. Immediate asserts race the thread; a bounded poll makes
    the tests deterministic.

    Thin wrapper around :func:`tests.fixtures.wait_for.wait_for` that
    raises ``AssertionError`` on timeout (wait_for returns bool — this
    helper converts False to an assertion failure with a message).
    """
    if not wait_for(predicate, timeout=timeout):
        raise AssertionError(f"{msg} (waited {timeout}s)")


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
    """When RegisterHotKey fails, start() falls back to the WH_KEYBOARD_LL
    low-level hook (preferred, robust for ESC), and only to GetAsyncKeyState
    polling if the hook also cannot be installed (ESC-CANCEL-DELIVERY)."""

    def test_fallback_on_register_failure(self, mock_win32):
        """RegisterHotKey returns 0 -> falls back to low-level hook (not raise).

        The low-level hook is now the preferred reliable path, so when
        RegisterHotKey fails but SetWindowsHookExW succeeds, the backend
        uses hook mode (``_using_polling`` is False) rather than polling.
        """
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0  # BOOL FALSE
        mock_kernel32.GetLastError.return_value = 1409

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            # Preferred fallback is the low-level hook (not polling).
            # _hook_handle is set on the worker thread after start()
            # returns (ready_event precedes the detection branch).
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
        """If BOTH RegisterHotKey and the low-level hook fail, poll via
        GetAsyncKeyState (the legacy safe fallback)."""
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409
        # Force the hook install to fail too.
        mock_user32.SetWindowsHookExW.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            # _using_polling is set on the worker thread after start()
            # returns (ready_event precedes the detection branch) —
            # poll for it instead of racing the thread.
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
        """After RegisterHotKey failure, falls back to the low-level hook
        (not polling) and does not raise."""
        mock_user32, _ = mock_win32
        mock_user32.RegisterHotKey.return_value = 0

        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        try:
            backend.start(MagicMock())
            assert backend._ready_event.is_set()
            assert backend._success is True  # hook fallback, not an error
            # Preferred fallback is the low-level hook, not polling.
            # _hook_handle is set on the worker thread after start()
            # returns (ready_event precedes the detection branch).
            _wait_until(
                lambda: backend._hook_handle is not None,
                msg="LL hook handle never installed",
            )
            assert not backend._using_polling
            assert backend._hook_handle is not None
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
        """<alt> no longer raises ValueError at start() time.

        Modifier-only specs now PREFER the WH_KEYBOARD_LL hook when it
        installs successfully (the hook sees raw modifier VKs); the
        polling loop is the fallback when the hook can't be installed.
        This test pins both paths: hook succeeds by default (hook mode),
        and forcing the hook to fail lands on the modifier-only polling
        loop without raising.
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

        # Force the hook to fail → the modifier-only polling loop is the
        # fallback (still no ValueError).
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
        # Force the LL hook to fail so the modifier-only POLLING loop
        # runs (modifier-only specs prefer the hook when it installs).
        mock_user32.SetWindowsHookExW.return_value = 0
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

            # Phase 1: nothing pressed — callback must not fire. Wait
            # 30ms and verify (wait_for returns True if the predicate
            # became truthy — we expect False here).
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.03), "Callback fired before Alt was pressed"
            # Phase 2: press Alt (held). Toggle mode defers the fire to
            # release, so the callback still must not fire while held.
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

            # Hold for 200ms — far longer than the polling interval.
            # Wait_for returns True if the predicate became truthy —
            # we expect False (toggle mode defers to release, so the
            # callback must NOT fire while the key is held).
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.2), (
                f"Callback fired {callback.call_count} times while Alt held — toggle mode must defer to release"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_when_other_held(
        self,
        mock_win32,
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

            # Phase 1: press Alt+Ctrl (held). Wait 50ms and verify
            # no fire (wait_for returns True if predicate became
            # truthy — we expect False here).
            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.05), "Callback fired while Alt+Ctrl held"
            # Phase 2: release Alt but keep Ctrl held.
            state["value"] = 2
            # Per the FIX-HOTKEY-AND-NOTIFICATION behavior, toggle mode
            # checks _other_modifiers_pressed() at release time. Since
            # Ctrl is still held, the toggle fire is suppressed. Wait
            # 50ms and verify no fire.
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.05), (
                "Callback fired on Alt release while Ctrl still held — should be suppressed (combo)"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_suppresses_on_non_modifier_combo(
        self,
        mock_win32,
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

            # Phase 1: press Alt. Wait 80ms and verify no fire
            # (toggle mode defers to release).
            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.08)
            # Phase 2: press C while Alt held (Alt+C combo). Wait 80ms
            # and verify still no fire.
            state["value"] = 2
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.08)
            # Phase 3: release everything. Wait 120ms and verify no fire
            # (the non-modifier key during the hold suppresses the toggle).
            state["value"] = 3
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.12), (
                f"Callback fired {callback.call_count} times after Alt+C "
                f"combo — should be suppressed (user was doing Alt+C, not "
                f"invoking bare Alt hotkey)"
            )
        finally:
            backend.stop()

    def test_modifier_only_polling_loop_ptt_fires_on_press_and_release(
        self,
        mock_win32,
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
            # Hold for an extended period — must NOT fire press repeatedly.
            # Wait 100ms and verify press_callback.call_count stays at 1
            # (wait_for returns True if the predicate became truthy —
            # we expect False here, meaning no additional fire).
            _press_count_after_hold_start = press_callback.call_count
            assert not wait_for(
                lambda: press_callback.call_count > _press_count_after_hold_start,
                timeout=0.1,
            ), f"PTT press callback fired {press_callback.call_count} times during hold — must fire exactly once"
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


# ─── Toggle-on-key-up (USER-REQUESTED FIX) ────────────────────────────────


class TestToggleFiresOnKeyUp:
    """USER-REQUESTED FIX: in toggle mode with ``set_toggle_on_keyup(True)``,
    the dictation toggle must fire on KEY-UP (release), not on key-down.

    This prevents a press-and-hold from starting and then immediately
    stopping recording: while the key is held (no key-up), the callback
    must NEVER fire; it fires exactly once when the key is released.
    """

    def test_toggle_fires_on_key_up_not_while_held(self, mock_win32):
        """Simulate press -> hold -> release of <f2> in toggle mode with
        toggle_on_keyup=True. The callback must NOT fire while held and
        must fire exactly once on release.
        """
        mock_user32, _ = mock_win32
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend.set_toggle_on_keyup(True)
        # Force the GetAsyncKeyState polling path: RegisterHotKey fails AND
        # the low-level hook fails to install, so the backend falls back to
        # the polling loop (the path our state-machine drives).
        mock_user32.RegisterHotKey.return_value = 0
        mock_user32.SetWindowsHookExW.return_value = 0
        # State: 0 = up, 1 = held down.
        state = {"value": 0}

        def fake_get_async_key_state(vk):
            if vk != 0x71:  # VK_F2
                return 0
            # HOTKEY-DEFER-001: the polling loop seeds was_pressed from the
            # key state at registration time. The loop makes two GetAsyncKeyState
            # calls during start() (seed_state + seed_mods) before the loop runs;
            # returning "not pressed" for those means the first real press is
            # seen as a genuine press->release transition. After seeding, drive
            # the state purely from state["value"].
            return 0x8000 if state["value"] == 1 else 0

        mock_user32.GetAsyncKeyState.side_effect = fake_get_async_key_state

        callback = MagicMock()
        try:
            backend.start(callback)

            # Phase 1: press and HOLD. Must NOT fire while held. Wait
            # 150ms and verify (wait_for returns True if predicate
            # became truthy — we expect False here).
            state["value"] = 1
            assert not wait_for(lambda: callback.call_count > 0, timeout=0.15), (
                "Toggle callback fired while the key was held — must defer to key-up (release)"
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
        """Two independent press/release cycles must produce exactly two
        fires (one per release), not start-then-stop on a single hold.
        """
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
                # expected in toggle_on_keyup mode). We track
                # GetAsyncKeyState call count and wait for it to advance,
                # confirming the loop has polled at least once since the
                # press — this avoids the race where we release before
                # the loop sees the press.
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


# ─── FIX-HOTKEY-ARCHITECTURE: Caps Lock toggle suppression ─────────────────


class TestCapsLockSuppression:
    """FIX-HOTKEY-ARCHITECTURE: when the hotkey is <caps_lock>, the
    polling backend should suppress the OS-level caps-state toggle by
    sending a synthetic Caps Lock keypress via SendInput (modern Win32
    keyboard-injection API — replaces the deprecated ``keybd_event``).
    """

    def test_caps_lock_hotkey_calls_sendinput_on_press(self, mock_win32):
        """When Caps Lock (VK=0x14) is pressed, _suppress_caps_lock_toggle
        should call SendInput to undo the OS-level toggle."""
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
        # SendInput returns 1 (single event inserted) — the modern
        # keyboard-injection success path (mirrors production).
        mock_user32.SendInput.return_value = 1

        callback = MagicMock()
        try:
            backend.start(callback)

            # Wait for the polling loop to observe the press and call
            # SendInput (the suppression path fires on the not-held →
            # held transition). SendInput should be called for the
            # synthetic keydown + keyup (2 calls per suppression cycle).
            _wait_until(
                lambda: mock_user32.SendInput.call_count >= 2,
                timeout=2.0,
                msg="SendInput was not called for caps-lock suppression",
            )
        finally:
            backend.stop()


# ─── PERF-01 / CPU-01: polling-fallback timer hardening ───────────
# The polling loop (tier-3 fallback) must call winmm.timeBeginPeriod(8)
# before the loop and timeEndPeriod(8) in a finally, and sleep at 8ms
# (not 1ms).  These assertions pin the battery-drain fix so a future
# refactor can't silently revert to Sleep(1) without timer accuracy.


class TestPollingFallbackTimerHardening:
    """PERF-01 / CPU-01: the GetAsyncKeyState fallback must not spin at
    1000 Hz.  It sets 8ms timer resolution and sleeps 8ms/iter, and
    restores the timer on exit."""

    def _force_polling_fallback(self, mock_win32):
        """Make start() land on the GetAsyncKeyState polling path.

        RegisterHotKey fails AND the low-level hook fails to install, so
        the dispatcher takes the ``else`` branch and calls
        ``_run_polling_loop``.
        """
        mock_user32, mock_kernel32 = mock_win32
        mock_user32.RegisterHotKey.return_value = 0
        mock_kernel32.GetLastError.return_value = 1409
        mock_user32.SetWindowsHookExW.return_value = 0  # hook install fails
        return mock_user32, mock_kernel32

    def _drive_one_iteration(self, backend, mock_kernel32):
        """Run the polling loop for exactly one iteration, then exit.

        ``Sleep`` flips the stop flag on its first call so the
        ``while not is_set()`` condition exits after one pass — this
        avoids guessing how many times ``is_set()`` is called.
        """

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
        """winmm.timeBeginPeriod(8) is called on entry and timeEndPeriod(8)
        on exit of the polling fallback."""
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
        """PERF-01 / CPU-01: the modifier-only polling fallback
        (``_run_modifier_only_polling_loop``) also sets timeBeginPeriod(8)
        and sleeps 8ms/iter, restored via finally."""
        # <alt> is modifier-only, so start() enters the modifier loop.
        # Force its polling fallback by failing the low-level hook too.
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
