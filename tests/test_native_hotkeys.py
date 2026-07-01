"""Tests for voice_typer.server.native_hotkeys module.

Covers:
- Hotkey spec parsing (parse_hotkey_spec)
- Key name normalization (_normalize_key_name)
- Modifier canonicalization (_canonical_modifier)
- Backend factory (create_native_backend) and platform validation
- Wire-protocol line handling (_handle_line) and hotkey matching
- NativeHotkeyRecorder (capture mode)
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ─── parse_hotkey_spec ─────────────────────────────────────────────────────


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
        # win, super, cmd all map to "cmd"
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


# ─── Key name normalization ────────────────────────────────────────────────


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


# ─── Modifier canonicalization ─────────────────────────────────────────────


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


# ─── Platform backend factory & validation ─────────────────────────────────


class TestPlatformBackends:
    """Verify each platform backend validates correctly."""

    def test_mac_backend_accepts_fn_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        from voice_typer.server.platform_utils import is_macos
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


# ─── Wire-protocol line handling ───────────────────────────────────────────


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
        """For <ctrl>+<alt>+v, V alone should NOT fire — need Ctrl+Alt held."""
        from voice_typer.server import native_hotkeys
        monkeypatch.setattr(native_hotkeys, "is_linux", lambda: True)
        monkeypatch.setattr(native_hotkeys, "is_macos", lambda: False)
        monkeypatch.setattr(native_hotkeys, "is_windows", lambda: False)
        monkeypatch.setattr(sys, "platform", "linux")
        from voice_typer.server.native_hotkeys import LinuxEvdevHotkey
        b = LinuxEvdevHotkey("<ctrl>+<alt>+v")
        fired = []
        b._callback = lambda: fired.append("press")

        # Press V without modifiers — should NOT fire
        b._handle_line("KEY_DOWN:V")
        assert fired == []

        # Hold Ctrl+Alt, then press V — should fire
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
        b._handle_line("MOD_DOWN:Alt")   # now Alt is held, but Ctrl is too
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
        """Windows emits MOD_DOWN:Win — should match <win> hotkey."""
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


# ─── Binary discovery ──────────────────────────────────────────────────────


class TestBinaryDiscovery:
    """Verify get_native_binary_path finds the binary in various locations."""

    def test_returns_none_when_platform_unknown(self, monkeypatch):
        from voice_typer.server import native_hotkeys
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
        # The actual binary may or may not exist in the test env — just
        # verify the lookup logic doesn't crash.
        path = native_hotkeys.get_native_binary_path()
        # Could be None or a Path — either is OK
        assert path is None or isinstance(path, Path)


# ─── Config defaults ───────────────────────────────────────────────────────


class TestConfigDefaults:
    """Verify platform-aware default hotkey.

    FIX-HOTKEY-ARCHITECTURE: the default is now ``<caps_lock>`` on ALL
    platforms (including macOS). Previously macOS defaulted to ``<fn>``
    and unknown platforms to ``<f2>``; both are no longer used as
    defaults because Caps Lock is universally present and the Fn key
    is firmware-only on Windows/Linux laptops.
    """

    def test_default_is_caps_lock_on_macos(self, monkeypatch):
        monkeypatch.setattr(sys, "platform", "darwin")
        from voice_typer.server import config
        monkeypatch.setattr(config, "is_macos", lambda: True)
        monkeypatch.setattr(config, "is_windows", lambda: False)
        monkeypatch.setattr(config, "is_linux", lambda: False)
        assert config._default_hotkey_for_platform() == "<caps_lock>"

    def test_default_is_caps_lock_on_windows(self, monkeypatch):
        from voice_typer.server import config
        monkeypatch.setattr(config, "is_macos", lambda: False)
        monkeypatch.setattr(config, "is_windows", lambda: True)
        monkeypatch.setattr(config, "is_linux", lambda: False)
        assert config._default_hotkey_for_platform() == "<caps_lock>"

    def test_default_is_caps_lock_on_linux(self, monkeypatch):
        from voice_typer.server import config
        monkeypatch.setattr(config, "is_macos", lambda: False)
        monkeypatch.setattr(config, "is_windows", lambda: False)
        monkeypatch.setattr(config, "is_linux", lambda: True)
        assert config._default_hotkey_for_platform() == "<caps_lock>"

    def test_default_is_caps_lock_on_unknown_platform(self, monkeypatch):
        from voice_typer.server import config
        monkeypatch.setattr(config, "is_macos", lambda: False)
        monkeypatch.setattr(config, "is_windows", lambda: False)
        monkeypatch.setattr(config, "is_linux", lambda: False)
        assert config._default_hotkey_for_platform() == "<caps_lock>"
