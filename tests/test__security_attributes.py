"""Real unit tests for ``voice_typer.server._security_attributes``."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import _security_attributes as sa_mod
from voice_typer.server._security_attributes import (
    _create_restrictive_security_attributes,
)


def _set_byref_value(byref_obj, value):
    """Mutate the underlying ``c_ulong``/``c_void_p`` wrapped by ``ctypes.byref``."""
    byref_obj._obj.value = value


def _write_sid_pointer_into_buf(buf, ptr_value=0xDEADBEEF):
    """Write a non-zero pointer at offset ``0`` in ``buf``."""
    offset = 0
    fake_ptr = ctypes.c_void_p(ptr_value)
    ctypes.memmove(
        ctypes.addressof(buf) + offset,
        ctypes.byref(fake_ptr),
        ctypes.sizeof(fake_ptr),
    )


def _gti_success_side_effect(token, info_class, buf, buf_len, ret_len_ref):
    """Side effect for ``GetTokenInformation`` that simulates success."""
    _set_byref_value(ret_len_ref, 64)
    if buf is not None:
        _write_sid_pointer_into_buf(buf)
        return 1
    return 0  # first call's return value is ignored by the SUT


def _configure_full_success(advapi32):
    """Configure ``advapi32`` mocks for the full success path."""
    advapi32.GetTokenInformation.side_effect = _gti_success_side_effect
    advapi32.InitializeSecurityDescriptor.return_value = 1  # BOOL success
    advapi32.SetEntriesInAclW.return_value = 0  # ERROR_SUCCESS
    advapi32.SetSecurityDescriptorDacl.return_value = 1  # BOOL success


@pytest.fixture
def fake_windll():
    """Mock ``ctypes.windll`` and ``is_windows`` so the Windows-only"""
    mock_windll = MagicMock()
    mock_advapi32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_windll.advapi32 = mock_advapi32
    mock_windll.kernel32 = mock_kernel32

    # Sane defaults, OpenProcessToken succeeds, GetCurrentProcess
    mock_kernel32.GetCurrentProcess.return_value = 0xFFFFFFFF
    mock_kernel32.CloseHandle.return_value = 1
    mock_advapi32.OpenProcessToken.return_value = 1  # BOOL success

    with (
        patch.object(sa_mod, "is_windows", return_value=True),
        patch("ctypes.windll", mock_windll, create=True),
    ):
        yield {
            "windll": mock_windll,
            "advapi32": mock_advapi32,
            "kernel32": mock_kernel32,
        }


class TestPlatformGuard:
    def test_returns_none_on_non_windows(self):
        """On non-Windows, the function returns None immediately"""
        with (
            patch.object(sa_mod, "is_windows", return_value=False),
            patch("ctypes.windll", MagicMock(), create=True) as mock_wd,
        ):
            assert _create_restrictive_security_attributes() is None
            mock_wd.advapi32.assert_not_called()


class TestErrorPaths:
    def test_open_process_token_failure_returns_none(self, fake_windll):
        """When ``OpenProcessToken`` returns 0, the function returns"""
        fake_windll["advapi32"].OpenProcessToken.return_value = 0
        assert _create_restrictive_security_attributes() is None
        fake_windll["kernel32"].CloseHandle.assert_not_called()

    def test_get_token_information_second_call_failure_returns_none(self, fake_windll):
        """When the second ``GetTokenInformation`` (data query) returns"""

        def _fail_second(token, info_class, buf, buf_len, ret_len_ref):
            _set_byref_value(ret_len_ref, 64)
            if buf is not None:
                return 0  # second call fails
            return 0

        fake_windll["advapi32"].GetTokenInformation.side_effect = _fail_second
        assert _create_restrictive_security_attributes() is None

    def test_null_sid_returns_none(self, fake_windll):
        """When the extracted SID pointer is NULL (0), returns None."""

        def _gti_null_sid(token, info_class, buf, buf_len, ret_len_ref):
            _set_byref_value(ret_len_ref, 64)
            if buf is not None:
                # Do NOT write a SID pointer, p_sid reads as 0.
                return 1
            return 0

        fake_windll["advapi32"].GetTokenInformation.side_effect = _gti_null_sid
        assert _create_restrictive_security_attributes() is None

    def test_initialize_security_descriptor_failure_returns_none(self, fake_windll):
        """When ``InitializeSecurityDescriptor`` returns 0, returns None."""
        adv = fake_windll["advapi32"]
        adv.GetTokenInformation.side_effect = _gti_success_side_effect
        adv.InitializeSecurityDescriptor.return_value = 0
        assert _create_restrictive_security_attributes() is None

    def test_set_entries_in_acl_success_dacl_failure_returns_none(self, fake_windll):
        """``SetEntriesInAclW=0`` (success) → success branch; if"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        adv.SetSecurityDescriptorDacl.return_value = 0  # BOOL failure
        assert _create_restrictive_security_attributes() is None

    def test_set_entries_in_acl_failure_returns_none(self, fake_windll):
        """``SetEntriesInAclW=non-zero`` (failure) → returns None directly"""
        adv = fake_windll["advapi32"]
        adv.GetTokenInformation.side_effect = _gti_success_side_effect
        adv.InitializeSecurityDescriptor.return_value = 1
        adv.SetEntriesInAclW.return_value = 5  # ERROR_ACCESS_DENIED
        adv.SetSecurityDescriptorDacl.return_value = 0  # would fail if reached
        assert _create_restrictive_security_attributes() is None
        # the SUT must NOT call SetSecurityDescriptorDacl on the
        adv.SetSecurityDescriptorDacl.assert_not_called()

    def test_exception_during_execution_returns_none(self, fake_windll):
        """Any unexpected exception is caught by the outer ``except"""
        fake_windll["advapi32"].GetTokenInformation.side_effect = RuntimeError("kaboom")
        assert _create_restrictive_security_attributes() is None


