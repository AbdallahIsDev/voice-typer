"""Win32 code-path coverage for ``voice_typer.server.clipboard``."""

from __future__ import annotations

import ctypes
import sys
import types
from ctypes import wintypes
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import (
    clipboard as clip_mod,  # noqa: E402
    clipboard_target_safety as safety_mod,  # noqa: E402
)
from voice_typer.server.clipboard import (  # noqa: E402
    ClipboardManager,
    _focused_window_is_credential_dialog,
    _is_content_editable,
    _is_elevated_target,
    _is_password_field,
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


class TestIsElevatedTargetWindows:
    @pytest.fixture(autouse=True)
    def _reset_we_elevated(self):
        """ARCH-11: clear the safety module's ``_WE_ELEVATED`` cache so"""
        safety_mod._WE_ELEVATED = None
        yield
        safety_mod._WE_ELEVATED = None

    def test_returns_false_when_no_foreground_window(self, fake_win32):
        """GetForegroundWindow returning 0 → False."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        assert _is_elevated_target() is False

    def test_returns_false_when_pid_is_zero(self, fake_win32):
        """GetWindowThreadProcessId leaving pid=0 → False."""
        # Don't set side_effect, pid.value stays at default 0.
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

    def test_returns_true_when_our_open_process_token_fails(self, fake_win32):
        """OpenProcessToken for our_token returning 0 → True (fail-closed)."""

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
        assert result is True

    def test_returns_true_when_our_get_token_info_fails(self, fake_win32):
        """GetTokenInformation for our_token (real call) returning 0 → True."""

        def _set_pid(hwnd, byref_obj):
            _set_byref_value(byref_obj, 1234)

        fake_win32["user32"].GetWindowThreadProcessId.side_effect = _set_pid
        fake_win32["advapi32"].OpenProcessToken.return_value = 1
        # 4 GetTokenInformation calls:
        fake_win32["advapi32"].GetTokenInformation.side_effect = [1, 1, 1, 0]
        fake_target_ptr = MagicMock()
        fake_target_ptr.__getitem__.return_value = 1
        with patch("ctypes.cast", side_effect=[fake_target_ptr]):
            result = _is_elevated_target()
        assert result is True

    def test_returns_true_on_exception(self, fake_win32):
        """Any unexpected exception → True (fail-closed, EC-15)."""
        fake_win32["user32"].GetForegroundWindow.side_effect = RuntimeError("unexpected")
        assert _is_elevated_target() is True


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

    def test_returns_true_on_exception(self, fake_win32):
        """Any exception → True (fail-closed, EC-15)."""
        fake_win32["user32"].GetForegroundWindow.side_effect = OSError("nope")
        assert _focused_window_is_credential_dialog() is True


class TestIsPasswordFieldWindows:
    @pytest.fixture(autouse=True)
    def _reset_uia_singleton(self):
        """Reset the module-level UIA singleton so each test's per-test"""
        safety_mod._UIA_SINGLETON = None
        safety_mod._UIA_MODULE = None
        safety_mod._UIA_SINGLETON_INIT_ATTEMPTED = False
        safety_mod._WE_ELEVATED = None
        yield
        # Restore after test in case the test set them.
        safety_mod._UIA_SINGLETON = None
        safety_mod._UIA_MODULE = None
        safety_mod._UIA_SINGLETON_INIT_ATTEMPTED = False
        safety_mod._WE_ELEVATED = None

    def test_returns_true_when_no_foreground_window(self, fake_win32):
        """Unresolvable field state → True (fail closed, target treated unsafe)."""
        fake_win32["user32"].GetForegroundWindow.return_value = 0
        # Make comtypes import fail so we exercise the ImportError branch.
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard_target_safety._focused_window_is_credential_dialog",
                return_value=False,
            ),
        ):
            # Function should not raise; the unknown state blocks the paste.
            result = _is_password_field()
        assert result is True

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

    def test_returns_true_on_comtypes_import_error(self, fake_win32):
        """comtypes ImportError → the unknown field state fails CLOSED.

        The window-class probe is still consulted for the log line, but the
        block decision no longer depends on it.
        """
        # Setting sys.modules entries to None makes Python raise
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard_target_safety._focused_window_is_credential_dialog",
                return_value=False,
            ) as mock_cred,
        ):
            result = _is_password_field()
        assert result is True
        mock_cred.assert_called_once()

    def test_returns_true_when_comtypes_missing_and_cred_dialog_present(self, fake_win32):
        """comtypes ImportError + credential dialog → True (fail closed)."""
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard_target_safety._focused_window_is_credential_dialog",
                return_value=True,
            ),
        ):
            result = _is_password_field()
        assert result is True

    def test_returns_true_on_uia_call_exception(self, fake_win32):
        """comtypes installed but UIA raises → field state UNKNOWN → fail CLOSED."""
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
            patch(
                "voice_typer.server.clipboard_target_safety._focused_window_is_credential_dialog",
                return_value=False,
            ),
        ):
            result = _is_password_field()
        # An unresolved field state must never be treated as "safe".
        assert result is True

    def test_returns_true_on_outer_exception(self, fake_win32):
        """An unexpected exception while resolving the field → fail CLOSED."""
        with (
            patch.dict(sys.modules, {"comtypes": None, "comtypes.client": None}),
            patch.object(clip_mod, "log"),
            patch(
                "voice_typer.server.clipboard_target_safety._focused_window_is_credential_dialog",
                side_effect=RuntimeError("cred dialog broke"),
            ),
        ):
            result = _is_password_field()
        # An unresolved field state must never be treated as "safe".
        assert result is True

    def test_returns_true_when_wintypes_import_fails(self, fake_win32):
        """If ``from ctypes import wintypes`` raises → fail CLOSED."""
        saved_attr = getattr(ctypes, "wintypes", None)
        had_attr = hasattr(ctypes, "wintypes")
        if had_attr:
            del ctypes.wintypes
        try:
            with patch.dict(sys.modules, {"ctypes.wintypes": None}):
                result = _is_password_field()
            assert result is True
        finally:
            if had_attr:
                # Restore the attribute so other tests aren't affected.
                ctypes.wintypes = saved_attr


