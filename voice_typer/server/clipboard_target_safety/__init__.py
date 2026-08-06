"""Clipboard target-safety checks (package split).

Extracted from the original 1101-LOC ``clipboard_target_safety.py``
monolith into a package with three focused submodules:

* :mod:`.targets` — target window detection (foreground window class,
  elevated-target check, contentEditable detection, AT-SPI tree walk,
  per-process "are we elevated?" cache).
* :mod:`.injection` — UIA singleton + focused-element fetching (the COM
  infrastructure that drives the Windows password-field / contentEditable
  safety checks).
* :mod:`.validation` — safety validation rules (password-field
  detection on Windows / macOS / Linux, paste-safety warning dedup,
  platform-unavailable warning guards, AX-result shape helper).

The package re-exports every name that the original monolith exposed, so
``from voice_typer.server.clipboard_target_safety import X`` call sites
(including the re-exports in ``voice_typer.server.clipboard`` and the
test-suite's ``from voice_typer.server import clipboard_target_safety
as safety_mod`` pattern) keep working unchanged.

Module-level mutable globals (``_WE_ELEVATED``, ``_UIA_SINGLETON``,
``_PYATSPI_STATE_FOCUSED``, ``_PYOBJC_UNAVAILABLE_WARNED``,
``_PYATSPI_UNAVAILABLE_WARNED``, ``_PASTE_SAFETY_WARNED``,
``_UIA_SINGLETON_INIT_ATTEMPTED``, ``_UIA_MODULE``) and the read-only
``_WE_ELEVATED_LOCK`` / ``_UIA_SINGLETON_LOCK`` / ``_CRED_DIALOG_CLASSES``
live IN THIS FILE. The submodule functions access them via
``_pkg.NAME`` (where ``_pkg`` is this package) so that test fixtures
that do ``safety_mod._WE_ELEVATED = None`` (reset) and
``patch.object(safety_mod, "is_windows", ...)`` propagate to the
functions defined in :mod:`.targets`, :mod:`.injection`, and
:mod:`.validation`. A plain ``global NAME`` inside a submodule would
write to the SUBMODULE's namespace and be invisible to the test patches
applied on the package — hence the ``_pkg.NAME`` access pattern.

The same pattern is used for cross-submodule function calls
(``_is_elevated_target`` calls ``_pkg._get_we_elevated`` rather than
``_get_we_elevated``) so that ``patch.object(safety_mod,
"_get_we_elevated", ...)`` patches propagate to the calling functions.
"""

from __future__ import annotations

import threading
from typing import Any  # noqa: F401  (re-exported for type hints in submodules + tests)

# ``is_windows`` is resolved dynamically through the clipboard
# module so the test suite's ``patch.object(clip_mod, "is_windows")``
# (which is what the Win32 coverage tests use to simulate Windows) is
# honoured. A static ``from platform_utils import is_windows`` would
# snapshot the real function and bypass the patch.
#
# NOTE: The import is deferred to first call to avoid a circular import
# (clipboard/__init__.py imports back from clipboard_target_safety).


def _get_clipboard_module():
    """Lazy access to the clipboard module (breaks circular import)."""
    import voice_typer.server.clipboard as _cb

    return _cb


def is_windows() -> bool:
    """Delegate to ``clipboard.is_windows`` so test patches propagate.

    Tests patch BOTH ``clip_mod.is_windows`` and ``safety_mod.is_windows``.
    The submodule functions call ``_pkg.is_windows()`` (which is THIS
    function); a patch on ``safety_mod.is_windows`` replaces this
    function in the package namespace and the submodules see the patched
    version via ``_pkg.is_windows``.
    """
    return _get_clipboard_module().is_windows()


def is_macos() -> bool:
    """Delegate to ``clipboard.is_macos`` so test patches propagate.

    Mirrors :func:`is_windows`. Used by the macOS Secure Input check
    (:func:`_is_secure_input_enabled` in :mod:`.validation`) to
    short-circuit on non-macOS hosts without spawning ``ioreg``.
    """
    return _get_clipboard_module().is_macos()


# Keep the SAME log object as clipboard.py. Resolved dynamically via the
# clipboard module so a single test patch of
# ``voice_typer.server.clipboard.log`` (which is what the test suite
# patches) also covers these extracted helpers. Binding at import time
# (``log = clipboard.log``) would snapshot the original Logger and miss
# such patches.
def _log():
    return _get_clipboard_module().log


# ─── Module-level mutable globals ────────────────────────────────────
#
# These are accessed by the submodules via ``_pkg.NAME`` (where
# ``_pkg`` is this package) so that test patches / resets on
# ``safety_mod.NAME`` propagate to the functions defined in
# ``targets.py`` / ``injection.py`` / ``validation.py``.
#
# A plain ``global NAME`` inside a submodule would write to the
# SUBMODULE's namespace, NOT the package's — so a test doing
# ``safety_mod._WE_ELEVATED = None`` would set the package attribute
# to None while the submodule's ``global _WE_ELEVATED`` writes would
# land in the submodule's namespace, and the two would drift apart.
# Routing all reads / writes through ``_pkg.NAME`` keeps a single
# source of truth (this file's module-level bindings).

# Module-level cache for "are WE elevated?" — this value never changes
# during the lifetime of the process, so computing it on every paste is
# wasted work (OpenProcessToken + GetTokenInformation + CloseHandle =
# 3 kernel calls per paste). Cached on first access.
# ``None`` = not yet computed.
_WE_ELEVATED: bool | None = None

