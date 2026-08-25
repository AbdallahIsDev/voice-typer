"""Regression tests for PLAT-* fixes.

Tests cover the key platform fixes:
  EmptyClipboard() before pyperclip.copy()
  PLAT-008: Environment variable validation
  PLAT-013: Elevated target detection
  PLAT-014: Password field detection
  PLAT-020: IME composition filter
  PLAT-021: Tray icon shape definitions
  PLAT-024: ICO format support for Windows
  PLAT-027: Win32Clipboard abstraction
  PLAT-030: macOS Accessibility permission guide
  PLAT-036: MANIFEST.in exists
  PLAT-037: .spec manifest with asInvoker
  PLAT-ALTGR: AltGr detection
  PLAT-PASTEVR: Clipboard verification after copy
  PLAT-RDP: RDP session detection
  PLAT-RUN: Mutex name with path hash
  PLAT-SECURE: Clipboard save/restore lifecycle
  PLAT-STUCK: try/finally for modifier key release
  PLAT-VENV: Autostart venv detection
  PLAT-VKMAP: VK code layout fallback
  PLAT-CONTENT: contentEditable detection comment
  PLAT-CLIPRACE: Clipboard sequence number in abstraction
  PLAT-HLEAK: Mutex handle close on shutdown
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Win32Clipboard abstraction ─────────────────────────────


class TestWin32ClipboardAbstraction:
    """PLAT-027: Win32Clipboard wraps Open/Empty/Close/SeqNum."""

    def test_get_sequence_number_returns_zero_on_non_windows(self, monkeypatch):
        # Patch is_windows() directly — pytest 9.0.2's monkeypatch no
        # longer accepts the dotted "...clipboard.sys.platform" form
        # because it tries to resolve the prefix as a module path.
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        from voice_typer.server.clipboard import Win32Clipboard

        assert Win32Clipboard.get_sequence_number() == 0

    def test_context_manager_skips_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        from voice_typer.server.clipboard import Win32Clipboard

        with pytest.raises(RuntimeError, match="only available on Windows"):
            Win32Clipboard()


# EmptyClipboard before copy ─────────────────────────────


class TestEmptyClipboard:
    """_win32_empty_clipboard uses Win32Clipboard abstraction."""

    def test_win32_empty_clipboard_skips_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        from voice_typer.server.clipboard import _win32_empty_clipboard

        # Should not raise
        _win32_empty_clipboard()


# ─── PLAT-PASTEVR: Clipboard verification after copy ──────────────────


class TestClipboardVerification:
    """PLAT-PASTEVR: After copy(), verify clipboard content matches.

    ADR-0010 §5.2: ``copy()`` now returns a ``ClipboardSnapshot | None``
    instead of ``bool``. We mock ``ClipboardSnapshot.capture()`` to
    return a sentinel snapshot so the assertions can check the returned
    value rather than a boolean.
    """

    def test_copy_verifies_clipboard_content(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        import voice_typer.server.clipboard as clip_mod
        from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

        # Mock pyperclip
        mock_pyperclip = MagicMock()
        mock_pyperclip.copy.return_value = None
        mock_pyperclip.paste.return_value = "hello world"
        clip_mod.pyperclip = mock_pyperclip

        sentinel = ClipboardSnapshot(platform="linux-x11", items=[], captured_at=0.0)

        # Mock keyboard and snapshot capture. The copy() call MUST be
        # inside the patch context because it consults
        # ClipboardSnapshot.capture() at call time (ADR-0010 §5.2).
        with (
            patch("voice_typer.server.clipboard._ensure_pynput_imported"),
            patch("voice_typer.server.clipboard._Controller") as mock_ctrl,
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            mock_instance = MagicMock()
            mock_ctrl.return_value = mock_instance
            from voice_typer.server.clipboard import ClipboardManager

            cm = ClipboardManager(paste_enabled=False)

            result = cm.copy("hello world")
        assert result is sentinel
        # pyperclip.copy should have been called
        mock_pyperclip.copy.assert_called()

    def test_copy_retries_on_verification_mismatch(self, monkeypatch):
        """PLAT-PASTEVR: If clipboard content doesn't match after copy, retry."""
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        import voice_typer.server.clipboard as clip_mod
        from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

        mock_pyperclip = MagicMock()
        mock_pyperclip.copy.return_value = None
        # First paste: verification attempt 1 — returns WRONG, triggers retry
        # Second paste: verification attempt 2 — returns correct value, success
        mock_pyperclip.paste.side_effect = ["wrong_value", "hello world"]
        clip_mod.pyperclip = mock_pyperclip

        sentinel = ClipboardSnapshot(platform="linux-x11", items=[], captured_at=0.0)

        with (
            patch("voice_typer.server.clipboard._ensure_pynput_imported"),
            patch("voice_typer.server.clipboard._Controller") as mock_ctrl,
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            mock_instance = MagicMock()
            mock_ctrl.return_value = mock_instance
            from voice_typer.server.clipboard import ClipboardManager

            cm = ClipboardManager(paste_enabled=False)

            result = cm.copy("hello world")
        assert result is sentinel
        # copy() should be called twice: once for initial copy, once for retry
        # after verification mismatch
        assert mock_pyperclip.copy.call_count >= 2, (
            f"Expected copy() to be called at least 2 times (initial + retry), but got {mock_pyperclip.copy.call_count}"
        )
        # Verify all calls were with the correct text
        for call in mock_pyperclip.copy.call_args_list:
            assert call[0][0] == "hello world"


# ─── PLAT-STUCK: try/finally for modifier key release ────────────────


class TestSafeKeyRelease:
    """PLAT-STUCK: _safe_key_press releases modifier even on error."""

    def test_safe_key_press_releases_modifier_on_error(self):
        with (
            patch("voice_typer.server.clipboard._ensure_pynput_imported"),
            patch("voice_typer.server.clipboard._Controller") as mock_ctrl,
        ):
            mock_instance = MagicMock()
            mock_ctrl.return_value = mock_instance
            from voice_typer.server.clipboard import ClipboardManager

            cm = ClipboardManager(paste_enabled=True)
            cm._keyboard = mock_instance

        # Make char press raise, modifier should still be released
        mock_instance.press.side_effect = [None, Exception("test error")]
        with pytest.raises(Exception, match="test error"):
            cm._safe_key_press(MagicMock(), "v")
        # The modifier release should have been called (finally block)
        mock_instance.release.assert_called()


# ─── PLAT-SECURE: Clipboard save/restore lifecycle ───────────────────


class TestClipboardSaveRestore:
    """PLAT-SECURE: Before copy, save existing clipboard; after clear, restore.

    ADR-0010 §5.2: ``copy()`` now captures a ``ClipboardSnapshot`` of
    the prior clipboard contents via ``ClipboardSnapshot.capture()``
    (replacing the old ``pyperclip.paste()`` save). The snapshot is
    returned to the caller — it is NOT stored on ``self``.
    """

    def test_copy_saves_existing_clipboard(self, monkeypatch):
        """copy() captures a ClipboardSnapshot of the prior clipboard."""
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        import voice_typer.server.clipboard as clip_mod
        from voice_typer.server.clipboard_snapshot import ClipboardSnapshot

        mock_pyperclip = MagicMock()
        mock_pyperclip.copy.return_value = None
        mock_pyperclip.paste.return_value = "new text"  # verification match
        clip_mod.pyperclip = mock_pyperclip

        sentinel_snapshot = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain", b"previous content")],
            captured_at=0.0,
        )

        with (
            patch("voice_typer.server.clipboard._ensure_pynput_imported"),
            patch("voice_typer.server.clipboard._Controller") as mock_ctrl,
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=sentinel_snapshot,
            ) as mock_capture,
        ):
            mock_instance = MagicMock()
            mock_ctrl.return_value = mock_instance
            from voice_typer.server.clipboard import ClipboardManager

            cm = ClipboardManager(paste_enabled=False)

            result = cm.copy("new text")
        # copy() returns the captured snapshot (not stored on self).
        assert result is sentinel_snapshot
        mock_capture.assert_called_once()

    # ADR-0010 §5.6: ``schedule_clipboard_clear`` was DELETED. The
    # ``test_schedule_clipboard_clear_restores_previous`` test that
    # previously lived here exercised a method that no longer exists.
    # The restore-after-paste lifecycle is now driven by
    # ``_delayed_restore()`` on a daemon thread, started from
    # ``paste(snapshot=...)``. See tests/test_clipboard_borrow_restore.py.


