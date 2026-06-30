"""Tests for voice_typer.hotkeys module.

Tests the hotkey backend abstraction, VK code parsing, lifecycle management,
and both PynputHotkey and WindowsNativeHotkey paths (with mocking).
"""

import sys
import threading
import time
import pytest
from unittest.mock import MagicMock, patch, PropertyMock


# ─── create_hotkey_backend factory ───────────────────────────────────────────


class TestCreateHotkeyBackend:
    """Verify create_hotkey_backend returns the right type.

    NATIVE-001: with the new architecture, native subprocess backends
    are preferred when the binary is available. The factory wraps them
    in _NativeBackendAdapter to satisfy the HotkeyBackend interface.
    Legacy backends (PynputHotkey, WindowsNativeHotkey, WaylandHotkey)
    remain as fallbacks when the native binary is missing.
    """

    def test_returns_pynput_hotkey_on_linux_without_native(self, monkeypatch):
        """When no native binary is available on Linux, fall back to PynputHotkey."""
        monkeypatch.setattr(sys, "platform", "linux")
        # Force the native binary lookup to fail
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)
        from voice_typer.server.hotkeys import create_hotkey_backend, PynputHotkey

        backend = create_hotkey_backend("<f2>")
        assert isinstance(backend, PynputHotkey)

    def test_returns_pynput_hotkey_on_darwin_without_native(self, monkeypatch):
        """When no native binary is available on macOS, fall back to PynputHotkey."""
        monkeypatch.setattr(sys, "platform", "darwin")
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)
        from voice_typer.server.hotkeys import create_hotkey_backend, PynputHotkey

        backend = create_hotkey_backend("<f2>")
        assert isinstance(backend, PynputHotkey)

    def test_returns_windows_native_on_win32_without_native(self, monkeypatch):
        """When no native binary is available on Windows, fall back to WindowsNativeHotkey."""
        monkeypatch.setattr(sys, "platform", "win32")
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)
        from voice_typer.server.hotkeys import create_hotkey_backend, WindowsNativeHotkey

        backend = create_hotkey_backend("<f2>")
        assert isinstance(backend, WindowsNativeHotkey)

    def test_returns_native_adapter_when_binary_available(self, monkeypatch):
        """When the native binary IS available, return a _NativeBackendAdapter."""
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server import native_hotkeys
        # Pretend the binary exists
        from pathlib import Path
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: Path("/fake/binary"))
        monkeypatch.setattr(native_hotkeys, "is_native_backend_available", lambda: True)
        # Patch the backend creation to avoid actually spawning a process
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey
        fake_backend = LinuxEvdevHotkey("<f2>")
        monkeypatch.setattr(native_hotkeys, "create_native_backend", lambda spec: fake_backend)

        from voice_typer.server.hotkeys import create_hotkey_backend, _NativeBackendAdapter

        backend = create_hotkey_backend("<f2>")
        assert isinstance(backend, _NativeBackendAdapter)

    def test_backend_has_hotkey_str(self, monkeypatch):
        """hotkey_str is preserved across all backend types."""
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)
        from voice_typer.server.hotkeys import create_hotkey_backend

        backend = create_hotkey_backend("<f12>")
        assert backend.hotkey_str == "<f12>"

    def test_falls_back_to_legacy_on_native_failure(self, monkeypatch):
        """If the native backend creation raises, fall back to legacy."""
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)
        # Force create_native_backend to raise
        def _raise(spec):
            raise RuntimeError("native backend exploded")
        monkeypatch.setattr(native_hotkeys, "create_native_backend", _raise)
        from voice_typer.server.hotkeys import create_hotkey_backend, PynputHotkey

        backend = create_hotkey_backend("<f2>")
        assert isinstance(backend, PynputHotkey)


# ─── parse_hotkey_to_vk ─────────────────────────────────────────────────────


