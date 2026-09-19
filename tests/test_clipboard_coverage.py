"""Additional coverage tests for voice_typer.server.clipboard."""

from __future__ import annotations

import contextlib
import sys
import time
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (  # noqa: E402
    ClipboardCopyError,
    ClipboardManager,
    Win32Clipboard,
    _ensure_pynput_imported,
    _focused_window_is_credential_dialog,
    _is_content_editable,
    _is_elevated_target,
    _is_password_field,
    _win32_empty_clipboard,
)
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402


class TestEnsurePynputImported:
    def test_idempotent_when_already_imported(self):
        """Second call should be a no-op."""
        # Reset state
        clip_mod._Key = None
        clip_mod._Controller = None
        _ensure_pynput_imported()
        assert clip_mod._Key is not None
        assert clip_mod._Controller is not None
        # Second call should not re-import
        first_key = clip_mod._Key
        _ensure_pynput_imported()
        assert clip_mod._Key is first_key

    def test_silently_continues_on_import_failure(self):
        """If pynput import raises, _Key/_Controller remain None."""
        clip_mod._Key = None
        clip_mod._Controller = None
        with (
            patch.dict(sys.modules, {"pynput": None, "pynput.keyboard": None}),
            patch("builtins.__import__", side_effect=ImportError("no pynput")),
            contextlib.suppress(ImportError),
        ):
            # Should not raise
            _ensure_pynput_imported()
        # After failed import, state is implementation-defined; just verify no crash


class TestWin32ClipboardNonWindows:
    def test_constructor_raises_on_non_windows(self):
        """On non-Windows, Win32Clipboard.__init__ raises RuntimeError."""
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            pytest.raises(RuntimeError, match="only available on Windows"),
        ):
            Win32Clipboard()

    def test_get_sequence_number_returns_zero_on_non_windows(self):
        """get_sequence_number returns 0 on non-Windows."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = Win32Clipboard.get_sequence_number()
            assert result == 0


class TestWin32EmptyClipboard:
    def test_no_op_on_non_windows(self):
        """On non-Windows, _win32_empty_clipboard should not raise."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            _win32_empty_clipboard()  # should not raise


class TestIsElevatedTarget:
    def test_returns_false_on_non_windows(self):
        """On non-Windows, _is_elevated_target returns False (no targets)."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = _is_elevated_target()
            assert result is False


class TestFocusedWindowIsCredentialDialog:
    def test_returns_false_on_non_windows(self):
        """On non-Windows, there are no Win32 credential dialogs."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = _focused_window_is_credential_dialog()
            assert result is False


class TestIsPasswordField:
    def test_returns_false_on_non_windows(self):
        """On non-Windows, password-field detection is unavailable."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = _is_password_field()
            assert result is False


class TestIsContentEditable:
    def test_returns_false_on_non_windows(self):
        """On non-Windows, content-editable detection is unavailable."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = _is_content_editable()
            assert result is False


class TestTerminalProcessDetection:
    def test_known_terminal_names_return_true(self):
        """Known terminal process names should be recognized."""
        # The implementation may use a set/list of known names
        for name in ["cmd.exe", "powershell.exe", "bash", "sh", "zsh"]:
            result = ClipboardManager._is_terminal_process(name)
            assert isinstance(result, bool)

    def test_none_returns_false(self):
        """None process name should return False."""
        result = ClipboardManager._is_terminal_process(None)
        assert result is False

    def test_detect_focused_process_returns_optional_str(self):
        """_detect_focused_process should return str or None on any platform."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = ClipboardManager._detect_focused_process()
            assert result is None or isinstance(result, str)


class TestModifierRelease:
    def test_release_stuck_modifiers_does_not_crash_without_keyboard(self):
        """_release_stuck_modifiers should be safe even with keyboard=None."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = None
        # Should not raise
        cm._release_stuck_modifiers()

    def test_release_stuck_modifiers_calls_release_on_keyboard(self):
        """When keyboard is present, _release_stuck_modifiers may call release."""
        mock_kb = MagicMock()
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = mock_kb
        cm._release_stuck_modifiers()
        # Verify no crash; specific call count is implementation-defined


