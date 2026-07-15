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
import sys
import types
from ctypes import wintypes
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

# ---------------------------------------------------------------------------
# Module-level heavy-import mocking.
#
# These setdefault() calls run at *collection* time — before
# voice_typer.server.clipboard is imported — so the module's
# ``import pyperclip`` and ``import pynput`` lines resolve to mocks.
# ---------------------------------------------------------------------------
mock_pynput = MagicMock()
mock_pynput_kb = MagicMock()
sys.modules.setdefault("pynput", mock_pynput)
sys.modules.setdefault("pynput.keyboard", mock_pynput_kb)
sys.modules.setdefault("pyperclip", MagicMock())

from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard import (  # noqa: E402
    ClipboardCopyError,
    ClipboardManager,
    Win32Clipboard,
    _focused_window_is_credential_dialog,
    _is_content_editable,
    _is_elevated_target,
    _is_password_field,
    _win32_empty_clipboard,
)
from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: E402


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


# ===========================================================================
# Win32Clipboard
# ===========================================================================


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

    def test_swallows_runtime_error_from_context_manager(self, fake_win32):
        """If Win32Clipboard.__init__ raises, _win32_empty_clipboard returns."""
        # Even though is_windows() is True, force the constructor to raise
        # by patching it.
        with patch.object(clip_mod, "Win32Clipboard", side_effect=RuntimeError("nope")):
            # Should not raise — the function has a broad except.
            _win32_empty_clipboard()


# ===========================================================================
# _is_elevated_target
# ===========================================================================


