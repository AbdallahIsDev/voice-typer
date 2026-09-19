"""Tests for voice_typer.server.native_hotkeys module."""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path

import pytest


class TestParseHotkeySpec:
    """Verify hotkey spec parsing."""

    def test_empty_spec_returns_none(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        assert parse_hotkey_spec("") is None
        assert parse_hotkey_spec("   ") is None
        assert parse_hotkey_spec("<>") is None
        assert parse_hotkey_spec("< >") is None

    def test_fn_only(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<fn>")
        assert p is not None
        assert p["modifiers"] == {"fn"}
        assert p["main_key"] is None
        assert p["is_fn_only"] is True
        assert p["is_modifier_only"] is True
        assert p["is_caps_lock"] is False

    def test_globe_alias_for_fn(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<globe>")
        assert p is not None
        assert p["modifiers"] == {"fn"}
        assert p["is_fn_only"] is True

    def test_caps_lock(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<caps_lock>")
        assert p is not None
        assert p["modifiers"] == set()
        assert p["main_key"] == "CapsLock"
        assert p["is_caps_lock"] is True
        assert p["is_modifier_only"] is False

    def test_capslock_no_underscore(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<capslock>")
        assert p is not None
        assert p["main_key"] == "CapsLock"

    def test_modifier_only_alt(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<alt>")
        assert p is not None
        assert p["modifiers"] == {"alt"}
        assert p["main_key"] is None
        assert p["is_modifier_only"] is True
        assert p["is_fn_only"] is False

    def test_modifier_aliases(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        for token in ["<win>", "<super>", "<cmd>"]:
            p = parse_hotkey_spec(token)
            assert p is not None
            assert p["modifiers"] == {"cmd"}, f"{token} should map to cmd"

    def test_single_function_key(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<f2>")
        assert p is not None
        assert p["main_key"] == "F2"
        assert p["modifiers"] == set()

    def test_combo(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<ctrl>+<alt>+v")
        assert p is not None
        assert p["modifiers"] == {"ctrl", "alt"}
        assert p["main_key"] == "V"

    def test_combo_with_modifier_first(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<ctrl>+<f2>")
        assert p is not None
        assert p["modifiers"] == {"ctrl"}
        assert p["main_key"] == "F2"

    def test_combo_with_fn(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        p = parse_hotkey_spec("<fn>+<space>")
        assert p is not None
        assert p["modifiers"] == {"fn"}
        assert p["main_key"] == "Space"

    def test_extra_non_modifier_keys_ignored(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        # Spec with multiple non-modifier keys: only the first is used
        p = parse_hotkey_spec("<a>+<b>")
        assert p is not None
        assert p["main_key"] == "A"

    def test_altgr_alias(self):
        from voice_typer.server.native_hotkeys import parse_hotkey_spec

        for token in ["<altgr>", "<right_alt>", "<ralt>"]:
            p = parse_hotkey_spec(token)
            assert p is not None
            assert p["modifiers"] == {"altgr"}, f"{token} should map to altgr"


class TestNormalizeKeyName:
    """Verify _normalize_key_name converts spec tokens to wire-protocol names."""

    def test_function_keys(self):
        from voice_typer.server.native_hotkeys import _normalize_key_name

        assert _normalize_key_name("f1") == "F1"
        assert _normalize_key_name("f12") == "F12"
        assert _normalize_key_name("f24") == "F24"

    def test_special_keys(self):
        from voice_typer.server.native_hotkeys import _normalize_key_name

        assert _normalize_key_name("space") == "Space"
        assert _normalize_key_name("enter") == "Enter"
        assert _normalize_key_name("esc") == "Esc"
        assert _normalize_key_name("caps_lock") == "CapsLock"
        assert _normalize_key_name("page_up") == "PageUp"

    def test_single_letter(self):
        from voice_typer.server.native_hotkeys import _normalize_key_name

        assert _normalize_key_name("a") == "A"
        assert _normalize_key_name("z") == "Z"

    def test_single_digit(self):
        from voice_typer.server.native_hotkeys import _normalize_key_name

        assert _normalize_key_name("0") == "0"
        assert _normalize_key_name("9") == "9"

    def test_arrows(self):
        from voice_typer.server.native_hotkeys import _normalize_key_name

        assert _normalize_key_name("up") == "Up"
        assert _normalize_key_name("down") == "Down"
        assert _normalize_key_name("left") == "Left"
        assert _normalize_key_name("right") == "Right"


class TestCanonicalModifier:
    """Verify _canonical_modifier converts wire-protocol names to lowercase canonical form."""

    def test_macos_names(self):
        from voice_typer.server.native_hotkeys import _canonical_modifier

        assert _canonical_modifier("Ctrl") == "ctrl"
        assert _canonical_modifier("Shift") == "shift"
        assert _canonical_modifier("Alt") == "alt"
        assert _canonical_modifier("Cmd") == "cmd"

    def test_windows_name(self):
        from voice_typer.server.native_hotkeys import _canonical_modifier

        # Win is Windows-specific name for the Super/Cmd modifier
        assert _canonical_modifier("Win") == "cmd"

    def test_linux_name(self):
        from voice_typer.server.native_hotkeys import _canonical_modifier

        assert _canonical_modifier("Super") == "cmd"

    def test_unknown_returns_none(self):
        from voice_typer.server.native_hotkeys import _canonical_modifier

        assert _canonical_modifier("Foo") is None
        assert _canonical_modifier("") is None


class TestPlatformBackends:
    """Verify each platform backend validates correctly."""

    def test_mac_backend_accepts_fn_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        # Need to patch is_macos since it caches via sys.platform
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
        from voice_typer.server.native_hotkeys import MacNativeHotkey

        b = MacNativeHotkey("<fn>")
        assert b._validate_platform() is None
        assert b.supports_fn is True

    def test_windows_backend_rejects_fn(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
        monkeypatch.setattr(sys, "platform", "win32")
        from voice_typer.server.native_hotkeys import WindowsHookHotkey

        b = WindowsHookHotkey("<fn>")
        err = b._validate_platform()
        assert err is not None
        assert "firmware" in err.lower() or "not supported" in err.lower()

    def test_linux_backend_rejects_fn(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<fn>")
        err = b._validate_platform()
        assert err is not None
        assert "firmware" in err.lower() or "not supported" in err.lower()

    def test_windows_backend_accepts_caps_lock(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
        monkeypatch.setattr(sys, "platform", "win32")
        from voice_typer.server.native_hotkeys import WindowsHookHotkey

        b = WindowsHookHotkey("<caps_lock>")
        assert b._validate_platform() is None

    def test_mac_backend_wrong_platform(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import MacNativeHotkey

        b = MacNativeHotkey("<fn>")
        err = b._validate_platform()
        assert err is not None
        assert "macos" in err.lower()


class TestLineHandling:
    """Verify _handle_line parses wire-protocol events correctly."""

    def test_ready_event(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        b._handle_line("READY")
        assert b._ready_event.is_set()
        assert not b._failed

    def test_error_event(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        b._handle_line("ERROR:Permission denied")
        assert b._failed
        assert b._error_message == "Permission denied"
        assert b._ready_event.is_set()  # unblocks start()

    def test_key_down_caps_lock(self, monkeypatch):
        """KEY_DOWN:CapsLock should fire the callback for <caps_lock> hotkey."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        fired = []
        b._callback = lambda: fired.append("press")
        b._handle_line("KEY_DOWN:CapsLock")
        assert fired == ["press"]

    def test_key_up_caps_lock_fires_release(self, monkeypatch):
        """KEY_UP:CapsLock should fire the release callback."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        released = []
        b._on_release_callback = lambda: released.append("release")
        b._handle_line("KEY_UP:CapsLock")
        assert released == ["release"]

    def test_wrong_key_doesnt_fire(self, monkeypatch):
        """KEY_DOWN:F2 should NOT fire for a <caps_lock> hotkey."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        fired = []
        b._callback = lambda: fired.append("press")
        b._handle_line("KEY_DOWN:F2")
        assert fired == []

    def test_combo_requires_all_modifiers(self, monkeypatch):
        """For <ctrl>+<alt>+v, V alone should NOT fire, need Ctrl+Alt held."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<ctrl>+<alt>+v")
        fired = []
        b._callback = lambda: fired.append("press")

        # Press V without modifiers, should NOT fire
        b._handle_line("KEY_DOWN:V")
        assert fired == []

        b._handle_line("KEY_UP:V")

        # Hold Ctrl+Alt, then press V, should fire
        b._handle_line("MOD_DOWN:Ctrl")
        b._handle_line("MOD_DOWN:Alt")
        b._handle_line("KEY_DOWN:V")
        assert fired == ["press"]

    def test_combo_rejects_extra_modifiers(self, monkeypatch):
        """For <ctrl>+<alt>+v, Ctrl+Alt+Shift+V should NOT fire (extra Shift)."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<ctrl>+<alt>+v")
        fired = []
        b._callback = lambda: fired.append("press")

        b._handle_line("MOD_DOWN:Ctrl")
        b._handle_line("MOD_DOWN:Alt")
        b._handle_line("MOD_DOWN:Shift")  # extra modifier
        b._handle_line("KEY_DOWN:V")
        assert fired == []  # NOT fired

    def test_modifier_only_alt_fires(self, monkeypatch):
        """For <alt> hotkey, MOD_DOWN:Alt alone (no other modifiers) should fire."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<alt>")
        fired = []
        b._callback = lambda: fired.append("press")
        b._handle_line("MOD_DOWN:Alt")
        assert fired == ["press"]

    def test_modifier_only_alt_with_extra_doesnt_fire(self, monkeypatch):
        """For <alt> hotkey, Alt+Ctrl should NOT fire (extra Ctrl)."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<alt>")
        fired = []
        b._callback = lambda: fired.append("press")
        b._handle_line("MOD_DOWN:Ctrl")  # held first
        b._handle_line("MOD_DOWN:Alt")  # now Alt is held, but Ctrl is too
        assert fired == []

    def test_fn_only_fires_on_fn_down(self, monkeypatch):
        """For <fn> hotkey on macOS, FN_DOWN should fire."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "darwin")
        from voice_typer.server.native_hotkeys import MacNativeHotkey

        b = MacNativeHotkey("<fn>")
        fired = []
        b._callback = lambda: fired.append("press")
        b._handle_line("FN_DOWN")
        assert fired == ["press"]
        # FN_UP should fire the release callback
        released = []
        b._on_release_callback = lambda: released.append("release")
        b._handle_line("FN_UP")
        assert released == ["release"]

    def test_win_modifier_normalized_to_cmd(self, monkeypatch):
        """Windows emits MOD_DOWN:Win, should match <win> hotkey."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: False)
        monkeypatch.setattr(sys, "platform", "win32")
        from voice_typer.server.native_hotkeys import WindowsHookHotkey

        b = WindowsHookHotkey("<win>")
        fired = []
        b._callback = lambda: fired.append("press")
        b._handle_line("MOD_DOWN:Win")
        assert fired == ["press"]

    def test_unknown_line_doesnt_crash(self, monkeypatch):
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        # Should not raise
        b._handle_line("UNKNOWN:foo")
        b._handle_line("garbage")
        b._handle_line("")


class TestBinaryDiscovery:
    """Verify get_native_binary_path finds the binary in various locations."""

    def test_returns_none_when_platform_unknown(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "freebsd")
        from voice_typer.server.native_hotkeys import get_native_binary_path

        assert get_native_binary_path() is None

    def test_env_var_override(self, monkeypatch, tmp_path):
        from voice_typer.server.native_hotkeys import get_native_binary_path

        fake = tmp_path / "fake-binary"
        fake.write_text("#!/bin/sh\n")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_BINARY", str(fake))
        monkeypatch.setattr(sys, "platform", "linux")
        assert get_native_binary_path() == fake

    def test_dev_mode_lookup(self, monkeypatch):
        """In dev mode, the binary sits at voice_typer/server/native/<name>."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(sys, "platform", "linux")
        # The actual binary may or may not exist in the test env, just
        path = native_hotkeys.get_native_binary_path()
        # Could be None or a Path, either is OK
        assert path is None or isinstance(path, Path)


class TestConfigDefaults:
    """Verify platform-aware default hotkey."""

    def test_default_is_caps_lock_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils, "is_macos", lambda: True)
        monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
        monkeypatch.setattr(platform_utils, "is_linux", lambda: False)
        from voice_typer.server import config

        assert config._default_hotkey_for_platform() == "<caps_lock>"

    def test_default_is_caps_lock_on_windows(self, monkeypatch):
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils, "is_macos", lambda: False)
        monkeypatch.setattr(platform_utils, "is_windows", lambda: True)
        monkeypatch.setattr(platform_utils, "is_linux", lambda: False)
        from voice_typer.server import config

        assert config._default_hotkey_for_platform() == "<caps_lock>"

    def test_default_is_caps_lock_on_linux(self, monkeypatch):
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils, "is_macos", lambda: False)
        monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
        monkeypatch.setattr(platform_utils, "is_linux", lambda: True)
        from voice_typer.server import config

        assert config._default_hotkey_for_platform() == "<caps_lock>"

    def test_default_is_caps_lock_on_unknown_platform(self, monkeypatch):
        from voice_typer.server import platform_utils

        monkeypatch.setattr(platform_utils, "is_macos", lambda: False)
        monkeypatch.setattr(platform_utils, "is_windows", lambda: False)
        monkeypatch.setattr(platform_utils, "is_linux", lambda: False)
        from voice_typer.server import config

        assert config._default_hotkey_for_platform() == "<caps_lock>"


# Liveness watchdog ─────────────────────────────────────────


class TestLivenessWatchdog:
    """tracks event timestamps and respawns the binary if it stops"""

    def test_watchdog_state_initialized(self, monkeypatch):
        """``__init__`` initializes the watchdog state variables."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        # Watchdog state variables exist.
        assert hasattr(b, "_last_event_received_at")
        assert hasattr(b, "_last_pong_received_at")
        assert hasattr(b, "_pong_supported")
        assert hasattr(b, "_watchdog_thread")
        assert hasattr(b, "_watchdog_stop_event")
        assert hasattr(b, "_on_watchdog_restart_callback")
        # Initial values.
        assert b._last_event_received_at > 0  # set to time.time()
        assert b._last_pong_received_at == 0.0  # no PONG yet
        assert b._pong_supported is False
        assert b._watchdog_thread is None  # not started until _spawn_process
        assert b._on_watchdog_restart_callback is None

    def test_handle_line_updates_last_event_received_at(self, monkeypatch):
        """Non-PONG lines update ``_last_event_received_at``."""
        import time

        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        # Set an old timestamp.
        b._last_event_received_at = 0.0
        old_ts = b._last_event_received_at

        # READY updates the timestamp.
        b._handle_line("READY")
        assert b._last_event_received_at > old_ts

        # KEY_DOWN also updates.
        old_ts = b._last_event_received_at
        time.sleep(0.001)  # ensure time advances
        b._callback = lambda: None  # stub for <caps_lock>
        b._handle_line("KEY_DOWN:CapsLock")
        assert b._last_event_received_at > old_ts

    def test_handle_line_pong_updates_pong_timestamp(self, monkeypatch):
        """PONG lines update ``_last_pong_received_at`` and set ``_pong_supported``."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        assert b._last_pong_received_at == 0.0
        assert b._pong_supported is False

        b._handle_line("PONG")

        assert b._last_pong_received_at > 0.0
        assert b._pong_supported is True

    def test_handle_line_pong_does_not_update_event_timestamp(self, monkeypatch):
        """PONG does NOT update ``_last_event_received_at`` (separate signal)."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        b._last_event_received_at = 1000.0  # old sentinel
        b._last_pong_received_at = 0.0

        b._handle_line("PONG")

        # Event timestamp unchanged (PONG is separate).
        assert b._last_event_received_at == 1000.0
        # PONG timestamp updated.
        assert b._last_pong_received_at > 0.0

    def test_watchdog_constants_exist(self):
        """G4-H-31: the watchdog interval/timeout constants are defined."""
        from voice_typer.server.native_hotkeys import base

        assert hasattr(base, "_WATCHDOG_PING_INTERVAL_SECONDS")
        assert hasattr(base, "_WATCHDOG_PONG_TIMEOUT_SECONDS")
        assert hasattr(base, "_WATCHDOG_RESPAWN_SECONDS")
        # Sanity-check the values match the task spec.
        assert base._WATCHDOG_PING_INTERVAL_SECONDS == 30.0
        assert base._WATCHDOG_PONG_TIMEOUT_SECONDS == 5.0
        assert base._WATCHDOG_RESPAWN_SECONDS == 60.0

    def test_spawn_process_uses_stdin_pipe(self, monkeypatch, tmp_path):
        """G4-H-31: ``_spawn_process`` opens stdin as PIPE (was DEVNULL)"""
        import subprocess
        from unittest.mock import patch

        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        # Create a fake binary.
        fake_bin = tmp_path / "fake-native"
        fake_bin.write_text("#!/bin/sh\nwhile true; do sleep 1; done\n")
        fake_bin.chmod(0o755)
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.get_native_binary_path",
            lambda: fake_bin,
        )
        # Popen call (the TOCTOU re-verification itself is pinned by
        monkeypatch.setattr(
            "voice_typer.server.native_hotkeys.binary_path.verify_native_binary_or_skip",
            lambda _path: True,
        )

        b = LinuxEvdevHotkey("<caps_lock>")
        captured_kwargs = {}
        original_popen = subprocess.Popen

        class FakePopen(original_popen):
            def __init__(self, *args, **kwargs):
                captured_kwargs.update(kwargs)
                super().__init__(*args, **kwargs)

        with patch("subprocess.Popen", FakePopen):
            try:
                b._spawn_process()
            except Exception:
                pass  # May fail on READY timeout, that's OK, we just check the Popen kwargs.
            finally:
                with contextlib.suppress(Exception):
                    b.stop()

        assert captured_kwargs.get("stdin") == subprocess.PIPE, (
            f"G4-H-31: _spawn_process must use stdin=subprocess.PIPE so the "
            f"watchdog can write PING; got stdin={captured_kwargs.get('stdin')!r}"
        )


class TestWatchdogRespawnRace:
    """FR-21 (High): the watchdog's respawn path (``stop()`` + ``start(cb)``)"""

    def test_shutdown_requested_initialized_false(self, monkeypatch):
        """``__init__`` initializes ``_shutdown_requested`` to False."""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        assert hasattr(b, "_shutdown_requested"), (
            "FR-21: SubprocessHotkeyBackend must have a _shutdown_requested attribute"
        )
        assert b._shutdown_requested is False, "FR-21: _shutdown_requested must initialize to False"

    def test_stop_default_latches_shutdown_requested(self, monkeypatch):
        """``stop()`` (default ``shutdown=True``) latches"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        assert b._shutdown_requested is False

        b.stop()  # default shutdown=True

        assert b._shutdown_requested is True, "FR-21: stop() (default shutdown=True) must latch _shutdown_requested"
        assert b._stop_event.is_set()

    def test_stop_latches_shutdown_before_idempotency_guard(self, monkeypatch):
        """FR-21 regression: the main-thread ``stop()`` must latch"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        assert b._shutdown_requested is False

        # Simulate the watchdog's cleanup stop() having already set
        b._stop_event.set()

        # Main thread calls stop() (default shutdown=True).
        b.stop()

        assert b._shutdown_requested is True, (
            "FR-21: stop() must set _shutdown_requested=True BEFORE the "
            "idempotency guard returns, so a concurrent main-thread stop() "
            "latches the shutdown request even when _stop_event is already set"
        )

    def test_watchdog_cleanup_stop_does_not_latch_shutdown(self, monkeypatch):
        """FR-21: ``stop(shutdown=False)`` (used by the watchdog's own"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        assert b._shutdown_requested is False

        # Watchdog's cleanup stop: shutdown=False.
        b.stop(shutdown=False)

        # Must NOT latch the shutdown flag.
        assert b._shutdown_requested is False, (
            "FR-21: stop(shutdown=False) must not set _shutdown_requested, "
            "the watchdog's cleanup stop is a respawn step, not a shutdown"
        )
        # But it MUST still set _stop_event (tear down the backend).
        assert b._stop_event.is_set(), (
            "FR-21: stop(shutdown=False) must still set _stop_event to "
            "tear down the backend (only the shutdown latch is suppressed)"
        )

    def test_watchdog_does_not_respawn_after_shutdown_requested(self, monkeypatch):
        """FR-21 end-to-end regression: if ``_shutdown_requested`` is True"""
        from unittest.mock import MagicMock

        from voice_typer.server import native_hotkeys
        from voice_typer.server.native_hotkeys import base

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        b._callback = lambda: None  # stashed callback for respawn

        b._last_event_received_at = 0.0
        b._last_pong_received_at = 0.0
        b._pong_supported = True

        # Make the watchdog's PING/PONG waits return fast (otherwise
        monkeypatch.setattr(base, "_WATCHDOG_PING_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(base, "_WATCHDOG_PONG_TIMEOUT_SECONDS", 0.01)

        # Fake alive process so the PING write proceeds (the loop
        fake_proc = MagicMock()
        fake_proc.poll.return_value = None  # alive
        fake_proc.stdin = MagicMock()
        b._process = fake_proc

        start_calls: list = []
        stop_shutdown_flags: list = []

        def tracking_stop(*, shutdown: bool = True) -> None:
            stop_shutdown_flags.append(shutdown)
            # Simulate the side-effect of stop() that the watchdog
            b._stop_event.set()
            b._watchdog_stop_event.set()

        b.stop = tracking_stop
        b.start = lambda cb: start_calls.append(cb)

        # Race simulation: the main thread called stop() (shutdown=True)
        b._shutdown_requested = True

        # Run the watchdog loop inline (deterministic, no real thread).
        b._watchdog_loop()

        assert start_calls == [], (
            f"FR-21: watchdog must NOT call start() when _shutdown_requested is True; got start_calls={start_calls}"
        )
        # The watchdog's cleanup stop must use shutdown=False (otherwise
        assert stop_shutdown_flags == [False], (
            f"FR-21: watchdog cleanup must call stop(shutdown=False); got stop_shutdown_flags={stop_shutdown_flags}"
        )

    def test_watchdog_respawns_when_shutdown_not_requested(self, monkeypatch):
        """FR-21 negative control: when ``_shutdown_requested`` is False"""
        from unittest.mock import MagicMock

        from voice_typer.server import native_hotkeys
        from voice_typer.server.native_hotkeys import base

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<caps_lock>")
        cb = lambda: None  # noqa: E731
        b._callback = cb

        # Hung binary.
        b._last_event_received_at = 0.0
        b._last_pong_received_at = 0.0
        b._pong_supported = True

        monkeypatch.setattr(base, "_WATCHDOG_PING_INTERVAL_SECONDS", 0.01)
        monkeypatch.setattr(base, "_WATCHDOG_PONG_TIMEOUT_SECONDS", 0.01)

        fake_proc = MagicMock()
        fake_proc.poll.return_value = None
        fake_proc.stdin = MagicMock()
        b._process = fake_proc

        start_calls: list = []

        def tracking_stop(*, shutdown: bool = True) -> None:
            b._stop_event.set()
            b._watchdog_stop_event.set()

        b.stop = tracking_stop
        b.start = lambda c: start_calls.append(c)

        # _shutdown_requested is False (no concurrent shutdown).
        assert b._shutdown_requested is False

        b._watchdog_loop()

        assert start_calls == [cb], (
            "FR-21 negative control: watchdog must still call start(cb) "
            f"when _shutdown_requested is False; got start_calls={start_calls}"
        )


class TestMultiSpecPooling:
    """
    Verify the multi-spec matcher API on ``SubprocessHotkeyBackend``.
    These tests pin the contract that one backend instance can match
    """

    def test_add_extra_matcher_parses_spec(self, monkeypatch):
        """``add_extra_matcher`` parses the spec and stores it. The"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        assert b._extra_matchers == []
        b.add_extra_matcher("esc", "<esc>")
        assert len(b._extra_matchers) == 1
        m = b._extra_matchers[0]
        assert m["role"] == "esc"
        assert m["parsed"] is not None
        assert m["parsed"]["main_key"] == "Esc"
        # Primary spec is unaffected.
        assert b._parsed is not None
        assert b._parsed["main_key"] == "F2"

    def test_add_extra_matcher_idempotent_on_role(self, monkeypatch):
        """Calling ``add_extra_matcher`` twice with the same role"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        b.add_extra_matcher("esc", "<esc>")
        b.set_role_callback("esc", lambda: None)
        # Re-register with a different spec (shouldn't normally happen
        b.add_extra_matcher("esc", "<f4>")
        assert len(b._extra_matchers) == 1, (
            f"add_extra_matcher must not duplicate entries for the same role; got {b._extra_matchers}"
        )
        # Callback preserved across the spec replacement.
        assert b._extra_matchers[0]["callback"] is not None
        assert b._extra_matchers[0]["parsed"]["main_key"] == "F4"

    def test_add_extra_matcher_rejects_unparseable_spec(self, monkeypatch):
        """An unparseable spec raises ``ValueError`` so the caller"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        with pytest.raises(ValueError):
            b.add_extra_matcher("esc", "")

    def test_set_role_callback_routes_to_extra_matcher(self, monkeypatch):
        """callback on the matching extra matcher, NOT on the primary"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        esc_cb = lambda: None  # noqa: E731
        b.add_extra_matcher("esc", "<esc>")
        b.set_role_callback("esc", esc_cb)
        assert b._extra_matchers[0]["callback"] is esc_cb
        # Primary callback slot is unaffected.
        assert getattr(b, "_callback", None) is not esc_cb

    def test_set_role_callback_dictation_sets_primary(self, monkeypatch):
        """``set_role_callback(\"dictation\", cb)`` sets the primary"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        cb = lambda: None  # noqa: E731
        b.set_role_callback("dictation", cb)
        assert b._callback is cb

    def test_set_role_callback_unknown_role_raises(self, monkeypatch):
        """``set_role_callback`` for a role that wasn't registered via"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        with pytest.raises(KeyError):
            b.set_role_callback("esc", lambda: None)

    def test_remove_extra_matcher(self, monkeypatch):
        """``remove_extra_matcher`` drops the matcher for the given"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        b.add_extra_matcher("esc", "<esc>")
        b.add_extra_matcher("repaste", "<ctrl>+<shift>+v")
        assert len(b._extra_matchers) == 2
        b.remove_extra_matcher("esc")
        assert len(b._extra_matchers) == 1
        assert b._extra_matchers[0]["role"] == "repaste"
        # Removing a non-existent role is a no-op.
        b.remove_extra_matcher("nonexistent")
        assert len(b._extra_matchers) == 1

    def test_extra_matcher_add_remove_readd_no_leak(self, monkeypatch):
        """Role teardown + re-register cycle must not leak matcher"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")

        def first_cb() -> str:
            return "first"

        b.add_extra_matcher("esc", "<esc>")
        b.set_role_callback("esc", first_cb)
        assert len(b._extra_matchers) == 1

        b.remove_extra_matcher("esc")
        assert b._extra_matchers == [], "remove must drop the matcher object entirely"
        # Role is unknown after remove: set_role_callback must raise.
        with pytest.raises(KeyError):
            b.set_role_callback("esc", lambda: None)

        def second_cb() -> str:
            return "second"

        b.add_extra_matcher("esc", "<f4>")
        b.set_role_callback("esc", second_cb)
        assert len(b._extra_matchers) == 1, "re-add after remove must not duplicate"
        assert b._extra_matchers[0]["parsed"]["main_key"] == "F4"
        assert b._extra_matchers[0]["callback"] is second_cb

        # Repeated remove is still a no-op / stays empty.
        b.remove_extra_matcher("esc")
        assert b._extra_matchers == []

    def test_extra_matcher_fires_on_matching_event(self, monkeypatch):
        """extra matcher's callback fires, independently of the primary"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        dictation_fired: list[str] = []
        esc_fired: list[str] = []
        b._callback = lambda: dictation_fired.append("press")
        b.add_extra_matcher("esc", "<esc>")
        b.set_role_callback("esc", lambda: esc_fired.append("press"))

        # Pressing ESC should fire the ESC extra matcher only.
        b._handle_line("KEY_DOWN:Esc")
        assert esc_fired == ["press"]
        assert dictation_fired == [], "Primary matcher must NOT fire for ESC"

    def test_primary_matcher_fires_for_primary_spec(self, monkeypatch):
        """When the event stream matches the primary spec, the"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<f2>")
        dictation_fired: list[str] = []
        esc_fired: list[str] = []
        b._callback = lambda: dictation_fired.append("press")
        b.add_extra_matcher("esc", "<esc>")
        b.set_role_callback("esc", lambda: esc_fired.append("press"))

        # Pressing F2 should fire the primary (dictation) matcher only.
        b._handle_line("KEY_DOWN:F2")
        assert dictation_fired == ["press"]
        assert esc_fired == [], "ESC extra matcher must NOT fire for F2"

    def test_no_double_fire_when_both_specs_could_match(self, monkeypatch):
        """At most ONE matcher fires per event. If the primary spec"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        # Both primary and extra matcher use the same spec.
        b = LinuxEvdevHotkey("<f2>")
        primary_fired: list[str] = []
        extra_fired: list[str] = []
        b._callback = lambda: primary_fired.append("press")
        b.add_extra_matcher("extra", "<f2>")
        b.set_role_callback("extra", lambda: extra_fired.append("press"))

        b._handle_line("KEY_DOWN:F2")
        # Primary fires first (it's tried first in _try_match), extra
        assert primary_fired == ["press"]
        assert extra_fired == [], "Extra matcher must NOT fire when primary already matched (short-circuit)"

    def test_delegated_start_skips_spawn(self, monkeypatch):
        """marks itself ready, but does NOT spawn a subprocess. This is"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<esc>")
        b._delegated = True
        cb = lambda: None  # noqa: E731
        b.start(cb)
        # Callback recorded, ready_event set, but no process spawned.
        assert b._callback is cb
        assert b._ready_event.is_set()
        assert b._process is None
        assert b._reader_thread is None
        assert b.is_alive() is True

    def test_delegated_stop_is_noop_for_subprocess(self, monkeypatch):
        """A delegated backend's ``stop()`` sets ``_stop_event`` but"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<esc>")
        b._delegated = True
        b.start(lambda: None)
        b.stop()
        assert b._stop_event.is_set()
        assert b.is_alive() is False

    def test_delegated_stop_does_not_log_stopping_line(self, monkeypatch, caplog):
        """``[NATIVE-HOTKEY] Stopping <platform> backend`` INFO line."""
        import logging as _logging

        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<esc>")
        b._delegated = True
        b.start(lambda: None)
        with caplog.at_level(_logging.INFO):
            b.stop()
        stopping = [r for r in caplog.records if "Stopping" in r.getMessage()]
        assert stopping == []
        # The stop is still effective (event latched, no longer alive).
        assert b._stop_event.is_set()
        assert b.is_alive() is False

    def test_delegated_backend_callback_never_invoked(self, monkeypatch):
        """Even if a delegated backend's ``_handle_line`` is called"""
        from voice_typer.server import native_hotkeys

        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey

        b = LinuxEvdevHotkey("<esc>")
        b._delegated = True
        fired: list[str] = []
        b.start(lambda: fired.append("press"))
        # No reader thread exists to call _handle_line.
        assert b._reader_thread is None
        # The callback IS set (is_alive relies on _ready_event, not
        assert b._callback is not None


class TestHotkeyDispatcherPooling:
    """Verify ``HotkeyDispatcher`` pools ESC + repaste into the shared"""

    def test_shared_backend_set_after_register(self, monkeypatch):
        """After ``register()`` succeeds, ``_shared_backend`` is the"""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        app = SimpleNamespace()
        app.config = SimpleNamespace(
            hotkey="<f2>",
            recording_mode="toggle",
            esc_cancel_enabled=False,
            repaste_hotkey=None,
            save=MagicMock(return_value=True),
        )
        app.tray = MagicMock()
        app._stop_dictation = MagicMock()
        app.toggle_dictation = MagicMock()
        app._cancel_dictation = MagicMock()
        app.repaste_last = MagicMock()
        dispatcher = HotkeyDispatcher(app)

        new_backend = MagicMock()
        new_backend.is_alive.return_value = True
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            MagicMock(return_value=new_backend),
        )
        result = dispatcher.register()
        assert result is True
        assert dispatcher._shared_backend is new_backend
        assert dispatcher._hotkey_backend is new_backend

    def test_shared_backend_cleared_on_stop_all(self):
        """``stop_all`` clears ``_shared_backend`` so a post-shutdown"""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        app = SimpleNamespace()
        app.config = SimpleNamespace(
            hotkey="<f2>",
            recording_mode="toggle",
            esc_cancel_enabled=False,
            repaste_hotkey=None,
            save=MagicMock(return_value=True),
        )
        app.tray = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        dispatcher._hotkey_backend = MagicMock()
        dispatcher._shared_backend = dispatcher._hotkey_backend
        dispatcher.stop_all()
        assert dispatcher._shared_backend is None

    def test_native_of_returns_native_for_adapter(self, monkeypatch):
        """``_native_of`` returns the wrapped ``SubprocessHotkeyBackend``"""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        app = SimpleNamespace()
        app.config = SimpleNamespace(
            hotkey="<f2>",
            recording_mode="toggle",
            esc_cancel_enabled=False,
            repaste_hotkey=None,
            save=MagicMock(return_value=True),
        )
        app.tray = MagicMock()
        dispatcher = HotkeyDispatcher(app)

        # A non-adapter backend returns None.
        assert dispatcher._native_of(MagicMock(spec=[])) is None
        assert dispatcher._native_of(None) is None

        # An adapter wrapping a native with add_extra_matcher returns
        native = MagicMock()
        adapter = MagicMock()
        adapter._native = native
        # MagicMock auto-has add_extra_matcher, so _native_of returns it.
        assert dispatcher._native_of(adapter) is native

    def test_pool_aux_into_shared_returns_false_when_no_shared(self):
        """legacy backend without ``add_extra_matcher``),"""
        from types import SimpleNamespace
        from unittest.mock import MagicMock

        from voice_typer.server.hotkey_dispatcher import HotkeyDispatcher

        app = SimpleNamespace()
        app.config = SimpleNamespace(
            hotkey="<f2>",
            recording_mode="toggle",
            esc_cancel_enabled=False,
            repaste_hotkey=None,
            save=MagicMock(return_value=True),
        )
        app.tray = MagicMock()
        dispatcher = HotkeyDispatcher(app)
        # No shared backend set.
        assert dispatcher._shared_backend is None
        result = dispatcher._pool_aux_into_shared("esc", "<esc>", lambda: None, None)
        assert result is False
