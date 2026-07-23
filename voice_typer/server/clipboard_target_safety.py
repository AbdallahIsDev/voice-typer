"""Clipboard target-safety checks (ARCH-11 extraction).

Extracted from ``voice_typer.server.clipboard`` so the Win32 UI
Automation focus / password-field / elevated-target detection is
separated from the clipboard I/O helpers.

Exposes (via module-level aliases in ``clipboard.py``) the helpers used
by ``ClipboardManager._is_safe_paste_target``:
``_is_elevated_target``, ``_focused_window_is_credential_dialog``,
``_is_password_field``, ``_is_content_editable``, plus the cached UIA
singleton helpers (``_get_uia_singleton`` / ``_get_uia_focused_element``)
and the credential-dialog class set (``_CRED_DIALOG_CLASSES``).
"""

from __future__ import annotations

import contextlib
import threading
from typing import Any

# ARCH-11: ``is_windows`` is resolved dynamically through the clipboard
# module so the test suite's ``patch.object(clip_mod, "is_windows")``
# (which is what the Win32 coverage tests use to simulate Windows) is
# honoured. A static ``from platform_utils import is_windows`` would
# snapshot the real function and bypass the patch.
import voice_typer.server.clipboard as _clipboard


def is_windows() -> bool:
    return _clipboard.is_windows()


# ARCH-11: keep the SAME log object as clipboard.py. We resolve it
# dynamically via the clipboard module so a single test patch of
# ``voice_typer.server.clipboard.log`` (which is what the test suite
# patches) also covers these extracted helpers. Binding at import time
# (``log = clipboard.log``) would snapshot the original Logger and miss
# such patches.


def _log():
    return _clipboard.log


# ─── PLAT-013: Elevated target detection ──────────────────────────────


# CLIP-6: module-level cache for "are WE elevated?" — this value
# never changes during the lifetime of the process, so computing it
# on every paste is wasted work (OpenProcessToken + GetTokenInformation
# + CloseHandle = 3 kernel calls per paste). Cached on first access.
# ``None`` = not yet computed.
_WE_ELEVATED: bool | None = None

# XV-103: guards the ``_WE_ELEVATED`` init race. The clipboard paste
# path is called from the main IPC thread, but a prewarm / caps-lock
# polling thread can also call into ``_is_elevated_target`` (which calls
# ``_get_we_elevated``). Without this lock, two threads could both
# observe ``_WE_ELEVATED is None`` and both run the Win32 token query,
# stomping each other's write. The check-then-act is benign on
# correctness (the value is process-stable) but the redundant token
# query leaks handles if both threads enter the ``try`` block before
# either sets the cache.
_WE_ELEVATED_LOCK = threading.Lock()

# PVT-G5-045 (session-5): per-paste security checks fail open (return
# False). Logging every failure at WARNING would spam the log at paste
# rate; logging at DEBUG without deduplication would still emit one
# record per paste. Use module-level "first-occurrence" flags so the
# operator gets one WARNING-equivalent record per failure mode per
# session — enough to notice the regression without flooding the log.
_PASTE_SAFETY_WARNED: set[str] = set()


def _warn_paste_safety_once(key: str, fn_name: str, exc: BaseException) -> None:
    """Emit a one-shot DEBUG log for a per-paste safety-check failure.

    The first time ``key`` is seen in this process we also bump it to
    WARNING so the operator notices; subsequent failures of the same
    kind are logged at DEBUG (with traceback) for forensic value without
    spamming the log at paste rate.
    """
    if key not in _PASTE_SAFETY_WARNED:
        _PASTE_SAFETY_WARNED.add(key)
        _log().warning(
            "[CLIPBOARD] %s failed: %s — failing open (paste allowed); "
            "further occurrences of this failure will be logged at DEBUG",
            fn_name,
            exc,
            exc_info=True,
        )
    else:
        _log().debug(
            "[CLIPBOARD] %s failed: %s — failing open (paste allowed)",
            fn_name,
            exc,
            exc_info=True,
        )