class TestIsElevatedTargetWindows:
    def test_returns_false_when_no_foreground_window(self, fake_win32):
        """GetForegroundWindow returning 0 → False."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        assert _is_elevated_target() is False

    def test_returns_false_when_pid_is_zero(self, fake_win32):
        """GetWindowThreadProcessId leaving pid=0 → False."""
        # Don't set side_effect — pid.value stays at default 0.
        assert _is_elevated_target() is False

    def test_returns_false_when_open_process_fails(self, fake_win32):
        """OpenProcess returning 0 → False."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["kernel32"].OpenProcess.return_value = 0
        assert _is_elevated_target() is False

    def test_returns_false_when_open_process_token_fails(self, fake_win32):
        """OpenProcessToken returning 0 → False."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["advapi32"].OpenProcessToken.return_value = 0
        assert _is_elevated_target() is False

    def test_returns_false_when_get_token_info_fails(self, fake_win32):
        """GetTokenInformation returning 0 on second call → False."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        # First GetTokenInformation (probe) returns 1 (success),
        # second returns 0 (failure) → triggers `return False`.
        fake_win32["advapi32"].GetTokenInformation.side_effect = [1, 0]
        assert _is_elevated_target() is False

    def test_returns_false_when_target_not_elevated(self, fake_win32):
        """target_elevated=False → falls through, returns False."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        # Both GetTokenInformation calls succeed; buf is zeros → elevated=False
        fake_win32["advapi32"].GetTokenInformation.return_value = 1
        assert _is_elevated_target() is False

    def test_returns_true_when_target_elevated_and_we_are_not(self, fake_win32):
        """target_elevated=True, we_elevated=False → return True."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        # All GetTokenInformation calls succeed.
        fake_win32["advapi32"].GetTokenInformation.return_value = 1

        # Patch ctypes.cast so the FIRST dereference (target) yields 1
        # (elevated) and the SECOND (us) yields 0 (not elevated).
        fake_target_ptr = MagicMock()
        fake_target_ptr.__getitem__.return_value = 1  # target_elevated=True
        fake_our_ptr = MagicMock()
        fake_our_ptr.__getitem__.return_value = 0  # we_elevated=False
        with (
            patch("ctypes.cast", side_effect=[fake_target_ptr, fake_our_ptr]),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = _is_elevated_target()
        assert result is True
        mock_log.warning.assert_called()

    def test_returns_false_when_target_and_we_both_elevated(self, fake_win32):
        """target_elevated=True, we_elevated=True → return False."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["advapi32"].GetTokenInformation.return_value = 1
        fake_target_ptr = MagicMock()
        fake_target_ptr.__getitem__.return_value = 1
        fake_our_ptr = MagicMock()
        fake_our_ptr.__getitem__.return_value = 1
        with patch("ctypes.cast", side_effect=[fake_target_ptr, fake_our_ptr]):
            result = _is_elevated_target()
        assert result is False

    def test_returns_false_when_our_open_process_token_fails(self, fake_win32):
        """OpenProcessToken for our_token returning 0 → False."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        # First OpenProcessToken (target) succeeds; second (our) fails.
        fake_win32["advapi32"].OpenProcessToken.side_effect = [1, 0]
        fake_win32["advapi32"].GetTokenInformation.return_value = 1
        fake_target_ptr = MagicMock()
        fake_target_ptr.__getitem__.return_value = 1
        with patch("ctypes.cast", side_effect=[fake_target_ptr]):
            result = _is_elevated_target()
        assert result is False

    def test_returns_false_when_our_get_token_info_fails(self, fake_win32):
        """GetTokenInformation for our_token (real call) returning 0 → False.

        Covers the second `if not advapi32.GetTokenInformation(...)` check
        inside the ``our_token`` branch (line 253).
        """

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["advapi32"].OpenProcessToken.return_value = 1
        # 4 GetTokenInformation calls:
        #   1: target probe (success)
        #   2: target real  (success)
        #   3: our probe    (success)
        #   4: our real     (FAIL → triggers `return False` at line 253)
        fake_win32["advapi32"].GetTokenInformation.side_effect = [1, 1, 1, 0]
        fake_target_ptr = MagicMock()
        fake_target_ptr.__getitem__.return_value = 1
        with patch("ctypes.cast", side_effect=[fake_target_ptr]):
            result = _is_elevated_target()
        assert result is False

    def test_returns_false_on_exception(self, fake_win32):
        """Any unexpected exception → False (broad except)."""
        fake_win32["user32"].GetForegroundWindow.side_effect = RuntimeError("unexpected")
        assert _is_elevated_target() is False


# ===========================================================================
# _focused_window_is_credential_dialog
# ===========================================================================


class TestFocusedWindowIsCredentialDialog:
    def test_returns_true_for_credential_dialog_class(self, fake_win32):
        """Class name 'CredentialDialog' → True."""
        fake_win32["buf"].return_value.value = "CredentialDialog"
        fake_win32["user32"].GetClassNameW.return_value = 15  # length > 0
        assert _focused_window_is_credential_dialog() is True

    def test_returns_true_for_cred_dialog_caller_wnd(self, fake_win32):
        """Class name 'CredDialogCallerWnd' → True."""
        fake_win32["buf"].return_value.value = "CredDialogCallerWnd"
        fake_win32["user32"].GetClassNameW.return_value = 20
        assert _focused_window_is_credential_dialog() is True

    def test_returns_true_for_passport_window(self, fake_win32):
        """Class name 'PassportWindow' → True."""
        fake_win32["buf"].return_value.value = "PassportWindow"
        fake_win32["user32"].GetClassNameW.return_value = 14
        assert _focused_window_is_credential_dialog() is True

    def test_returns_false_for_normal_window(self, fake_win32):
        """Class name 'Edit' → False."""
        fake_win32["buf"].return_value.value = "Edit"
        fake_win32["user32"].GetClassNameW.return_value = 4
        assert _focused_window_is_credential_dialog() is False

    def test_returns_false_when_no_foreground_window(self, fake_win32):
        """GetForegroundWindow returning 0 → False."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        assert _focused_window_is_credential_dialog() is False

    def test_returns_false_when_getclassname_returns_zero(self, fake_win32):
        """GetClassNameW returning 0 length → False."""
        fake_win32["user32"].GetClassNameW.return_value = 0
        assert _focused_window_is_credential_dialog() is False

    def test_returns_false_on_exception(self, fake_win32):
        """Any exception → False."""
        fake_win32["user32"].GetForegroundWindow.side_effect = OSError("nope")
        assert _focused_window_is_credential_dialog() is False


# ===========================================================================
# _is_password_field
# ===========================================================================


class TestIsPasswordFieldWindows:
    @pytest.fixture(autouse=True)
    def _reset_uia_singleton(self):
        """Reset the module-level UIA singleton so each test's per-test
        comtypes mock is consulted fresh.

        PERF-FIX-001: the UIA COM object is now a module-level
        singleton in clipboard.py (_UIA_SINGLETON, _UIA_MODULE,
        _UIA_SINGLETON_INIT_ATTEMPTED). Without this reset, the first
        test that triggers _get_uia_singleton() caches its fake_uia mock
        in the singleton, and every subsequent test receives the stale
        mock — causing false positives/negatives depending on test order.
        """
        clip_mod._UIA_SINGLETON = None
        clip_mod._UIA_MODULE = None
        clip_mod._UIA_SINGLETON_INIT_ATTEMPTED = False
        yield
        # Restore after test in case the test set them.
        clip_mod._UIA_SINGLETON = None
        clip_mod._UIA_MODULE = None
        clip_mod._UIA_SINGLETON_INIT_ATTEMPTED = False

    def test_returns_false_when_no_foreground_window(self, fake_win32):
        """No focused window → False (comtypes path returns False too)."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        # Make comtypes import fail so we exercise the ImportError branch.
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard._focused_window_is_credential_dialog",
                return_value=False,
            ),
        ):
            # Function should not raise; returns False.
            result = _is_password_field()
        assert result is False

    def test_returns_true_for_password_field_via_uia(self, fake_win32):
        """When UIA reports IsPassword=True → True + warning logged."""
        # Build a fake comtypes.client module.
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_focused = MagicMock(name="focused_element")
        fake_focused.GetCurrentPropertyValue.return_value = True  # IsPassword
        fake_uia.GetFocusedElement.return_value = fake_focused
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        # IMPORTANT: when Python imports comtypes.client, it sets the
        # ``client`` attribute on the parent ``comtypes`` module.  We
        # must pre-bind that attribute to our fake so subsequent
        # ``comtypes.client.X`` access resolves correctly.
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with (
            patch.dict(
                sys.modules,
                {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
            ),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = _is_password_field()
        assert result is True
        mock_log.warning.assert_called()
        # Verify UIA was queried for the password property (id 30022).
        fake_focused.GetCurrentPropertyValue.assert_called_with(30022)

    def test_returns_false_for_non_password_edit(self, fake_win32):
        """When UIA reports IsPassword=False → False."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_focused = MagicMock(name="focused_element")
        fake_focused.GetCurrentPropertyValue.return_value = False
        fake_uia.GetFocusedElement.return_value = fake_focused
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_password_field()
        assert result is False

    def test_returns_false_when_focused_element_is_none(self, fake_win32):
        """When UIA returns None for focused element → False."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_uia.GetFocusedElement.return_value = None
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_password_field()
        assert result is False

    def test_returns_false_on_comtypes_import_error(self, fake_win32):
        """comtypes ImportError → fallback to window-class heuristic."""
        # Setting sys.modules entries to None makes Python raise
        # ImportError on ``import comtypes.client`` — no need to patch
        # builtins.__import__ (which would break the outer ``import ctypes``
        # and cause the function to bail out before reaching the heuristic).
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard._focused_window_is_credential_dialog",
                return_value=False,
            ) as mock_cred,
        ):
            result = _is_password_field()
        assert result is False
        mock_cred.assert_called_once()

    def test_returns_true_when_comtypes_missing_and_cred_dialog_present(self, fake_win32):
        """comtypes ImportError + credential dialog → True (fail closed)."""
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard._focused_window_is_credential_dialog",
                return_value=True,
            ),
        ):
            result = _is_password_field()
        assert result is True

    def test_returns_false_on_uia_call_exception(self, fake_win32):
        """comtypes installed but UIA call raises → fail open (False)."""
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        # GetModule raises to simulate UIA failure (not ImportError).
        fake_comtypes_client.GetModule.side_effect = RuntimeError("UIA broken")

        with (
            patch.dict(
                sys.modules,
                {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
            ),
            patch.object(clip_mod, "log"),
        ):
            result = _is_password_field()
        assert result is False

    def test_returns_false_on_outer_exception(self, fake_win32):
        """Any unexpected exception in the outer try → False."""
        # comtypes not installed so we hit the cred-dialog fallback,
        # which itself raises → outer except catches.
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard._focused_window_is_credential_dialog",
                side_effect=RuntimeError("cred dialog broke"),
            ),
        ):
            result = _is_password_field()
        # Outer except catches and returns False.
        assert result is False

    def test_returns_false_when_wintypes_import_fails(self, fake_win32):
        """If ``from ctypes import wintypes`` raises → outer except → False.

        Covers lines 396-397 (the outer ``except Exception`` in
        ``_is_password_field``).

        Making ``from ctypes import wintypes`` fail is non-trivial:
        once the real ``ctypes.wintypes`` submodule has been imported
        anywhere in the process, the ``ctypes`` module object caches
        the ``wintypes`` attribute, and ``from ctypes import wintypes``
        will happily return that cached attribute (even if
        ``sys.modules['ctypes.wintypes']`` is patched to ``None``).

        The fix is two-pronged:

        1. Delete the cached ``wintypes`` attribute from the ``ctypes``
           module object (so the from-import has to look it up).
        2. Set ``sys.modules['ctypes.wintypes'] = None`` so the lookup
           raises ``ImportError``.

        Both mutations are restored in the ``finally`` block.
        """
        saved_attr = getattr(ctypes, "wintypes", None)
        had_attr = hasattr(ctypes, "wintypes")
        if had_attr:
            del ctypes.wintypes
        try:
            with patch.dict(sys.modules, {"ctypes.wintypes": None}):
                result = _is_password_field()
            assert result is False
        finally:
            if had_attr:
                # Restore the attribute so other tests aren't affected.
                ctypes.wintypes = saved_attr


