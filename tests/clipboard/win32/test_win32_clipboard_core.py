"""Win32 code-path coverage for ``voice_typer.server.clipboard``.

These tests mock ``ctypes.windll`` (and friends) so the Windows-only
branches of clipboard.py execute on Linux.  Brings clipboard.py from
~40% to >=75% coverage.

The strategy:

1. Patch ``voice_typer.server.clipboard.is_windows`` to return ``True``
   so the ``if not is_windows(): return ...`` early-exits are skipped.
2. Patch ``ctypes.windll`` with a ``MagicMock`` exposing ``user32``,
   ``kernel32``, and ``advapi32`` attributes.  Each Win32 API call
   becomes a mock call whose return value we control.
3. For functions that use ``ctypes.byref(dword)`` to receive an output
   value (e.g. ``GetWindowThreadProcessId``), we install ``side_effect``
   callbacks that mutate ``byref_obj._obj.value`` — the underlying
   ``c_ulong`` instance — to fake the kernel writing into the buffer.
4. For ``_send_ctrl_v_win32``, we provide *real* ``ctypes.Structure``
   subclasses (``INPUT``, ``KEYBDINPUT``, ``INPUT_union``) so the
   ``(INPUT * 4)(...)`` array-construction syntax and
   ``ctypes.sizeof(INPUT)`` work natively.  ``SendInput`` itself is a
   ``MagicMock``.
5. For ``_is_password_field`` and ``_is_content_editable``, we mock
   ``comtypes`` / ``comtypes.client`` in ``sys.modules`` so the
   ``import comtypes.client`` line resolves to our mock.
"""

from __future__ import annotations

import ctypes
import types
from ctypes import wintypes
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# pynput / pynput.keyboard / pyperclip are mocked at collection time by
# tests/clipboard/conftest.py (single source of truth —  dedup).
# ---------------------------------------------------------------------------
# UIA singleton moved to clipboard_target_safety; reset it there.
from voice_typer.server import (
    clipboard as clip_mod,  # noqa: E402
)
from voice_typer.server.clipboard import (  # noqa: E402
    Win32Clipboard,
    _win32_empty_clipboard,
)


# ---------------------------------------------------------------------------
# Real ctypes structures for _send_ctrl_v_win32 testing.
#
# pynput._util.win32 exposes INPUT / KEYBDINPUT / INPUT_union / SendInput.
# We define minimal ctypes-compatible versions so the array-construction
# and sizeof() calls in _send_ctrl_v_win32 work on Linux.
# ---------------------------------------------------------------------------
class _KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        # ULONG_PTR — accepts int 0
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
    """Build a fake ``pynput._util.win32`` module with real ctypes types.

    ``SendInput`` is a MagicMock so the test can configure the return
    value (4 = success, 0 = total failure, 1..3 = partial success).
    """
    mod = types.ModuleType("pynput._util.win32")
    mod.INPUT = _INPUT
    mod.KEYBDINPUT = _KEYBDINPUT
    mod.INPUT_union = _InputUnion
    mod.SendInput = MagicMock(return_value=sendinput_return)
    return mod


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_win32():
    """Mock ``ctypes.windll`` so Windows-only code runs on Linux.

    Yields a dict with ``user32``, ``kernel32``, and ``advapi32`` mocks
    that tests can configure per-case.
    """
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
        # is_macos() BEFORE is_windows(), so on a macOS host the real
        # darwin predicate would otherwise hijack these simulated-
        # Windows tests into the Cmd+V branch.
        patch.object(clip_mod, "is_macos", return_value=False),
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.create_unicode_buffer") as mock_buf,
    ):
        # Default buffer returns "Edit" — a benign window class.
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
    """Helper: mutate the c_ulong instance wrapped by ``ctypes.byref``.

    ``ctypes.byref(obj)`` returns a ``CArgObject`` whose ``_obj``
    attribute is the underlying object.  We use this to fake the kernel
    writing an output value into a ``wintypes.DWORD`` passed by-ref.
    """
    byref_obj._obj.value = value


