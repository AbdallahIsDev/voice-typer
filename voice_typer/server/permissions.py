"""OS permission detection and onboarding for hotkey backends.

This module is the single source of truth for "can we monitor the keyboard
on this platform?" and "what should we do if we can't?".

Platform summary
----------------
- **Windows**: ``WH_KEYBOARD_LL`` needs no special permission. The native
  binary works out of the box. ``check_keyboard_permission()`` always
  returns ``GRANTED``.
- **macOS**: ``CGEventTap`` (used by ``macos-key-listener``) requires
  *Accessibility* permission. Without it, the binary emits
  ``ERROR:Accessibility permission required...``. This module detects
  that error, classifies it as a permission issue, and provides a helper
  to deep-link the user to ``System Settings → Privacy & Security →
  Accessibility``.
- **Linux**: reading ``/dev/input/event*`` (evdev) requires the user to
  be in the ``input`` group, plus a udev rule granting group-read access.
  This module detects the "permission denied" error from the binary and,
  for AppImage users, runs ``pkexec`` to invoke
  ``scripts/linux/install_permissions.py`` (which installs the udev rule,
  adds the user to the group, and configures Caps Lock neutralization).

The module is intentionally side-effect-free at import time. All
``request_*`` functions are invoked explicitly by the hotkey adapter when
an error is detected.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable
from enum import Enum
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_linux, is_macos, is_windows

log = logging.getLogger("voice_typer.server.permissions")


# ─── Permission state ──────────────────────────────────────────────────────


class PermissionState(str, Enum):
    """Three-state permission model.

    - ``GRANTED``: the OS reports we have the permission, or no
      permission is needed on this platform (e.g. Windows).
    - ``DENIED``: the OS reports we don't have the permission.
    - ``UNKNOWN``: we can't tell (e.g. macOS without pyobjc, or an
      unsupported platform).
    """

    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"


# ─── Public API ────────────────────────────────────────────────────────────


def check_keyboard_permission() -> PermissionState:
    """Return the current keyboard-monitoring permission state.

    On Windows this always returns ``GRANTED`` (``WH_KEYBOARD_LL`` needs
    no permission). On macOS it probes the Accessibility permission via
    the ``AXIsProcessTrustedWithOptions`` CoreFoundation call (when
    pyobjc is available). On Linux it checks whether the current user is
    in the ``input`` group AND whether at least one ``/dev/input/event*``
    device is readable.
    """
    if is_windows():
        return PermissionState.GRANTED
    if is_macos():
        return _check_macos_accessibility()
    if is_linux():
        return _check_linux_input_access()
    return PermissionState.UNKNOWN


def permission_error_is_permission_denied(error_message: str) -> bool:
    """Classify a native binary ``ERROR:`` line as a permission issue.

    Returns True for:
    - ``Accessibility permission required...`` (macOS)
    - ``Permission denied opening /dev/input/event*...`` (Linux)
    - ``Add yourself to the 'input' group...`` (Linux)
    - ``No keyboard devices found ... Are you in the 'input' group?`` (Linux)

    Returns False for all other errors (binary not found, parse error,
    etc.) — those are handled by the startup fallback chain, not the
    permission onboarding flow.
    """
    if not error_message:
        return False
    lower = error_message.lower()
    return (
        "accessibility" in lower
        or "permission denied" in lower
        or "input' group" in lower
        or "input group" in lower
        or "/dev/input" in lower
    )


def request_keyboard_permission(
    on_granted: Callable[[], None] | None = None,
) -> None:
    """Open the OS permission UI so the user can grant the permission.

    - **macOS**: opens ``System Settings → Privacy & Security →
      Accessibility`` via the ``x-apple.systempreferences:`` scheme,
      with a fallback to the older ``Security.prefPane`` bundle path.
    - **Linux**: invokes ``pkexec`` to run
      ``scripts/linux/install_permissions.py`` (installs udev rule +
      adds user to ``input`` group + configures Caps Lock). If pkexec
      isn't available, falls back to ``gksu`` / ``kdesu`` / a
      terminal-based prompt.
    - **Windows**: no-op (no permission needed).

    The optional ``on_granted`` callback is invoked when the permission
    is detected as granted (best-effort — see ``schedule_permission_retry``
    for the retry mechanism).
    """
    if is_macos():
        _open_macos_accessibility_settings()
    elif is_linux():
        _open_linux_pkexec_prompt()
    # Windows: no-op

    if on_granted is not None:
        # Best-effort: schedule a retry to detect when the user grants
        # permission. The caller may also set up its own retry timer.
        schedule_permission_retry(on_granted)


# ─── Permission retry mechanism ────────────────────────────────────────────

# Default: retry every 60 seconds, up to 5 times. These match the design
# in ADR 0006 Section B.5.
PERMISSION_RETRY_INTERVAL_SECONDS = 60.0
PERMISSION_RETRY_MAX_ATTEMPTS = 5

# TASK-14: ``Optional["object"]`` made the ``_retry_timer.cancel()``
# call below raise ``Object of class `object` has no attribute `cancel```
# because the ``object`` type has no ``cancel`` method.  Switch to ``Any``
# to match the runtime ``threading.Timer`` type (which is fully dynamic
# at the stub level — older Python's threading.Timer is not annotated).
_retry_timer: Any | None = None  # threading.Timer
_retry_count = 0
# RETRY-LOCK-FIX: previously a dead ``_retry_lock_used = False`` flag
# that was never read or set anywhere. ``schedule_permission_retry`` and
# ``cancel_permission_retry`` were not lock-guarded — two concurrent
# callers could both cancel the old timer, both create new timers, and
# the second assignment orphans the first Timer reference (thread leak).
# RLock (not Lock) because ``schedule_permission_retry`` calls
# ``cancel_permission_retry`` while holding the lock — non-reentrant
# Lock deadlocked; verified by test failure.
_retry_lock = threading.RLock()


def schedule_permission_retry(
    callback: Callable[[], None],
    interval: float = PERMISSION_RETRY_INTERVAL_SECONDS,
    max_attempts: int = PERMISSION_RETRY_MAX_ATTEMPTS,
) -> None:
    """Schedule a periodic check for permission grant.

    After the user opens the OS permission UI (via
    ``request_keyboard_permission``), the native backend has already
    failed and won't auto-restart. This function polls
    ``check_keyboard_permission()`` every ``interval`` seconds; when it
    returns ``GRANTED``, the ``callback`` is invoked (the callback is
    responsible for restarting the native backend) and the timer stops.

    After ``max_attempts`` checks, the timer gives up. This prevents
    infinite polling if the user never grants permission.
    """
    global _retry_timer, _retry_count

    # RETRY-LOCK-FIX: guard the cancel-and-reschedule sequence so two
    # concurrent callers cannot both create orphaned Timer threads.
    with _retry_lock:
        # Cancel any existing retry timer
        cancel_permission_retry()

        _retry_count = 0

        def _poll() -> None:
            global _retry_count
            _retry_count += 1
            state = check_keyboard_permission()
            log.info(
                "[PERMISSION] Retry %d/%d: state=%s",
                _retry_count, max_attempts, state.value,
            )
            if state == PermissionState.GRANTED:
                log.info("[PERMISSION] Permission granted — invoking callback")
                try:
                    callback()
                except Exception:
                    log.exception("[PERMISSION] Retry callback raised")
                return
            if _retry_count >= max_attempts:
                log.info(
                    "[PERMISSION] Giving up after %d attempts "
                    "(will retry on next hotkey failure)",
                    max_attempts,
                )
                return
            # Schedule next poll
            global _retry_timer
            with _retry_lock:
                _retry_timer = threading.Timer(interval, _poll)
                _retry_timer.daemon = True
                _retry_timer.start()

        _retry_timer = threading.Timer(interval, _poll)
        _retry_timer.daemon = True
        _retry_timer.start()


def cancel_permission_retry() -> None:
    """Cancel any pending permission retry timer. Safe to call multiple times."""
    global _retry_timer, _retry_count
    with _retry_lock:
        if _retry_timer is not None:
            with contextlib.suppress(Exception):
                _retry_timer.cancel()
            _retry_timer = None
        _retry_count = 0


# ─── macOS implementation ──────────────────────────────────────────────────


def _check_macos_accessibility() -> PermissionState:
    """Probe macOS Accessibility permission.

    Uses ``AXIsProcessTrustedWithOptions`` via pyobjc if available.
    Falls back to ``UNKNOWN`` if pyobjc isn't installed (we can't probe
    without it).
    """
    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import CFDictionaryCreate
        # AXIsProcessTrustedWithOptions takes an options dict; passing
        # kAXTrustedCheckOptionPrompt=True would pop the OS dialog.
        # We just want to *check*, not prompt, so pass an empty dict.
        options = CFDictionaryCreate(
            None, [], [], 0, None, None
        )
        trusted = AXIsProcessTrustedWithOptions(options)
        return PermissionState.GRANTED if trusted else PermissionState.DENIED
    except ImportError:
        # pyobjc not installed — can't probe. The native binary will
        # emit ERROR on first use and the adapter will prompt the user.
        return PermissionState.UNKNOWN
    except Exception:
        log.exception("[PERMISSION] macOS Accessibility check failed")
        return PermissionState.UNKNOWN


def _open_macos_accessibility_settings() -> None:
    """Open System Settings → Privacy & Security → Accessibility.

    Uses the ``x-apple.systempreferences:`` URL scheme (macOS 13+).
    Falls back to opening the Security & Privacy prefpane directly
    (macOS 12 and earlier).
    """
    # Primary: deep-link via URL scheme (macOS Ventura+)
    deep_link = (
        "x-apple.systempreferences:com.apple.settings.PrivacySecurity"
        ".extension?Privacy_Accessibility"
    )
    try:
        subprocess.Popen(
            ["open", deep_link],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("[PERMISSION] Opened macOS Accessibility settings via URL scheme")
        return
    except OSError as exc:
        log.warning(
            "[PERMISSION] Failed to open via 'open %s': %s — "
            "falling back to prefpane path",
            deep_link, exc,
        )

    # Fallback: open the Security & Privacy prefpane directly
    prefpane_paths = [
        "/System/Library/PreferencePanes/Security.prefPane/",
        "/System/Library/PreferencePanes/SecurityAndPrivacy.prefPane/",
    ]
    for path in prefpane_paths:
        if os.path.exists(path):
            try:
                subprocess.Popen(
                    ["open", path],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                log.info("[PERMISSION] Opened prefpane: %s", path)
                return
            except OSError:
                continue

    log.error("[PERMISSION] Could not open macOS Accessibility settings")


# ─── Linux implementation ──────────────────────────────────────────────────


def _check_linux_input_access() -> PermissionState:
    """Check whether the current user can read /dev/input/event* devices.

    Returns ``GRANTED`` if the user is in the ``input`` group AND at
    least one ``/dev/input/event*`` device is readable. Returns
    ``DENIED`` otherwise.
    """
    # Check group membership
    try:
        import grp
        input_group = grp.getgrnam("input")
        username = os.environ.get("USER") or os.environ.get("LOGNAME", "")
        if username and username not in input_group.gr_mem:
            # Also check the current process's supplementary groups
            groups = os.getgroups()
            if input_group.gr_gid not in groups:
                return PermissionState.DENIED
    except (KeyError, OSError):
        # 'input' group doesn't exist on this system — definitely denied
        return PermissionState.DENIED

    # Check that at least one event device is readable
    try:
        import glob
        devices = glob.glob("/dev/input/event*")
        if not devices:
            # No devices at all — can't tell (headless? container?)
            return PermissionState.UNKNOWN
        for dev in devices:
            if os.access(dev, os.R_OK):
                return PermissionState.GRANTED
        return PermissionState.DENIED
    except OSError:
        return PermissionState.UNKNOWN


def _open_linux_pkexec_prompt() -> None:
    """Run install_permissions.py via pkexec to grant keyboard permission.

    For AppImage users (no package manager), this is the zero-command
    path: the OS shows a GUI sudo prompt (polkit), the user types their
    password once, and the install script installs the udev rule + adds
    the user to the ``input`` group + configures Caps Lock.

    Falls back to ``gksu`` / ``kdesu`` / a terminal-based prompt if
    pkexec isn't available.
    """
    # Find the install_permissions.py script
    install_script = _find_linux_install_script()
    if install_script is None:
        log.error(
            "[PERMISSION] install_permissions.py not found — "
            "cannot auto-grant Linux keyboard permission. "
            "Run scripts/linux/install_permissions.py manually as root."
        )
        return

    # Try pkexec first (modern Linux, GUI prompt via polkit)
    if shutil.which("pkexec"):
        try:
            subprocess.Popen(
                ["pkexec", sys.executable, str(install_script)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("[PERMISSION] Launched pkexec to install Linux permissions")
            return
        except OSError as exc:
            log.warning("[PERMISSION] pkexec failed: %s — trying fallbacks", exc)

    # Fallback: gksu (deprecated but still present on some systems)
    if shutil.which("gksu"):
        try:
            subprocess.Popen(
                ["gksu", f"{sys.executable} {install_script}"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("[PERMISSION] Launched gksu to install Linux permissions")
            return
        except OSError:
            pass

    # Fallback: kdesu (KDE)
    if shutil.which("kdesu"):
        try:
            subprocess.Popen(
                ["kdesu", "-t", "--", sys.executable, str(install_script)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            log.info("[PERMISSION] Launched kdesu to install Linux permissions")
            return
        except OSError:
            pass

    # Last resort: tell the user to run it manually in a terminal
    log.error(
        "[PERMISSION] No GUI sudo helper found (pkexec/gksu/kdesu). "
        "Please run: sudo %s %s",
        sys.executable, install_script,
    )


def _find_linux_install_script():
    """Find scripts/linux/install_permissions.py.

    Search order:
    1. Alongside the voice_typer package (dev mode)
    2. In /usr/share/voice-typer/scripts/ (installed package)
    3. Next to sys.executable (PyInstaller bundle)
    """
    from pathlib import Path

    candidates = [
        # Dev mode: voice_typer/server/../../scripts/linux/install_permissions.py
        Path(__file__).resolve().parent.parent.parent / "scripts" / "linux" / "install_permissions.py",
        # Installed package (deb/rpm)
        Path("/usr/share/voice-typer/scripts/install_permissions.py"),
        # PyInstaller bundle
        Path(sys.executable).resolve().parent / "scripts" / "linux" / "install_permissions.py",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


# ─── Tray notification helper ──────────────────────────────────────────────


def show_permission_notification(tray, error_message: str) -> None:
    """Show a tray notification about the permission issue.

    ``tray`` is the app's tray object (must have a ``notify(title, body)``
    method). If ``tray`` is None, the notification is only logged.
    """
    if is_macos():
        title = f"{APP_NAME} needs permission"
        body = (
            "Click to open System Settings → Accessibility. "
            "Add Voice Typer (and its key-listener helper) to the list."
        )
    elif is_linux():
        title = "Voice Typer needs keyboard permission"
        body = (
            "Click to grant access. Your system will ask for your password "
            "to install the keyboard permission (udev rule + input group). "
            "After granting, log out and back in for the change to take effect."
        )
    else:
        # Windows shouldn't reach here — no permission needed
        title = APP_NAME
        body = error_message

    log.warning("[PERMISSION] %s: %s (error: %s)", title, body, error_message)

    if tray is not None:
        try:
            tray.notify(title, body)
        except Exception:
            log.exception("[PERMISSION] tray.notify failed")
    # If tray is None, the log.warning above is the only signal — the
    # caller may also surface this in the UI.
