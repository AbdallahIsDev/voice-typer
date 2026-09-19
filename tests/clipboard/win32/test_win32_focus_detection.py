"""Win32 code-path coverage for ``voice_typer.server.clipboard``."""

from __future__ import annotations

import ctypes
import types
from ctypes import wintypes
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import (
    clipboard as clip_mod,  # noqa: E402
)
from voice_typer.server.clipboard import (  # noqa: E402
    ClipboardManager,
)


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        # ULONG_PTR, accepts int 0
        ("dwExtraInfo", wintypes.WPARAM),
    )
    KEYUP = 0x0002


class _InputUnion(ctypes.Union):
    _fields_ = (("ki", _KEYBDINPUT),)


class _INPUT(ctypes.Structure):
    _fields_ = (
        ("type", wintypes.DWORD),
        ("ii", _InputUnion),
    )
    KEYBOARD = 1


def _make_pynput_win32_module(sendinput_return: int = 4) -> types.ModuleType:
    """Build a fake ``pynput._util.win32`` module with real ctypes types."""
    mod = types.ModuleType("pynput._util.win32")
    mod.INPUT = _INPUT
    mod.KEYBDINPUT = _KEYBDINPUT
    mod.INPUT_union = _InputUnion
    mod.SendInput = MagicMock(return_value=sendinput_return)
    return mod


@pytest.fixture
def fake_win32():
    """Mock ``ctypes.windll`` so Windows-only code runs on Linux."""
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_advapi32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.advapi32 = mock_advapi32

    # Default, sane return values for the most common calls.
    mock_user32.OpenClipboard.return_value = 1  # success
    mock_user32.CloseClipboard.return_value = 1
    mock_user32.EmptyClipboard.return_value = 1
    mock_user32.GetClipboardSequenceNumber.return_value = 42
    mock_user32.GetForegroundWindow.return_value = 0x12345
    mock_user32.GetClassNameW.return_value = 0  # filled per-test
    mock_kernel32.GetLastError.return_value = 0
    mock_kernel32.GetCurrentProcess.return_value = 0xABCD
    mock_kernel32.OpenProcess.return_value = 0x1000  # non-zero handle
    mock_kernel32.CloseHandle.return_value = 1
    mock_advapi32.OpenProcessToken.return_value = 1  # success
    mock_advapi32.GetTokenInformation.return_value = 1  # success

    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        # Pin is_macos() False too: the dispatch in manager.py checks
        patch.object(clip_mod, "is_macos", return_value=False),
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.create_unicode_buffer") as mock_buf,
    ):
        # Default buffer returns "Edit", a benign window class.
        buf_instance = MagicMock()
        buf_instance.value = "Edit"
        mock_buf.return_value = buf_instance
        yield {
            "user32": mock_user32,
            "kernel32": mock_kernel32,
            "advapi32": mock_advapi32,
            "windll": mock_windll,
            "buf": mock_buf,
        }


def _set_byref_value(byref_obj, value):
    """Helper: mutate the c_ulong instance wrapped by ``ctypes.byref``."""
    byref_obj._obj.value = value


class TestDetectFocusedProcessWindows:
    def test_detect_returns_process_name_on_success(self, fake_win32):
        """Full happy path: returns lowercase process name."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 4321)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["kernel32"].QueryFullProcessImageNameW.return_value = 1
        fake_win32["buf"].return_value.value = "C:\\Windows\\System32\\cmd.exe"

        result = ClipboardManager._detect_focused_process()
        assert result == "cmd.exe"

    def test_detect_returns_none_when_no_foreground_window(self, fake_win32):
        """GetForegroundWindow returning 0 → None."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        assert ClipboardManager._detect_focused_process() is None

    def test_detect_returns_none_when_pid_zero(self, fake_win32):
        """GetWindowThreadProcessId leaving pid=0 → None."""
        # No side_effect, pid.value stays at default 0.
        assert ClipboardManager._detect_focused_process() is None

    def test_detect_returns_none_when_open_process_fails(self, fake_win32):
        """OpenProcess returning 0 → None."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 4321)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["kernel32"].OpenProcess.return_value = 0
        assert ClipboardManager._detect_focused_process() is None

    def test_detect_returns_none_when_query_fails(self, fake_win32):
        """QueryFullProcessImageNameW returning 0 → None (falls through)."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 4321)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["kernel32"].QueryFullProcessImageNameW.return_value = 0
        result = ClipboardManager._detect_focused_process()
        assert result is None

    def test_detect_returns_none_on_oserror(self, fake_win32):
        """OSError from Win32 API → None (caught by narrowed except)."""
        fake_win32["user32"].GetForegroundWindow.side_effect = OSError("boom")
        assert ClipboardManager._detect_focused_process() is None


class TestIsTerminalProcess:
    def test_recognizes_cmd(self):
        assert ClipboardManager._is_terminal_process("cmd.exe") is True

    def test_recognizes_powershell(self):
        assert ClipboardManager._is_terminal_process("powershell.exe") is True

    def test_recognizes_pwsh(self):
        assert ClipboardManager._is_terminal_process("pwsh.exe") is True

    def test_recognizes_windowsterminal(self):
        assert ClipboardManager._is_terminal_process("windowsterminal.exe") is True

    def test_recognizes_linux_terminals(self):
        assert ClipboardManager._is_terminal_process("gnome-terminal") is True
        assert ClipboardManager._is_terminal_process("xterm") is True
        assert ClipboardManager._is_terminal_process("kitty") is True

    def test_returns_false_for_unknown(self):
        assert ClipboardManager._is_terminal_process("notepad.exe") is False

    def test_returns_false_for_none(self):
        assert ClipboardManager._is_terminal_process(None) is False

    def test_returns_false_for_empty(self):
        assert ClipboardManager._is_terminal_process("") is False

    def test_case_insensitive(self):
        """Process name comparison should be case-insensitive."""
        assert ClipboardManager._is_terminal_process("CMD.EXE") is True
        assert ClipboardManager._is_terminal_process("PowerShell.EXE") is True