def _get_we_elevated() -> bool:
    """CLIP-6: Return whether THIS process is running elevated.

    Cached at module level because the value cannot change during the
    process lifetime (you can't elevate an already-running process).
    The first call performs the Win32 token query; subsequent calls
    return the cached value.

    Returns False on non-Windows, on failure, or when we cannot open
    our own process token (fail-open — same as the previous behavior).

    XV-103: init is guarded by ``_WE_ELEVATED_LOCK`` using
    double-checked locking so concurrent first-callers (e.g. the main
    IPC paste thread plus the caps-lock polling thread) don't both
    run the Win32 token query and stomp the cache. The fast path
    (cache hit) is lock-free; only the cold path acquires the lock.
    """
    global _WE_ELEVATED
    # Fast path: cache already populated — no lock needed.
    if _WE_ELEVATED is not None:
        return _WE_ELEVATED
    # Cold path: acquire the lock and re-check (another thread may
    # have populated the cache while we were waiting).
    with _WE_ELEVATED_LOCK:
        if _WE_ELEVATED is not None:
            return _WE_ELEVATED
        if not is_windows():
            _WE_ELEVATED = False
            return _WE_ELEVATED
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32

            our_token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(our_token)):
                _WE_ELEVATED = False
                return _WE_ELEVATED
            try:
                # TokenElevation = 20
                ret_len = wintypes.DWORD()
                advapi32.GetTokenInformation(our_token, 20, None, 0, ctypes.byref(ret_len))
                our_buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(
                    our_token, 20, our_buf, ctypes.sizeof(our_buf), ctypes.byref(ret_len)
                ):
                    _WE_ELEVATED = False
                    return _WE_ELEVATED
                _WE_ELEVATED = bool(ctypes.cast(our_buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(our_token)
        except Exception as exc:
            # PVT-G5-045 (session-5): cached and called once per process, so
            # a plain WARNING (no dedup) is appropriate — operators get
            # exactly one record per session if the Win32 token query path
            # is broken.
            _log().warning(
                "[CLIPBOARD] _get_we_elevated failed: %s — failing open (paste allowed)",
                exc,
                exc_info=True,
            )
            _WE_ELEVATED = False
        return _WE_ELEVATED


def _is_elevated_target(hwnd: int | None = None) -> bool:
    """PLAT-013: Check if the foreground window belongs to an elevated process.

    Uses GetWindowThreadProcessId + OpenProcess + GetTokenInformation to
    determine if the target process is running elevated.  If we are not
    elevated but the target is, UIPI will block our SendInput calls.

    Returns True if the foreground window is elevated and we are not.
    Returns False if we can't determine (fail open) or if elevation
    matches.

    CLIP-6 (Perf): the "are we elevated?" check is now cached at
    module-level via :func:`_get_we_elevated`. The value cannot change
    during the process lifetime, so we compute it once on first paste
    and reuse on every subsequent paste. The target-side check still
    runs per-paste because the foreground window can change between
    pastes.

    CLIP-12 (Perf): ``hwnd`` parameter accepts a pre-fetched
    foreground window handle so callers
    (:meth:`_is_safe_paste_target`) that already fetched
    ``GetForegroundWindow`` can pass it in to avoid a redundant
    Win32 round-trip. When ``hwnd`` is ``None`` (the default), the
    function fetches it itself — preserving backward compatibility
    with direct callers and tests.
    """
    if not is_windows():
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        advapi32 = ctypes.windll.advapi32

        if hwnd is None:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False

        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False

        process_query_limited_information = 0x1000
        h_process = kernel32.OpenProcess(process_query_limited_information, False, pid.value)
        if not h_process:
            return False

        try:
            # Check if the target process is elevated
            token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(h_process, 0x0008, ctypes.byref(token)):
                return False
            try:
                # TokenElevation = 20
                ret_len = wintypes.DWORD()
                advapi32.GetTokenInformation(token, 20, None, 0, ctypes.byref(ret_len))
                buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(token, 20, buf, ctypes.sizeof(buf), ctypes.byref(ret_len)):
                    return False
                # TOKEN_ELEVATION struct: DWORD TokenIsElevated
                target_elevated = bool(ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(token)

            # CLIP-6: check if WE are elevated (cached, computed once).
            we_elevated = _get_we_elevated()

            # If target is elevated and we're not, warn
            if target_elevated and not we_elevated:
                _log().warning(
                    "[CLIPBOARD] Target window (pid=%d) is elevated but we are not — paste may fail due to UIPI",
                    pid.value,
                )
                return True
            return False
        finally:
            kernel32.CloseHandle(h_process)
    except Exception as exc:
        # EC-15: fail-closed — if the elevation check itself raises,
        # block paste rather than risk pasting into an elevated target
        # we couldn't verify.
        _log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


# ─── PLAT-014: Password field detection ───────────────────────────────


# PLAT-014: Known credential dialog window classes on Windows.
# When comtypes is unavailable, we fall back to checking the focused
# window's class name against this set. This is a COARSE heuristic —
# it only catches the standard Windows credential UI, not arbitrary
# password fields in third-party apps. comtypes/UIA is required for
# full coverage (see _is_password_field above).
_CRED_DIALOG_CLASSES: set[str] = {
    "CredentialDialog",  # Generic credential dialog
    "CredDialogCallerWnd",  # CredUI dialog
    "NN Credentials Dialog",  # Network credentials
    "PassportWindow",  # Microsoft account
}


def _focused_window_is_credential_dialog(hwnd: int | None = None) -> bool:
    """PLAT-014: Check if the focused window is a known credential dialog.

    Uses GetForegroundWindow + GetClassNameW via ctypes. Returns True
    if the window class matches a known credential dialog class. This
    is the comtypes-absence fallback for password field protection.

    CLIP-12: ``hwnd`` parameter accepts a pre-fetched foreground
    window handle so callers (like :meth:`_is_safe_paste_target`)
    that already fetched ``GetForegroundWindow`` can pass it in to
    avoid a redundant Win32 round-trip. When ``hwnd`` is ``None``
    (the default), the function fetches it itself — preserving
    backward compatibility with direct callers and tests.
    """
    if not is_windows():
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        if hwnd is None:
            hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        # GetClassNameW returns the class name length
        class_name = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, class_name, 256)
        if length <= 0:
            return False
        cls = class_name.value
        return cls in _CRED_DIALOG_CLASSES
    except Exception as exc:
        # EC-15: fail-closed — if the credential-dialog check raises,
        # block paste rather than risk pasting into an undetected
        # credential prompt.
        _log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _is_password_field(focused: Any = None, hwnd: int | None = None) -> bool:
    """PLAT-014: Check if the focused element is a password field.

    On Windows, uses UI Automation to check IsPasswordPropertyId.
    If the focused element has IsPassword=True, skip paste and warn
    the user that dictation into password fields is disabled for security.

    Returns True if a password field is detected, False otherwise.

    PERF-FIX-001: previously this function created a fresh IUIAutomation
    COM instance on every call (CoCreateInstance + GetModule), which is
    a 10-50ms cross-process RPC.  Combined with ``_is_content_editable``
    doing the same, that was 5+ UIA RPCs per paste (100-800ms total in
    browsers/Electron/Office).  Now uses the module-level cached
    ``_get_uia_focused_element()`` helper which:
      - Creates the IUIAutomation instance ONCE (cached in
        ``_UIA_SINGLETON``).
      - Fetches the focused element ONCE per paste and returns it so
        both password and content-editable checks can read multiple
        properties from the same element without re-fetching.

    PLAT-014 (revised): The previous code had a no-op ctypes fallback
    block (lines 310-316) that just did ``pass`` if comtypes wasn't
    installed — meaning password detection silently failed open. Now
    we explicitly log when comtypes is unavailable and the check is
    skipped, so operators can install comtypes to enable the check.

    CLIP-2 (High, Security): when comtypes IS installed but the UIA
    call raises (e.g. desktop-bridge app, UAC dialog, broken COM
    registration), the previous code failed OPEN — paste was allowed
    into a potentially password-bearing field with no detection. Now
    we ALSO call :func:`_focused_window_is_credential_dialog` as a
    fallback heuristic; if the focused window class matches a known
    credential dialog, we fail CLOSED (return True) so paste is
    blocked. This closes the security gap when UIA is partially
    broken.

    CLIP-4/5 (Perf): ``focused`` parameter accepts a pre-fetched UIA
    element so callers (:meth:`_is_safe_paste_target`) can fetch it
    once and pass to both ``_is_password_field`` and
    ``_is_content_editable``. When ``focused`` is provided, the
    per-call ``CoInitialize``/``CoUninitialize`` is skipped (caller
    is expected to manage the COM apartment).

    CLIP-12 (Perf): ``hwnd`` parameter accepts a pre-fetched
    foreground window handle to avoid redundant
    ``GetForegroundWindow`` calls when invoking the credential-dialog
    heuristic fallback.
    """
    if not is_windows():
        return False
    try:
        # Try using comtypes for UI Automation (preferred path)
        try:
            import comtypes
            import comtypes.client

            # CLIP-5: when called via _is_safe_paste_target with a
            # pre-fetched ``focused`` element, the caller has already
            # CoInitialize'd the thread. Skip the per-call init/teardown
            # to avoid COM ref-count churn on every paste.
            owns_com = focused is None
            if owns_com:
                comtypes.CoInitialize()
            try:
                if focused is None:
                    focused = _get_uia_focused_element()
                if focused is not None:
                    # UIA_IsPasswordPropertyId = 30022
                    is_password = focused.GetCurrentPropertyValue(30022)
                    if is_password:
                        _log().warning(
                            "[CLIPBOARD] Password field detected — "
                            "dictation into password fields is disabled for security"
                        )
                        return True
            finally:
                if owns_com:
                    with contextlib.suppress(Exception):
                        comtypes.CoUninitialize()
        except ImportError:
            # PLAT-014: comtypes not installed. Pre-fix this failed
            # OPEN (returned False → paste allowed into any field).
            # Now we log a WARNING (not INFO) so operators notice at
            # default log levels, and we fail CLOSED for known
            # credential-dialog window classes. The fail-closed path
            # only blocks when the focused window class matches a
            # known credential dialog (see _CRED_DIALOG_CLASSES below);
            # for all other windows we still fail open to avoid
            # blocking legitimate dictation, but with a louder log.
            _log().warning(
                "[CLIPBOARD] comtypes not installed — password field detection "
                "disabled. Install 'comtypes' (pip install comtypes) to enable "
                "password field protection. Falling back to window-class heuristic."
            )
            # PLAT-014: window-class heuristic for known credential
            # dialogs. This is a coarse fallback — it only catches the
            # standard Windows credential UI, not arbitrary password
            # fields in third-party apps. comtypes/UIA is required for
            # full coverage.
            try:
                if _focused_window_is_credential_dialog(hwnd):
                    _log().warning(
                        "[CLIPBOARD] Credential dialog window detected (comtypes "
                        "fallback) — dictation blocked for security"
                    )
                    return True
            except Exception as exc:
                # EC-15: wire the one-shot paste-safety warning so the
                # swallowed exception is visible (was silent ``pass``).
                # The function falls through to ``return False`` below
                # (fail-open) because the cred-dialog heuristic is the
                # last resort — we cannot positively identify a password
                # field, so we allow paste rather than block all
                # dictation when comtypes is merely absent.
                _warn_paste_safety_once(
                    "uia_password_cred_dialog_importerror_fallback",
                    "_is_password_field",
                    exc,
                )
        except Exception as exc:
            # CLIP-2 (High, Security): comtypes is installed but the
            # UIA call raised (e.g. desktop-bridge app, UAC dialog,
            # broken COM registration). Previously this failed OPEN
            # — paste was allowed into a potentially password-bearing
            # field with no detection. Now we ALSO call the
            # credential-dialog window-class heuristic as a fallback;
            # if the focused window class matches a known credential
            # dialog, fail CLOSED (return True) so paste is blocked.
            _log().warning(
                "[CLIPBOARD] UIA password field check failed: %s — "
                "falling back to credential-dialog heuristic (CLIP-2)",
                exc,
            )
            try:
                if _focused_window_is_credential_dialog(hwnd):
                    _log().warning(
                        "[CLIPBOARD] Credential dialog window detected (UIA "
                        "failed) — dictation blocked for security (CLIP-2)"
                    )
                    return True
            except Exception as exc:
                # EC-15: wire the one-shot paste-safety warning so the
                # swallowed exception is visible (was silent ``pass``).
                # Falls through to ``return False`` (fail-open) for the
                # same reason as the ImportError branch above.
                _warn_paste_safety_once(
                    "uia_password_cred_dialog_uia_error_fallback",
                    "_is_password_field",
                    exc,
                )

        # No raw ctypes fallback: implementing IsPassword via raw ctypes
        # requires defining the full IUIAutomation COM interface vtable
        # by hand (80+ methods), which is fragile and error-prone.
        # comtypes is the supported way to call UIA from Python.
        return False
    except Exception as exc:
        # EC-15: fail-closed — the outer try covers the whole password-
        # field detection path. If something unexpected broke it (not
        # the inner comtypes/UIA errors already handled above), block
        # paste rather than risk pasting into a credential prompt.
        _log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


# PERF-FIX-001: module-level cached IUIAutomation instance.
# Creating a fresh IUIAutomation COM instance on every paste was costing
# 10-50ms per call (cross-process RPC).  Caching it here eliminates that
# cost for every subsequent paste.  The instance is created lazily on
# first use and reused for the lifetime of the process.
_UIA_SINGLETON = None
_UIA_MODULE = None
_UIA_SINGLETON_INIT_ATTEMPTED = False

# XV-103: guards the ``_UIA_SINGLETON`` init race. The ``_UIA_SINGLETON_INIT_ATTEMPTED``
# flag is itself a check-then-act race: two threads can both observe
# ``False``, both run ``comtypes.client.GetModule`` + ``CoCreateInstance``,
# and both write to the module-level cache. CoCreateInstance returns a
# fresh COM proxy each time, so the loser overwrites the winner's proxy —
# the abandoned proxy leaks until GC, and on failure paths the
# ``_UIA_SINGLETON_INIT_ATTEMPTED`` flag is set by whichever thread runs
# last, masking the other thread's in-flight init. Double-checked locking
# fixes this: the fast path (init already attempted) is lock-free; only
# the cold path acquires the lock and re-checks the flag.
_UIA_SINGLETON_LOCK = threading.Lock()


def _get_uia_singleton():
    """Return the cached IUIAutomation instance, or None if unavailable.

    PERF-FIX-001: caches both the comtypes module reference (from
    GetModule("UIAutomationCore.dll")) and the IUIAutomation COM
    instance so we don't pay the CoCreateInstance cost on every paste.

    XV-103: init is guarded by ``_UIA_SINGLETON_LOCK`` using
    double-checked locking so concurrent first-callers don't both
    run ``comtypes.client.GetModule`` + ``CoCreateInstance`` and
    overwrite each other's cached proxy. The fast path (init already
    attempted) is lock-free; only the cold path acquires the lock.

    XV-103 (subtlety): the ``_UIA_SINGLETON_INIT_ATTEMPTED`` flag is
    set in a ``finally`` block AFTER ``_UIA_SINGLETON`` is assigned.
    Setting it earlier would let racing fast-path callers (which check
    the flag WITHOUT the lock) observe the flag set while
    ``_UIA_SINGLETON`` is still ``None`` — they'd return ``None`` and
    permanently disable UIA checks for their code path even though
    init eventually succeeded.
    """
    global _UIA_SINGLETON, _UIA_MODULE, _UIA_SINGLETON_INIT_ATTEMPTED
    # Fast path: init already completed — no lock needed.
    if _UIA_SINGLETON_INIT_ATTEMPTED:
        return _UIA_SINGLETON
    # Cold path: acquire the lock and re-check (another thread may
    # have completed the init while we were waiting).
    with _UIA_SINGLETON_LOCK:
        if _UIA_SINGLETON_INIT_ATTEMPTED:
            return _UIA_SINGLETON
        try:
            if not is_windows():
                return None
            try:
                import comtypes.client

                _UIA_MODULE = comtypes.client.GetModule("UIAutomationCore.dll")
                _UIA_SINGLETON = comtypes.CoCreateInstance(
                    _UIA_MODULE.CUIAutomation._reg_clsid_,
                    interface=_UIA_MODULE.IUIAutomation,
                )
            except Exception as exc:
                _log().debug(
                    "[CLIPBOARD] IUIAutomation singleton init failed: %s — UIA checks disabled",
                    exc,
                )
                _UIA_SINGLETON = None
            return _UIA_SINGLETON
        finally:
            # Set the flag LAST so racing fast-path readers never see
            # the flag set with _UIA_SINGLETON still None.
            _UIA_SINGLETON_INIT_ATTEMPTED = True


def _get_uia_focused_element():
    """Return the focused UI element via the cached IUIAutomation singleton.

    PERF-FIX-001: reuses the module-level ``_UIA_SINGLETON`` so we don't
    pay CoCreateInstance + GetModule on every call.  Returns None if
    UIA is unavailable or no element is focused.
    """
    uia = _get_uia_singleton()
    if uia is None:
        return None
    try:
        return uia.GetFocusedElement()
    except Exception as exc:
        _log().debug(
            "[CLIPBOARD] GetFocusedElement failed: %s — failing open",
            exc,
        )
        return None


def _is_content_editable(focused: Any = None) -> bool:
    """PLAT-CONTENT: Check if the focused element is a contentEditable element.

    On Windows, uses UI Automation (same comtypes infrastructure as
    ``_is_password_field``) to check if the focused element is an Edit
    or Document control that supports rich text input. This allows the
    clipboard module to log when the paste target is a rich editor
    (Word, LibreOffice, Gmail compose) and potentially paste HTML in
    a future version.

    Returns True if the focused element is contentEditable, False otherwise.
    Returns False on non-Windows or when comtypes is unavailable.

    CLIP-4/5 (Perf): ``focused`` parameter accepts a pre-fetched UIA
    element so callers (:meth:`_is_safe_paste_target`) can fetch it
    once and pass to both ``_is_password_field`` and
    ``_is_content_editable``. When ``focused`` is provided, the
    per-call ``CoInitialize``/``CoUninitialize`` is skipped (caller
    is expected to manage the COM apartment).
    """
    if not is_windows():
        return False
    try:
        import comtypes

        # CLIP-5: skip per-call COM init when caller pre-fetched.
        owns_com = focused is None
        if owns_com:
            comtypes.CoInitialize()
        try:
            if focused is None:
                focused = _get_uia_focused_element()
            if focused is None:
                return False
            # UIA_ControlTypePropertyId = 30003
            control_type = focused.GetCurrentPropertyValue(30003)
            # UIA_ControlType_Edit = 50004, UIA_ControlType_Document = 50036
            # These are the control types that typically support contentEditable.
            if control_type in (50004, 50036):
                # Check if the element supports the Value pattern (text input)
                # UIA_IsValuePatternAvailablePropertyId = 30101
                has_value = focused.GetCurrentPropertyValue(30101)
                if has_value:
                    return True
            return False
        finally:
            if owns_com:
                with contextlib.suppress(Exception):
                    comtypes.CoUninitialize()
    except ImportError:
        return False
    except Exception as exc:
        # EC-15: ``_is_content_editable`` is informational only (the
        # caller logs but does NOT block paste on True — see
        # ``_is_safe_paste_target`` docstring, CLIP-3). Fail-OPEN here
        # is correct: returning True would falsely report a rich-editor
        # target without improving security. We still log the failure
        # so a broken UIA install is observable.
        _log().warning(
            "paste-safety check failed; keeping fail-open (contentEditable is informational, not a security gate): %s",
            exc,
        )
        return False


# ─── G4-H-05: macOS/Linux password field detection ────────────────────
#
# PLAT-014 was Windows-only — on macOS/Linux, ``_is_safe_paste_target``
# returned ``True`` unconditionally, allowing dictated text to be pasted
# into password fields, SSH passphrase prompts, credit-card forms, etc.
#
# G4-H-05 closes that gap by querying the platform-native accessibility
# APIs:
#
#   * macOS: Accessibility API via pyobjc (AppKit + ApplicationServices).
#     A focused UI element with ``AXIsSecure=True`` or role
#     ``AXSecureTextField`` is treated as a password field.
#
#   * Linux: AT-SPI2 via pyatspi. A focused accessible with role
#     ``ATSPI_ROLE_PASSWORD_TEXT`` is treated as a password field.
#
# Both helpers use LAZY imports (the codebase pattern) so a missing
# pyobjc/pyatspi on a non-target platform does not break startup. If the
# platform library is unavailable, the helper logs a WARNING (once, to
# avoid log spam) and returns False — the caller then falls back to the
# legacy fail-open behavior of allowing paste. The docstrings document
# this residual risk.
#
# Residual risk: SIGKILL or a hard crash mid-paste still leaks dictated
# text into the focused field — this fix only gates the paste keystroke
# itself, not the clipboard content. The clipboard snapshot/restore
# lifecycle (ADR-0010 §5) limits the clipboard-side exposure to the
# configured restore delay (default 150 ms).


# Once-only warning guards so missing platform libs don't spam the log
# on every paste. The first paste logs a WARNING; subsequent pastes log
# at DEBUG. Reset to False (via test fixtures) by re-importing the
# module.
_PYOBJC_UNAVAILABLE_WARNED: bool = False
_PYATSPI_UNAVAILABLE_WARNED: bool = False


def reset_platform_unavailable_warnings() -> None:
    """Reset the once-only warning guards (test helper).

    Production code never calls this — the guards are intentionally
    sticky so a noisy startup doesn't flood the log. Tests that want
    to assert the WARNING is emitted on the first call can call this
    between cases.
    """
    global _PYOBJC_UNAVAILABLE_WARNED, _PYATSPI_UNAVAILABLE_WARNED
    _PYOBJC_UNAVAILABLE_WARNED = False
    _PYATSPI_UNAVAILABLE_WARNED = False


def _is_password_field_macos() -> bool:
    """G4-H-05: Detect macOS password fields via the Accessibility API.

    Uses ``pyobjc`` (``AppKit.NSWorkspace`` + ``ApplicationServices``)
    to query the focused UI element of the frontmost application:

      1. ``NSWorkspace.sharedWorkspace().frontmostApplication()`` →
         the frontmost ``NSRunningApplication``.
      2. ``AXUIElementCreateApplication(pid)`` → the AXUIElement for
         that app.
      3. ``AXUIElementCopyAttributeValue(app, "AXFocusedUIElement")``
         → the focused UI element.
      4. Check the element's ``AXRole`` (``AXSecureTextField`` ⇒
         password field) and the ``AXIsSecure`` attribute (``True`` ⇒
         password field, covers custom controls).

    Returns ``True`` if a password field is detected (paste should be
    blocked), ``False`` otherwise.

    Lazy import: if ``pyobjc`` is not installed (Linux/Windows hosts
    or a headless macOS without the AppKit bridge), logs a WARNING
    (once) and returns ``False`` — the caller falls back to the
    legacy fail-open behavior of allowing paste. Residual risk:
    dictated text can still be pasted into macOS password fields
    until ``pyobjc`` is installed.

    Exceptions from the AX API (broken accessibility permission, app
    doesn't expose AX tree, etc.) are caught and logged at DEBUG —
    fail-open to avoid blocking legitimate dictation when the AX
    infrastructure is degraded.
    """
    global _PYOBJC_UNAVAILABLE_WARNED
    try:
        import AppKit  # noqa: F401
        import ApplicationServices  # noqa: F401
    except ImportError:
        if not _PYOBJC_UNAVAILABLE_WARNED:
            _log().warning(
                "[CLIPBOARD] pyobjc (ApplicationServices/AppKit) not installed — "
                "macOS password field detection disabled. Install pyobjc "
                "(pip install pyobjc-framework-ApplicationServices "
                "pyobjc-framework-Cocoa) to enable password field protection. "
                "Falling back to fail-open (paste allowed)."
            )
            _PYOBJC_UNAVAILABLE_WARNED = True
        else:
            _log().debug("[CLIPBOARD] pyobjc not installed — macOS password field check skipped (already warned)")
        return False

    try:
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        if workspace is None:
            return False
        front_app = workspace.frontmostApplication()
        if front_app is None:
            return False
        try:
            pid = front_app.processIdentifier()
        except Exception as exc:
            # EC-15: fail-closed — if we cannot read the frontmost app's
            # PID, we cannot query its AX tree, so we cannot verify the
            # target is safe. Block paste.
            _log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        if pid is None or pid <= 0:
            return False

        app_elem = ApplicationServices.AXUIElementCreateApplication(pid)
        if app_elem is None:
            return False

        # Get the focused UI element within the app.
        # AXUIElementCopyAttributeValue signature:
        #   (element, attribute, value out) -> OSStatus
        # pyobjc returns (OSStatus, value) tuple for the out-param.
        try:
            focused_result = ApplicationServices.AXUIElementCopyAttributeValue(app_elem, "AXFocusedUIElement", None)
        except Exception as exc:
            # EC-15: fail-closed — if we cannot fetch the focused UI
            # element, we cannot check whether it is a password field.
            # Block paste.
            _log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        if not focused_result or not isinstance(focused_result, tuple):
            return False
        if len(focused_result) < 2 or focused_result[0] != 0:
            return False
        focused = focused_result[1]
        if focused is None:
            return False

        # Check role: "AXSecureTextField" is the canonical macOS
        # password text field role.
        try:
            role_result = ApplicationServices.AXUIElementCopyAttributeValue(focused, "AXRole", None)
        except Exception:
            role_result = None
        if (
            role_result
            and isinstance(role_result, tuple)
            and len(role_result) >= 2
            and role_result[0] == 0
            and role_result[1] == "AXSecureTextField"
        ):
            _log().warning(
                "[CLIPBOARD] macOS password field detected (AXSecureTextField) — "
                "dictation into password fields is disabled for security"
            )
            return True

        # Also check the AXIsSecure attribute (covers custom controls
        # that hide password input behind a non-standard role).
        try:
            secure_result = ApplicationServices.AXUIElementCopyAttributeValue(focused, "AXIsSecure", None)
        except Exception:
            secure_result = None
        if (
            secure_result
            and isinstance(secure_result, tuple)
            and len(secure_result) >= 2
            and secure_result[0] == 0
            and bool(secure_result[1])
        ):
            _log().warning(
                "[CLIPBOARD] macOS password field detected (AXIsSecure=True) — "
                "dictation into password fields is disabled for security"
            )
            return True

        return False
    except Exception as exc:
        _log().debug(
            "[CLIPBOARD] macOS AX password check failed: %s — failing open",
            exc,
        )
        return False


def _find_focused_atspi_accessible(desktop: Any, max_depth: int = 10) -> Any:
    """Walk the AT-SPI tree to find the focused accessible.

    The AT-SPI spec says the ``ATSPI_STATE_FOCUSED`` state is set on
    the actual UI element receiving keyboard input — NOT on its
    ancestors (which may have ``ATSPI_STATE_ACTIVE`` / ``SHOWING`` but
    not ``FOCUSED``). This helper does a depth-first traversal of the
    tree, returning the first accessible found with ``FOCUSED`` set.

    A depth limit prevents infinite loops on malformed accessibility
    trees (e.g. an app that reports itself as its own parent) and
    bounds the worst-case traversal time on huge trees (some apps
    expose thousands of accessibles).

    Returns ``None`` when no focused accessible is found within the
    depth limit, or when the AT-SPI tree is malformed.
    """
    if desktop is None or max_depth <= 0:
        return None

    # First check the root itself (defensive — desktop itself is never
    # FOCUSED in practice, but if a future caller passes a sub-tree
    # root, this catches the case where the root IS the focused leaf).
    try:
        root_state = desktop.getState()
    except Exception:
        root_state = None
    if root_state is not None:
        try:
            if root_state.contains(_PYATSPI_STATE_FOCUSED):
                return desktop
        except Exception as exc:
            # EC-15: wire the one-shot paste-safety warning so the
            # swallowed exception is visible (was silent ``pass``).
            # The traversal continues below; if it also fails to find
            # a focused accessible, the caller fails open.
            _warn_paste_safety_once(
                "atspi_focused_state_check",
                "_find_focused_atspi_accessible",
                exc,
            )

    # Depth-first traversal: at each level, recurse into the first
    # child whose subtree contains a focused accessible. We cap the
    # total recursion depth at ``max_depth`` to bound worst-case time.
    try:
        child_count = desktop.childCount
    except Exception as exc:
        # EC-15: this helper returns an element (``Any``) or ``None`` —
        # NOT a bool. Changing the return type to ``True`` would break
        # the caller (``focused.getRole()`` on a bool raises). We log
        # via the one-shot paste-safety warning for dedup and keep the
        # ``None`` return; the caller's own fail-closed paths
        # (``_is_password_field_linux``) cover the security posture.
        _warn_paste_safety_once(
            "atspi_focused_childcount_lookup",
            "_find_focused_atspi_accessible",
            exc,
        )
        return None
    for i in range(child_count):
        try:
            child = desktop.getChildAtIndex(i)
        except Exception:
            continue
        if child is None:
            continue
        result = _find_focused_atspi_accessible(child, max_depth - 1)
        if result is not None:
            return result
    return None


# Cached reference to ``pyatspi.STATE_FOCUSED`` so we don't re-resolve
# the attribute on every tree-walk iteration. Populated on first call
# to ``_is_password_field_linux``. ``None`` means "not yet resolved"
# (which is also the value when pyatspi is unavailable).
_PYATSPI_STATE_FOCUSED: Any = None


def _is_password_field_linux() -> bool:
    """G4-H-05: Detect Linux password fields via AT-SPI2.

    Uses ``pyatspi`` to query the focused accessible. The traversal:

      1. ``pyatspi.Registry.getDesktop(0)`` → the AT-SPI desktop.
      2. Walk down through children that have
         ``ATSPI_STATE_FOCUSED`` set, descending until no child has
         the focused state. The leaf at that point is the focused
         UI element.
      3. If the leaf's role is ``ATSPI_ROLE_PASSWORD_TEXT``, treat
         it as a password field.

    Returns ``True`` if a password field is detected (paste should be
    blocked), ``False`` otherwise.

    Lazy import: if ``pyatspi`` is not installed (Linux hosts without
    the AT-SPI2 Python bindings, or non-Linux platforms), logs a
    WARNING (once) and returns ``False`` — the caller falls back to
    the legacy fail-open behavior of allowing paste. Residual risk:
    dictated text can still be pasted into Linux password fields
    until ``pyatspi`` is installed (``pip install pyatspi`` or
    ``apt install python3-pyatspi``).

    Exceptions from AT-SPI2 (no desktop bus, broken registry, app
    that doesn't expose an accessible tree) are caught and logged at
    DEBUG — fail-open to avoid blocking legitimate dictation when
    the AT-SPI2 infrastructure is degraded (e.g. raw framebuffer
    apps, headless sessions).
    """
    global _PYATSPI_UNAVAILABLE_WARNED, _PYATSPI_STATE_FOCUSED
    try:
        import pyatspi
    except ImportError:
        if not _PYATSPI_UNAVAILABLE_WARNED:
            _log().warning(
                "[CLIPBOARD] pyatspi not installed — Linux password field "
                "detection disabled. Install pyatspi (pip install pyatspi) "
                "or your distro's equivalent (apt install python3-pyatspi) "
                "to enable password field protection. Falling back to "
                "fail-open (paste allowed)."
            )
            _PYATSPI_UNAVAILABLE_WARNED = True
        else:
            _log().debug("[CLIPBOARD] pyatspi not installed — Linux password field check skipped (already warned)")
        return False

    # Resolve and cache the STATE_FOCUSED constant once.  The cached
    # module-level global is read directly by ``_find_focused_atspi_accessible``
    # during the tree walk below — no local binding is needed here.
    if _PYATSPI_STATE_FOCUSED is None:
        try:
            _PYATSPI_STATE_FOCUSED = pyatspi.STATE_FOCUSED
        except AttributeError:
            # Defensive: older pyatspi versions may expose it differently.
            try:
                _PYATSPI_STATE_FOCUSED = pyatspi.STATE_FOCUSED
            except Exception:
                _PYATSPI_STATE_FOCUSED = 1 << 10  # fallback value

    try:
        try:
            desktop = pyatspi.Registry.getDesktop(0)
        except Exception as exc:
            # EC-15: fail-closed — if the AT-SPI2 desktop is unavailable,
            # we cannot traverse the accessibility tree to find the
            # focused element. Block paste.
            _log().warning("paste-safety check failed; failing closed: %s", exc)
            return True
        if desktop is None:
            return False

        focused = _find_focused_atspi_accessible(desktop, max_depth=10)
        if focused is None:
            return False

        try:
            role = focused.getRole()
        except Exception as exc:
            # EC-15: fail-closed — if we cannot read the focused
            # accessible's role, we cannot determine whether it is a
            # password field. Block paste.
            _log().warning("paste-safety check failed; failing closed: %s", exc)
            return True

        try:
            password_role = pyatspi.ROLE_PASSWORD_TEXT
        except AttributeError:
            password_role = None
        if password_role is not None and role == password_role:
            _log().warning(
                "[CLIPBOARD] Linux password field detected "
                "(ATSPI_ROLE_PASSWORD_TEXT) — dictation into password "
                "fields is disabled for security"
            )
            return True

        return False
    except Exception as exc:
        _log().debug(
            "[CLIPBOARD] Linux AT-SPI2 password check failed: %s — failing open",
            exc,
        )
        return False
