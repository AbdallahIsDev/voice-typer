"""OS permission detection and onboarding for hotkey backends.

This package is the single source of truth for "can we monitor the keyboard
on this platform?" and "what should we do if we can't?".

Architecture note: this package was split from the original 1144-LOC
``permissions.py`` monolith into 4 focused submodules + this facade
``__init__.py``. All mutable state (``_retry_timer``,
``_retry_count``, ``_cancelled``, ``_retry_lock``, ``_PYOBJC_AVAILABLE``)
lives HERE on the facade module so that test mutations on
``voice_typer.server.permissions.<name>`` propagate to the submodule
functions that read/write the same state (the submodules access state
via ``import voice_typer.server.permissions as _p; _p.<name>``). This
preserves the pre-split behavior where tests could reset module-level
globals directly (mirrors the crash_handler split pattern).

Submodules:
- :mod:`voice_typer.server.permissions.checker` — core permission checker
  logic: dispatchers (``check_keyboard_permission``,
  ``check_microphone_permission``, ``verify_microphone_accessible``), the
  retry timer (``schedule_permission_retry`` /
  ``cancel_permission_retry``), the pyobjc availability cache
  (``_is_pyobjc_available`` / ``reset_pyobjc_cache``), the error
  classifier (``permission_error_is_permission_denied``), the request
  dispatchers (``request_keyboard_permission`` /
  ``request_microphone_permission`` /
  ``request_microphone_permission_result``), the tray notification helper
  (``show_permission_notification``), and the ``PermissionState`` /
  ``MicrophonePermissionState`` enums.
- :mod:`voice_typer.server.permissions.mic` — microphone permission
  probes (``_check_macos_microphone`` / ``_check_windows_microphone`` /
  ``_check_linux_microphone``) and macOS settings openers
  (``_open_macos_microphone_settings`` /
  ``_trigger_macos_microphone_consent_prompt``).
- :mod:`voice_typer.server.permissions.accessibility` — macOS
  Accessibility permission probe (``_check_macos_accessibility``) and
  settings opener (``_open_macos_accessibility_settings``).
- :mod:`voice_typer.server.permissions.filesystem` — Linux
  ``/dev/input/event*`` permission probe (``_check_linux_input_access``)
  and the pkexec install-permissions launcher
  (``_open_linux_pkexec_prompt`` / ``_find_linux_install_script``).

Platform summary
----------------
- **Windows**: ``WH_KEYBOARD_LL`` needs no special permission. The native
  binary works out of the box. ``check_keyboard_permission()`` always
  returns ``GRANTED``.
- **macOS**: ``CGEventTap`` (used by ``macos-key-listener``) requires
  *Accessibility* permission. Without it, the binary emits
  ``ERROR:Accessibility permission required...``. This package detects
  that error, classifies it as a permission issue, and provides a helper
  to deep-link the user to ``System Settings → Privacy & Security →
  Accessibility``.
- **Linux**: reading ``/dev/input/event*`` (evdev) requires the user to
  be in the ``input`` group, plus a udev rule granting group-read access.
  This package detects the "permission denied" error from the binary and,
  for AppImage users, runs ``pkexec`` to invoke
  ``scripts/linux/install_permissions.py`` (which installs the udev rule,
  adds the user to the group, and configures Caps Lock neutralization).

The udev rule installed by ``scripts/linux/install_permissions.py`` is::

    KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"

(/LINUX-UDEV: the previous onboarding instructions referenced
``event*`` + ``MODE="0640"`` which (a) over-matches non-keyboard event
devices and (b) grants no group-write access. The correct pattern is
``event[0-9]*`` with ``MODE="0660"`` — group-readable AND group-writable
so the ``input`` group can both read events and (rarely needed) write
LED state. See :data:`LINUX_UDEV_RULE` for the canonical string.)

The package is intentionally side-effect-free at import time. All
``request_*`` functions are invoked explicitly by the hotkey adapter when
an error is detected.
"""

from __future__ import annotations