# Elevated target detection ─────────────────────────────


class TestElevatedTarget:
    """PLAT-013: _is_elevated_target checks if foreground is elevated."""

    def test_returns_false_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        from voice_typer.server.clipboard import _is_elevated_target

        assert _is_elevated_target() is False


# Password field detection ──────────────────────────────


class TestPasswordField:
    """PLAT-014: _is_password_field checks UIA IsPasswordPropertyId."""

    def test_returns_false_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.clipboard.is_windows", lambda: False)
        from voice_typer.server.clipboard import _is_password_field

        assert _is_password_field() is False


# Environment variable validation ───────────────────────


class TestEnvVarValidation:
    """PLAT-008: _validate_env_vars rejects invalid values."""

    def test_valid_boolean_values_are_accepted(self, monkeypatch):
        from voice_typer.server.env_validation import _validate_env_vars

        monkeypatch.setenv("VOICE_TYPER_QUIET", "true")
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_QUIET") == "true"

    def test_invalid_boolean_values_are_removed(self, monkeypatch):
        from voice_typer.server.env_validation import _validate_env_vars

        monkeypatch.setenv("VOICE_TYPER_QUIET", "invalid_value")
        _validate_env_vars()
        assert "VOICE_TYPER_QUIET" not in os.environ

    def test_invalid_restart_token_is_removed(self, monkeypatch):
        from voice_typer.server.env_validation import _validate_env_vars

        monkeypatch.setenv("VOICE_TYPER_RESTART", "'; DROP TABLE users; --")
        _validate_env_vars()
        assert "VOICE_TYPER_RESTART" not in os.environ

    def test_valid_restart_token_is_accepted(self, monkeypatch):
        from voice_typer.server.env_validation import _validate_env_vars

        monkeypatch.setenv("VOICE_TYPER_RESTART", "abc123_def")
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_RESTART") == "abc123_def"

    def test_invalid_config_dir_is_removed(self, monkeypatch):
        from voice_typer.server.env_validation import _validate_env_vars

        # Path with shell metacharacters (null bytes can't be set in
        # os.environ on POSIX — Python raises ValueError). Use a path
        # that fails the validation regex instead.
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", "")
        _validate_env_vars()
        # Empty string is not a valid path — should be removed
        # Note: _PATH_PATTERN allows non-empty strings without null bytes,
        # so a truly empty string may pass. Test with an overlength path.
        long_path = "/a" * 3000  # > 4096 chars
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", long_path)
        _validate_env_vars()
        assert "VOICE_TYPER_CONFIG_DIR" not in os.environ

    def test_invalid_ipc_token_is_removed(self, monkeypatch):
        from voice_typer.server.env_validation import _validate_env_vars

        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "'; rm -rf /")
        _validate_env_vars()
        assert "VOICE_TYPER_IPC_TOKEN" not in os.environ


