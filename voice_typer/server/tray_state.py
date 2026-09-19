"""Tray state model."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

from voice_typer.server.tray_elapsed_timer import ElapsedTimer
from voice_typer.server.tray_types import AppState

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray")


def set_state(tray: TrayIcon, state: AppState, message: str = "") -> None:
    """Update tray icon state and tooltip."""
    if state == tray._state and message == tray._message:
        return
    prev_state = tray._state
    tray._state = state
    tray._message = message
    # RECORDING ⇄ non-RECORDING and TRANSCRIBING ⇄
    record_or_transcribe_changed = (prev_state in (AppState.RECORDING, AppState.TRANSCRIBING)) != (
        state in (AppState.RECORDING, AppState.TRANSCRIBING)
    )
    if record_or_transcribe_changed:
        tray._menu_cache_valid = False
    if state == AppState.RECORDING and prev_state != AppState.RECORDING:
        tray._recording_started_at = time.monotonic()
        tray._start_elapsed_timer()
    elif state != AppState.RECORDING and prev_state == AppState.RECORDING:
        tray._cancel_elapsed_timer()
        tray._recording_started_at = None
    if tray._icon:
        tray._apply_state(state, message)
    else:
        with tray._queue_lock:
            tray._pending_states.append((state, message))
    tray._publish_tray_state()
    if record_or_transcribe_changed:
        tray._maybe_publish_tray_menu()


def set_microphones(tray: TrayIcon, mics: list[dict] | None) -> None:
    """Cache the mic device list + invalidate the menu cache."""
    tray._microphones = list(mics) if mics else []
    invalidate_menu_cache_locked(tray)
    tray._maybe_publish_tray_menu()


def set_autostart_enabled(tray: TrayIcon, enabled: bool) -> None:
    """Update the cached autostart state."""
    tray._autostart_enabled = enabled
    invalidate_menu_cache_locked(tray)


def set_notifications_enabled(tray: TrayIcon, enabled: bool) -> None:
    """Update the cached notifications state."""
    tray._notifications_enabled = enabled
    invalidate_menu_cache_locked(tray)


def set_hotkey(tray: TrayIcon, hotkey: str) -> None:
    """Update the stored hotkey string for the next menu rebuild."""
    tray._hotkey = hotkey
    invalidate_menu_cache_locked(tray)
    tray._maybe_publish_tray_menu()
    tray._publish_tray_state()


def refresh_config(tray: TrayIcon, config) -> None:
    """Replace the cached Config reference and rebuild the menu."""
    tray._config = config
    tray._hotkey = getattr(config, "hotkey", tray._hotkey) or tray._hotkey
    invalidate_menu_cache_locked(tray)
    tray._maybe_publish_tray_menu()
    tray._publish_tray_state()


# ``tray._menu_lock`` rationale (relocated from ``TrayIcon.__init__``
def invalidate_menu_cache_locked(tray: TrayIcon) -> None:
    """Clear ``_menu_cache_valid`` under ``_menu_lock`` without"""
    with tray._menu_lock:
        tray._menu_cache_valid = False


def format_elapsed(seconds: float) -> str:
    """Format seconds as mm:ss (under 1h) or h:mm:ss (1h+);
    delegates to ElapsedTimer.format_elapsed."""
    return ElapsedTimer.format_elapsed(seconds)


def on_elapsed_tick(tray: TrayIcon) -> None:
    """refresh tooltip with latest elapsed time (1s tick while
    RECORDING). Re-applies state to pystray Icon + publishes to Tauri."""
    if tray._icon is not None:
        tray._apply_state(tray._state, tray._message)
    tray._publish_tray_state()


def set_elapsed_timer_ref(tray: TrayIcon, timer: threading.Thread | None) -> None:
    """Sync self._elapsed_timer with the helper's Timer."""
    tray._elapsed_timer = timer


def start_elapsed_timer(tray: TrayIcon) -> None:
    """Start/restart the 1s elapsed-recording timer (delegates to ElapsedTimer
    helper; no-op if helper missing, backward compat with _FakeTray)."""
    helper = getattr(tray, "_elapsed_timer_helper", None)
    if helper is not None:
        helper.start()


def cancel_elapsed_timer(tray: TrayIcon) -> None:
    """Cancel the elapsed-recording timer if running (idempotent)."""
    helper = getattr(tray, "_elapsed_timer_helper", None)
    if helper is not None:
        helper.cancel()
