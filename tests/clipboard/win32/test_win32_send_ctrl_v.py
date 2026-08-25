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
from unittest.mock import MagicMock, PropertyMock, patch

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
    ClipboardManager,
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


class TestSendCtrlVWin32:
    def _make_cm(self):
        """Delegate to the shared canonical factory (XS-42 helper dedup).

        ``_send_ctrl_v_win32`` only reads ``self._keyboard`` (via
        ``_safe_key_press``); the factory's other pre-populated cached
        flags are inert for this suite.
        """
        from tests.fixtures.clipboard_helpers import make_clipboard_manager

        return make_clipboard_manager()

    def test_calls_sendinput_with_four_events(self, fake_win32):
        """Happy path: SendInput returns 4 → success, no fallback.

        production code now defines INPUT/KEYBDINPUT/
        INPUT_union inline via ctypes.Structure (no longer imports
        from pynput._util.win32) and calls user32.SendInput directly
        via ctypes.windll.user32. The fake_win32 fixture patches
        ctypes.windll so we control user32.SendInput.return_value
        directly.
        """
        cm = self._make_cm()
        mock_user32 = fake_win32["user32"]
        mock_user32.SendInput.return_value = 4
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            cm._send_ctrl_v_win32()
        # SendInput was called with 4 events.
        mock_user32.SendInput.assert_called_once()
        args, _ = mock_user32.SendInput.call_args
        assert args[0] == 4

    def test_logs_warning_on_sendinput_zero_and_falls_back_to_pynput(self, fake_win32):
        """SendInput returning 0 → log info, fall back to pynput."""
        cm = self._make_cm()
        mock_user32 = fake_win32["user32"]
        mock_user32.SendInput.return_value = 0
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            with patch.object(clip_mod, "log") as mock_log:
                cm._send_ctrl_v_win32()
        # pynput fallback called via _safe_key_press.
        cm._keyboard.press.assert_any_call("ctrl_key")
        cm._keyboard.press.assert_any_call("v")
        # Info log emitted.
        info_calls = [c for c in mock_log.info.call_args_list if "falling back to pynput" in str(c)]
        assert len(info_calls) >= 1

    def test_logs_warning_on_partial_sendinput_success(self, fake_win32):
        """SendInput returning 1..3 → log error, synthesize KEYUP, no fallback."""
        cm = self._make_cm()
        mock_user32 = fake_win32["user32"]
        # First SendInput returns 2 (partial); second (KEYUP cleanup) is
        # also a mock — return value doesn't matter for this test.
        mock_user32.SendInput.side_effect = [2, 2]
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            with patch.object(clip_mod, "log") as mock_log:
                cm._send_ctrl_v_win32()
        # SendInput called twice: first (4 events), then (2 KEYUP cleanup).
        assert mock_user32.SendInput.call_count == 2
        # Second call used 2 events (KEYUP for V and Ctrl).
        second_args, _ = mock_user32.SendInput.call_args_list[1]
        assert second_args[0] == 2
        # pynput fallback NOT called.
        cm._keyboard.press.assert_not_called()
        # Error logged.
        error_calls = [
            c
            for c in mock_log.error.call_args_list
            if "partial success" in str(c).lower() or "NOT falling back" in str(c)
        ]
        assert len(error_calls) >= 1

    def test_swallows_exception_during_keyup_cleanup(self, fake_win32):
        """If KEYUP cleanup raises, the exception is logged but swallowed."""
        cm = self._make_cm()
        mock_user32 = fake_win32["user32"]
        # First SendInput returns 1 (partial); second raises.
        mock_user32.SendInput.side_effect = [1, RuntimeError("cleanup failed")]
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl_key"
            with patch.object(clip_mod, "log") as mock_log:
                # Should not raise.
                cm._send_ctrl_v_win32()
        # Debug log emitted for cleanup failure.
        debug_calls = [
            c for c in mock_log.debug.call_args_list if "KEYUP cleanup" in str(c) or "failed to synthesize" in str(c)
        ]
        assert len(debug_calls) >= 1


# ===========================================================================
# ClipboardManager.schedule_clipboard_clear — inner _clear function
# ---------------------------------------------------------------------------
# ADR-0010 §5.6: ``schedule_clipboard_clear`` (and the ``_clear_thread``
# / ``_saved_clipboard`` instance attributes it managed) was DELETED.
# The borrow/restore lifecycle is now driven by ``ClipboardSnapshot``
# capture in ``copy()`` and ``_delayed_restore()`` in ``paste()``. The
# entire ``TestScheduleClipboardClearInner`` class below has been
# removed — the production method it exercised no longer exists.
# ===========================================================================


# ===========================================================================
# ClipboardManager._release_stuck_modifiers
# (exercise the except blocks)
# ===========================================================================


class TestModifierReleaseExceptBranches:
    def test_release_stuck_modifiers_swallows_release_exception(self):
        """If keyboard.release raises, the inner except swallows it."""
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = MagicMock()
        cm._keyboard.release.side_effect = RuntimeError("release failed")
        # Set _Key to a MagicMock so the loop iterates.
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.ctrl = "ctrl"
            mock_key.shift = "shift"
            mock_key.alt = "alt"
            mock_key.cmd = "cmd"
            # Should not raise.
            cm._release_stuck_modifiers()

    def test_release_stuck_modifiers_swallows_outer_exception(self):
        """If a ``_Key.X`` attribute access raises, the outer except catches it.

        Covers lines 709-710 (the outer ``except Exception: pass`` in
        ``_release_stuck_modifiers``).  We make ``_Key.ctrl`` raise via
        a ``PropertyMock`` so the tuple construction itself raises
        before the inner try/except even runs.
        """
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = MagicMock()
        with patch.object(clip_mod, "_Key") as mock_key:
            # Set up valid attrs for shift/alt/cmd, but make ``ctrl``
            # raise on access — the tuple build fails on the first
            # attribute, triggering the outer except.
            mock_key.shift = "shift"
            mock_key.alt = "alt"
            mock_key.cmd = "cmd"
            type(mock_key).ctrl = PropertyMock(side_effect=RuntimeError("ctrl access broke"))
            # Should not raise — outer except swallows.
            cm._release_stuck_modifiers()

    # NOTE: ``_send_keystroke_sequence`` was DELETED as dead production
    # code  — the live keystroke path uses ``_safe_key_press``.
    # The former ``test_send_keystroke_sequence_finally_catches_release_exception``
    # case only exercised the deleted method's double-release finally
    # block (``_safe_key_press`` doesn't have a double-release — it
    # releases only the modifier in its finally). Coverage of the live
    # path's release-on-exception is provided by
    # ``TestSafeKeyPress::test_safe_key_press_releases_modifier_on_exception``
    # in tests/test_clipboard_coverage.py.