class TestIsContentEditableWindows:
    @pytest.fixture(autouse=True)
    def _reset_uia_singleton(self):
        """Reset the module-level UIA singleton (see TestIsPasswordFieldWindows)."""
        safety_mod._UIA_SINGLETON = None
        safety_mod._UIA_MODULE = None
        safety_mod._UIA_SINGLETON_INIT_ATTEMPTED = False
        yield
        safety_mod._UIA_SINGLETON = None
        safety_mod._UIA_MODULE = None
        safety_mod._UIA_SINGLETON_INIT_ATTEMPTED = False

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


class TestIsSafePasteTargetWindows:
    def test_returns_true_for_generic_dialog_class(self, fake_win32):
        """Class '#32770' (generic Win32 Dialog) → True."""
        fake_win32["buf"].return_value.value = "#32770"
        with (
            patch("voice_typer.server.clipboard._is_elevated_target", return_value=False),
            patch("voice_typer.server.clipboard._is_password_field", return_value=False),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True

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

    def test_returns_false_on_outer_runtime_exception(self, fake_win32):
        """unexpected RuntimeError → False (fail CLOSED)."""
        fake_win32["user32"].GetForegroundWindow.side_effect = RuntimeError("boom")
        assert ClipboardManager._is_safe_paste_target() is False

    def test_returns_true_on_outer_import_error(self, fake_win32):
        """ImportError → True (fail open, broken infra)."""
        import builtins

        real_import = builtins.__import__

        def _fail_ctypes(name, *args, **kwargs):
            if name == "ctypes":
                raise ImportError("ctypes unavailable (simulated)")
            return real_import(name, *args, **kwargs)

        fake_win32["buf"].return_value.value = "Edit"
        with (
            patch("builtins.__import__", side_effect=_fail_ctypes),
            patch.object(clip_mod, "log"),
        ):
            result = ClipboardManager._is_safe_paste_target()
        assert result is True

    def test_returns_false_when_is_elevated_check_raises(self, fake_win32):
        """If _is_elevated_target raises, fail-closed → False."""
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
        assert result is False

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