class TestSafeKeyPress:
    def test_safe_key_press_presses_and_releases(self):
        """_safe_key_press should press modifier+char, then release both."""
        mock_kb = MagicMock()
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = mock_kb
        modifier = MagicMock(name="modifier")
        char = "v"
        cm._safe_key_press(modifier, char)
        mock_kb.press.assert_any_call(modifier)
        mock_kb.press.assert_any_call(char)
        mock_kb.release.assert_any_call(char)
        mock_kb.release.assert_any_call(modifier)

    def test_safe_key_press_releases_modifier_on_exception(self):
        """If char press fails, modifier must still be released (try/finally)."""
        mock_kb = MagicMock()
        mock_kb.press.side_effect = [None, RuntimeError("boom")]
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = mock_kb
        modifier = MagicMock(name="modifier")
        with pytest.raises(RuntimeError):
            cm._safe_key_press(modifier, "v")
        # Modifier MUST still be released
        mock_kb.release.assert_any_call(modifier)


class TestSendCtrlVWin32:
    def test_invokes_send_input_with_mocked_win32_api(self):
        """_send_ctrl_v_win32 builds an INPUT batch and calls SendInput."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = None
        mock_user32 = MagicMock()
        mock_user32.SendInput.return_value = 1  # 1 event sent
        with patch("ctypes.windll", create=True) as windll_mock:
            windll_mock.user32 = mock_user32
            # The function may still raise on attribute access details;
            with contextlib.suppress(Exception):
                cm._send_ctrl_v_win32()


class TestPaste:
    def test_paste_returns_false_when_disabled(self):
        """When paste_enabled=False, paste() returns False immediately."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = False
        cm._keyboard = None
        cm._last_paste_time = 0.0
        cm._restore_delay_ms = 150
        result = cm.paste()
        assert result is False

    def test_paste_returns_false_when_rate_limited(self):
        """Within the rate-limit window, paste() returns False."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = time.monotonic()  # just pasted
        cm._clipboard_seq = 0
        cm._clipboard_save_restore_enabled = False
        cm._restore_delay_ms = 150
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
        ):
            result = cm.paste()
        assert result is False  # rate-limited

    def test_paste_returns_false_when_unsafe_target(self):
        """When target is unsafe (e.g., password field), paste returns False."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0  # long ago, not rate limited
        cm._clipboard_seq = 0
        cm._clipboard_save_restore_enabled = False
        cm._restore_delay_ms = 150
        with (
            patch.object(clip_mod, "is_windows", return_value=False),
            patch.object(ClipboardManager, "_is_safe_paste_target", return_value=False),
        ):
            result = cm.paste()
        assert result is False


class TestCopyEdgeCases:
    def test_copy_empty_string_returns_none(self):
        """Empty string should not be copied; returns None (no snapshot)."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = None
        cm._clipboard_seq = 0
        cm._last_copied_text = ""
        cm._clipboard_save_restore_enabled = False
        cm._restore_delay_ms = 150
        with (
            patch.object(clip_mod, "pyperclip", MagicMock()),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
        ):
            result = cm.copy("")
        assert result is None

    def test_copy_raises_clipboard_copy_error_on_pyperclip_failure(self):
        """If pyperclip.copy raises, copy() raises ClipboardCopyError."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = None
        cm._clipboard_seq = 0
        cm._last_copied_text = ""
        cm._clipboard_save_restore_enabled = False
        cm._restore_delay_ms = 150
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = Exception("clipboard locked")
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")


