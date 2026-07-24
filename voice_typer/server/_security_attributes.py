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

Failure mode (CR-003 fix, IMPROVE-mode run 2026-07-21)
------------------------------------------------------
If anything goes wrong (token query fails, SID extraction fails,
``SetEntriesInAclW`` fails, etc.), we return ``None``.  The caller
then passes a NULL ``lpMutexAttributes`` to ``CreateMutexW``, which
uses the **default per-user DACL** from the process token.  This is
the safe fallback: it preserves the original per-user protection
level rather than WIDENING access to world-open.

**Pre-CR-003 bug**: the fallback installed a NULL DACL via
``SetSecurityDescriptorDacl(sd, True, None, False)``.  Win32 semantics:
a NULL DACL is NOT the same as an empty DACL — it grants EVERY token
``MUTEX_ALL_ACCESS``.  Combined with CR-001/CR-002 (struct offset bugs
that made ``SetEntriesInAclW`` always fail), the fallback was always
taken, so every single-instance mutex on Windows effectively had a
NULL DACL — allowing any process in any session to ``OpenMutex`` and
either hold it (DoS) or release it prematurely (second-instance
corruption).  CR-003 removes the NULL-DACL fallback entirely.

CR-001 / CR-002 fix (struct offset bugs)
----------------------------------------
The pre-CR-001 code used manual ``addressof(buf) + sizeof(LPVOID)``
arithmetic to extract the SID pointer from ``TOKEN_USER`` — but
``TOKEN_USER.User.Sid`` is at offset 0, not ``sizeof(LPVOID)`` (which
lands on the ``Attributes`` DWORD on x64).  The pre-CR-002 code used
manual byte-array + ``memmove`` to build ``TRUSTEE_W`` — but the
manual offset calculation skipped the 4-byte alignment pad before
``ptstrName`` on x64, so ``ptstrName`` ended up NULL and
``SetEntriesInAclW`` always returned ``ERROR_INVALID_PARAMETER``.

