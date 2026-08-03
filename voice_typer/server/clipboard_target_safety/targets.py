"""Target window detection for clipboard paste safety.

Contains the helpers that DETECT the foreground window's identity /
properties:

* :func:`_get_we_elevated` — cached "are WE elevated?" check (process-
  stable; computed once via Win32 token query, cached in
  ``_pkg._WE_ELEVATED``).
* :func:`_is_elevated_target` — checks if the foreground window belongs
  to an elevated process (uses :func:`_pkg._get_we_elevated` for the
  "we" side and a fresh Win32 token query for the "target" side).
* :func:`_focused_window_is_credential_dialog` — checks the foreground
  window's class name against ``_pkg._CRED_DIALOG_CLASSES`` (coarse
  comtypes-absence fallback for password-field protection).
* :func:`_is_content_editable` — uses UIA (via
  :func:`_pkg._get_uia_focused_element` from :mod:`.injection`) to check
  if the focused element supports rich-text input.
* :func:`_find_focused_atspi_accessible` — walks the Linux AT-SPI tree
  to find the focused accessible (used by ``_is_password_field_linux``
  in :mod:`.validation`).

All cross-module references (``is_windows``, ``_log``, mutable globals,
UIA helpers in :mod:`.injection`, paste-safety warning helper in
:mod:`.validation`) go through ``_pkg.NAME`` (where ``_pkg`` is this
package) so test patches / resets on
``voice_typer.server.clipboard_target_safety.NAME`` propagate to the
functions defined here. A plain ``global NAME`` would write to THIS
submodule's namespace and be invisible to the test patches applied on
the package — hence the ``_pkg.NAME`` access pattern.
"""

from __future__ import annotations

from typing import Any  # noqa: F401  (used in type hints)

# ``_pkg`` is bound at module load time to the partial package object
# (``__init__.py`` is still executing when this submodule is loaded).
# Attribute lookups on ``_pkg`` happen at CALL TIME, by which point
# ``__init__.py`` has finished and all names (mutable globals + the
# re-exported functions from .injection and .validation) are defined.
# This mirrors the ``_cb`` pattern used in
# ``voice_typer/server/clipboard/manager.py``.
import voice_typer.server.clipboard_target_safety as _pkg


def _get_we_elevated() -> bool:
    """Return whether THIS process is running elevated.

    Cached at module level (in ``_pkg._WE_ELEVATED``) because the value
    cannot change during the process lifetime (you can't elevate an
    already-running process). The first call performs the Win32 token
    query; subsequent calls return the cached value.

    Returns False on non-Windows, on failure, or when we cannot open
    our own process token (fail-open — same as the previous behavior).

    Init is guarded by ``_pkg._WE_ELEVATED_LOCK`` using double-checked
    locking so concurrent first-callers (e.g. the main IPC paste thread
    plus the caps-lock polling thread) don't both run the Win32 token
    query and stomp the cache. The fast path (cache hit) is lock-free;
    only the cold path acquires the lock.
    """
    # Fast path: cache already populated — no lock needed.
    if _pkg._WE_ELEVATED is not None:
        return _pkg._WE_ELEVATED
    # Cold path: acquire the lock and re-check (another thread may
    # have populated the cache while we were waiting).
    with _pkg._WE_ELEVATED_LOCK:
        if _pkg._WE_ELEVATED is not None:
            return _pkg._WE_ELEVATED
        if not _pkg.is_windows():
            _pkg._WE_ELEVATED = False
            return _pkg._WE_ELEVATED
        try:
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            advapi32 = ctypes.windll.advapi32

            our_token = wintypes.HANDLE()
            if not advapi32.OpenProcessToken(kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(our_token)):
                _pkg._WE_ELEVATED = False
                return _pkg._WE_ELEVATED
            try:
                # TokenElevation = 20
                ret_len = wintypes.DWORD()
                advapi32.GetTokenInformation(our_token, 20, None, 0, ctypes.byref(ret_len))
                our_buf = ctypes.create_string_buffer(ret_len.value or 4)
                if not advapi32.GetTokenInformation(
                    our_token, 20, our_buf, ctypes.sizeof(our_buf), ctypes.byref(ret_len)
                ):
                    _pkg._WE_ELEVATED = False
                    return _pkg._WE_ELEVATED
                _pkg._WE_ELEVATED = bool(ctypes.cast(our_buf, ctypes.POINTER(wintypes.DWORD))[0])
            finally:
                kernel32.CloseHandle(our_token)
        except Exception as exc:
            # Cached and called once per process, so a plain WARNING
            # (no dedup) is appropriate — operators get exactly one
            # record per session if the Win32 token query path is
            # broken.
            _pkg._log().warning(
                "[CLIPBOARD] _get_we_elevated failed: %s — failing open (paste allowed)",
                exc,
                exc_info=True,
            )
            _pkg._WE_ELEVATED = False
        return _pkg._WE_ELEVATED


