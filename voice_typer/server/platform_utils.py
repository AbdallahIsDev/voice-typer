"""Platform utilities, centralized platform detection."""

import logging
import sys

log = logging.getLogger("voice_typer.server.platform_utils")


def is_windows() -> bool:
    """Return True if running on Windows."""
    return sys.platform == "win32"


def is_macos() -> bool:
    """Return True if running on macOS."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Return True if running on Linux."""
    return sys.platform.startswith("linux")


def is_wayland_session() -> bool:
    """Return True if running under a Wayland session (Linux only)."""
    if sys.platform != "linux":
        return False  # Windows + macOS can never be Wayland
    import os

    xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
    if xdg_session.lower() == "wayland":
        return True
    return bool(os.environ.get("WAYLAND_DISPLAY"))


def platform_name() -> str:
    """Return a human-readable platform name."""
    if is_windows():
        return "windows"
    elif is_macos():
        return "macos"
    elif is_linux():
        return "linux"
    else:
        return sys.platform


def _set_windows_process_metadata(app_name: str) -> None:
    """Set Windows process metadata for Task Manager / diagnostic tools."""
    if not is_windows():
        return
    try:
        import ctypes
        from ctypes import wintypes

        # 1. Set the console title so the window caption reflects the
        kernel32 = ctypes.windll.kernel32
        try:
            kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
            kernel32.SetConsoleTitleW.restype = wintypes.BOOL
            kernel32.SetConsoleTitleW(app_name)
        except (OSError, AttributeError, ValueError, TypeError):
            # Best-effort, may fail under pythonw.exe (no console).
            log.debug(
                "[PLATFORM] SetConsoleTitleW failed (non-fatal)",
                exc_info=True,
            )

        # 2. Set the AppUserModelID so Windows identifies this process
        try:
            shell32 = ctypes.windll.shell32
            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
            # ``wintypes.HRESULT`` is only present in the
            shell32.SetCurrentProcessExplicitAppUserModelID.restype = getattr(wintypes, "HRESULT", wintypes.LONG)
            # BRAND-: use just the app name (no "abdallahisdev." prefix)
            shell32.SetCurrentProcessExplicitAppUserModelID(app_name.replace(" ", ""))
        except (OSError, AttributeError, ValueError, TypeError):
            # Best-effort, requires Windows 7+ with shell32.
            log.debug(
                "[PLATFORM] SetCurrentProcessExplicitAppUserModelID failed (non-fatal)",
                exc_info=True,
            )

    except (ImportError, OSError, AttributeError):
        # Best-effort, ctypes or kernel32/shell32 may not be available.
        log.debug(
            "[PLATFORM] Windows process-metadata setup unavailable (non-fatal)",
            exc_info=True,
        )


# Environment variable validation lives in ``app.py::_validate_env_vars``.