class TestWin32ClipboardEnterExit:
    def test_enter_opens_clipboard_and_returns_self(self, fake_win32):
        """__enter__ calls OpenClipboard and returns the instance."""
        clip = Win32Clipboard()
        result = clip.__enter__()
        assert result is clip
        fake_win32["user32"].OpenClipboard.assert_called_once_with(0)
        assert clip._opened is True

    def test_enter_logs_warning_on_open_failure(self, fake_win32):
        """When OpenClipboard returns 0, __enter__ logs and continues."""
        fake_win32["user32"].OpenClipboard.return_value = 0
        fake_win32["kernel32"].GetLastError.return_value = 5
        clip = Win32Clipboard()
        with patch.object(clip_mod, "log") as mock_log:
            result = clip.__enter__()
        assert result is clip
        assert clip._opened is False
        mock_log.warning.assert_called_once()
        # First positional arg is the format string; second is the err code.
        args, _ = mock_log.warning.call_args
        assert args[1] == 5

    def test_enter_catches_exception(self, fake_win32):
        """If OpenClipboard raises, __enter__ logs and doesn't propagate."""
        fake_win32["user32"].OpenClipboard.side_effect = OSError("denied")
        clip = Win32Clipboard()
        with patch.object(clip_mod, "log") as mock_log:
            result = clip.__enter__()
        assert result is clip
        assert clip._opened is False
        mock_log.warning.assert_called_once()

    def test_exit_closes_clipboard_if_opened(self, fake_win32):
        """When _opened=True, __exit__ calls CloseClipboard."""
        clip = Win32Clipboard()
        clip.__enter__()
        assert clip._opened is True
        clip.__exit__(None, None, None)
        fake_win32["user32"].CloseClipboard.assert_called_once_with()
        assert clip._opened is False

    def test_exit_skips_close_if_not_opened(self, fake_win32):
        """When _opened=False, __exit__ does NOT call CloseClipboard."""
        clip = Win32Clipboard()
        # __enter__ failed to open (OpenClipboard returned 0)
        fake_win32["user32"].OpenClipboard.return_value = 0
        clip.__enter__()
        assert clip._opened is False
        clip.__exit__(None, None, None)
        fake_win32["user32"].CloseClipboard.assert_not_called()

    def test_exit_catches_close_exception(self, fake_win32):
        """If CloseClipboard raises, __exit__ swallows it."""
        fake_win32["user32"].CloseClipboard.side_effect = OSError("boom")
        clip = Win32Clipboard()
        clip.__enter__()
        # Should NOT raise
        clip.__exit__(None, None, None)
        assert clip._opened is False


class TestWin32ClipboardEmpty:
    def test_empty_returns_true_on_success(self, fake_win32):
        """empty() returns True when EmptyClipboard succeeds."""
        with Win32Clipboard() as clip:
            assert clip.empty() is True
        fake_win32["user32"].EmptyClipboard.assert_called_once_with()

    def test_empty_returns_false_when_not_opened(self, fake_win32):
        """empty() returns False if the clipboard wasn't opened."""
        fake_win32["user32"].OpenClipboard.return_value = 0
        clip = Win32Clipboard()
        clip.__enter__()
        assert clip._opened is False
        assert clip.empty() is False
        fake_win32["user32"].EmptyClipboard.assert_not_called()

    def test_empty_returns_false_on_exception(self, fake_win32):
        """empty() returns False if EmptyClipboard raises."""
        fake_win32["user32"].EmptyClipboard.side_effect = OSError("busy")
        with Win32Clipboard() as clip:
            assert clip.empty() is False


class TestWin32ClipboardGetSequenceNumber:
    def test_returns_sequence_number_on_windows(self, fake_win32):
        """get_sequence_number() returns the Win32 clipboard seq."""
        fake_win32["user32"].GetClipboardSequenceNumber.return_value = 99
        assert Win32Clipboard.get_sequence_number() == 99

    def test_returns_zero_when_user32_lacks_method(self, fake_win32):
        """Falls back to 0 when GetClipboardSequenceNumber is missing."""
        # Remove the attribute on the mock
        del fake_win32["user32"].GetClipboardSequenceNumber
        assert Win32Clipboard.get_sequence_number() == 0

    def test_returns_zero_on_exception(self, fake_win32):
        """Falls back to 0 when the call raises."""
        fake_win32["user32"].GetClipboardSequenceNumber.side_effect = OSError("nope")
        assert Win32Clipboard.get_sequence_number() == 0


# ===========================================================================
# _win32_empty_clipboard
# ===========================================================================


class TestWin32EmptyClipboard:
    def test_uses_context_manager_and_calls_empty(self, fake_win32):
        """_win32_empty_clipboard creates a Win32Clipboard and calls empty."""
        # Should not raise.
        _win32_empty_clipboard()
        fake_win32["user32"].OpenClipboard.assert_called_once()
        fake_win32["user32"].EmptyClipboard.assert_called_once_with()
        fake_win32["user32"].CloseClipboard.assert_called_once_with()

    def test_swallows_oserror_from_context_manager(self, fake_win32):
        """If Win32Clipboard.__init__ raises OSError, _win32_empty_clipboard returns.

        EC-15 narrowed the catch from bare ``except Exception: pass`` to
        ``(OSError, AttributeError)`` — the expected failure modes for
        Win32 ctypes calls. RuntimeError (a programmer error) is intentionally
        NOT swallowed so it surfaces during development.
        """
        # Even though is_windows() is True, force the constructor to raise
        # by patching it.
        with patch.object(clip_mod, "Win32Clipboard", side_effect=OSError("nope")):
            # Should not raise — the function has the narrowed except.
            _win32_empty_clipboard()


# ===========================================================================
# _is_elevated_target
# ===========================================================================