# ===========================================================================
# _is_content_editable
# ===========================================================================


class TestIsContentEditableWindows:
    @pytest.fixture(autouse=True)
    def _reset_uia_singleton(self):
        """Reset the module-level UIA singleton (see TestIsPasswordFieldWindows)."""
        clip_mod._UIA_SINGLETON = None
        clip_mod._UIA_MODULE = None
        clip_mod._UIA_SINGLETON_INIT_ATTEMPTED = False
        yield
        clip_mod._UIA_SINGLETON = None
        clip_mod._UIA_MODULE = None
        clip_mod._UIA_SINGLETON_INIT_ATTEMPTED = False

    def test_returns_true_for_edit_with_value_pattern(self, fake_win32):
        """Edit control type (50004) with Value pattern → True."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_focused = MagicMock(name="focused_element")
        # 30003 = ControlType; 30101 = IsValuePatternAvailable
        fake_focused.GetCurrentPropertyValue.side_effect = [50004, True]
        fake_uia.GetFocusedElement.return_value = fake_focused
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_content_editable()
        assert result is True

    def test_returns_true_for_document_with_value_pattern(self, fake_win32):
        """Document control type (50036) with Value pattern → True."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_focused = MagicMock(name="focused_element")
        fake_focused.GetCurrentPropertyValue.side_effect = [50036, True]
        fake_uia.GetFocusedElement.return_value = fake_focused
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_content_editable()
        assert result is True

    def test_returns_false_for_non_edit_control_type(self, fake_win32):
        """Control type that isn't Edit/Document → False."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_focused = MagicMock(name="focused_element")
        # 50004 (Edit) but no Value pattern
        fake_focused.GetCurrentPropertyValue.side_effect = [50004, False]
        fake_uia.GetFocusedElement.return_value = fake_focused
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_content_editable()
        assert result is False

    def test_returns_false_for_other_control_type(self, fake_win32):
        """Button (50000) → not Edit/Document → False."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_focused = MagicMock(name="focused_element")
        fake_focused.GetCurrentPropertyValue.return_value = 50000  # Button
        fake_uia.GetFocusedElement.return_value = fake_focused
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_content_editable()
        assert result is False

    def test_returns_false_when_focused_is_none(self, fake_win32):
        """When UIA returns None → False."""
        fake_uia_mod = MagicMock(name="UIA_module")
        fake_uia = MagicMock(name="uia_instance")
        fake_uia.GetFocusedElement.return_value = None
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.return_value = fake_uia_mod
        fake_comtypes.CoCreateInstance.return_value = fake_uia

        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_content_editable()
        assert result is False

    def test_returns_false_on_comtypes_import_error(self, fake_win32):
        """comtypes ImportError → False."""
        with patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}):
            result = _is_content_editable()
        assert result is False

    def test_returns_false_on_exception(self, fake_win32):
        """Any other exception → False."""
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.side_effect = RuntimeError("UIA broken")
        with patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ):
            result = _is_content_editable()
        assert result is False


