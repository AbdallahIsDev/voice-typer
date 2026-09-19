"""Win32 SECURITY_ATTRIBUTES builder for the single-instance mutex."""

from __future__ import annotations

import contextlib
import ctypes
import logging
import weakref

log = logging.getLogger(__name__)

# SEC-001: Sid is PSID/PVOID → ctypes.c_void_p (portable; not POINTER(c_void_p)).


def _create_restrictive_security_attributes():
    """SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL.

    Builds a Win32 SECURITY_ATTRIBUTES structure whose DACL allows only
    the current user (SID) to access the named mutex. This prevents other
    user sessions from opening or manipulating our mutex object.

    Returns a ctypes SECURITY_ATTRIBUTES structure, or None on failure
    (in which case the caller passes NULL ``lpMutexAttributes`` to
    ``CreateMutexW``: using the default per-user DACL from the process
    token, which is still per-user-restrictive but offers no additional
    cross-session hardening).
    """
    # Call-time shim lookup so tests that patch _security_attributes.is_windows work.
    from voice_typer.server import _security_attributes as _sa_shim

    if not _sa_shim.is_windows():
        return None
    try:
        from ctypes import wintypes

        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        # Win32 structs via ctypes; Sid is c_void_p (PSID).
        class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("Sid", ctypes.c_void_p),
                ("Attributes", wintypes.DWORD),
            ]

        class TOKEN_USER(ctypes.Structure):  # noqa: N801
            _fields_ = [("User", SID_AND_ATTRIBUTES)]

        # TRUSTEE_W x64 pack=8: ptstrName at offset 24; field is PVOID (c_void_p).
        class TRUSTEE_W(ctypes.Structure):  # noqa: N801
            _pack_ = 8
            _fields_ = [
                ("pMultipleTrustee", ctypes.POINTER(ctypes.c_void_p)),
                ("MultipleTrusteeOperation", wintypes.DWORD),
                ("TrusteeForm", wintypes.DWORD),
                ("TrusteeType", wintypes.DWORD),
                ("ptstrName", ctypes.c_void_p),
            ]

        class EXPLICIT_ACCESS_W(ctypes.Structure):  # noqa: N801
            _pack_ = 8
            _fields_ = [
                ("grfAccessPermissions", wintypes.DWORD),
                ("grfAccessMode", wintypes.DWORD),
                ("grfInheritance", wintypes.DWORD),
                ("Trustee", TRUSTEE_W),
            ]

        # Get current process token
        token = wintypes.HANDLE()
        if not advapi32.OpenProcessToken(
            kernel32.GetCurrentProcess(),
            0x0008,  # TOKEN_QUERY
            ctypes.byref(token),
        ):
            return None
        try:
            # Get required buffer size for TokenUser
            ret_len = wintypes.DWORD()
            advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(ret_len))
            buf = ctypes.create_string_buffer(ret_len.value)
            if not advapi32.GetTokenInformation(token, 1, buf, ret_len.value, ctypes.byref(ret_len)):
                return None

            # TOKEN_USER.User.Sid is at offset 0.
            tu = TOKEN_USER.from_buffer_copy(buf)
            p_sid = tu.User.Sid
            if not p_sid:
                return None

            # Build a SECURITY_DESCRIPTOR with a DACL containing only
            sd_size = 1024
            sd = ctypes.create_string_buffer(sd_size)
            if not advapi32.InitializeSecurityDescriptor(sd, 1):  # SECURITY_DESCRIPTOR_REVISION
                return None

            # Build an EXPLICIT_ACCESS_W for the current user.
            ea = EXPLICIT_ACCESS_W()
            ctypes.memset(ctypes.byref(ea), 0, ctypes.sizeof(ea))
            ea.grfAccessPermissions = 0x1F0003  # MUTEX_ALL_ACCESS
            ea.grfAccessMode = 0  # GRANT_ACCESS
            ea.grfInheritance = 0  # NO_INHERITANCE
            ea.Trustee.pMultipleTrustee = None
            ea.Trustee.MultipleTrusteeOperation = 0  # NO_MULTIPLE_TRUSTEE
            ea.Trustee.TrusteeForm = 0  # TRUSTEE_IS_SID
            ea.Trustee.TrusteeType = 1  # TRUSTEE_IS_USER
            ea.Trustee.ptstrName = p_sid  # c_void_p, no cast needed

            # Set the DACL
            new_acl = wintypes.LPVOID()
            if advapi32.SetEntriesInAclW(1, ctypes.byref(ea), None, ctypes.byref(new_acl)) != 0:
                # SEC-001: never fall back to a NULL DACL (world-open); use token default.
                log.warning(
                    "[SECURITY] SetEntriesInAclW failed; falling back to "
                    "default per-user DACL (no NULL DACL, cross-user "
                    "protection preserved at the default level)"
                )
                return None
            if not advapi32.SetSecurityDescriptorDacl(sd, True, new_acl, False):
                # free the ACL on the SetSecurityDescriptorDacl
                with contextlib.suppress(Exception):
                    kernel32.LocalFree(new_acl)
                return None

            # Build SECURITY_ATTRIBUTES
            class SECURITY_ATTRIBUTES(ctypes.Structure):  # noqa: N801
                _fields_ = [
                    ("nLength", wintypes.DWORD),
                    ("lpSecurityDescriptor", wintypes.LPVOID),
                    ("bInheritHandle", wintypes.BOOL),
                ]

            sa = SECURITY_ATTRIBUTES()
            sa.nLength = ctypes.sizeof(SECURITY_ATTRIBUTES)
            sa.lpSecurityDescriptor = ctypes.c_void_p(ctypes.addressof(sd))
            sa.bInheritHandle = False
            # Keep references alive so they don't get GC'd while the mutex holds them.
            sa._sd_ref = sd
            sa._acl_ref = new_acl
            # SetEntriesInAclW LocalAlloc's the ACL; free via weakref at process exit.
            sa._acl_finalizer = weakref.finalize(
                sa,
                lambda acl=new_acl, k32=kernel32: __local_free_safe(k32, acl),
            )
            return sa
        finally:
            kernel32.CloseHandle(token)
    except Exception as exc:
        # WARNING so struct-offset regressions are visible (fallback is still safe).
        log.warning(
            "[SECURITY] Restrictive DACL construction failed: %s, falling back to default per-user DACL",
            exc,
            exc_info=True,
        )
        return None


def __local_free_safe(kernel32, handle) -> None:  # noqa: N801
    """Best-effort LocalFree, never raises (called from weakref.finalize)."""
    try:
        kernel32.LocalFree(handle)
    except Exception:
        log.debug("[SECURITY] LocalFree failed during ACL cleanup", exc_info=True)
