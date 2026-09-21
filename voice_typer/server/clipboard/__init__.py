"""Clipboard management and auto-paste package ( split)."""

from __future__ import annotations

import atexit  # noqa: F401  (re-exported for tests that inspect atexit usage)
import contextlib
import logging
import os  # noqa: F401  (used by linux primitives; re-exported)
import shutil  # noqa: F401  (used by linux primitives; re-exported)
import subprocess  # noqa: F401  (used by linux primitives; re-exported)
import threading  # noqa: F401  (re-exported for tests)
import time  # noqa: F401  (patched by tests via clip_mod.time)
from typing import Any  # noqa: F401

import pyperclip  # noqa: F401  (patched by tests via clip_mod.pyperclip)

from voice_typer.server.clipboard_snapshot import ClipboardSnapshot  # noqa: F401
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)

# Package-level logger. Tests patch ``voice_typer.server.clipboard.log``.
log = logging.getLogger(__name__)

# Retry lives in manager._copy: except OSError as copy_err with winerror == 5.
# PLAT-CONTENT: contentEditable target check lives in safety/targets (informational).
# Pynput fallback + UIPI notes live in linux/windows backends.

# empty binding. No ``# type: ignore[assignment]`` marker is needed
# ``# type: ignore[assignment]`` markers  removed stay dropped
_Key: Any | None = None
_Controller: type | None = None


# Import order matters: .linux and .windows have NO inter-submodule

# Win32 UI Automation focus / password-field / elevated-target
from voice_typer.server.clipboard_target_safety import (  # noqa: E402,F401
    _CRED_DIALOG_CLASSES,
    UiaUnavailableError,
    _find_focused_atspi_accessible,
    _focused_window_is_credential_dialog,
    _get_uia_focused_element,
    _get_uia_singleton,
    _get_we_elevated,
    _is_content_editable,
    _is_elevated_target,
    _is_password_field,
    _is_password_field_linux,
    _is_password_field_macos,
    _is_secure_input_enabled,
    reset_platform_unavailable_warnings,
)

# Mutable globals re-exported dynamically via PEP 562 ``__getattr__``
from .linux import (  # noqa: E402,F401
    _RICH_EDITOR_PROCESS_NAMES,
    _TERMINAL_PROCESS_NAMES,
    _WTYPE_SHORT_TEXT_THRESHOLD,
    _copy_to_clipboard,
    _ensure_pynput_imported,
    _have_wl_clipboard,
    _have_wtype,
    _is_wayland_paste_session,
    _is_wayland_session,
    _linux_copy,
    _linux_paste,
    _linux_paste_via_wtype,
    _linux_wayland_copy,
    _linux_wayland_paste,
    _paste_from_clipboard,
)
from .manager import (  # noqa: E402,F401
    ClipboardCopyError,
    ClipboardManager,
    _force_restore_pending_at_exit,
    _pending_restores,
    _pending_restores_lock,
)
from .windows import (  # noqa: E402,F401
    Win32Clipboard,
    _send_ctrl_v_win32,
    _send_shift_insert_win32,
    _win32_empty_clipboard,
    _win32_exclude_clipboard_from_monitoring,
)

