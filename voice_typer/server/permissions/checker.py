"""Core permission checker logic for the ``permissions`` package."""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from enum import Enum

import voice_typer.server.permissions as _p

log = logging.getLogger("voice_typer.server.permissions")


class PermissionState(str, Enum):
    """Three-state permission model."""

    GRANTED = "granted"
    DENIED = "denied"
    UNKNOWN = "unknown"
    ERROR = "error"


class MicrophonePermissionState(str, Enum):
    """three-state model for OS-level microphone permission."""

    GRANTED = "granted"
    DENIED = "denied"
    PROMPT = "prompt"
    UNKNOWN = "unknown"


def check_keyboard_permission() -> PermissionState:
    """Return the current keyboard-monitoring permission state."""
    if _p.is_windows():
        return PermissionState.GRANTED
    if _p.is_macos():
        return _p._check_macos_accessibility()
    if _p.is_linux():
        return _p._check_linux_input_access()
    return PermissionState.UNKNOWN


def permission_error_is_permission_denied(error_message: str) -> bool:
    """Classify a native binary ``ERROR:`` line as a permission issue.

    Returns True for:
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
    """Open the OS permission UI so the user can grant the permission."""
    if _p.is_macos():
        # trigger the native macOS TCC consent dialog ONCE via
        _p._trigger_macos_accessibility_consent_prompt()
        _p._open_macos_accessibility_settings()
    elif _p.is_linux():
        _p._open_linux_pkexec_prompt()
    # Windows: no-op

    if on_granted is not None:
        # Best-effort: schedule a retry to detect when the user grants
        _p.schedule_permission_retry(on_granted)


# Default: retry every 60 seconds, up to 5 times. These match the design
PERMISSION_RETRY_INTERVAL_SECONDS = 60.0
PERMISSION_RETRY_MAX_ATTEMPTS = 5


def schedule_permission_retry(
    callback: Callable[[], None],
    interval: float = PERMISSION_RETRY_INTERVAL_SECONDS,
    max_attempts: int = PERMISSION_RETRY_MAX_ATTEMPTS,
) -> None:
    """Schedule a periodic check for permission grant."""
    # RETRY-LOCK-FIX: guard the cancel-and-reschedule sequence so two
    with _p._retry_lock:
        # Cancel any existing retry timer
        _p.cancel_permission_retry()

        _p._retry_count = 0
        _p._cancelled = False

        def _poll() -> None:
            _p._retry_count += 1
            if _p._cancelled:
                # cancelled between scheduling and this poll firing, skip
                return
            state = _p.check_keyboard_permission()
            log.info(
                "[PERMISSION] Retry %d/%d: state=%s",
                _p._retry_count,
                max_attempts,
                state.value,
            )
            if state == PermissionState.GRANTED:
                if _p._cancelled:
                    # cancelled between the state check and the callback fire
                    return
                log.info("[PERMISSION] Permission granted, invoking callback")
                try:
                    callback()
                except Exception:
                    log.exception("[PERMISSION] Retry callback raised")
                return
            if _p._retry_count >= max_attempts:
                log.info(
                    "[PERMISSION] Giving up after %d attempts (will retry on next hotkey failure)",
                    max_attempts,
                )
                return
            # Schedule next poll
            with _p._retry_lock:
                if _p._cancelled:
                    return
                _p._retry_timer = threading.Timer(interval, _poll)
                _p._retry_timer.daemon = True
                _p._retry_timer.start()

        _p._retry_timer = threading.Timer(interval, _poll)
        _p._retry_timer.daemon = True
        _p._retry_timer.start()


def cancel_permission_retry() -> None:
    """Cancel any pending permission retry timer. Safe to call multiple times."""
    with _p._retry_lock:
        _p._cancelled = True
        if _p._retry_timer is not None:
            with contextlib.suppress(Exception):
                _p._retry_timer.cancel()
            _p._retry_timer = None
        _p._retry_count = 0


def _is_pyobjc_available() -> bool:
    """probe whether pyobjc (``ApplicationServices``) is importable."""
    if _p._PYOBJC_AVAILABLE is not None:
        return _p._PYOBJC_AVAILABLE
    try:
        from ApplicationServices import (  # type: ignore[import-not-found]  # noqa: F401
            AXIsProcessTrustedWithOptions,
        )
    except ImportError:
        _p._PYOBJC_AVAILABLE = False
    else:
        _p._PYOBJC_AVAILABLE = True
    return _p._PYOBJC_AVAILABLE


def reset_pyobjc_cache() -> None:
    """clear the cached pyobjc availability flag."""
    _p._PYOBJC_AVAILABLE = None


def check_microphone_permission() -> MicrophonePermissionState:
    """probe the OS-level microphone permission state."""
    try:
        if _p.is_macos():
            return _p._check_macos_microphone()
        if _p.is_windows():
            return _p._check_windows_microphone()
        if _p.is_linux():
            return _p._check_linux_microphone()
        return MicrophonePermissionState.UNKNOWN
    except Exception:
        log.exception("[PERMISSION] check_microphone_permission probe raised")
        return MicrophonePermissionState.UNKNOWN


def verify_microphone_accessible() -> None:
    """pre-flight check that the OS reports microphone permission"""
    state = _p.check_microphone_permission()
    if state == MicrophonePermissionState.DENIED:
        from voice_typer.server.asr_errors import MicrophonePermissionDeniedError

        raise MicrophonePermissionDeniedError(
            "Microphone permission denied by OS",
            state="denied",
        )


def request_microphone_permission(
    on_granted: Callable[[], None] | None = None,
) -> None:
    """request microphone permission from the OS."""
    if _p.is_macos():
        _p._open_macos_microphone_settings()
        _p._trigger_macos_microphone_consent_prompt()
        if on_granted is not None:
            _p.schedule_permission_retry(on_granted)
    elif _p.is_linux():
        log.debug("[PERMISSION] request_microphone_permission is a no-op on Linux")
    elif _p.is_windows():
        log.debug("[PERMISSION] request_microphone_permission is a no-op on Windows")
    else:
        log.warning("[PERMISSION] request_microphone_permission: unknown platform")


def request_microphone_permission_result(
    on_granted: Callable[[], None] | None = None,
) -> dict:
    """without a follow-up ``onboarding_check_permissions`` round-trip."""
    try:
        if _p.is_macos():
            _p._open_macos_microphone_settings()
            _p._trigger_macos_microphone_consent_prompt()
            platform_name = "macos"
            requested = True
            error = None
            instructions = (
                "Open System Settings -> Privacy & Security -> Microphone and "
                f"enable {_p.APP_NAME}. The consent dialog should appear automatically."
            )
        elif _p.is_linux():
            platform_name = "linux"
            requested = False
            error = None
            instructions = (
                "Linux uses PipeWire/PulseAudio permissions managed outside "
                "the app. Ensure your user is in the ``audio`` group "
                "(``sudo usermod -aG audio $USER && sudo systemctl restart pipewire``)."
            )
        elif _p.is_windows():
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
            _p.schedule_permission_retry(on_granted)
    except Exception as exc:
        log.exception("[PERMISSION] request_microphone_permission_result failed")
        platform_name = (
            "macos" if _p.is_macos() else "linux" if _p.is_linux() else "windows" if _p.is_windows() else "unknown"
        )
        requested = False
        error = str(exc)
        instructions = None

    return {
        "requested": requested,
        "platform": platform_name,
        "error": error,
        "instructions": instructions,
    }


def show_permission_notification(tray, error_message: str) -> None:
    """can push translations via the ``set_tray_locale`` IPC. The English"""
    # Local import to avoid a circular dependency at module import time
    from voice_typer.server import i18n

    if _p.is_macos():
        title = i18n.t(_p._PERMISSION_NOTIFY_MACOS_TITLE_KEY, app=_p.APP_NAME)
        # Resolve the runtime bundle id (never hardcoded) and
        from voice_typer.server.server_platform.macos_bundle_id import (
            resolve_host_bundle_id,
            tccutil_reset_command_str,
        )

        bundle_id = resolve_host_bundle_id()
        if bundle_id:
            body = i18n.t(
                _p._PERMISSION_NOTIFY_MACOS_BODY_CMD_KEY,
                app=_p.APP_NAME,
                command=tccutil_reset_command_str("Accessibility", bundle_id),
            )
        else:
            body = i18n.t(_p._PERMISSION_NOTIFY_MACOS_BODY_KEY, app=_p.APP_NAME)
    elif _p.is_linux():
        title = i18n.t(_p._PERMISSION_NOTIFY_LINUX_TITLE_KEY, app=_p.APP_NAME)
        body = i18n.t(_p._PERMISSION_NOTIFY_LINUX_BODY_KEY)
    else:
        # Windows shouldn't reach here, no permission needed. No i18n
        title = _p.APP_NAME
        body = error_message

    log.warning("[PERMISSION] %s: %s (error: %s)", title, body, error_message)

    if tray is not None:
        try:
            tray.notify(title, body)
        except Exception:
            log.exception("[PERMISSION] tray.notify failed")
    # If tray is None, the log.warning above is the only signal, the