# ===========================================================================
# ClipboardManager._is_safe_paste_target (Windows branch)
# ===========================================================================


class TestIsSafePasteTargetWindows:
    def test_returns_false_for_uac_dialog_class(self, fake_win32):
        """Class '#32770' (UAC/consent) → False."""
        fake_win32["buf"].return_value.value = "#32770"
        assert ClipboardManager._is_safe_paste_target() is False

    def test_returns_false_for_credential_dialog_xaml_host(self, fake_win32):
        """Class 'Credential Dialog Xaml Host' → False."""
        fake_win32["buf"].return_value.value = "Credential Dialog Xaml Host"
        assert ClipboardManager._is_safe_paste_target() is False

    def test_returns_false_for_cred_dialog(self, fake_win32):
        """Class 'CredDialog' → False."""
        fake_win32["buf"].return_value.value = "CredDialog"
        assert ClipboardManager._is_safe_paste_target() is False

    def test_returns_false_when_target_is_elevated(self, fake_win32):
        """_is_elevated_target returning True → False."""
        fake_win32["buf"].return_value.value = "Edit"  # safe class
        with (
            patch("voice_typer.server.clipboard._is_elevated_target", return_value=True),
            patch("voice_typer.server.clipboard._is_password_field", return_value=False),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is False

    def test_returns_false_when_password_field(self, fake_win32):
        """_is_password_field returning True → False."""
        fake_win32["buf"].return_value.value = "Edit"
        with (
            patch("voice_typer.server.clipboard._is_elevated_target", return_value=False),
            patch("voice_typer.server.clipboard._is_password_field", return_value=True),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is False

    def test_returns_true_for_normal_window(self, fake_win32):
        """Class 'Edit', not elevated, not password → True."""
        fake_win32["buf"].return_value.value = "Edit"
        with (
            patch("voice_typer.server.clipboard._is_elevated_target", return_value=False),
            patch("voice_typer.server.clipboard._is_password_field", return_value=False),
            patch(
                "voice_typer.server.clipboard._is_content_editable",
                return_value=False,
            ),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True

    def test_returns_true_when_content_editable_logged(self, fake_win32):
        """contentEditable=True still returns True but logs info."""
        fake_win32["buf"].return_value.value = "Edit"
        with (
            patch("voice_typer.server.clipboard._is_elevated_target", return_value=False),
            patch("voice_typer.server.clipboard._is_password_field", return_value=False),
            patch(
                "voice_typer.server.clipboard._is_content_editable",
                return_value=True,
            ),
            patch.object(clip_mod, "log") as mock_log,
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True
        mock_log.info.assert_called()

    def test_returns_true_when_no_foreground_window(self, fake_win32):
        """GetForegroundWindow returning 0 → True (fail open)."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        assert ClipboardManager._is_safe_paste_target() is True

    def test_returns_true_on_outer_exception(self, fake_win32):
        """Any unexpected exception → True (fail open)."""
        fake_win32["user32"].GetForegroundWindow.side_effect = RuntimeError("boom")
        assert ClipboardManager._is_safe_paste_target() is True

    def test_returns_true_when_is_elevated_check_raises(self, fake_win32):
        """If _is_elevated_target raises, debug log + continue → True."""
        fake_win32["buf"].return_value.value = "Edit"
        with (
            patch(
                "voice_typer.server.clipboard._is_elevated_target",
                side_effect=RuntimeError("boom"),
            ),
            patch("voice_typer.server.clipboard._is_password_field", return_value=False),
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True

    def test_returns_true_when_content_editable_check_raises(self, fake_win32):
        """If _is_content_editable raises, debug log + continue → True."""
        fake_win32["buf"].return_value.value = "Edit"
        with (
            patch("voice_typer.server.clipboard._is_elevated_target", return_value=False),
            patch("voice_typer.server.clipboard._is_password_field", return_value=False),
            patch(
                "voice_typer.server.clipboard._is_content_editable",
                side_effect=RuntimeError("boom"),
            ),
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True


# ===========================================================================
# ClipboardManager._is_terminal_process / _detect_focused_process
# ===========================================================================


class TestDetectFocusedProcessWindows:
    def test_detect_returns_process_name_on_success(self, fake_win32):
        """Full happy path: returns lowercase process name."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 4321)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        # QueryFullProcessImageNameW writes the path into the buffer and
        # returns 1 (success).  The fake buf.value is the path.
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
        # No side_effect — pid.value stays at default 0.
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

    def test_detect_returns_none_on_exception(self, fake_win32):
        """Any exception → None."""
        fake_win32["user32"].GetForegroundWindow.side_effect = RuntimeError("boom")
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


# ===========================================================================
# ClipboardManager.copy — Windows branches
# ===========================================================================


class TestCopyWindowsBranches:
    def _make_cm(self):
        """Build a ClipboardManager with mocked pynput controller.

        ADR-0010 §5.6: ``_clear_thread`` / ``_saved_clipboard`` were
        deleted from the class. We set ``_restore_delay_ms`` (new in
        §5.3) so ``paste()``'s restore-delay lookup doesn't blow up if
        a test triggers the restore path.
        """
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0
        cm._clipboard_seq = 0
        cm._last_copied_text = ""
        cm._clipboard_save_restore_enabled = True
        cm._restore_delay_ms = 150
        return cm

    def test_copy_saves_clipboard_before_overwrite_on_windows(self, fake_win32):
        """copy() captures a ClipboardSnapshot of the prior clipboard.

        ADR-0010 §5.2: ``copy()`` now returns a ``ClipboardSnapshot``
        (or ``None``) instead of ``bool``. The snapshot is captured via
        ``ClipboardSnapshot.capture()`` and returned to the caller — it
        is NOT stored on ``self``.
        """
        cm = self._make_cm()
        sentinel_snapshot = ClipboardSnapshot(
            platform="windows",
            items=[(1, "CF_TEXT", b"old clipboard")],
            captured_at=0.0,
        )
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"  # verification match
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=sentinel_snapshot,
            ) as mock_capture,
        ):
            result = cm.copy("new text")
        assert result is sentinel_snapshot
        mock_capture.assert_called_once()

    def test_copy_handles_pyperclip_paste_exception_during_save(self, fake_win32):
        """If ClipboardSnapshot.capture() returns None, copy() still succeeds.

        ADR-0010 §5.2: snapshot capture may return None (clipboard
        locked or empty). copy() treats None as "no snapshot to
        restore" — degraded but safe mode — and still returns None
        (the snapshot value) while succeeding the actual text copy.
        """
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=None,
            ),
        ):
            result = cm.copy("new text")
        assert result is None  # capture failed → no snapshot returned

    def test_copy_skips_save_when_save_restore_disabled(self, fake_win32):
        """When _clipboard_save_restore_enabled=False, no snapshot is captured.

        ADR-0010 §5.2 / DP7: the config flag actually gates snapshot
        capture. ``copy()`` returns ``None`` because no snapshot was
        captured — the text copy itself still succeeds.
        """
        cm = self._make_cm()
        cm._clipboard_save_restore_enabled = False
        mock_pyper = MagicMock()
        mock_pyper.paste.return_value = "new text"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(
                ClipboardSnapshot,
                "capture",
                return_value=None,
            ) as mock_capture,
        ):
            result = cm.copy("new text")
        assert result is None  # save_restore disabled → no snapshot
        # Snapshot capture must NOT be attempted when the flag is off.
        mock_capture.assert_not_called()
        # paste() called only for verification, not for save
        assert mock_pyper.paste.call_count >= 1

    def test_copy_retries_on_access_denied(self, fake_win32):
        """pyperclip.copy raising OSError(winerror=5) is retried 3 times.

        ADR-0010 §5.2: after the third failure, copy() raises
        ``ClipboardCopyError`` (instead of returning ``False``).
        """
        cm = self._make_cm()
        mock_pyper = MagicMock()
        # First two copy() calls raise ACCESS_DENIED, third succeeds.
        copy_err = OSError("denied")
        copy_err.winerror = 5
        mock_pyper.copy.side_effect = [copy_err, copy_err, None]
        # paste() during verification returns the text → success.
        mock_pyper.paste.return_value = "hello"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time"),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
        ):
            result = cm.copy("hello")
        # copy() succeeded → returns the snapshot (None because capture was mocked None).
        assert result is None
        assert mock_pyper.copy.call_count == 3

    def test_copy_raises_after_three_access_denied(self, fake_win32):
        """After 3 ACCESS_DENIED attempts, copy() raises ClipboardCopyError."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        copy_err = OSError("denied")
        copy_err.winerror = 5
        mock_pyper.copy.side_effect = copy_err  # always raises
        mock_pyper.paste.return_value = "hello"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "time"),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")
        assert mock_pyper.copy.call_count == 3

    def test_copy_propagates_non_access_denied_oserror(self, fake_win32):
        """OSError without winerror=5 propagates as ClipboardCopyError."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = OSError("other error")
        mock_pyper.paste.return_value = "hello"
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=None),
            pytest.raises(ClipboardCopyError),
        ):
            cm.copy("hello")
        # Only the first attempt — no retry.
        assert mock_pyper.copy.call_count == 1

    def test_copy_verification_retries_on_mismatch(self, fake_win32):
        """pyperclip.paste() returning wrong text triggers verify-retry."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.return_value = None
        # All 3 paste() calls return mismatched text → loop runs to end.
        mock_pyper.paste.return_value = "wrong"
        sentinel = ClipboardSnapshot(platform="windows", items=[], captured_at=0.0)
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log") as mock_log,
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            result = cm.copy("expected")
        assert result is sentinel  # copy returns the snapshot (best-effort)
        # Verify logged the verification-failed warning.
        warning_calls = [c for c in mock_log.warning.call_args_list if "verification" in str(c).lower()]
        assert len(warning_calls) >= 1
        # Final error log after 3 retries fail.
        error_calls = [c for c in mock_log.error.call_args_list if "verification" in str(c).lower()]
        assert len(error_calls) >= 1

    def test_copy_verification_succeeds_after_one_mismatch(self, fake_win32):
        """First paste() mismatches, second matches → break out of verify loop."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.return_value = None
        mock_pyper.paste.side_effect = ["wrong", "expected"]
        sentinel = ClipboardSnapshot(platform="windows", items=[], captured_at=0.0)
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            result = cm.copy("expected")
        assert result is sentinel

    def test_copy_verification_swallows_paste_exception(self, fake_win32):
        """pyperclip.paste() raising during verification is swallowed."""
        cm = self._make_cm()
        mock_pyper = MagicMock()
        mock_pyper.copy.return_value = None
        # All paste() calls during verification raise → swallowed.
        mock_pyper.paste.side_effect = [OSError("boom"), OSError("boom"), OSError("boom")]
        sentinel = ClipboardSnapshot(platform="windows", items=[], captured_at=0.0)
        with (
            patch.object(clip_mod, "pyperclip", mock_pyper),
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "capture", return_value=sentinel),
        ):
            result = cm.copy("expected")
        assert result is sentinel