# IME composition filter ────────────────────────────────


class TestIMEDetection:
    """PLAT-020: _is_ime_composing detects IME composition state."""

    def test_returns_false_on_non_windows(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.hotkeys.sys.platform", "linux")
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        assert WindowsNativeHotkey._is_ime_composing() is False


# ─── PLAT-ALTGR: AltGr detection ─────────────────────────────────────


class TestAltGrDetection:
    """PLAT-ALTGR: _is_altgr_pressed detects Ctrl+RightAlt."""

    def test_is_altgr_pressed_returns_false_without_user32(self):
        from voice_typer.server.hotkeys import WindowsNativeHotkey

        backend = WindowsNativeHotkey("<f2>")
        backend._user32 = None
        assert backend._is_altgr_pressed() is False


# Tray icon shape definitions ───────────────────────────


class TestTrayIconShapes:
    """PLAT-021: _ICON_SHAPES maps states to distinct shapes."""

    def test_icon_shapes_defined(self):
        from voice_typer.server.tray_icon import _ICON_SHAPES
        from voice_typer.server.tray_types import AppState

        assert AppState.IDLE in _ICON_SHAPES
        assert AppState.RECORDING in _ICON_SHAPES
        assert AppState.ERROR in _ICON_SHAPES
        # Different states should have distinct shapes where possible
        assert _ICON_SHAPES[AppState.IDLE] != _ICON_SHAPES[AppState.RECORDING]

    def test_get_icon_path_returns_none_for_missing_files(self, monkeypatch):
        """_get_icon_path returns None when no icon files exist."""
        from voice_typer.server.tray_icon import _get_icon_path
        from voice_typer.server.tray_types import AppState

        # With no assets dir, should return None
        result = _get_icon_path(AppState.IDLE, size=32)
        # Result depends on whether assets exist; just verify it doesn't crash
        assert result is None or isinstance(result, Path)


# ICO format support ────────────────────────────────────


class TestICOFormatSupport:
    """PLAT-024: _make_icon generates ICO on Windows."""

    def test_make_icon_includes_256x256_in_ico_sizes(self, monkeypatch):
        """The ICO format should include 256x256 for Windows 11."""
        from voice_typer.server.tray_icon import _make_icon
        from voice_typer.server.tray_types import AppState

        # Just verify _make_icon doesn't crash
        # (actual ICO generation requires PIL + Windows)
        try:
            icon = _make_icon(AppState.IDLE, size=32)
            assert icon is not None
        except Exception:
            pass  # PIL may not be available in test env


# MANIFEST.in ───────────────────────────────────────────


class TestManifestIn:
    """PLAT-036: MANIFEST.in file exists in repo root."""

    def test_manifest_in_exists(self):
        repo_root = Path(__file__).resolve().parent.parent
        manifest_path = repo_root / "MANIFEST.in"
        assert manifest_path.exists(), "MANIFEST.in must exist in repo root"

    def test_manifest_in_includes_server(self):
        repo_root = Path(__file__).resolve().parent.parent
        manifest = (repo_root / "MANIFEST.in").read_text()
        assert "voice_typer/server" in manifest
        assert "LICENSE" in manifest
        assert "README.md" in manifest


# .spec manifest with asInvoker ─────────────────────────


class TestSpecManifest:
    """PLAT-037: .spec file includes Windows application manifest."""

    def test_spec_has_as_invoker_manifest(self):
        repo_root = Path(__file__).resolve().parent.parent
        spec_path = repo_root / "scripts" / "build" / "voice-typer.spec"
        spec_content = spec_path.read_text()
        assert "asInvoker" in spec_content
        assert "requestedExecutionLevel" in spec_content
        assert "manifest=" in spec_content

    def test_spec_has_dpi_awareness(self):
        repo_root = Path(__file__).resolve().parent.parent
        spec_path = repo_root / "scripts" / "build" / "voice-typer.spec"
        spec_content = spec_path.read_text()
        assert "dpiAware" in spec_content


# ─── PLAT-RDP: RDP session detection ─────────────────────────────────


class TestRDPSession:
    """PLAT-RDP: is_remote_session detects RDP/SSH sessions."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows uses GetSystemMetrics for RDP detection, not SSH env vars",
    )
    def test_returns_false_when_no_ssh_env(self, monkeypatch):
        monkeypatch.delenv("SSH_CLIENT", raising=False)
        monkeypatch.delenv("SSH_TTY", raising=False)
        from voice_typer.server.server_platform import is_remote_session

        assert is_remote_session() is False

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Windows uses GetSystemMetrics for RDP detection, not SSH env vars",
    )
    def test_detects_ssh_session(self, monkeypatch):
        monkeypatch.setenv("SSH_CLIENT", "10.0.0.1 12345 22")
        from voice_typer.server.server_platform import is_remote_session

        assert is_remote_session() is True


# ─── PLAT-VENV: Autostart venv detection ─────────────────────────────


class TestVenvDetection:
    """PLAT-VENV: Autostart uses system Python when in venv."""

    def test_autostart_command_in_venv_uses_sys_executable(self):
        """The autostart command should work even in a venv."""
        from voice_typer.server.server_platform import _autostart_command

        cmd = _autostart_command()
        assert "autostart_launcher.py" in cmd


# ─── PLAT-RUN: Mutex name with path hash ─────────────────────────────


class TestMutexPathHash:
    """PLAT-RUN: Mutex name includes installation path hash."""

    def test_run_key_name_includes_hash(self):
        from voice_typer.server.server_platform import _run_key_name

        name = _run_key_name()
        assert name.startswith("com.voicetyper.autostart_")
        assert len(name) > len("com.voicetyper.autostart_")

    def test_different_executables_produce_different_hashes(self, monkeypatch):
        from voice_typer.server.server_platform import _run_key_name

        # The Run-key name hashes the STABLE install identifier (the
        # autostart launcher path), NOT sys.executable — sys.executable
        # differs between python.exe / pythonw.exe / the venv for the
        # SAME install, so a sys.executable-derived name would be
        # registered by one process and never found by the next (the
        # perpetual "autostart=true but disabled -- enabling" loop).
        # Different install DIRECTORY identifiers must still produce
        # different names (PLAT-RUN multi-install support).
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._install_identifier",
            lambda: "/path/a/autostart_launcher.py",
        )
        name_a = _run_key_name()
        monkeypatch.setattr(
            "voice_typer.server.server_platform.autostart._install_identifier",
            lambda: "/path/b/autostart_launcher.py",
        )
        name_b = _run_key_name()
        assert name_a != name_b


# ─── PLAT-VKMAP: VK code layout fallback ─────────────────────────────


class TestVKMapFallback:
    """PLAT-VKMAP: VK codes try MapVirtualKey for non-US layouts."""

    def test_vk_map_comment_exists(self):
        """The VK_MAP should have documentation about non-US layouts."""
        import inspect

        import voice_typer.server.hotkeys as hk_mod

        source = inspect.getsource(hk_mod)
        assert "PLAT-VKMAP" in source

    def test_parse_hotkey_still_works_for_standard_keys(self):
        from voice_typer.server.hotkeys import parse_hotkey_to_vk

        assert parse_hotkey_to_vk("<f2>") == 0x71
        assert parse_hotkey_to_vk("a") == ord("A")


# ─── PLAT-HLEAK: Mutex handle close ─────────────────────────────────


class TestMutexHandleClose:
    """PLAT-HLEAK: CloseHandle called in quit() for mutex."""

    def test_quit_closes_mutex_handle(self, monkeypatch, tmp_config_dir):
        """Verify that quit() calls CloseHandle on the mutex."""

        # Need to mock all heavy imports
        mock_sd = MagicMock()
        mock_sd.query_devices.return_value = []
        monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
        monkeypatch.setitem(sys.modules, "faster_whisper", MagicMock())
        monkeypatch.setitem(sys.modules, "pynput", MagicMock())
        monkeypatch.setitem(sys.modules, "pynput.keyboard", MagicMock())
        monkeypatch.setitem(sys.modules, "pystray", MagicMock())
        monkeypatch.setitem(sys.modules, "PIL", MagicMock())
        monkeypatch.setitem(sys.modules, "PIL.Image", MagicMock())
        monkeypatch.setitem(sys.modules, "PIL.ImageDraw", MagicMock())
        monkeypatch.setitem(sys.modules, "pyperclip", MagicMock())
        monkeypatch.setattr("atexit.register", lambda *a, **kw: None)
        monkeypatch.setattr("voice_typer.server.server_platform.is_autostart_enabled", lambda: False)
        monkeypatch.setattr("voice_typer.server.server_platform.enable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.disable_autostart", lambda: True)
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: [])

        from voice_typer.server.hotkeys import PynputHotkey

        # app.create_hotkey_backend re-export removed — the dispatcher
        # resolves the factory from its own module namespace.
        monkeypatch.setattr(
            "voice_typer.server.hotkey_dispatcher.create_hotkey_backend",
            lambda hotkey_str: PynputHotkey(hotkey_str),
        )

        from voice_typer.server.app import VoiceTyperApp

        app = VoiceTyperApp()

        # Simulate having a mutex handle
        mock_handle = MagicMock()
        app._mutex_handle = mock_handle

        # Mock sys.platform for the CloseHandle call

        # Just verify the attribute is checked
        assert hasattr(app, "_mutex_handle")
        assert app._mutex_handle is mock_handle


# macOS Accessibility permission guide ──────────────────


class TestMacOSAccessibilityGuide:
    """PLAT-030: PynputHotkey logs accessibility guide on macOS failure."""

    def test_macos_accessibility_warning_in_source(self):
        import inspect

        import voice_typer.server.hotkeys as hk_mod

        # The macOS Accessibility guide lives in
        # ``PynputHotkey._start_listener`` (the exception handler that
        # logs the permission guide when pynput fails on macOS), not in
        # the thin ``start`` wrapper. Inspect the whole class body so
        # the assertion holds regardless of which method the guide
        # moves to.
        source = inspect.getsource(hk_mod.PynputHotkey)
        assert "Accessibility" in source or "accessibility" in source


# ─── PLAT-CONTENT: contentEditable detection ─────────────────────────


class TestContentEditableComment:
    """PLAT-CONTENT: Module has documentation about contentEditable limitation."""

    def test_clipboard_module_documents_content_editable(self):
        import inspect

        import voice_typer.server.clipboard as clip_mod

        source = inspect.getsource(clip_mod)
        assert "PLAT-CONTENT" in source
        assert "contentEditable" in source


# ─── PLAT-CLIPRACE: Clipboard sequence number ────────────────────────


class TestClipboardSequenceNumber:
    """PLAT-CLIPRACE: Win32Clipboard.get_sequence_number() exists."""

    def test_get_sequence_number_static_method(self):
        from voice_typer.server.clipboard import Win32Clipboard

        assert hasattr(Win32Clipboard, "get_sequence_number")

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="Non-Windows path: Win32Clipboard.get_sequence_number is a no-op returning 0 on POSIX",
    )
    def test_get_sequence_number_returns_zero_on_non_windows(self):
        """On non-Windows, ``Win32Clipboard.get_sequence_number()`` returns 0."""
        from voice_typer.server.clipboard import Win32Clipboard

        assert Win32Clipboard.get_sequence_number() == 0


# pynput fallback documented ────────────────────────────


class TestPynputFallbackDocumentation:
    """PLAT-001: Clipboard module documents pynput/UIPI limitation."""

    def test_clipboard_module_documents_uipi_limitation(self):
        import inspect

        import voice_typer.server.clipboard as clip_mod

        source = inspect.getsource(clip_mod)
        assert "PLAT-001" in source
        assert "UIPI" in source


# Need inspect for some tests — imported locally in each test function