__all__ = [
    # Public API
    "ClipboardCopyError",
    "ClipboardManager",
    "ClipboardSnapshot",
    "Win32Clipboard",
    # Linux / Wayland primitives
    "_RICH_EDITOR_PROCESS_NAMES",
    "_TERMINAL_PROCESS_NAMES",
    "_copy_to_clipboard",
    "_ensure_pynput_imported",
    "_have_wl_clipboard",
    "_have_wtype",
    "_is_wayland_paste_session",
    "_is_wayland_session",
    "_linux_copy",
    "_linux_paste",
    "_linux_paste_via_wtype",
    "_linux_wayland_copy",
    "_linux_wayland_paste",
    "_paste_from_clipboard",
    # Win32 primitives
    "_send_ctrl_v_win32",
    "_send_shift_insert_win32",
    "_win32_empty_clipboard",
    "_win32_exclude_clipboard_from_monitoring",
    # atexit / pending-restores registry
    "_force_restore_pending_at_exit",
    "_pending_restores",
    "_pending_restores_lock",
    # Pynput lazy-import state (mutated by _ensure_pynput_imported)
    "_Controller",
    "_Key",
    # Target-safety re-exports ()
    "_CRED_DIALOG_CLASSES",
    "_MACOS_SECURE_INPUT_WARNED",
    "_PYATSPI_UNAVAILABLE_WARNED",
    "_PYATSPI_STATE_FOCUSED",
    "_PYOBJC_UNAVAILABLE_WARNED",
    "_UIA_MODULE",
    "_UIA_SINGLETON",
    "_UIA_SINGLETON_INIT_ATTEMPTED",
    "_WE_ELEVATED",
    "UiaUnavailableError",
    "_find_focused_atspi_accessible",
    "_focused_window_is_credential_dialog",
    "_get_uia_focused_element",
    "_get_uia_singleton",
    "_get_we_elevated",
    "_is_content_editable",
    "_is_elevated_target",
    "_is_password_field",
    "_is_password_field_linux",
    "_is_password_field_macos",
    "_is_secure_input_enabled",
    "reset_platform_unavailable_warnings",
    # Platform utils (re-exported so tests can patch via clip_mod.is_windows etc.)
    "is_linux",
    "is_macos",
    "is_windows",
    # Third-party (re-exported so tests can patch via clip_mod.pyperclip etc.)
    "pyperclip",
    "subprocess",
    "time",
    "log",
]


# SIGTERM / SIGHUP → force clipboard restore on exit───


def _signal_restore_handler(signum: int, frame: Any) -> None:  # noqa: ARG001
    """G4 signal handler: restore pending snapshots, then re-raise."""
    with contextlib.suppress(Exception):
        _force_restore_pending_at_exit()
    # Restore default disposition and re-raise so the process exits
    try:
        import os as _os_module
        import signal as _signal_module

        _signal_module.signal(signum, _signal_module.SIG_DFL)
        _os_module.kill(_os_module.getpid(), signum)
    except Exception:
        # If re-raise fails (e.g. signal already in flight, or
        import os as _os_module_fallback

        _os_module_fallback._exit(128 + signum)


_SIGNAL_HANDLERS_REGISTERED = False
if not _SIGNAL_HANDLERS_REGISTERED:
    try:
        import signal as _signal_module

        # POSIX-only: SIGHUP exists only on POSIX. On Windows,
        if hasattr(_signal_module, "SIGHUP"):
            _signal_module.signal(_signal_module.SIGTERM, _signal_restore_handler)
            _signal_module.signal(_signal_module.SIGHUP, _signal_restore_handler)
            _SIGNAL_HANDLERS_REGISTERED = True
    except (ValueError, OSError):
        # ValueError: not in the main thread (signal.signal can only
        pass
    except Exception:  # pragma: no cover, defensive
        log.debug("[CLIPBOARD] signal handler registration failed", exc_info=True)


# The following seven names live in ``voice_typer.server.clipboard_target_safety``
_DYNAMIC_REEXPORT_MUTABLE_GLOBALS = frozenset(
    {
        "_PYATSPI_STATE_FOCUSED",
        "_PYATSPI_UNAVAILABLE_WARNED",
        "_PYOBJC_UNAVAILABLE_WARNED",
        "_UIA_MODULE",
        "_UIA_SINGLETON",
        "_UIA_SINGLETON_INIT_ATTEMPTED",
        "_WE_ELEVATED",
        "_MACOS_SECURE_INPUT_WARNED",
    }
)


def __getattr__(name: str):  # noqa: D401
    """PEP 562: dynamically resolve mutable target-safety globals."""
    if name in _DYNAMIC_REEXPORT_MUTABLE_GLOBALS:
        from voice_typer.server import clipboard_target_safety as _safety

        return getattr(_safety, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():  # pragma: no cover, exercised by ``dir(clip_mod)`` in REPLs
    """PEP 562: include the dynamically-resolved mutable globals in ``dir()``."""
    module_attrs = list(globals().keys())
    for _name in sorted(_DYNAMIC_REEXPORT_MUTABLE_GLOBALS):
        if _name not in module_attrs:
            module_attrs.append(_name)
    return module_attrs