# ===========================================================================
# ClipboardManager.paste — Windows branches
# ===========================================================================


class TestPasteWindowsBranches:
    def _make_cm(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm.paste_enabled = True
        cm._keyboard = MagicMock()
        cm._last_paste_time = 0.0  # not rate-limited
        cm._clipboard_seq = 0
        cm._clipboard_save_restore_enabled = False
        cm._last_copied_text = "test"
        cm._restore_delay_ms = 150
        return cm

    def test_paste_recopies_when_seq_changes(self, fake_win32):
        """If clipboard seq changed between copy and paste, re-copy."""
        cm = self._make_cm()
        cm._clipboard_seq = 10  # was 10 at copy time
        # _get_clipboard_sequence_number returns a different value now.
        mock_pyper = MagicMock()
        with patch.object(clip_mod, "pyperclip", mock_pyper), patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(
                    ClipboardManager,
                    "_get_clipboard_sequence_number",
                    return_value=99,
                ),
                patch.object(
                    ClipboardManager,
                    "_is_safe_paste_target",
                    return_value=True,
                ),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
            ):
                result = cm.paste()
        assert result is True
        # Re-copied _last_copied_text ("test")
        mock_pyper.copy.assert_any_call("test")
        mock_send.assert_called_once()

    def test_paste_logs_error_when_recopy_fails(self, fake_win32):
        """If re-copy raises, error is logged and paste continues."""
        cm = self._make_cm()
        cm._clipboard_seq = 10
        mock_pyper = MagicMock()
        mock_pyper.copy.side_effect = OSError("re-copy failed")
        with patch.object(clip_mod, "pyperclip", mock_pyper), patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(
                    ClipboardManager,
                    "_get_clipboard_sequence_number",
                    return_value=99,
                ),
                patch.object(
                    ClipboardManager,
                    "_is_safe_paste_target",
                    return_value=True,
                ),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is True
        # An error was logged for the re-copy failure.
        error_calls = [
            c for c in mock_log.error.call_args_list if "re-copy" in str(c).lower() or "Failed to re-copy" in str(c)
        ]
        assert len(error_calls) >= 1

    def test_paste_skips_when_seq_zero(self, fake_win32):
        """When _clipboard_seq is 0, seq-check branch is skipped."""
        cm = self._make_cm()
        cm._clipboard_seq = 0
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(ClipboardManager, "_detect_focused_process", return_value=None),
                patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
            ):
                result = cm.paste()
        assert result is True
        mock_send.assert_called_once()

    def test_paste_uses_send_ctrl_v_win32_on_windows(self, fake_win32):
        """On Windows with a non-terminal target, _send_ctrl_v_win32 is called."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value="notepad.exe",
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
            ):
                result = cm.paste()
        assert result is True
        mock_send.assert_called_once()

    def test_paste_uses_shift_insert_for_terminal_on_windows(self, fake_win32):
        """For terminal processes on Windows, Shift+Insert is used."""
        cm = self._make_cm()
        # _Key must be set for _safe_key_press to work.
        with patch.object(clip_mod, "_Key") as mock_key:
            mock_key.shift = "shift_key"
            mock_key.insert = "insert_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                with (
                    patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="cmd.exe",
                    ),
                    patch.object(ClipboardManager, "_send_ctrl_v_win32") as mock_send,
                ):
                    result = cm.paste()
        assert result is True
        # _send_ctrl_v_win32 NOT called — terminal path used instead.
        mock_send.assert_not_called()
        # Shift+Insert pressed via _safe_key_press.
        cm._keyboard.press.assert_any_call("shift_key")
        cm._keyboard.press.assert_any_call("insert_key")

    def test_paste_uses_cmd_v_for_terminal_on_macos(self, fake_win32):
        """For terminal processes on macOS, Cmd+V is used (line 879)."""
        cm = self._make_cm()
        # ADR-0010 §5.3: production paste() now checks ``_Controller is None``
        # (was ``_Key is None``). Patch both so the early-return guard
        # doesn't fire on the macOS branch (which runs is_windows=False).
        with patch.object(clip_mod, "_Key") as mock_key, patch.object(clip_mod, "_Controller", MagicMock()):
            mock_key.cmd = "cmd_key"
            mock_key.shift = "shift_key"
            mock_key.insert = "insert_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                # Override the is_windows=True from fake_win32 with
                # is_macos=True so the macOS branch is taken.  We keep
                # is_windows=False to avoid the Windows seq-check path.
                with (
                    patch.object(clip_mod, "is_windows", return_value=False),
                    patch.object(clip_mod, "is_macos", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_is_safe_paste_target",
                        return_value=True,
                    ),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="cmd.exe",
                    ),
                ):
                    result = cm.paste()
        assert result is True
        # Cmd+V pressed via _safe_key_press.
        cm._keyboard.press.assert_any_call("cmd_key")
        cm._keyboard.press.assert_any_call("v")

    def test_paste_uses_cmd_v_for_non_terminal_on_macos(self, fake_win32):
        """For non-terminal processes on macOS, Cmd+V is used (line 883)."""
        cm = self._make_cm()
        # ADR-0010 §5.3: patch ``_Controller`` too (see above).
        with patch.object(clip_mod, "_Key") as mock_key, patch.object(clip_mod, "_Controller", MagicMock()):
            mock_key.cmd = "cmd_key"
            with patch.object(clip_mod, "time") as mock_time:
                mock_time.monotonic.return_value = 100.0
                mock_time.sleep = MagicMock()
                with (
                    patch.object(clip_mod, "is_windows", return_value=False),
                    patch.object(clip_mod, "is_macos", return_value=True),
                    patch.object(
                        ClipboardManager,
                        "_is_safe_paste_target",
                        return_value=True,
                    ),
                    patch.object(
                        ClipboardManager,
                        "_detect_focused_process",
                        return_value="notepad.exe",
                    ),
                ):
                    result = cm.paste()
        assert result is True
        cm._keyboard.press.assert_any_call("cmd_key")
        cm._keyboard.press.assert_any_call("v")

    def test_paste_logs_rdp_session(self, fake_win32):
        """When is_remote_session() returns True, paste_delay is increased."""
        cm = self._make_cm()
        # Build a fake server_platform.is_remote_session that returns True.
        fake_platform = MagicMock()
        fake_platform.is_remote_session.return_value = True
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.dict(
                    sys.modules,
                    {"voice_typer.server.server_platform": fake_platform},
                ),
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is True
        # Verify RDP info log was emitted.
        info_calls = [c for c in mock_log.info.call_args_list if "RDP" in str(c)]
        assert len(info_calls) >= 1

    def test_paste_logs_rdp_check_exception(self, fake_win32):
        """If is_remote_session import fails, paste continues with default delay."""
        cm = self._make_cm()
        # Make the server_platform module raise on attribute access.
        fake_platform = MagicMock()
        type(fake_platform).is_remote_session = PropertyMock(side_effect=RuntimeError("boom"))
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.dict(
                    sys.modules,
                    {"voice_typer.server.server_platform": fake_platform},
                ),
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
            ):
                result = cm.paste()
        assert result is True

    def test_paste_returns_false_when_unsafe_target(self, fake_win32):
        """_is_safe_paste_target returning False → paste returns False."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=False),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is False
        info_calls = [
            c for c in mock_log.info.call_args_list if "security-sensitive" in str(c) or "Paste blocked" in str(c)
        ]
        assert len(info_calls) >= 1

    def test_paste_logs_rich_editor_info(self, fake_win32):
        """Pasting into a known rich editor logs an info message."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value="winword.exe",
                ),
                patch.object(ClipboardManager, "_send_ctrl_v_win32"),
                patch.object(clip_mod, "log") as mock_log,
            ):
                result = cm.paste()
        assert result is True
        rich_calls = [c for c in mock_log.info.call_args_list if "rich editor" in str(c).lower()]
        assert len(rich_calls) >= 1

    def test_paste_returns_false_on_send_ctrl_v_exception(self, fake_win32):
        """If _send_ctrl_v_win32 raises, paste() catches and returns False."""
        cm = self._make_cm()
        with patch.object(clip_mod, "time") as mock_time:
            mock_time.monotonic.return_value = 100.0
            mock_time.sleep = MagicMock()
            with (
                patch.object(ClipboardManager, "_is_safe_paste_target", return_value=True),
                patch.object(
                    ClipboardManager,
                    "_detect_focused_process",
                    return_value=None,
                ),
                patch.object(
                    ClipboardManager,
                    "_send_ctrl_v_win32",
                    side_effect=RuntimeError("sendinput broken"),
                ),
                patch.object(clip_mod, "log"),
            ):
                result = cm.paste()
        assert result is False


# ===========================================================================
# ClipboardManager._send_ctrl_v_win32
# ===========================================================================


class TestSendCtrlVWin32:
    def _make_cm(self):
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = MagicMock()
        return cm

    def _patch_pynput_win32(self, sendinput_return=4):
        """Patch sys.modules so ``from pynput._util.win32 import ...``
        returns our real ctypes structures + a mocked SendInput.
        """
        mod = _make_pynput_win32_module(sendinput_return)
        return patch.dict(
            sys.modules,
            {"pynput._util": MagicMock(), "pynput._util.win32": mod},
        )

    def test_calls_sendinput_with_four_events(self, fake_win32):
        """Happy path: SendInput returns 4 → success, no fallback."""
        cm = self._make_cm()
        with self._patch_pynput_win32(sendinput_return=4) as mods:
            mod = mods["pynput._util.win32"]
            with patch.object(clip_mod, "_Key") as mock_key:
                mock_key.ctrl = "ctrl_key"
                cm._send_ctrl_v_win32()
            # SendInput was called with 4 events.
            mod.SendInput.assert_called_once()
            args, _ = mod.SendInput.call_args
            assert args[0] == 4

    def test_logs_warning_on_sendinput_zero_and_falls_back_to_pynput(self, fake_win32):
        """SendInput returning 0 → log info, fall back to pynput."""
        cm = self._make_cm()
        with self._patch_pynput_win32(sendinput_return=0), patch.object(clip_mod, "_Key") as mock_key:
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
        with self._patch_pynput_win32(sendinput_return=2) as mods:
            mod = mods["pynput._util.win32"]
            with patch.object(clip_mod, "_Key") as mock_key:
                mock_key.ctrl = "ctrl_key"
                with patch.object(clip_mod, "log") as mock_log:
                    cm._send_ctrl_v_win32()
        # SendInput called twice: first (4 events), then (2 KEYUP cleanup).
        assert mod.SendInput.call_count == 2
        # Second call used 2 events (KEYUP for V and Ctrl).
        second_args, _ = mod.SendInput.call_args_list[1]
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
        with self._patch_pynput_win32(sendinput_return=1) as mods:
            mod = mods["pynput._util.win32"]
            # First SendInput returns 1 (partial); second raises.
            mod.SendInput.side_effect = [1, RuntimeError("cleanup failed")]
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
# ClipboardManager._release_stuck_modifiers / _send_keystroke_sequence
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

    def test_send_keystroke_sequence_finally_catches_release_exception(self):
        """If the finally-block release raises, it's swallowed by the
        inner ``except Exception: pass``.

        We make the *try*-block releases succeed (so no exception
        propagates out of the function) and the *finally*-block
        releases raise (to exercise lines 911-913).
        """
        cm = ClipboardManager.__new__(ClipboardManager)
        cm._keyboard = MagicMock()
        # release order in try: char, modifier (2 successes)
        # release order in finally: modifier, char (2 raises, swallowed)
        cm._keyboard.release.side_effect = [
            None,  # release(char) in try
            None,  # release(modifier) in try
            RuntimeError("finally 1"),  # release(modifier) in finally
            RuntimeError("finally 2"),  # release(char) in finally
        ]
        modifier = MagicMock(name="modifier")
        # Should not raise — finally's inner except swallows.
        cm._send_keystroke_sequence(modifier, "v")
        # All 4 release calls consumed.
        assert cm._keyboard.release.call_count == 4