# Stdlib imports — also re-exported as module attributes so tests can
# patch ``permissions.subprocess.Popen`` / ``permissions.os.path.exists``
# / ``permissions.shutil.which`` / ``permissions.threading.Timer``. The
# patches mutate the underlying global module objects, so submodule
# references to ``subprocess.Popen`` / ``os.path.exists`` / etc. see
# the patches automatically.
import contextlib  # noqa: F401 — used by checker.cancel_permission_retry
import logging
import os  # noqa: F401 — re-exported; tests patch permissions.os.path
import shutil  # noqa: F401 — re-exported; tests patch permissions.shutil.which
import subprocess  # noqa: F401 — re-exported; tests patch permissions.subprocess.Popen
import sys  # noqa: F401 — re-exported; filesystem submodules use sys.executable
import threading  # noqa: F401 — used for _retry_lock; tests patch permissions.threading.Timer
from collections.abc import Callable  # noqa: F401 — re-exported for type hints
from typing import Any  # noqa: F401 — re-exported for type hints

# Re-imports of platform helpers and constants — these become module
# attributes on the facade so tests can monkeypatch
# ``permissions.is_macos`` / ``permissions.is_windows`` /
# ``permissions.is_linux`` and the patched values are observed by the
# submodule dispatchers (which access them via ``_p.is_macos()`` etc.).
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: F401
from voice_typer.server.branding import APP_NAME  # noqa: F401
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)

log = logging.getLogger("voice_typer.server.permissions")


# ─── Constants ─────────────────────────────────────────────────────────────


# LINUX-UDEV: canonical udev rule string installed by
# ``scripts/linux/install_permissions.py``. Exposed as a module constant so
# the onboarding instructions (in :mod:`voice_typer.server.onboarding`) and
# the pkexec installer agree on the exact pattern. The previous onboarding
# text referenced ``event*`` (over-matches) + ``MODE="0640"`` (no
# group-write). The correct pattern is ``event[0-9]*`` + ``MODE="0660"``.
LINUX_UDEV_RULE = 'KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"'


# ─── Mutable state (lives HERE so test mutations propagate) ───────────────
#
# These module-level variables are the canonical storage for the
# permissions package's mutable state. Submodule functions access them
# via ``import voice_typer.server.permissions as _p; _p.<name>`` (NOT via
# ``global``) so a test that does ``permissions._PYOBJC_AVAILABLE = True``
# is observed by the submodule function on its next read.

# ``Optional["object"]`` made the ``_retry_timer.cancel()``
# call below raise ``Object of class `object` has no attribute `cancel``
# because the ``object`` type has no ``cancel`` method.  Switch to ``Any``
# to match the runtime ``threading.Timer`` type (which is fully dynamic
# at the stub level — older Python's threading.Timer is not annotated).
_retry_timer: Any | None = None  # threading.Timer
_retry_count = 0
# cancellation flag set by ``cancel_permission_retry`` so an
# in-flight ``_poll`` callback can short-circuit instead of firing the
# success callback after the user cancelled.
_cancelled: bool = False
# RETRY-LOCK-FIX: previously a dead ``_retry_lock_used = False`` flag
# that was never read or set anywhere. ``schedule_permission_retry`` and
# ``cancel_permission_retry`` were not lock-guarded — two concurrent
# callers could both cancel the old timer, both create new timers, and
# the second assignment orphans the first Timer reference (thread leak).
# RLock (not Lock) because ``schedule_permission_retry`` calls
# ``cancel_permission_retry`` while holding the lock — non-reentrant
# Lock deadlocked; verified by test failure.
_retry_lock = threading.RLock()


# ─── pyobjc availability cache ─────────────────────────────────────────────
# module-level cache for "is pyobjc importable on this host?".
# ``None`` means "not yet probed"; a bool is the cached answer. Probing
# ``from ApplicationServices import AXIsProcessTrustedWithOptions`` on
# every call to ``_check_macos_microphone`` / ``_check_macos_accessibility``
# was an O(import-lookup) cost paid on every permission check, which on
# Linux/CI (where pyobjc isn't installed) added up across hundreds of
# probe calls. Caching drops the steady-state cost to a single attribute
# read. Reset via ``reset_pyobjc_cache()`` (tests / hot-reload).
_PYOBJC_AVAILABLE: bool | None = None

