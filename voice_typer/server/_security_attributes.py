"""Win32 SECURITY_ATTRIBUTES builder for the single-instance mutex.

Extracted from ``voice_typer.server.app`` (RW-00) so the 90+ lines of
raw Win32 ctypes live in a focused, security-reviewable module instead
of inside the 2300-line god-class.  Public surface is unchanged:

    from voice_typer.server.app import _create_restrictive_security_attributes

still works (re-exported from ``app.py``), and
``app._ensure_single_instance`` continues to call it the same way.

Why a restrictive DACL matters
------------------------------
The single-instance guard is a Win32 named mutex under the ``Local\\``
namespace.  By default, kernel objects in the ``Local\\`` namespace
inherit a permissive security descriptor that allows any process
running in the same session (including other users in multi-user
scenarios like Terminal Services, Fast User Switching, or run-as
contexts) to ``OpenMutex`` *our* mutex and either:

  * **Hold it open forever** — denying service to legitimate Voice
    Typer launches (denial-of-service).
  * **Release it prematurely** — allowing a second instance to start
    and corrupt the on-disk config / crash-recovery state (safety
    bypass).

To prevent both, we build a SECURITY_ATTRIBUTES whose DACL contains
exactly one ACE: ``MUTEX_ALL_ACCESS`` granted to the *current* user's
SID.  Any other principal (including other SIDs in the same session)
is implicitly denied by the absence of a matching ACE — DACLs are
allow-lists, so anything not explicitly allowed is denied.

Failure mode
------------
If anything goes wrong (token query fails, SID extraction fails,
``SetEntriesInAclW`` fails, etc.), we return ``None``.  The caller
then passes a NULL ``lpMutexAttributes`` to ``CreateMutexW``, which
uses the default NULL DACL.  That's still functional (the mutex works
as a single-instance guard for the *current* user in the common case)
but offers no cross-user protection — so we log nothing here and let
the caller decide whether to warn.
"""

from __future__ import annotations

import logging

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def _create_restrictive_security_attributes():
    """SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL.

    Builds a Win32 SECURITY_ATTRIBUTES structure whose DACL allows only
    the current user (SID) to access the named mutex. This prevents other
    user sessions from opening or manipulating our mutex object.

    Returns a ctypes SECURITY_ATTRIBUTES structure, or None on failure
    (in which case the default NULL DACL is used — still functional but
    less restrictive).
    """
    if not is_windows():
        return None
    try:
        import ctypes
        from ctypes import wintypes

        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

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

            # Extract SID from TOKEN_USER structure
            # TOKEN_USER: SID_AND_ATTRIBUTES (pSid, dwAttributes)
            p_sid = ctypes.cast(
                ctypes.addressof(buf) + ctypes.sizeof(wintypes.LPVOID),
                ctypes.POINTER(wintypes.LPVOID),
            )[0]
            if not p_sid:
                return None

            # Build a SECURITY_DESCRIPTOR with a DACL containing only
            # one ACE: grant GENERIC_ALL to the current user SID.
            sd_size = 1024
            sd = ctypes.create_string_buffer(sd_size)
            if not advapi32.InitializeSecurityDescriptor(sd, 1):  # SECURITY_DESCRIPTOR_REVISION
                return None

            # Build an explicit access array for the current user
            class EXPLICIT_ACCESS(ctypes.Structure):  # noqa: N801
                _fields_ = [
                    ("grfAccessPermissions", wintypes.DWORD),
                    ("grfAccessMode", wintypes.DWORD),
                    ("grfInheritance", wintypes.DWORD),
                    ("Trustee", ctypes.c_byte * 64),  # TRUSTEE is variable-size
                ]

            ea = EXPLICIT_ACCESS()
            # Grant all access
            ctypes.memset(ctypes.byref(ea), 0, ctypes.sizeof(ea))
            ea.grfAccessPermissions = 0x1F0003  # MUTEX_ALL_ACCESS
            ea.grfAccessMode = 0  # GRANT_ACCESS
            ea.grfInheritance = 0  # NO_INHERITANCE

            # Build TRUSTEE manually
            # TRUSTEE_IS_SID = 0, TRUSTEE_IS_WELL_KNOWN_GROUP = 5
            # Simplified: use SetEntriesInAcl with the SID
            trustee_bytes = ctypes.create_string_buffer(64)
            ctypes.memset(trustee_bytes, 0, 64)
            # pMultipleTrustee = NULL
            # MultipleTrusteeOperation = 0 (NO_MULTIPLE_TRUSTEE)
            # TrusteeForm = 0 (TRUSTEE_IS_SID)
            # TrusteeType = 1 (TRUSTEE_IS_USER)
            # ptstrName = pSid
            offset = ctypes.sizeof(wintypes.LPVOID)  # pMultipleTrustee
            offset += ctypes.sizeof(wintypes.DWORD)  # MultipleTrusteeOperation
            offset += ctypes.sizeof(wintypes.DWORD)  # TrusteeForm
            offset += ctypes.sizeof(wintypes.DWORD)  # TrusteeType
            ctypes.memmove(
                ctypes.addressof(trustee_bytes) + offset,
                ctypes.byref(ctypes.c_void_p(p_sid)),
                ctypes.sizeof(ctypes.c_void_p),
            )
            # Copy the trustee fields into ea
            ctypes.memmove(ctypes.byref(ea.Trustee), trustee_bytes, 64)

            # Set the DACL
            new_acl = wintypes.LPVOID()
            if advapi32.SetEntriesInAclW(1, ctypes.byref(ea), None, ctypes.byref(new_acl)) != 0:
                # Fallback: use a simpler approach with NULL DACL
                log.debug(
                    "[SECURITY] SetEntriesInAclW failed; falling back to NULL DACL "
                    "(no cross-user protection for single-instance mutex)",
                    exc_info=True,
                )
                if not advapi32.SetSecurityDescriptorDacl(sd, True, None, False):
                    return None
            else:
                if not advapi32.SetSecurityDescriptorDacl(sd, True, new_acl, False):
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
            # RW-6 (pyrefly): build the LPVOID via ``c_void_p(addressof(sd))``
            # instead of ``cast(sd, LPVOID)``. Both produce a ``c_void_p``
            # pointing at the security-descriptor buffer, but pyrefly 1.x
            # rejects the ``cast`` form because it cannot prove
            # ``c_char_Array[N]`` satisfies the ``_CanCastTo`` type-variable
            # bound on ``ctypes.cast``. ``addressof`` returns the buffer's
            # integer address, which ``c_void_p`` accepts unambiguously —
            # no false positive, identical runtime behaviour.
            sa.lpSecurityDescriptor = ctypes.c_void_p(ctypes.addressof(sd))
            sa.bInheritHandle = False
            # Keep references alive so they don't get GC'd while the mutex holds them
            sa._sd_ref = sd
            sa._acl_ref = new_acl
            return sa
        finally:
            kernel32.CloseHandle(token)
    except Exception:
        # If we can't build a restrictive DACL, return None and fall back
        # to default (NULL) security attributes
        return None