def _is_elevated_target(hwnd: int | None = None) -> bool:
    """Check if the foreground window belongs to an elevated process.

    Uses GetWindowThreadProcessId + OpenProcess + GetTokenInformation to
    determine if the target process is running elevated. If we are not
    elevated but the target is, UIPI will block our SendInput calls.

    Returns True if the foreground window is elevated and we are not.
    Returns False if we can't determine (fail open) or if elevation
    matches.

    The "are we elevated?" check is cached at module-level via
    :func:`_pkg._get_we_elevated`. The value cannot change during the
    process lifetime, so we compute it once on first paste and reuse on
    every subsequent paste. The target-side check still runs per-paste
    because the foreground window can change between pastes.

    ``hwnd`` parameter accepts a pre-fetched foreground window handle so
    callers (:meth:`ClipboardManager._is_safe_paste_target`) that
    already fetched ``GetForegroundWindow`` can pass it in to avoid a
    redundant Win32 round-trip. When ``hwnd`` is ``None`` (the
    default), the function fetches it itself — preserving backward
    compatibility with direct callers and tests.
    """
    if not _pkg.is_windows():
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

            # Check if WE are elevated (cached, computed once).
            we_elevated = _pkg._get_we_elevated()

            # If target is elevated and we're not, warn
            if target_elevated and not we_elevated:
                _pkg._log().warning(
                    "[CLIPBOARD] Target window (pid=%d) is elevated but we are not — paste may fail due to UIPI",
                    pid.value,
                )
                return True
            return False
        finally:
            kernel32.CloseHandle(h_process)
    except Exception as exc:
        # fail-closed — if the elevation check itself raises,
        # block paste rather than risk pasting into an elevated target
        # we couldn't verify.
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _focused_window_is_credential_dialog(hwnd: int | None = None) -> bool:
    """Check if the focused window is a known credential dialog.

    Uses GetForegroundWindow + GetClassNameW via ctypes. Returns True
    if the window class matches a known credential dialog class. This
    is the comtypes-absence fallback for password field protection.

    ``hwnd`` parameter accepts a pre-fetched foreground window handle so
    callers (like :meth:`ClipboardManager._is_safe_paste_target`) that
    already fetched ``GetForegroundWindow`` can pass it in to avoid a
    redundant Win32 round-trip. When ``hwnd`` is ``None`` (the
    default), the function fetches it itself — preserving backward
    compatibility with direct callers and tests.
    """
    if not _pkg.is_windows():
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
        return cls in _pkg._CRED_DIALOG_CLASSES
    except Exception as exc:
        # fail-closed — if the credential-dialog check raises,
        # block paste rather than risk pasting into an undetected
        # credential prompt.
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _is_content_editable(focused: Any = None) -> bool:
    """Check if the focused element is a contentEditable element.

    On Windows, uses UI Automation (same comtypes infrastructure as
    :func:`_is_password_field` in :mod:`.validation`) to check if the
    focused element is an Edit or Document control that supports rich
    text input. This allows the clipboard module to log when the paste
    target is a rich editor (Word, LibreOffice, Gmail compose) and
    potentially paste HTML in a future version.

    Returns True if the focused element is contentEditable, False
    otherwise. Returns False on non-Windows or when comtypes is
    unavailable.

    ``focused`` parameter accepts a pre-fetched UIA element so callers
    (:meth:`ClipboardManager._is_safe_paste_target`) can fetch it once
    and pass to both :func:`_is_password_field` (in :mod:`.validation`)
    and this function. When ``focused`` is provided, the per-call
    ``CoInitialize``/``CoUninitialize`` is skipped (caller is expected
    to manage the COM apartment).
    """
    if not _pkg.is_windows():
        return False
    try:
        import comtypes

        # Skip per-call COM init when caller pre-fetched the element.
        owns_com = focused is None
        if owns_com:
            comtypes.CoInitialize()
        try:
            if focused is None:
                focused = _pkg._get_uia_focused_element()
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
                import contextlib

                with contextlib.suppress(Exception):
                    comtypes.CoUninitialize()
    except ImportError:
        return False
    except Exception as exc:
        # ``_is_content_editable`` is informational only (the
        # caller logs but does NOT block paste on True — see
        # ``_is_safe_paste_target`` docstring). Fail-OPEN here
        # is correct: returning True would falsely report a rich-editor
        # target without improving security. We still log the failure
        # so a broken UIA install is observable.
        _pkg._log().warning(
            "paste-safety check failed; keeping fail-open (contentEditable is informational, not a security gate): %s",
            exc,
        )
        return False


