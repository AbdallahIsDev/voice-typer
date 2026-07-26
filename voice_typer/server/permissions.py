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

The udev rule installed by ``scripts/linux/install_permissions.py`` is::

    KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"

(PVT-059/LINUX-UDEV: the previous onboarding instructions referenced
``event*`` + ``MODE="0640"`` which (a) over-matches non-keyboard event
devices and (b) grants no group-write access. The correct pattern is
``event[0-9]*`` with ``MODE="0660"`` — group-readable AND group-writable
so the ``input`` group can both read events and (rarely needed) write
LED state. See :data:`LINUX_UDEV_RULE` for the canonical string.)

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
      unsupported platform). This is the "soft unknown" — the probe ran
      successfully but the answer is indeterminate.
    - ``ERROR``: PVT-059 — the probe itself failed unexpectedly (raised
      an exception). Distinct from ``UNKNOWN`` so the renderer can show
      "Permission probe failed — click to retry" instead of the
      misleading "No extra permission needed" (which it used to render
      for any state with ``needed=False``).
    """

    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"
    ERROR = "error"


class MicrophonePermissionState(str, Enum):
    """PVT-061 — three-state model for OS-level microphone permission.

    - ``GRANTED``: the OS reports we have microphone access (or no
      permission is needed on this platform / Linux).
    - ``DENIED``: the OS reports we don't have microphone access.
    - ``PROMPT``: the OS will prompt the user on first access (macOS
      ``AVAuthorizationStatusNotDetermined``; Windows default state).
    - ``UNKNOWN``: we can't tell (e.g. pyobjc missing on macOS, or an
      unsupported platform).
    """

    GRANTED = "granted"
    DENIED = "denied"
    PROMPT = "prompt"
    UNKNOWN = "unknown"


# PVT-059/LINUX-UDEV: canonical udev rule string installed by
# ``scripts/linux/install_permissions.py``. Exposed as a module constant so
# the onboarding instructions (in :mod:`voice_typer.server.onboarding`) and
# the pkexec installer agree on the exact pattern. The previous onboarding
# text referenced ``event*`` (over-matches) + ``MODE="0640"`` (no
# group-write). The correct pattern is ``event[0-9]*`` + ``MODE="0660"``.
LINUX_UDEV_RULE = 'KERNEL=="event[0-9]*", SUBSYSTEM=="input", GROUP="input", MODE="0660"'


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
      terminal-based prompt. PVT-057: this is the zero-command path for
      AppImage users — the OS shows a polkit GUI prompt, the user types
      their password once, and the install script runs as root. The
      onboarding instructions should mention that clicking "Grant
      permission" triggers ``pkexec install_permissions.py``.
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


def request_keyboard_permission_result(
    on_granted: Callable[[], None] | None = None,
) -> dict:
    """PVT-057 — IPC-friendly wrapper around :func:`request_keyboard_permission`.

    The onboarding renderer calls this via the
    ``onboarding_request_keyboard_permission`` IPC handler when the user
    clicks "Grant permission" in the Permissions step. Returns a result
    dict so the renderer can surface success/failure (e.g. "pkexec not
    found — open a terminal and run sudo …") without a follow-up
    ``onboarding_check_permissions`` round-trip.

    Returns
    -------
    dict
        ``{"requested": bool, "platform": str, "error": str | None,
        "instructions": str | None}`` where:

        - ``requested``: True if the OS permission UI was launched
          (macOS) or the pkexec/gksu/kdesu helper was spawned (Linux).
          False on Windows (no-op) or unknown platforms.
        - ``platform``: ``"windows"`` / ``"macos"`` / ``"linux"`` /
          ``"unknown"``.
        - ``error``: ``None`` on success, or a short string explaining
          why the request couldn't be issued (e.g. "pkexec not found").
        - ``instructions``: optional human-readable next-step hint
          (e.g. "Run: sudo python3 …/install_permissions.py").
    """
    try:
        if is_macos():
            _open_macos_accessibility_settings()
            platform_name = "macos"
            requested = True
            error = None
            instructions = None
        elif is_linux():
            # _open_linux_pkexec_prompt logs an error if no GUI sudo
            # helper is available — detect that case by checking
            # shutil.which ourselves so we can return a useful error.
            install_script = _find_linux_install_script()
            if install_script is None:
                platform_name = "linux"
                requested = False
                error = "install_permissions.py not found"
                instructions = "Reinstall Voice Typer or run scripts/linux/install_permissions.py as root manually."
            elif not (shutil.which("pkexec") or shutil.which("gksu") or shutil.which("kdesu")):
                platform_name = "linux"
                requested = False
                error = "No GUI sudo helper found (pkexec/gksu/kdesu)"
                instructions = f"Open a terminal and run: sudo {sys.executable} {install_script}"
            else:
                _open_linux_pkexec_prompt()
                platform_name = "linux"
                requested = True
                error = None
                instructions = (
                    "A polkit password prompt should appear. After granting, "
                    "log out and back in for the group change to take effect."
                )
        elif is_windows():
            platform_name = "windows"
            requested = False  # no-op — no permission needed
            error = None
            instructions = None
        else:
            platform_name = "unknown"
            requested = False
            error = "Unsupported platform"
            instructions = None

        if on_granted is not None and requested:
            schedule_permission_retry(on_granted)
    except Exception as exc:
        log.exception("[PERMISSION] request_keyboard_permission_result failed")
        platform_name = "macos" if is_macos() else "linux" if is_linux() else "windows" if is_windows() else "unknown"
        requested = False
        error = str(exc)
        instructions = None

    return {
        "requested": requested,
        "platform": platform_name,
        "error": error,
        "instructions": instructions,
    }


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
# DE-32: cancellation flag set by ``cancel_permission_retry`` so an
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
    global _retry_timer, _retry_count, _cancelled

    # RETRY-LOCK-FIX: guard the cancel-and-reschedule sequence so two
    # concurrent callers cannot both create orphaned Timer threads.
    with _retry_lock:
        # Cancel any existing retry timer
        cancel_permission_retry()

        _retry_count = 0
        _cancelled = False

        def _poll() -> None:
            global _retry_count, _cancelled
            _retry_count += 1
            if _cancelled:
                # DE-32: cancelled between scheduling and this poll firing — skip
                return
            state = check_keyboard_permission()
            log.info(
                "[PERMISSION] Retry %d/%d: state=%s",
                _retry_count,
                max_attempts,
                state.value,
            )
            if state == PermissionState.GRANTED:
                if _cancelled:
                    # DE-32: cancelled between the state check and the callback fire
                    return
                log.info("[PERMISSION] Permission granted — invoking callback")
                try:
                    callback()
                except Exception:
                    log.exception("[PERMISSION] Retry callback raised")
                return
            if _retry_count >= max_attempts:
                log.info(
                    "[PERMISSION] Giving up after %d attempts (will retry on next hotkey failure)",
                    max_attempts,
                )
                return
            # Schedule next poll
            global _retry_timer
            with _retry_lock:
                if _cancelled:
                    return
                _retry_timer = threading.Timer(interval, _poll)
                _retry_timer.daemon = True
                _retry_timer.start()

        _retry_timer = threading.Timer(interval, _poll)
        _retry_timer.daemon = True
        _retry_timer.start()


def cancel_permission_retry() -> None:
    """Cancel any pending permission retry timer. Safe to call multiple times."""
    global _retry_timer, _retry_count, _cancelled
    with _retry_lock:
        _cancelled = True
        if _retry_timer is not None:
            with contextlib.suppress(Exception):
                _retry_timer.cancel()
            _retry_timer = None
        _retry_count = 0


# ─── Onboarding permission payload (PVT-058 / PVT-059) ────────────────────


def permission_probe_error_payload(error_message: str | None = None) -> dict:
    """PVT-059 — renderer-friendly error envelope for a failed permission probe.

    The onboarding ``check_permissions`` flow previously fell back to
    ``{"state": "unknown", "needed": False}`` when the underlying probe
    raised an exception, which the renderer rendered as "No extra
    permission needed" — masking the failure. This helper returns the
    corrected envelope so the renderer can distinguish "probe failed"
    (show a retry button) from "no permission needed" (auto-advance).

    Parameters
    ----------
    error_message:
        Optional short string describing the failure (e.g. "pyobjc
        ImportError"). Surfaced in the ``error`` field so the renderer
        can show it in a tooltip; not intended for end-user display.

    Returns
    -------
    dict
        ``{"platform": "unknown", "state": "error", "needed": False,
        "instructions": None, "error": str | None}``. The ``error`` key
        is the only addition beyond the four-key shape returned by
        :func:`check_permissions_payload` — the renderer can ignore it
        if it doesn't recognize it (backwards-compatible).
    """
    return {
        "platform": "unknown",
        "state": PermissionState.ERROR.value,
        "needed": False,
        "instructions": None,
        "error": error_message,
    }


def check_permissions_payload() -> dict:
    """PVT-058 — re-probe OS permissions and return the renderer-friendly dict.

    This is the canonical entry point for the ``onboarding_check_permissions``
    and ``onboarding_recheck_permission`` IPC handlers. It returns a dict
    matching :meth:`OnboardingController.check_permissions`'s shape so the
    renderer doesn't need to know which entry point produced it.

    The dict shape is::

        {
            "platform": "windows" | "macos" | "linux" | "unknown",
            "state": "granted" | "denied" | "unknown" | "error",
            "needed": bool,
            "instructions": None | {
                "title": str,
                "steps": list[str],
                "commands": list[str] | None,
            },
            "microphone": "granted" | "denied" | "prompt" | "unknown",
        }

    PVT-059: if the probe itself raises an unexpected exception, this
    returns :func:`permission_probe_error_payload` instead of letting the
    exception propagate — so the IPC handler's generic ``except`` clause
    never triggers the misleading "No extra permission needed" fallback.

    PVT-061: also probes the OS-level microphone permission (macOS
    AVCaptureDevice / Windows MediaFoundation) and returns it under the
    ``microphone`` key. The renderer can use this to gate the Microphone
    step (e.g. show "Open System Settings → Microphone" if denied).
    """
    try:
        state = check_keyboard_permission()
    except Exception as exc:
        # Defensive — check_keyboard_permission already catches
        # exceptions internally, but a future probe might not.
        log.exception("[PERMISSION] check_permissions_payload: keyboard probe raised")
        payload = permission_probe_error_payload(str(exc))
        payload["microphone"] = MicrophonePermissionState.UNKNOWN.value
        return payload

    if is_windows():
        platform_name = "windows"
        instructions = None
        needed = False
    elif is_macos():
        platform_name = "macos"
        needed = state != PermissionState.GRANTED
        instructions = (
            {
                "title": "Accessibility Permission Required",
                "steps": [
                    "Open System Settings → Privacy & Security → Accessibility",
                    "Add Voice Typer (and its key-listener helper) to the list",
                    "Toggle the switch ON for Voice Typer",
                ],
                "commands": None,
            }
            if needed
            else None
        )
    elif is_linux():
        platform_name = "linux"
        needed = state != PermissionState.GRANTED
        # PVT-059/LINUX-UDEV: use the corrected LINUX_UDEV_RULE constant
        # (event[0-9]* + MODE="0660") instead of the old incorrect
        # event* + MODE="0640" pattern.
        instructions = (
            {
                "title": "Input Group + udev Rule Required",
                "steps": [
                    "Add yourself to the 'input' group",
                    "Install the udev rule granting group-read on /dev/input/event[0-9]*",
                    "Log out and back in (or reboot) for the group change to take effect",
                ],
                "commands": [
                    "sudo usermod -aG input $USER",
                    "# udev rule (installed by scripts/linux/install_permissions.py):",
                    f"# {LINUX_UDEV_RULE}",
                    "# Click 'Grant permission' to trigger pkexec install_permissions.py",
                ],
            }
            if needed
            else None
        )
    else:
        platform_name = "unknown"
        instructions = None
        needed = False

    # PVT-061: probe microphone permission alongside keyboard permission.
    try:
        mic_state = check_microphone_permission()
    except Exception:
        log.exception("[PERMISSION] check_permissions_payload: microphone probe raised")
        mic_state = MicrophonePermissionState.UNKNOWN

    return {
        "platform": platform_name,
        "state": state.value,
        "needed": needed,
        "instructions": instructions,
        "microphone": mic_state.value,
    }


# ─── Native listener probe (PVT-008) ──────────────────────────────────────


def probe_native_listener(
    hotkey_str: str,
    timeout_seconds: float = 3.0,
) -> dict:
    """PVT-008 — briefly start the native key listener and report if it
    captures any key event.

    The onboarding "Test hotkey" button previously gave a false-positive
    success because it only probed the renderer's ``keydown`` handler —
    which fires even when the native backend can't see the key (e.g.
    macOS Accessibility denied, or Linux ``input`` group missing). This
    function probes the *actual native backend* by:

    1. Creating a native backend for ``hotkey_str`` via
       :func:`voice_typer.server.native_hotkeys.create_native_backend`.
    2. Starting it with a callback that sets a ``captured`` flag.
    3. Waiting up to ``timeout_seconds`` for the flag to flip.
    4. Stopping the backend cleanly (SIGTERM + reap).

    If the backend emits an ``ERROR:`` line (e.g. "Accessibility
    permission required"), the probe returns ``{"captured": False,
    "error": <message>}`` so the renderer can show the real reason
    instead of "✓ Test passed".

    Parameters
    ----------
    hotkey_str:
        The hotkey spec to register (e.g. ``"<caps_lock>"``). The user
        should press this key during the probe window.
    timeout_seconds:
        How long to wait for a key event before declaring failure.
        Default 3 s — long enough for a human to press the key, short
        enough not to stall the wizard.

    Returns
    -------
    dict
        ``{"captured": bool, "error": str | None, "binary_available": bool,
        "platform": str}``. The renderer uses ``captured`` to set the
        success/failure UI, ``error`` to explain failures, and
        ``binary_available`` to distinguish "binary missing" (install
        issue) from "binary present but permission denied" (OS issue).
    """
    platform_name = "macos" if is_macos() else "windows" if is_windows() else "linux" if is_linux() else "unknown"
    try:
        from voice_typer.server.native_hotkeys import (
            create_native_backend,
            is_native_backend_available,
        )
    except Exception as exc:
        log.exception("[PERMISSION] probe_native_listener: native_hotkeys import failed")
        return {
            "captured": False,
            "error": f"native_hotkeys module unavailable: {exc}",
            "binary_available": False,
            "platform": platform_name,
        }

    if not is_native_backend_available():
        return {
            "captured": False,
            "error": "Native key-listener binary not found or checksum mismatch.",
            "binary_available": False,
            "platform": platform_name,
        }

    backend = create_native_backend(hotkey_str)
    if backend is None:
        return {
            "captured": False,
            "error": f"Unsupported platform for native backend: {platform_name}",
            "binary_available": True,
            "platform": platform_name,
        }

    captured_flag = threading.Event()
    error_holder: list[str] = []

    def _on_press() -> None:
        captured_flag.set()

    def _on_error(message: str) -> None:
        error_holder.append(message)
        captured_flag.set()  # unblock the wait so we don't stall

    # Wire the error callback so ERROR: lines (e.g. "Accessibility
    # permission required") are surfaced instead of timing out.
    backend._on_error_callback = _on_error  # type: ignore[attr-defined]

    try:
        backend.start(_on_press)
    except Exception as exc:
        log.warning("[PERMISSION] probe_native_listener: start failed: %s", exc)
        return {
            "captured": False,
            "error": str(exc),
            "binary_available": True,
            "platform": platform_name,
        }

    try:
        captured_flag.wait(timeout=timeout_seconds)
    finally:
        with contextlib.suppress(Exception):
            backend.stop()

    if error_holder:
        return {
            "captured": False,
            "error": error_holder[0],
            "binary_available": True,
            "platform": platform_name,
        }
    if captured_flag.is_set():
        return {
            "captured": True,
            "error": None,
            "binary_available": True,
            "platform": platform_name,
        }
    return {
        "captured": False,
        "error": (f"Timed out after {timeout_seconds:.1f}s — press the hotkey ({hotkey_str}) during the test window."),
        "binary_available": True,
        "platform": platform_name,
    }


# ─── pyobjc availability cache (XV-123) ───────────────────────────────────


# XV-123: module-level cache for "is pyobjc importable on this host?".
# ``None`` means "not yet probed"; a bool is the cached answer. Probing
# ``from ApplicationServices import AXIsProcessTrustedWithOptions`` on
# every call to ``_check_macos_microphone`` / ``_check_macos_accessibility``
# was an O(import-lookup) cost paid on every permission check, which on
# Linux/CI (where pyobjc isn't installed) added up across hundreds of
# probe calls. Caching drops the steady-state cost to a single attribute
# read. Reset via ``reset_pyobjc_cache()`` (tests / hot-reload).
_PYOBJC_AVAILABLE: bool | None = None


def _is_pyobjc_available() -> bool:
    """XV-123 — probe whether pyobjc (``ApplicationServices``) is importable.

    Cached at module level in :data:`_PYOBJC_AVAILABLE` so repeated calls
    (e.g. one per permission check) don't re-pay the import-lookup cost.
    On non-macOS hosts (Linux sandbox, CI, Windows) where pyobjc isn't
    installed, the first call returns ``False`` and subsequent calls are
    O(1). On macOS with pyobjc installed, the first call returns ``True``
    and subsequent calls are O(1).

    Use :func:`reset_pyobjc_cache` to clear the cache (e.g. in tests).
    """
    global _PYOBJC_AVAILABLE
    if _PYOBJC_AVAILABLE is not None:
        return _PYOBJC_AVAILABLE
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]  # noqa: F401
            AXIsProcessTrustedWithOptions,
        )
    except ImportError:
        _PYOBJC_AVAILABLE = False
    else:
        _PYOBJC_AVAILABLE = True
    return _PYOBJC_AVAILABLE


def reset_pyobjc_cache() -> None:
    """XV-123 — clear the cached pyobjc availability flag.

    The next call to :func:`_is_pyobjc_available` will re-probe. Intended
    for tests (which monkeypatch the cache to exercise specific branches)
    and for hot-reload scenarios.
    """
    global _PYOBJC_AVAILABLE
    _PYOBJC_AVAILABLE = None


# ─── Microphone permission probe (PVT-061) ────────────────────────────────


def check_microphone_permission() -> MicrophonePermissionState:
    """PVT-061 — probe the OS-level microphone permission state.

    - **macOS**: uses pyobjc ``AVCaptureDevice.authorizationStatus(for: .audio)``
      to return one of ``GRANTED`` / ``DENIED`` / ``PROMPT`` (the
      ``AVAuthorizationStatusNotDetermined`` case). Returns ``UNKNOWN``
      if pyobjc isn't installed.
    - **Windows**: returns ``GRANTED`` (Windows doesn't gate microphone
      access at the per-app level the same way macOS does — the
      ``MediaFoundation`` device-open will fail at runtime if access is
      blocked, but there's no clean ahead-of-time probe without
      triggering the consent dialog). Future work: use the WinRT
      ``MediaCapture`` API to probe, but that requires the WinRT
      Python bindings which aren't a hard dependency.
    - **Linux**: returns ``GRANTED`` (no standard per-app mic permission
      system — PipeWire/PulseAudio access is controlled by the session
      manager but typically granted by default).
    - **Unsupported platform**: returns ``UNKNOWN``.
    """
    try:
        if is_macos():
            return _check_macos_microphone()
        if is_windows():
            return MicrophonePermissionState.GRANTED
        if is_linux():
            return MicrophonePermissionState.GRANTED
        return MicrophonePermissionState.UNKNOWN
    except Exception:
        log.exception("[PERMISSION] check_microphone_permission probe raised")
        return MicrophonePermissionState.UNKNOWN


def verify_microphone_accessible() -> None:
    """DE-4 — pre-flight check that the OS reports microphone permission
    as granted (or prompt — the OS will show the consent dialog on first
    PortAudio open in that case).

    Raises :class:`MicrophonePermissionDeniedError` (from
    :mod:`voice_typer.server.asr_errors`) when the OS reports the
    permission state as ``DENIED``. The IPC layer ``isinstance``-checks
    this exception type to surface the permission onboarding UI
    instead of a generic error toast.

    Does NOT raise on ``GRANTED`` / ``PROMPT`` / ``UNKNOWN``:
    - ``GRANTED``: nothing to do.
    - ``PROMPT``: the OS will show the consent dialog on first
      PortAudio open — pre-empting would double-prompt.
    - ``UNKNOWN`` (pyobjc missing on macOS, or unsupported platform):
      defer to the PortAudio-open re-classification path in
      :mod:`voice_typer.server.recording.recorder` which inspects the
      actual OSError message at runtime.
    """
    state = check_microphone_permission()
    if state == MicrophonePermissionState.DENIED:
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        raise MicrophonePermissionDeniedError(
            "Microphone permission denied by OS",
            state="denied",
        )


def _check_macos_microphone() -> MicrophonePermissionState:
    """Probe macOS microphone permission via AVFoundation (pyobjc).

    Maps ``AVAuthorizationStatus`` values:

    - ``AVAuthorizationStatusAuthorized`` (2) → ``GRANTED``
    - ``AVAuthorizationStatusDenied`` (1) → ``DENIED``
    - ``AVAuthorizationStatusRestricted`` (3) → ``DENIED`` (parental
      controls block access — functionally denied for the user)
    - ``AVAuthorizationStatusNotDetermined`` (0) → ``PROMPT`` (the OS
      will show the consent dialog on first access)
    """
    global _PYOBJC_AVAILABLE

    # XV-123: short-circuit when pyobjc isn't installed. Avoids paying
    # the ``from AVFoundation import ...`` lookup cost on every probe.
    if not _is_pyobjc_available():
        return MicrophonePermissionState.UNKNOWN

    try:
        from AVFoundation import AVCaptureDevice, AVMediaTypeAudio  # type: ignore[import-not-found]
    except ImportError:
        # XV-123: pyobjc was cached as available but AVFoundation isn't
        # importable (partial pyobjc install). Flip the cache so future
        # probes short-circuit to UNKNOWN without re-attempting the import.
        _PYOBJC_AVAILABLE = False
        return MicrophonePermissionState.UNKNOWN

    try:
        status = AVCaptureDevice.authorizationStatusForMediaType_(AVMediaTypeAudio())
    except Exception:
        log.exception("[PERMISSION] AVCaptureDevice.authorizationStatusForMediaType_ failed")
        return MicrophonePermissionState.UNKNOWN

    # AVAuthorizationStatus enum values (int):
    # 0 = NotDetermined, 1 = Denied, 2 = Authorized, 3 = Restricted
    if status == 2:
        return MicrophonePermissionState.GRANTED
    if status == 0:
        return MicrophonePermissionState.PROMPT
    if status in (1, 3):
        return MicrophonePermissionState.DENIED
    return MicrophonePermissionState.UNKNOWN


# ─── macOS implementation ──────────────────────────────────────────────────


def _check_macos_accessibility() -> PermissionState:
    """Probe macOS Accessibility permission.

    Uses ``AXIsProcessTrustedWithOptions`` via pyobjc if available.
    Falls back to ``UNKNOWN`` if pyobjc isn't installed (we can't probe
    without it).
    """
    global _PYOBJC_AVAILABLE

    # XV-123: short-circuit when pyobjc isn't installed. Avoids paying
    # the ``from ApplicationServices import ...`` lookup cost on every probe.
    if not _is_pyobjc_available():
        return PermissionState.UNKNOWN

    try:
        from ApplicationServices import AXIsProcessTrustedWithOptions
        from CoreFoundation import CFDictionaryCreate

        # AXIsProcessTrustedWithOptions takes an options dict; passing
        # kAXTrustedCheckOptionPrompt=True would pop the OS dialog.
        # We just want to *check*, not prompt, so pass an empty dict.
        options = CFDictionaryCreate(None, [], [], 0, None, None)
        trusted = AXIsProcessTrustedWithOptions(options)
        return PermissionState.GRANTED if trusted else PermissionState.DENIED
    except ImportError:
        # XV-123: pyobjc was cached as available but ApplicationServices /
        # CoreFoundation aren't importable (partial pyobjc install). Flip
        # the cache so future probes short-circuit to UNKNOWN, then return
        # UNKNOWN. The native binary will emit ERROR on first use and the
        # adapter will prompt the user.
        _PYOBJC_AVAILABLE = False
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
    deep_link = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Accessibility"
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
            "[PERMISSION] Failed to open via 'open %s': %s — falling back to prefpane path",
            deep_link,
            exc,
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


def _open_macos_microphone_settings() -> None:
    """Open System Settings -> Privacy & Security -> Microphone.

    Mirrors :func:`_open_macos_accessibility_settings` but targets the
    Microphone pane via the ``Privacy_Microphone`` deep-link marker.
    Falls back to opening the Security & Privacy prefpane directly
    (macOS 12 and earlier).
    """
    deep_link = "x-apple.systempreferences:com.apple.settings.PrivacySecurity.extension?Privacy_Microphone"
    try:
        subprocess.Popen(
            ["open", deep_link],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log.info("[PERMISSION] Opened macOS Microphone settings via URL scheme")
        return
    except OSError as exc:
        log.warning(
            "[PERMISSION] Failed to open via 'open %s': %s - falling back to prefpane path",
            deep_link,
            exc,
        )

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

    log.error("[PERMISSION] Could not open macOS Microphone settings")


def _trigger_macos_microphone_consent_prompt() -> None:
    """Actively trigger the macOS OS consent dialog for microphone access.

    Uses AVFoundation's
    ``AVCaptureDevice.requestAccessForMediaType:completionHandler:``
    to programmatically request microphone access. On a machine without
    pyobjc / AVFoundation (e.g. dev sandbox, Linux container), this is
    a silent no-op - the OS will instead prompt on the first
    PortAudio device open.
    """
    import sys as _sys

    av = _sys.modules.get("AVFoundation")
    if av is None:
        try:
            import AVFoundation as av  # type: ignore[import-not-found]
        except ImportError:
            log.debug("[PERMISSION] AVFoundation not available - skipping macOS mic consent prompt")
            return

    try:
        media_type_sentinel = av.AVMediaTypeAudio()
        av.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
            media_type_sentinel,
            lambda granted: None,
        )
        log.info("[PERMISSION] Triggered macOS microphone consent prompt via AVFoundation")
    except Exception:
        log.exception("[PERMISSION] Failed to trigger macOS microphone consent prompt")


def request_microphone_permission(
    on_granted: Callable[[], None] | None = None,
) -> None:
    """DE-5 - request microphone permission from the OS.

    Mirrors :func:`request_keyboard_permission` but for the microphone.
    On macOS, opens System Settings -> Privacy -> Microphone AND actively
    triggers the OS consent dialog via AVFoundation. On Windows / Linux,
    this is a no-op (Windows doesn't need permission; Linux uses
    PipeWire/PulseAudio permissions managed outside the app).

    Parameters
    ----------
    on_granted:
        Optional callback invoked when the OS reports the permission
        was granted (observed via the periodic
        :func:`schedule_permission_retry` poller). The callback is
        responsible for restarting the native backend / recorder.
    """
    if is_macos():
        _open_macos_microphone_settings()
        _trigger_macos_microphone_consent_prompt()
        if on_granted is not None:
            schedule_permission_retry(on_granted)
    elif is_linux():
        log.debug("[PERMISSION] request_microphone_permission is a no-op on Linux")
    elif is_windows():
        log.debug("[PERMISSION] request_microphone_permission is a no-op on Windows")
    else:
        log.warning("[PERMISSION] request_microphone_permission: unknown platform")


def request_microphone_permission_result(
    on_granted: Callable[[], None] | None = None,
) -> dict:
    """DE-5 - IPC-friendly wrapper around :func:`request_microphone_permission`.

    Mirrors :func:`request_keyboard_permission_result` but for the
    microphone. Returns a result dict so the renderer can surface
    success/failure without a follow-up ``onboarding_check_permissions``
    round-trip.

    Returns
    -------
    dict
        ``{"requested": bool, "platform": str, "error": str | None,
        "instructions": str | None}`` - see
        :func:`request_keyboard_permission_result` for field semantics.
    """
    try:
        if is_macos():
            _open_macos_microphone_settings()
            _trigger_macos_microphone_consent_prompt()
            platform_name = "macos"
            requested = True
            error = None
            instructions = (
                "Open System Settings -> Privacy & Security -> Microphone and "
                "enable Voice Typer. The consent dialog should appear automatically."
            )
        elif is_linux():
            platform_name = "linux"
            requested = False
            error = None
            instructions = (
                "Linux uses PipeWire/PulseAudio permissions managed outside "
                "the app. Ensure your user is in the ``audio`` group "
                "(``sudo usermod -aG audio $USER && sudo systemctl restart pipewire``)."
            )
        elif is_windows():
            platform_name = "windows"
            requested = False
            error = None
            instructions = (
                "Windows will prompt for microphone access on first use. "
                "Open Windows Settings -> Privacy -> Microphone if you need to reset."
            )
        else:
            platform_name = "unknown"
            requested = False
            error = "Unsupported platform"
            instructions = None

        if on_granted is not None and requested:
            schedule_permission_retry(on_granted)
    except Exception as exc:
        log.exception("[PERMISSION] request_microphone_permission_result failed")
        platform_name = "macos" if is_macos() else "linux" if is_linux() else "windows" if is_windows() else "unknown"
        requested = False
        error = str(exc)
        instructions = None

    return {
        "requested": requested,
        "platform": platform_name,
        "error": error,
        "instructions": instructions,
    }


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
        "[PERMISSION] No GUI sudo helper found (pkexec/gksu/kdesu). Please run: sudo %s %s",
        sys.executable,
        install_script,
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


# PVT-011-i18n: i18n keys for the permission notification. The English
# fallbacks live in ``voice_typer/server/i18n.py`` under
# ``notify.permissions.macos_title`` / ``notify.permissions.macos_body`` /
# ``notify.permissions.linux_title`` / ``notify.permissions.linux_body``.
# The renderer pushes translations for other locales via the
# ``set_tray_locale`` IPC, which calls ``i18n.register_locale()``.
_PERMISSION_NOTIFY_MACOS_TITLE_KEY = "notify.permissions.macos_title"
_PERMISSION_NOTIFY_MACOS_BODY_KEY = "notify.permissions.macos_body"
_PERMISSION_NOTIFY_LINUX_TITLE_KEY = "notify.permissions.linux_title"
_PERMISSION_NOTIFY_LINUX_BODY_KEY = "notify.permissions.linux_body"


def show_permission_notification(tray, error_message: str) -> None:
    """Show a tray notification about the permission issue.

    ``tray`` is the app's tray object (must have a ``notify(title, body)``
    method). If ``tray`` is None, the notification is only logged.

    PVT-011-i18n: the title/body strings are now resolved through
    :mod:`voice_typer.server.i18n` (via :func:`i18n.t`) so the renderer
    can push translations via the ``set_tray_locale`` IPC. The English
    fallback (registered at i18n import time) matches the previous
    hardcoded strings verbatim, so source-level regression tests that
    grep for the English text continue to find the substring.
    """
    # Local import to avoid a circular dependency at module import time
    # (i18n imports branding, branding is imported by this module).
    from voice_typer.server import i18n

    if is_macos():
        title = i18n.t(_PERMISSION_NOTIFY_MACOS_TITLE_KEY, app=APP_NAME)
        body = i18n.t(_PERMISSION_NOTIFY_MACOS_BODY_KEY)
    elif is_linux():
        title = i18n.t(_PERMISSION_NOTIFY_LINUX_TITLE_KEY)
        body = i18n.t(_PERMISSION_NOTIFY_LINUX_BODY_KEY)
    else:
        # Windows shouldn't reach here — no permission needed. No i18n
        # key for this branch (it's an unexpected path); fall back to
        # the raw APP_NAME + error_message so the log is useful.
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
