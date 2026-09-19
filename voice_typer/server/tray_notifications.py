"""Tray notification queue (RACE-022 lock)."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME
from voice_typer.server.tray_types import is_tauri_sidecar

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray_notifications")


# Notification dedup cache.
_NOTIFY_DEDUP_TTL_SECONDS: float = 5.0
_notify_dedup_cache: dict[tuple[str, str], float] = {}


# pystray Win32 NOTIFYICONDATAW field limits (ctypes WCHAR arrays):
_NOTIFY_MESSAGE_MAX_CHARS = 256
_NOTIFY_TITLE_MAX_CHARS = 64


def _truncate_notification(title: str, message: str) -> tuple[str, str]:
    """Truncate a notification (title, message) to fit the pystray"""
    if len(title) > _NOTIFY_TITLE_MAX_CHARS:
        title = "..." + title[-(_NOTIFY_TITLE_MAX_CHARS - 3) :]
    if len(message) > _NOTIFY_MESSAGE_MAX_CHARS:
        message = "..." + message[-(_NOTIFY_MESSAGE_MAX_CHARS - 3) :]
    return title, message


def _notify_dedup_seen(title: str, message: str) -> bool:
    """Return True if (title, message) was shown within the TTL window."""
    key = (title, message)
    now = time.monotonic()
    seen_at = _notify_dedup_cache.get(key)
    if seen_at is not None and (now - seen_at) < _NOTIFY_DEDUP_TTL_SECONDS:
        return True
    _notify_dedup_cache[key] = now
    return False


def notify(tray: TrayIcon, title: str, message: str) -> None:
    """Show a notification if notifications are enabled."""
    if not tray._notifications_enabled:
        return
    if _notify_dedup_seen(title, message):
        log.debug(
            "[TRAY] Suppressing duplicate notification (title=%r, message=%r) within %ss TTL window",
            title,
            message,
            _NOTIFY_DEDUP_TTL_SECONDS,
        )
        return
    if tray._icon or is_tauri_sidecar():
        # Under Tauri there is no pystray icon, ``do_notify`` detects that
        do_notify(tray, title, message)
    else:
        with tray._queue_lock:
            tray._pending_notifications.append((title, message))


def notify_safety(tray: TrayIcon, title: str, message: str) -> None:
    """RACE-022: guard ``_pending_notifications`` append with"""
    if tray._icon or is_tauri_sidecar():
        do_notify(tray, title, message)
    else:
        with tray._queue_lock:
            tray._pending_notifications.append((title, message))


def do_notify(tray: TrayIcon, title: str, message: str) -> None:
    """Send a notification through the icon."""
    title, message = _truncate_notification(title, message)
    # Tauri sidecar runtime: there is no pystray icon (the native tray is
    if tray._icon is None and is_tauri_sidecar():
        _publish_notification_event(title, message)
        return
    try:
        tray._icon.notify(message, title)
    except Exception as e:
        log.warning("[TRAY] Notification failed: %s", e)


def _publish_notification_event(title: str, message: str) -> None:
    """Publish a ``notification`` event for the Tauri host to render."""
    try:
        from voice_typer.server import event_bus

        event_bus.publish(
            {
                "type": "notification",
                "data": {"title": title, "message": message},
            }
        )
    except Exception as e:
        log.warning("[TRAY] Notification event publish failed: %s", e)


def on_parakeet_cpu_fallback(tray: TrayIcon, event: dict) -> None:
    """Handle ``parakeet_cpu_fallback`` events from parakeet_engine."""
    if not isinstance(event, dict):
        return
    if event.get("type") != "parakeet_cpu_fallback":
        return
    tray._cpu_fallback_active = True
    # Re-apply the current state so the tooltip updates immediately
    try:
        tray._apply_state(tray._state, tray._message)
        tray._publish_tray_state()
    except Exception:
        log.debug(
            "[TRAY] could not apply CPU-fallback state to tray icon",
            exc_info=True,
        )


_GPU_CPU_FALLBACK_MESSAGE = (
    "GPU transcription failed, switching to CPU. The next transcription may take up to a minute."
)


def on_gpu_cpu_fallback(tray: TrayIcon, event: dict) -> None:
    """``parakeet_cpu_fallback``) when GPU transcription fails and it tears"""
    if not isinstance(event, dict):
        return
    if event.get("type") != "gpu_cpu_fallback":
        return
    tray._cpu_fallback_active = True
    notify(tray, APP_NAME, _GPU_CPU_FALLBACK_MESSAGE)
    try:
        tray._apply_state(tray._state, tray._message)
        tray._publish_tray_state()
    except Exception:
        log.debug(
            "[TRAY] could not apply GPU-fallback state to tray icon",
            exc_info=True,
        )