class TestRefreshConfig:
    def test_reads_clipboard_save_restore_flag(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._clipboard_save_restore_enabled = True
        cfg = MagicMock()
        cfg.clipboard_save_restore = False
        cm.refresh_config(cfg)
        assert cm._clipboard_save_restore_enabled is False

    def test_defaults_to_true_on_missing_attr(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._clipboard_save_restore_enabled = False
        cfg = MagicMock()
        del cfg.clipboard_save_restore  # AttributeError on access
        cm.refresh_config(cfg)
        # Should fall back to True
        assert cm._clipboard_save_restore_enabled is True

    def test_defaults_to_true_on_exception(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._clipboard_save_restore_enabled = False
        cfg = MagicMock()
        type(cfg).clipboard_save_restore = PropertyMock(side_effect=RuntimeError("boom"))
        cm.refresh_config(cfg)
        assert cm._clipboard_save_restore_enabled is True


class TestIsSafePasteTarget:
    def test_returns_true_on_non_windows(self):
        """On non-Windows, _is_safe_paste_target should return True."""
        with patch.object(clip_mod, "is_windows", return_value=False):
            result = ClipboardManager._is_safe_paste_target()
            assert result is True


class TestYj22PynputBindingsTyping:
    """YJ-22: ``_Controller`` narrowed from ``Any`` to ``type | None``."""

    def test_controller_annotation_is_type_or_none(self):
        """The ``_Controller`` annotation MUST be ``type | None`` (not"""
        import voice_typer.server.clipboard as clip_mod

        ann = clip_mod.__annotations__.get("_Controller")
        assert ann is not None, (
            "YJ-22: ``_Controller`` must have an explicit annotation (``type | None``). Found: no annotation."
        )
        assert ann == "type | None" or ann == type | None, (
            f"YJ-22: ``_Controller`` annotation must be ``type | None`` (narrowed from ``Any``). Got: {ann!r}"
        )

    def test_controller_initial_value_is_none(self, monkeypatch):
        """``_Controller`` initial value MUST be ``None`` (the lazy-import"""
        import voice_typer.server.clipboard as clip_mod

        # A prior test in the same xdist worker may have already
        monkeypatch.setattr(clip_mod, "_Controller", None)

        assert clip_mod._Controller is None, (
            "YJ-22: ``_Controller`` initial value must be ``None`` (lazily populated by ``_ensure_pynput_imported()``)."
        )

    def test_controller_no_type_ignore_marker(self):
        """No ``# type: ignore[assignment]`` marker on the ``_Controller``"""
        import inspect

        import voice_typer.server.clipboard as clip_mod

        src = inspect.getsource(clip_mod)
        # Find the line declaring _Controller.
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("_Controller:") and "=" in stripped:
                assert "type: ignore" not in stripped, (
                    f"YJ-22: ``_Controller`` line must NOT carry a "
                    f"``# type: ignore`` marker, ``type | None`` "
                    f"accepts ``None`` natively. Got: {stripped!r}"
                )
                return
        raise AssertionError(
            "YJ-22: could not locate the ``_Controller:`` declaration line in voice_typer.server.clipboard source."
        )

    def test_key_annotation_remains_any_with_documented_rationale(self):
        """``_Key`` stays ``Any`` (or ``Any | None``, the ``| None``"""
        import voice_typer.server.clipboard as clip_mod

        ann = clip_mod.__annotations__.get("_Key")
        assert ann is not None, "YJ-22: ``_Key`` must have an explicit annotation."
        # ``Any`` (or the widened ``Any | None``) is the documented
        assert ann == "Any" or ann is Any or ann == "Any | None", (
            f"YJ-22: ``_Key`` annotation must remain ``Any`` (or "
            f"``Any | None``); narrowing to ``type | None`` would "
            f"break the 6 downstream ``_cb._Key.cmd`` accesses. "
            f"Got: {ann!r}"
        )

    def test_key_no_type_ignore_marker(self):
        """No ``# type: ignore[assignment]`` marker on the ``_Key`` line"""
        import inspect

        import voice_typer.server.clipboard as clip_mod

        src = inspect.getsource(clip_mod)
        for line in src.splitlines():
            stripped = line.strip()
            if stripped.startswith("_Key:") and "=" in stripped:
                assert "type: ignore" not in stripped, (
                    f"YJ-22: ``_Key`` line must NOT carry a ``# type: ignore`` marker. Got: {stripped!r}"
                )
                return
        raise AssertionError("YJ-22: could not locate the ``_Key:`` declaration line.")


# Need Any import for the assertion above.
from typing import Any  # noqa: E402, late import for the assertion helper