Both are now fixed by replacing the manual byte-array construction
with proper ``ctypes.Structure`` definitions (``SID_AND_ATTRIBUTES``,
``TOKEN_USER``, ``TRUSTEE_W`` with ``_pack_ = 8``).
"""

from __future__ import annotations

import contextlib
import ctypes
import logging
import weakref

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

# XS-101 (IMPROVE-mode run XS): ``ctypes.wintypes.VOID`` is absent on
# Linux/macOS (the ``wintypes`` module simply doesn't define it on
# non-Windows Python builds, even though ``ctypes`` itself is importable).
# The Windows-only body of ``_create_restrictive_security_attributes``
# below references ``wintypes.VOID`` inside the ``SID_AND_ATTRIBUTES``
# ``ctypes.Structure`` definition; on Linux this previously raised
# ``AttributeError: module 'ctypes.wintypes' has no attribute 'VOID'``
# whenever the function was invoked (which happens during the
# ``test__security_attributes.py`` suite — the test mocks
# ``ctypes.windll`` but does NOT patch ``wintypes.VOID``). On Windows,
# ``wintypes.VOID`` exists and is a synonym for ``ctypes.c_void_p``.
# We bind a module-level ``VOID`` symbol that prefers the genuine
# ``wintypes.VOID`` when available and falls back to ``ctypes.c_void_p``
# otherwise — then use ``VOID`` (not ``wintypes.VOID``) in the Structure
# ``_fields_`` declaration below.
try:
    from ctypes.wintypes import VOID  # type: ignore[attr-defined]
except (ImportError, AttributeError):  # pragma: no cover - non-Windows
    VOID = ctypes.c_void_p


def _create_restrictive_security_attributes():
    """SEC-001: Create a SECURITY_ATTRIBUTES with a restrictive DACL.

    Builds a Win32 SECURITY_ATTRIBUTES structure whose DACL allows only
    the current user (SID) to access the named mutex. This prevents other
    user sessions from opening or manipulating our mutex object.

    Returns a ctypes SECURITY_ATTRIBUTES structure, or None on failure
    (in which case the caller passes NULL ``lpMutexAttributes`` to
    ``CreateMutexW`` — using the default per-user DACL from the process
    token, which is still per-user-restrictive but offers no additional
    cross-session hardening).
    """
    if not is_windows():
        return None
    try:
        from ctypes import wintypes

        advapi32 = ctypes.windll.advapi32
        kernel32 = ctypes.windll.kernel32

        # CR-001 / CR-002 (IMPROVE-mode run, 2026-07-21): proper ctypes
        # Structure definitions replace the manual byte-array + memmove
        # construction that had wrong offsets on x64.
        # XS-101 (IMPROVE-mode run XS): ``VOID`` is the module-level
        # fallback symbol (``wintypes.VOID`` on Windows,
        # ``ctypes.c_void_p`` on Linux). Using ``VOID`` here instead of
        # ``wintypes.VOID`` lets the Structure be defined on Linux when
        # the test suite invokes this function with mocked
        # ``ctypes.windll``.
        class SID_AND_ATTRIBUTES(ctypes.Structure):  # noqa: N801
            _fields_ = [
                ("Sid", ctypes.POINTER(VOID)),
                ("Attributes", wintypes.DWORD),
            ]

        class TOKEN_USER(ctypes.Structure):  # noqa: N801
            _fields_ = [("User", SID_AND_ATTRIBUTES)]

        # Win32 TRUSTEE_W on x64 (with _pack_ = 8):
        #   pMultipleTrustee        @ 0   (8 bytes — pointer)
        #   MultipleTrusteeOperation @ 8   (4 bytes — DWORD)
        #   TrusteeForm             @ 12  (4 bytes — DWORD)
        #   TrusteeType             @ 16  (4 bytes — DWORD)
        #   <4-byte align pad>      @ 20
        #   ptstrName               @ 24  (8 bytes — pointer)
        # Total: 32 bytes.
        class TRUSTEE_W(ctypes.Structure):  # noqa: N801
            _pack_ = 8
            _fields_ = [
                ("pMultipleTrustee", ctypes.POINTER(ctypes.c_void_p)),
                ("MultipleTrusteeOperation", wintypes.DWORD),
                ("TrusteeForm", wintypes.DWORD),
                ("TrusteeType", wintypes.DWORD),
                ("ptstrName", ctypes.POINTER(ctypes.c_void_p)),
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

            # CR-001: extract SID from TOKEN_USER via proper Structure.
            # ``TOKEN_USER.User.Sid`` is at offset 0 (was: ``addressof(buf)
            # + sizeof(LPVOID)`` which landed on the ``Attributes`` DWORD).
            tu = TOKEN_USER.from_buffer_copy(buf)
            p_sid = tu.User.Sid
            if not p_sid:
                return None

            # Build a SECURITY_DESCRIPTOR with a DACL containing only
            # one ACE: grant MUTEX_ALL_ACCESS to the current user SID.
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
            # CR-002: ptstrName is now at the correct offset (24 on x64)
            # via the TRUSTEE_W Structure's _pack_ = 8 layout.
            ea.Trustee.ptstrName = ctypes.cast(p_sid, ctypes.POINTER(ctypes.c_void_p))

            # Set the DACL
            new_acl = wintypes.LPVOID()
            if advapi32.SetEntriesInAclW(1, ctypes.byref(ea), None, ctypes.byref(new_acl)) != 0:
                # CR-003: NO NULL DACL fallback. Pre-fix, this branch
                # installed a NULL DACL via SetSecurityDescriptorDacl(sd,
                # True, None, False) — Win32 semantics: NULL DACL grants
                # EVERY token MUTEX_ALL_ACCESS (world-open). Combined with
                # CR-001/CR-002 (which made SetEntriesInAclW always fail),
                # the fallback was always taken, so every mutex had a NULL
                # DACL. Now we return None and let CreateMutexW use the
                # default per-user DACL from the process token (the safe
                # baseline — still per-user-restrictive).
                log.warning(
                    "[SECURITY] SetEntriesInAclW failed; falling back to "
                    "default per-user DACL (no NULL DACL — cross-user "
                    "protection preserved at the default level)"
                )
                return None
            if not advapi32.SetSecurityDescriptorDacl(sd, True, new_acl, False):
                # CR-134: free the ACL on the SetSecurityDescriptorDacl
                # failure path too (LocalAlloc-allocated).
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
            # CR-134 (IMPROVE-mode run, 2026-07-21): SetEntriesInAclW
            # allocates the ACL via LocalAlloc(LMEM_FIXED, ...). The
            # caller is responsible for freeing it with LocalFree. Since
            # ``sa`` is held for the process lifetime (the mutex is held
            # by the single-instance gate), we register a weakref.finalize
            # that calls LocalFree when ``sa`` is garbage-collected (which
            # happens at process exit). This is correct hygiene and
            # documents intent — without it, static analyzers (PVS-Studio,
            # Coverity) flag the LocalAlloc-without-Free pattern.
            sa._acl_finalizer = weakref.finalize(
                sa,
                lambda acl=new_acl, k32=kernel32: __local_free_safe(k32, acl),
            )
            return sa
        finally:
            kernel32.CloseHandle(token)
    except Exception as exc:
        # PVT-G5-044: previously this swallowed the exception silently.
        # The fallback (default per-user DACL) is documented as safe, but
        # the failure itself was invisible — a regression of the CR-001/CR-002
        # struct-offset kind (which made ``SetEntriesInAclW`` always fail)
        # would be undetectable. Log at WARNING so operators notice.
        log.warning(
            "[SECURITY] Restrictive DACL construction failed: %s — falling back to default per-user DACL",
            exc,
            exc_info=True,
        )
        return None


def __local_free_safe(kernel32, handle) -> None:  # noqa: N801
    """Best-effort LocalFree — never raises (called from weakref.finalize)."""
    try:
        kernel32.LocalFree(handle)
    except Exception:
        log.debug("[SECURITY] LocalFree failed during ACL cleanup", exc_info=True)