class TestDaclConstruction:
    def test_explicit_access_grants_mutex_all_access(self, fake_windll):
        """The ``EXPLICIT_ACCESS`` ACE grants ``0x1F0003``"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        _create_restrictive_security_attributes()
        # SetEntriesInAclW(1, byref(ea), None, byref(new_acl))
        ea_byref = adv.SetEntriesInAclW.call_args[0][1]
        ea = ea_byref._obj  # the EXPLICIT_ACCESS instance
        assert ea.grfAccessPermissions == 0x1F0003  # MUTEX_ALL_ACCESS
        assert ea.grfAccessMode == 0  # GRANT_ACCESS
        assert ea.grfInheritance == 0  # NO_INHERITANCE

    def test_win32_api_call_arguments(self, fake_windll):
        """Verify the Win32 API calls receive the expected arguments:"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        _create_restrictive_security_attributes()

        # OpenProcessToken(process, TOKEN_QUERY, byref(token))
        opt_args = adv.OpenProcessToken.call_args[0]
        assert opt_args[1] == 0x0008  # TOKEN_QUERY

        # InitializeSecurityDescriptor(sd, SECURITY_DESCRIPTOR_REVISION=1)
        isd_args = adv.InitializeSecurityDescriptor.call_args[0]
        assert isd_args[1] == 1

        # SetEntriesInAclW(1, byref(ea), None, byref(new_acl))
        sia_args = adv.SetEntriesInAclW.call_args[0]
        assert sia_args[0] == 1  # cEntries
        assert sia_args[2] is None  # oldacl

        # SetSecurityDescriptorDacl(sd, True, dacl, False)
        sdd_args = adv.SetSecurityDescriptorDacl.call_args[0]
        assert sdd_args[1] is True  # bDaclPresent
        assert sdd_args[3] is False  # bDaclDefaulted


class TestSecurityAttributesOutput:
    def test_success_returns_security_attributes(self, fake_windll):
        """Full success path returns a ctypes Structure with the"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        sa = _create_restrictive_security_attributes()
        assert sa is not None
        assert hasattr(sa, "nLength")
        assert hasattr(sa, "lpSecurityDescriptor")
        assert hasattr(sa, "bInheritHandle")

    def test_security_attributes_field_values(self, fake_windll):
        """``sa.nLength == sizeof(SECURITY_ATTRIBUTES)``,"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        sa = _create_restrictive_security_attributes()

        class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("nLength", wintypes.DWORD),
                ("lpSecurityDescriptor", wintypes.LPVOID),
                ("bInheritHandle", wintypes.BOOL),
            ]

        assert sa.nLength == ctypes.sizeof(SECURITY_ATTRIBUTES)
        assert not sa.bInheritHandle  # False / 0
        assert int(sa.lpSecurityDescriptor or 0) != 0
        # Reference-keeping attributes (prevents GC while mutex holds SA)
        assert hasattr(sa, "_sd_ref")
        assert hasattr(sa, "_acl_ref")

    def test_close_handle_called_in_finally_on_success(self, fake_windll):
        """On the success path, ``CloseHandle`` is called exactly once"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        _create_restrictive_security_attributes()
        fake_windll["kernel32"].CloseHandle.assert_called_once()

    def test_close_handle_called_in_finally_on_mid_path_failure(self, fake_windll):
        """``CloseHandle`` is called even when a mid-path failure"""
        adv = fake_windll["advapi32"]
        adv.GetTokenInformation.side_effect = _gti_success_side_effect
        adv.InitializeSecurityDescriptor.return_value = 0  # failure
        _create_restrictive_security_attributes()
        fake_windll["kernel32"].CloseHandle.assert_called_once()


# ── Tests: SetEntriesInAclW return-code semantics (pin actual behaviour) ─


class TestSetEntriesInAclSemantics:
    """Document the corrected-check behaviour around ``SetEntriesInAclW``."""

    def test_failure_return_returns_none_without_dacl_call(self, fake_windll):
        """``SetEntriesInAclW=non-zero`` (error) → returns ``None`` directly"""
        adv = fake_windll["advapi32"]
        adv.GetTokenInformation.side_effect = _gti_success_side_effect
        adv.InitializeSecurityDescriptor.return_value = 1
        adv.SetEntriesInAclW.return_value = 5  # ERROR_ACCESS_DENIED
        adv.SetSecurityDescriptorDacl.return_value = 1  # would succeed if reached
        result = _create_restrictive_security_attributes()
        assert result is None
        # the SUT must NOT call SetSecurityDescriptorDacl on the
        adv.SetSecurityDescriptorDacl.assert_not_called()

    def test_success_return_uses_new_acl(self, fake_windll):
        """``SetEntriesInAclW=0`` (``ERROR_SUCCESS``) → success branch →"""
        adv = fake_windll["advapi32"]
        _configure_full_success(adv)
        _create_restrictive_security_attributes()
        args = adv.SetSecurityDescriptorDacl.call_args[0]
        assert args[2] is not None