# Guards the ``_WE_ELEVATED`` init race. The clipboard paste
# path is called from the main IPC thread, but a prewarm / caps-lock
# polling thread can also call into ``_is_elevated_target`` (which calls
# ``_get_we_elevated``). Without this lock, two threads could both
# observe ``_WE_ELEVATED is None`` and both run the Win32 token query,
# stomping each other's write. The check-then-act is benign on
# correctness (the value is process-stable) but the redundant token
# query leaks handles if both threads enter the ``try`` block before
# either sets the cache.
_WE_ELEVATED_LOCK = threading.Lock()

# Per-paste security checks fail open (return False). Logging every
# failure at WARNING would spam the log at paste rate; logging at DEBUG
# without deduplication would still emit one record per paste. Use
# module-level "first-occurrence" flags so the operator gets one
# WARNING-equivalent record per failure mode per session — enough to
# notice the regression without flooding the log.
_PASTE_SAFETY_WARNED: set[str] = set()


# Module-level cached IUIAutomation instance (Windows).
# Creating a fresh IUIAutomation COM instance on every paste was costing
# 10-50ms per call (cross-process RPC). Caching it here eliminates that
# cost for every subsequent paste. The instance is created lazily on
# first use and reused for the lifetime of the process.
_UIA_SINGLETON = None
_UIA_MODULE = None
_UIA_SINGLETON_INIT_ATTEMPTED = False

# Guards the ``_UIA_SINGLETON`` init race. The
# ``_UIA_SINGLETON_INIT_ATTEMPTED`` flag is itself a check-then-act
# race: two threads can both observe ``False``, both run
# ``comtypes.client.GetModule`` + ``CoCreateInstance``, and both write
# to the module-level cache. CoCreateInstance returns a fresh COM proxy
# each time, so the loser overwrites the winner's proxy — the abandoned
# proxy leaks until GC, and on failure paths the
# ``_UIA_SINGLETON_INIT_ATTEMPTED`` flag is set by whichever thread runs
# last, masking the other thread's in-flight init. Double-checked
# locking fixes this: the fast path (init already attempted) is
# lock-free; only the cold path acquires the lock and re-checks the flag.
_UIA_SINGLETON_LOCK = threading.Lock()


# Known credential dialog window classes on Windows.
# When comtypes is unavailable, we fall back to checking the focused
# window's class name against this set. This is a COARSE heuristic —
# it only catches the standard Windows credential UI, not arbitrary
# password fields in third-party apps. comtypes/UIA is required for
# full coverage (see ``_is_password_field`` in :mod:`.validation`).
_CRED_DIALOG_CLASSES: set[str] = {
    "CredentialDialog",  # Generic credential dialog
    "CredDialogCallerWnd",  # CredUI dialog
    "NN Credentials Dialog",  # Network credentials
    "PassportWindow",  # Microsoft account
}


# macOS/Linux password field detection ────────────────────
#
# On macOS/Linux, ``_is_safe_paste_target`` previously returned ``True``
# unconditionally, allowing dictated text to be pasted into password
# fields, SSH passphrase prompts, credit-card forms, etc.
#
# The platform-native helpers (in :mod:`.validation`) query the
# platform-native accessibility APIs:
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
# Once-only warning guards so missing platform libs don't spam the log
# on every paste. The first paste logs a WARNING; subsequent pastes log
# at DEBUG. Reset to False (via test fixtures) by calling
# ``reset_platform_unavailable_warnings()``.
_PYOBJC_UNAVAILABLE_WARNED: bool = False
_PYATSPI_UNAVAILABLE_WARNED: bool = False

# The ``_PYATSPI_STATE_FOCUSED`` module-level global is a BACKWARD-COMPAT
# SENTINEL — it's still SET by ``_is_password_field_linux`` (in
# :mod:`.validation`) when it resolves ``pyatspi.STATE_FOCUSED`` (so
# external code that inspects / patches the attribute, including the
# re-export in ``voice_typer/server/clipboard/__init__.py`` and the test
# reset in ``tests/test_clipboard_password_detection.py``, keeps
# working), but it is NO LONGER READ by ``_find_focused_atspi_accessible``.
# The state value is now passed explicitly as a function parameter,
# eliminating the hidden cross-function coupling via the module global
# and removing the latent ``TypeError`` if
# ``_find_focused_atspi_accessible`` was ever called before the global
# had been initialized (the global defaulted to ``None`` and
# ``root_state.contains(None)`` raised).
# Initializing to ``None`` preserves the prior "not yet resolved"
# sentinel semantics.
_PYATSPI_STATE_FOCUSED: Any = None

# (Medium): once-only warning guard for the macOS Secure Input
# check (``_is_secure_input_enabled`` in :mod:`.validation`). ``False``
# is the initial state — the first detection of secure-input-enabled
# mode emits the WARNING / tray toast; subsequent detections during
# the same session log at DEBUG so the log is not flooded at paste
# rate when the user keeps a password dialog open (which holds Secure
# Input active for the dialog's lifetime). Reset to ``False`` in tests
# via direct assignment on the package.
_MACOS_SECURE_INPUT_WARNED: bool = False


# ─── Re-exports from submodules ──────────────────────────────────────
# Import order: each submodule is self-contained (no inter-submodule
# import-time dependencies). They each ``import
# voice_typer.server.clipboard_target_safety as _pkg`` and access the
# mutable globals + cross-submodule functions via ``_pkg.NAME`` at call
# time, so the order in which we import them here doesn't matter for
# correctness.

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
    # voice_typer/server/clipboard/__init__.py — listed here so
    # ``from voice_typer.server.clipboard_target_safety import _WE_ELEVATED``
    # keeps working and so static-analysis tools see them in ``dir()``).
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
