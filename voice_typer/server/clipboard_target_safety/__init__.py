"""Clipboard target-safety checks (package split)."""

from __future__ import annotations

import threading
from typing import Any  # noqa: F401  (re-exported for type hints in submodules + tests)

# ``is_windows`` is resolved dynamically through the clipboard


def _get_clipboard_module():
    """Lazy access to the clipboard module (breaks circular import)."""
    import voice_typer.server.clipboard as _cb

    return _cb


def is_windows() -> bool:
    """Delegate to ``clipboard.is_windows`` so test patches propagate."""
    return _get_clipboard_module().is_windows()


def is_macos() -> bool:
    """Delegate to ``clipboard.is_macos`` so test patches propagate."""
    return _get_clipboard_module().is_macos()


# Keep the SAME log object as clipboard.py. Resolved dynamically via the
def _log():
    return _get_clipboard_module().log


# These are accessed by the submodules via ``_pkg.NAME`` (where

# Module-level cache for "are WE elevated?": this value never changes
_WE_ELEVATED: bool | None = None

# Guards the ``_WE_ELEVATED`` init race. The clipboard paste
_WE_ELEVATED_LOCK = threading.Lock()

# Per-paste security checks fail open (return False). Logging every
_PASTE_SAFETY_WARNED: set[str] = set()


# Module-level cached IUIAutomation instance (Windows).
_UIA_SINGLETON = None
_UIA_MODULE = None
_UIA_SINGLETON_INIT_ATTEMPTED: bool = False

# Guards the ``_UIA_SINGLETON`` init race. The
_UIA_SINGLETON_LOCK = threading.Lock()


# Known credential dialog window classes on Windows.
_CRED_DIALOG_CLASSES: set[str] = {
    "CredentialDialog",  # Generic credential dialog
    "CredDialogCallerWnd",  # CredUI dialog
    "NN Credentials Dialog",  # Network credentials
    "PassportWindow",  # Microsoft account
}


# On macOS/Linux, ``_is_safe_paste_target`` previously returned ``True``
_PYOBJC_UNAVAILABLE_WARNED: bool = False
_PYATSPI_UNAVAILABLE_WARNED: bool = False

# The ``_PYATSPI_STATE_FOCUSED`` module-level global is a BACKWARD-COMPAT
_PYATSPI_STATE_FOCUSED: Any = None

# (Medium): once-only warning guard for the macOS Secure Input
_MACOS_SECURE_INPUT_WARNED: bool = False


# Import order: each submodule is self-contained (no inter-submodule

from .injection import (  # noqa: E402,F401
    _get_uia_focused_element,
    _get_uia_singleton,
)
from .targets import (  # noqa: E402,F401
    _find_focused_atspi_accessible,
    _focused_window_is_credential_dialog,
    _get_we_elevated,
    _is_content_editable,
    _is_elevated_target,
)
from .validation import (  # noqa: E402,F401
    _ax_result_value,
    _is_password_field,
    _is_password_field_linux,
    _is_password_field_macos,
    _is_secure_input_enabled,
    _warn_paste_safety_once,
    reset_platform_unavailable_warnings,
)

__all__ = [
    # Helpers
    "is_windows",
    "is_macos",
    "_log",
    # Mutable globals (also accessible via PEP 562 __getattr__ in
    "_WE_ELEVATED",
    "_WE_ELEVATED_LOCK",
    "_PASTE_SAFETY_WARNED",
    "_UIA_SINGLETON",
    "_UIA_MODULE",
    "_UIA_SINGLETON_INIT_ATTEMPTED",
    "_UIA_SINGLETON_LOCK",
    "_PYOBJC_UNAVAILABLE_WARNED",
    "_PYATSPI_UNAVAILABLE_WARNED",
    "_PYATSPI_STATE_FOCUSED",
    "_CRED_DIALOG_CLASSES",
    # Target detection (targets.py)
    "_find_focused_atspi_accessible",
    "_focused_window_is_credential_dialog",
    "_get_we_elevated",
    "_is_content_editable",
    "_is_elevated_target",
    # UIA infrastructure (injection.py)
    "_get_uia_focused_element",
    "_get_uia_singleton",
    # Safety validation (validation.py)
    "_ax_result_value",
    "_is_password_field",
    "_is_password_field_linux",
    "_is_password_field_macos",
    "_warn_paste_safety_once",
    "reset_platform_unavailable_warnings",
]