def _find_focused_atspi_accessible(
    desktop: Any,
    state_focused: Any,
    max_depth: int = 10,
) -> Any:
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

    ``state_focused`` is a REQUIRED parameter (was a module-level
    ``_PYATSPI_STATE_FOCUSED`` global written by
    ``_is_password_field_linux`` and read here). The caller resolves
    ``pyatspi.STATE_FOCUSED`` once at the top of
    ``_is_password_field_linux`` (in :mod:`.validation`) and passes it
    down — eliminates the hidden cross-function coupling via the module
    global, and removes the latent ``TypeError`` if
    ``_find_focused_atspi_accessible`` was ever called before the global
    had been initialized (the global defaulted to ``None`` and
    ``root_state.contains(None)`` raised).

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
    except Exception as e:
        _pkg._log().debug("Linux AT-SPI desktop.getState() failed: %s", e, exc_info=True)
        root_state = None
    if root_state is not None:
        try:
            if root_state.contains(state_focused):
                return desktop
        except Exception as exc:
            # Wire the one-shot paste-safety warning so the
            # swallowed exception is visible (was silent ``pass``).
            # The traversal continues below; if it also fails to find
            # a focused accessible, the caller fails open.
            _pkg._warn_paste_safety_once(
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
        # This helper returns an element (``Any``) or ``None`` —
        # NOT a bool. Changing the return type to ``True`` would break
        # the caller (``focused.getRole()`` on a bool raises). We log
        # via the one-shot paste-safety warning for dedup and keep the
        # ``None`` return; the caller's own fail-closed paths
        # (``_is_password_field_linux``) cover the security posture.
        _pkg._warn_paste_safety_once(
            "atspi_focused_childcount_lookup",
            "_find_focused_atspi_accessible",
            exc,
        )
        return None
    for i in range(child_count):
        try:
            child = desktop.getChildAtIndex(i)
        except Exception as e:
            _pkg._log().debug("Linux AT-SPI desktop.getChildAtIndex(%d) failed: %s", i, e, exc_info=True)
            continue
        if child is None:
            continue
        result = _find_focused_atspi_accessible(child, state_focused, max_depth - 1)
        if result is not None:
            return result
    return None
