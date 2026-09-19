"""Target window detection for clipboard paste safety."""

from __future__ import annotations

import sys
import time
from typing import Any  # noqa: F401  (used in type hints)

# ``_pkg`` is bound at module load time to the partial package object
import voice_typer.server.clipboard_target_safety as _pkg


def _get_we_elevated() -> bool:
    """Return whether THIS process is running elevated.

    Returns False on non-Windows, on failure, or when we cannot open
    """
    # Fast path: cache already populated, no lock needed.
    if _pkg._WE_ELEVATED is not None:
        return _pkg._WE_ELEVATED
    # Cold path: acquire the lock and re-check (another thread may
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
            _pkg._log().warning(
                "[CLIPBOARD] _get_we_elevated failed: %s, failing open (paste allowed)",
                exc,
                exc_info=True,
            )
            _pkg._WE_ELEVATED = False
        return _pkg._WE_ELEVATED


def _is_elevated_target(hwnd: int | None = None) -> bool:
    """Check if the foreground window belongs to an elevated process.

    Returns True if the foreground window is elevated and we are not.
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
                    "[CLIPBOARD] Target window (pid=%d) is elevated but we are not, paste may fail due to UIPI",
                    pid.value,
                )
                return True
            return False
        finally:
            kernel32.CloseHandle(h_process)
    except Exception as exc:
        # fail-closed, if the elevation check itself raises,
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _focused_window_is_credential_dialog(hwnd: int | None = None) -> bool:
    """Check if the focused window is a known credential dialog."""
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
        # fail-closed, if the credential-dialog check raises,
        _pkg._log().warning("paste-safety check failed; failing closed: %s", exc)
        return True


def _is_content_editable(focused: Any = None) -> bool:
    """Check if the focused element is a contentEditable element.

    Returns True if the focused element is contentEditable, False
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
            if control_type in (50004, 50036):
                # Check if the element supports the Value pattern (text input)
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

    Returns ``None`` when no focused accessible is found within the
    """
    # Read the cache slot once (no lock, tuple read is atomic under
    if not _is_pytest_running():
        cached = _LAST_FOCUSED_ROLE
        if cached is not None:
            cached_role, cached_ts = cached
            if (time.monotonic() - cached_ts) < _ATSPI_FOCUSED_ROLE_CACHE_WINDOW_S:
                return _CachedFocusedAccessible(cached_role)
            # expired, clear the stale slot and fall through to a fresh walk
            _invalidate_focused_atspi_cache()

    accessible = _find_focused_atspi_accessible_uncached(desktop, state_focused, max_depth)
    if accessible is None:
        # Don't cache None, let the next call re-walk. A transient
        return None

    # Cache the role (only in production, under pytest the cache stays
    try:
        role = accessible.getRole()
    except Exception:
        _invalidate_focused_atspi_cache()
        return accessible

    if not _is_pytest_running():
        _set_focused_atspi_cache(role)
    return accessible


def _is_pytest_running() -> bool:
    """Return True if pytest is the active test runner."""
    return "pytest" in sys.modules


class _CachedFocusedAccessible:
    """Shim returned by ``_find_focused_atspi_accessible`` on cache hit."""

    __slots__ = ("_role",)

    def __init__(self, role: Any) -> None:
        self._role = role

    def getRole(self) -> Any:  # noqa: N802, mirrors pyatspi.Accessible.getRole
        return self._role


# Module-level cache slot for the focused accessible's role.
_LAST_FOCUSED_ROLE: tuple[Any, float] | None = None

# Cache window: 200 ms. Long enough that back-to-back pastes at 5/s
_ATSPI_FOCUSED_ROLE_CACHE_WINDOW_S: float = 0.200


def _invalidate_focused_atspi_cache() -> None:
    """Clear the focused-role cache."""
    global _LAST_FOCUSED_ROLE
    _LAST_FOCUSED_ROLE = None


def _set_focused_atspi_cache(role: Any) -> None:
    """Populate the focused-role cache with ``role`` and the current time."""
    global _LAST_FOCUSED_ROLE
    _LAST_FOCUSED_ROLE = (role, time.monotonic())


def _find_focused_atspi_accessible_uncached(
    desktop: Any,
    state_focused: Any,
    max_depth: int = 10,
) -> Any:
    """Walk the AT-SPI tree to find the focused accessible (uncached)."""
    if desktop is None or max_depth <= 0:
        return None

    # First check the root itself (defensive, desktop itself is never
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
            _pkg._warn_paste_safety_once(
                "atspi_focused_state_check",
                "_find_focused_atspi_accessible",
                exc,
            )

    # Depth-first traversal: at each level, recurse into the first
    try:
        child_count = desktop.childCount
    except Exception as exc:
        # This helper returns an element (``Any``) or ``None`` —
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
        # Recursive call goes to the UNCACHED helper, only the
        result = _find_focused_atspi_accessible_uncached(child, state_focused, max_depth - 1)
        if result is not None:
            return result
    return None