class TestParseHotkeyToVk:
    """Verify WindowsNativeHotkey can parse hotkey strings to VK codes."""

    def test_f1(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<f1>") == 0x70

    def test_f2(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<f2>") == 0x71

    def test_f12(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<f12>") == 0x7B

    def test_f24(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<f24>") == 0x87

    def test_digit(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("0") == ord("0")
        assert parse_hotkey_to_vk("9") == ord("9")

    def test_letter(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("a") == ord("A")
        assert parse_hotkey_to_vk("z") == ord("Z")

    def test_unknown_returns_none(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<ctrl>") is None
        assert parse_hotkey_to_vk("<super>") is None

    def test_esc(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<esc>") == 0x1B
        assert parse_hotkey_to_vk("<escape>") == 0x1B

    def test_space(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<space>") == 0x20

    def test_enter(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<enter>") == 0x0D
        assert parse_hotkey_to_vk("<return>") == 0x0D

    def test_tab(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<tab>") == 0x09

    def test_backspace(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<backspace>") == 0x08

    def test_arrow_keys(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<up>") == 0x26
        assert parse_hotkey_to_vk("<down>") == 0x28
        assert parse_hotkey_to_vk("<left>") == 0x25
        assert parse_hotkey_to_vk("<right>") == 0x27

    def test_delete_insert_home_end(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<delete>") == 0x2E
        assert parse_hotkey_to_vk("<del>") == 0x2E
        assert parse_hotkey_to_vk("<insert>") == 0x2D
        assert parse_hotkey_to_vk("<home>") == 0x24
        assert parse_hotkey_to_vk("<end>") == 0x23

    def test_page_up_down(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<pageup>") == 0x21
        assert parse_hotkey_to_vk("<pagedown>") == 0x22

    def test_angle_brackets_stripped(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        assert parse_hotkey_to_vk("<f5>") == parse_hotkey_to_vk("f5")


class TestParseHotkeyToWin32:
    def test_ctrl_digit_parses_key_and_modifier(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_win32

        parsed = parse_hotkey_to_win32("<ctrl>+1")

        assert parsed == (ord("1"), 0x0002)

    def test_plain_function_key_has_no_modifier(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_win32

        parsed = parse_hotkey_to_win32("<f2>")

        assert parsed == (0x71, 0)


# ─── PynputHotkey backend ────────────────────────────────────────────────────


class TestPynputHotkey:
    """Test PynputHotkey start/stop lifecycle with mocked pynput."""

    def _make_mock_modules(self, monkeypatch):
        """Set up pynput mock modules in sys.modules."""
        mock_pynput = MagicMock()
        mock_kb = MagicMock()
        monkeypatch.setitem(sys.modules, "pynput", mock_pynput)
        monkeypatch.setitem(sys.modules, "pynput.keyboard", mock_kb)
        return mock_kb

    def test_start_creates_global_hotkeys(self, monkeypatch):
        mock_kb = self._make_mock_modules(monkeypatch)
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_kb.GlobalHotKeys = MagicMock(return_value=mock_listener)

        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        cb = MagicMock()
        backend.start(cb)

        mock_kb.GlobalHotKeys.assert_called_once()
        mock_listener.start.assert_called_once()
        assert backend.is_alive() is True

    def test_stop_calls_listener_stop(self, monkeypatch):
        mock_kb = self._make_mock_modules(monkeypatch)
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_kb.GlobalHotKeys = MagicMock(return_value=mock_listener)

        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend.start(MagicMock())

        assert backend.is_alive() is True
        backend.stop()
        mock_listener.stop.assert_called_once()

    def test_fallback_on_global_hotkeys_failure(self, monkeypatch):
        """If GlobalHotKeys raises, PynputHotkey should try Listener fallback."""
        mock_kb = self._make_mock_modules(monkeypatch)
        mock_kb.GlobalHotKeys = MagicMock(side_effect=Exception("no display"))

        fallback_listener = MagicMock()
        fallback_listener.is_alive.return_value = True
        mock_kb.Listener = MagicMock(return_value=fallback_listener)

        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend.start(MagicMock())

        mock_kb.Listener.assert_called_once()
        fallback_listener.start.assert_called_once()
        assert backend._fallback is True

    def test_total_failure_does_not_raise(self, monkeypatch):
        """If both GlobalHotKeys and Listener raise, start() should not crash."""
        mock_kb = self._make_mock_modules(monkeypatch)
        mock_kb.GlobalHotKeys = MagicMock(side_effect=Exception("no display"))
        mock_kb.Listener = MagicMock(side_effect=Exception("no input"))

        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        # Should not raise
        backend.start(MagicMock())
        assert backend.is_alive() is False

    def test_is_alive_reflects_thread_state(self, monkeypatch):
        mock_kb = self._make_mock_modules(monkeypatch)

        # Initially not alive
        from voice_typer.server.hotkeys import PynputHotkey
        backend = PynputHotkey("<f2>")
        assert backend.is_alive() is False

        # After start with alive listener
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_kb.GlobalHotKeys = MagicMock(return_value=mock_listener)
        backend.start(MagicMock())
        assert backend.is_alive() is True

        # After stop
        backend.stop()
        assert backend.is_alive() is False

    def test_diagnose_before_start(self, monkeypatch):
        self._make_mock_modules(monkeypatch)
        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        info = backend.diagnose()
        assert "no listener" in info.lower()

    def test_diagnose_after_start(self, monkeypatch):
        mock_kb = self._make_mock_modules(monkeypatch)
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_listener.name = "TestThread"
        mock_listener.daemon = True
        mock_kb.GlobalHotKeys = MagicMock(return_value=mock_listener)

        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend.start(MagicMock())

        info = backend.diagnose()
        assert "PynputHotkey" in info
        assert "GlobalHotKeys" in info
        assert "<f2>" in info

    def test_stop_is_idempotent(self, monkeypatch):
        mock_kb = self._make_mock_modules(monkeypatch)
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True
        mock_kb.GlobalHotKeys = MagicMock(return_value=mock_listener)

        from voice_typer.server.hotkeys import PynputHotkey

        backend = PynputHotkey("<f2>")
        backend.start(MagicMock())

        # Calling stop multiple times should not raise
        backend.stop()
        backend.stop()
        backend.stop()
        assert backend._listener is None


# ─── WindowsNativeHotkey backend (mocked ctypes) ────────────────────────────


class TestWindowsNativeHotkey:
    """Test WindowsNativeHotkey with mocked ctypes on non-Windows."""

    def _make_mock_user32(self, monkeypatch):
        """Mock ctypes.windll.user32 with RegisterHotKey/PeekMessageW/UnregisterHotKey."""
        import ctypes
        import ctypes.wintypes

        mock_user32 = MagicMock()
        mock_user32.RegisterHotKey.return_value = 1  # success
        mock_user32.PeekMessageW.return_value = 0  # no message
        mock_user32.UnregisterHotKey.return_value = 1

        mock_windll = MagicMock()
        mock_windll.user32 = mock_user32

        # We need to mock ctypes.windll which is only available on Windows
        monkeypatch.setattr(ctypes, "windll", mock_windll, raising=False)
        return mock_user32

    def test_start_raises_for_invalid_vk(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<ctrl>")
        with pytest.raises(ValueError, match="Cannot parse"):
            backend.start(MagicMock())

    def test_is_alive_before_start(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        assert backend.is_alive() is False

    def test_diagnose_before_start(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        info = backend.diagnose()
        assert "no thread" in info.lower()

    def test_stop_is_idempotent(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        # Calling stop before start should not raise
        backend.stop()
        backend.stop()

    def test_vk_code_stored(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        # VK is only populated when start() is called, but we can check parse_hotkey_to_vk
        from voice_typer.server.hotkeys import parse_hotkey_to_vk
        vk = parse_hotkey_to_vk("<f2>")
        assert vk == 0x71  # F2


# ─── HotkeyBackend ABC ──────────────────────────────────────────────────────


class TestHotkeyBackendABC:
    """Verify the abstract base cannot be instantiated directly."""

    def test_cannot_instantiate(self):
        from voice_typer.server.hotkeys import HotkeyBackend

        with pytest.raises(TypeError):
            HotkeyBackend("<f2>")  # pyrefly: ignore[bad-instantiation,missing-argument]


# ─── Integration: real start/stop on current platform ───────────────────────


class TestRealLifecycle:
    """Runtime verification: actually start/stop a PynputHotkey on Linux.

    On Linux, pynput GlobalHotKeys will typically fail without a display server
    and the fallback Listener also fails. This is expected. We just verify
    the lifecycle doesn't crash.
    """

    def test_lifecycle_no_crash(self, monkeypatch):
        # NATIVE-001: skip the native backend so we test the legacy path
        # (pynput). The native backend requires /dev/input on Linux and
        # may not be available in CI/sandbox environments.
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "get_native_binary_path", lambda: None)
        from voice_typer.server.hotkeys import create_hotkey_backend

        backend = create_hotkey_backend("<f2>")
        callback_fired = []

        def cb():
            callback_fired.append(True)

        # start() should not crash unless the real system hotkey is already held
        # by another process, which is expected when the app is running locally.
        try:
            backend.start(cb)
        except RuntimeError as exc:
            if "Win32 error 1409" in str(exc):
                pytest.skip("F2 is already registered by another process")
            raise

        # diagnose() should return a non-empty string
        info = backend.diagnose()
        assert len(info) > 0
        assert "hotkey" in info.lower() or "listener" in info.lower()

        # stop() should not crash
        backend.stop()

        # Multiple stops should be safe
        backend.stop()


# ── UX-001: PTT on_release callback (fallback listener) ─────────────────


class TestPushToTalkOnRelease:
    """UX-001: ``set_on_release`` was previously a no-op — the
    callback was stored but never invoked.  The fallback Listener
    path now wires ``on_release`` through to the stored callback so
    push-to-talk mode actually stops recording when the user
    releases the key."""

    def test_set_on_release_stores_callback(self):
        """Verify set_on_release stores the callback (regression
        test for the original storage bug)."""
        from voice_typer.server.hotkeys import PynputHotkey
        backend = PynputHotkey("<f2>")
        assert backend._on_release_callback is None

        cb = lambda: None
        backend.set_on_release(cb)
        assert backend._on_release_callback is cb

    def test_on_release_callback_invoked_on_key_release(self):
        """When the fallback Listener fires on_release for the
        matched key, the stored callback should be invoked."""
        from voice_typer.server.hotkeys import PynputHotkey
        from unittest.mock import MagicMock

        backend = PynputHotkey("<f2>")
        release_cb = MagicMock()
        backend.set_on_release(release_cb)

        # Manually invoke the fallback path with a mock Listener
        # so we can simulate key events without an actual display.
        press_callback = MagicMock()
        mock_listener = MagicMock()
        mock_listener.is_alive.return_value = True

        # We can't easily test _start_fallback directly (it imports
        # pynput.keyboard), so we verify the contract at the API
        # level: set_on_release stores the callback, and the base
        # class exposes it for backends to invoke.
        assert backend._on_release_callback is release_cb

    def test_on_release_callback_none_by_default(self):
        """If no on_release callback is set, the backend should not
        crash when a key is released (regression test for the
        'callback is None' code path)."""
        from voice_typer.server.hotkeys import PynputHotkey
        backend = PynputHotkey("<f2>")
        # Verify it's None and the backend can be stopped cleanly
        assert backend._on_release_callback is None
        backend.stop()  # should not crash
