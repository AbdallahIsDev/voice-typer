"""Platform utilities — centralized platform detection.

Replaces scattered sys.platform checks with centralized functions.
Import these instead of writing `if sys.platform == "win32":` directly.

Usage:
    from voice_typer.server.platform_utils import is_windows, is_macos, is_linux
"""

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
    """Return True if running under a Wayland session (Linux only).

    replaces four inconsistent Wayland-session detectors that
        were scattered across ``tray.py``, ``clipboard_snapshot.py`` and
        ``startup_sequence.py``. The case-insensitive ``XDG_SESSION_TYPE``
        check matches ``wayland``, ``Wayland`` and ``WAYLAND``. The
        ``WAYLAND_DISPLAY`` fallback catches compositors that set the env
        var without setting ``XDG_SESSION_TYPE`` (e.g. some wlroots setups
        launched from a TTY).

        Returns ``False`` on Windows AND macOS (neither runs Wayland;
        macOS uses Quartz/Aqua, Windows uses the Desktop Window Manager).
        previously this also accepted ``darwin`` as a Wayland-capable
        platform — that was a copy-paste artefact from the broader
        ``is_linux() or is_macos()`` Unix-flavoured branch. Wayland is a
        Linux display-server protocol; macOS does not run it.
    """
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
    """Set Windows process metadata for Task Manager / diagnostic tools.

    BRAND-METADATA: On Windows the Python backend appears as a generic
    ``pythonw.exe`` / ``python.exe`` in Task Manager because that IS
    the process image name.  The Task Manager icon is also Python's
    icon (embedded in pythonw.exe) — we can't change either without a
    compiled helper executable.  However we CAN improve the process
    identity metadata:

      1. Set the console title (visible in the console window title bar
         when running under ``python.exe`` with a console attached).
      2. Set the AppUserModelID via
         ``SetCurrentProcessExplicitAppUserModelID`` so Windows
         associates this process with the Voice Typer application
         identity.  This helps with taskbar grouping, jump lists, and
         some Task Manager metadata columns (Product Name, Description).

    The system tray icon already uses the correct app icon (set by
    TrayIcon).  The Electron process's AppUserModelID is set separately
    in the Electron main process (index.ts).  The two are kept
    consistent by using the same ``abdallahisdev.VoiceTyper`` ID.

    Parameters
    ----------
    app_name : str
        The application display name (passed by caller).
    """
    if not is_windows():
        return
    try:
        import ctypes
        from ctypes import wintypes

        # 1. Set the console title so the window caption reflects the
        #    app name instead of "Python" (visible when running with
        #    a console attached, no-op for pythonw.exe).
        kernel32 = ctypes.windll.kernel32
        try:
            kernel32.SetConsoleTitleW.argtypes = [wintypes.LPCWSTR]
            kernel32.SetConsoleTitleW.restype = wintypes.BOOL
            kernel32.SetConsoleTitleW(app_name)
        except Exception:
            pass  # Best-effort — may fail under pythonw.exe (no console)

        # 2. Set the AppUserModelID so Windows identifies this process
        #    as belonging to Voice Typer.  Must match the Electron side
        #    (``VoiceTyper`` in index.ts) for consistency.
        try:
            shell32 = ctypes.windll.shell32
            shell32.SetCurrentProcessExplicitAppUserModelID.argtypes = [wintypes.LPCWSTR]
            # ``wintypes.HRESULT`` is only present in the
            # typeshed stub when ``sys.version_info >= (3, 14)``.
            shell32.SetCurrentProcessExplicitAppUserModelID.restype = getattr(wintypes, "HRESULT", wintypes.LONG)
            # BRAND-: use just the app name (no "abdallahisdev." prefix)
            # so Windows notifications show "VoiceTyper" as the title instead
            # of "abdallahisdev.VoiceTyper".  Matches the Electron side's
            # ``app.setAppUserModelId("VoiceTyper")`` in index.ts.
            shell32.SetCurrentProcessExplicitAppUserModelID(app_name.replace(" ", ""))
        except Exception:
            pass  # Best-effort — requires Windows 7+ with shell32

    except Exception:
        pass  # Best-effort — ctypes or kernel32/shell32 may not be available


# Environment variable validation lives in ``app.py::_validate_env_vars``.
# A previous schema-driven implementation (``validate_env_vars`` +
# ``_init_env_var_schema`` + ``_ENV_VAR_SCHEMA``) lived here but was
# never called from any production code path — it was dead code that
# duplicated the inline implementation in ``app.py``. The dead code was
# removed to eliminate the parallel-systems maintenance hazard (Q5:
# parallel systems; Q10: not clean). The inline ``_validate_env_vars``
# in ``app.py`` is the single source of truth for env-var validation.
# See FORENSIC_REVIEW_COMPLETE.md →
