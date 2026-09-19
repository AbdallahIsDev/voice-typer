"""OS permission detection and onboarding for hotkey backends."""

from __future__ import annotations

# Stdlib imports, also re-exported as module attributes so tests can
import contextlib  # noqa: F401, used by checker.cancel_permission_retry
import logging
import os  # noqa: F401, re-exported; tests patch permissions.os.path
import shutil  # noqa: F401, re-exported; tests patch permissions.shutil.which
import subprocess  # noqa: F401, re-exported; tests patch permissions.subprocess.Popen
import sys  # noqa: F401, re-exported; filesystem submodules use sys.executable
import threading  # noqa: F401, used for _retry_lock; tests patch permissions.threading.Timer
from collections.abc import Callable  # noqa: F401, re-exported for type hints
from typing import Any  # noqa: F401, re-exported for type hints

# Re-imports of platform helpers and constants, these become module
from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: F401
from voice_typer.server.branding import APP_NAME  # noqa: F401
from voice_typer.server.platform_utils import (  # noqa: F401
    is_linux,
    is_macos,
    is_windows,
)

log = logging.getLogger("voice_typer.server.permissions")


# LINUX-UDEV: canonical udev rule string installed by
LINUX_UDEV_RULE = 'KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"'


# These module-level variables are the canonical storage for the

# ``Optional["object"]`` made the ``_retry_timer.cancel()``
_retry_timer: Any | None = None  # threading.Timer
_retry_count = 0
# cancellation flag set by ``cancel_permission_retry`` so an
_cancelled: bool = False
# RETRY-LOCK-FIX: previously a dead ``_retry_lock_used = False`` flag
_retry_lock = threading.RLock()


# module-level cache for "is pyobjc importable on this host?".
_PYOBJC_AVAILABLE: bool | None = None

# module-level de-dup flag for the macOS Accessibility TCC
_a11y_prompt_shown: bool = False


# i18n keys for the permission notification. The English
_PERMISSION_NOTIFY_MACOS_TITLE_KEY = "notify.permissions.macos_title"
_PERMISSION_NOTIFY_MACOS_BODY_KEY = "notify.permissions.macos_body"
_PERMISSION_NOTIFY_MACOS_BODY_CMD_KEY = "notify.permissions.macos_body_with_command"
_PERMISSION_NOTIFY_LINUX_TITLE_KEY = "notify.permissions.linux_title"
_PERMISSION_NOTIFY_LINUX_BODY_KEY = "notify.permissions.linux_body"


# Constants, enums, and function references are imported here so they're

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