# module-level de-dup flag for the macOS Accessibility TCC
# consent-prompt trigger (``_trigger_macos_accessibility_consent_prompt``).
# ``AXIsProcessTrustedWithOptions(kAXTrustedCheckOptionPrompt=True)`` is
# the only sanctioned programmatic way to surface the native TCC dialog
# on macOS 14+. macOS itself de-duplicates the on-screen dialog, but
# the python-level flag avoids re-paying the CoreFoundation call cost
# on every ``request_keyboard_permission`` invocation (which the hotkey
# adapter can call on every binary error). ``False`` = not yet shown
# this process; flipped to ``True`` after the first prompt trigger.
# Reset only by process restart (matching the OS's own per-process
# TCC dialog de-duplication semantics).
_a11y_prompt_shown: bool = False


# ─── Tray notification i18n keys ───────────────────────────────────────────
# i18n keys for the permission notification. The English
# fallbacks live in ``voice_typer/server/i18n.py`` under
# ``notify.permissions.macos_title`` / ``notify.permissions.macos_body`` /
# ``notify.permissions.macos_body_with_command`` /
# ``notify.permissions.linux_title`` / ``notify.permissions.linux_body``.
# The renderer pushes translations for other locales via the
# ``set_tray_locale`` IPC, which calls ``i18n.register_locale()``.
_PERMISSION_NOTIFY_MACOS_TITLE_KEY = "notify.permissions.macos_title"
_PERMISSION_NOTIFY_MACOS_BODY_KEY = "notify.permissions.macos_body"
_PERMISSION_NOTIFY_MACOS_BODY_CMD_KEY = "notify.permissions.macos_body_with_command"
_PERMISSION_NOTIFY_LINUX_TITLE_KEY = "notify.permissions.linux_title"
_PERMISSION_NOTIFY_LINUX_BODY_KEY = "notify.permissions.linux_body"


# ─── Re-exports ────────────────────────────────────────────────────────────
#
# Constants, enums, and function references are imported here so they're
# accessible as ``permissions.<name>`` (tests read/call/patch these
# directly). The function bodies access mutable state and other functions
# via ``_p.<name>`` (above), NOT via ``global`` — so test mutations on
# the facade propagate.
#
# IMPORTANT: the imports below MUST come after the state declarations
# above. The submodules do ``import voice_typer.server.permissions as _p``
# at module load time, and they read ``_p._PYOBJC_AVAILABLE`` /
# ``_p._retry_lock`` etc. at call time — so those attributes must already
# exist on the facade by the time the submodule functions are called.
# Declaring the state here (before the submodule imports) ensures that
# the partial facade object in ``sys.modules`` has the state attributes
# by the time the submodules start loading.

from voice_typer.server.permissions.accessibility import (  # noqa: E402,F401
    _check_macos_accessibility,
    _open_macos_accessibility_settings,
    _trigger_macos_accessibility_consent_prompt,
)
from voice_typer.server.permissions.checker import (  # noqa: E402,F401
    PERMISSION_RETRY_INTERVAL_SECONDS,
    PERMISSION_RETRY_MAX_ATTEMPTS,
    MicrophonePermissionState,
    PermissionState,
    _is_pyobjc_available,
    cancel_permission_retry,
    check_keyboard_permission,
    check_microphone_permission,
    permission_error_is_permission_denied,
    request_keyboard_permission,
    request_microphone_permission,
    request_microphone_permission_result,
    reset_pyobjc_cache,
    schedule_permission_retry,
    show_permission_notification,
    verify_microphone_accessible,
)
from voice_typer.server.permissions.filesystem import (  # noqa: E402,F401
    _check_linux_input_access,
    _find_linux_install_script,
    _open_linux_pkexec_prompt,
)
from voice_typer.server.permissions.mic import (  # noqa: E402,F401
    _check_linux_microphone,
    _check_macos_microphone,
    _check_windows_microphone,
    _open_macos_microphone_settings,
    _trigger_macos_microphone_consent_prompt,
)
