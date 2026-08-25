"""Tray state mutations — extracted from ``tray.py``.

Owns the state-machine + cached-field setters that used to live on
the ``TrayIcon`` class:

  - :func:`set_state` — the tray state machine: no-op short-circuit,
    menu-cache invalidation on RECORDING/TRANSCRIBING membership
    flips, elapsed-timer start/stop, pystray apply vs pending-queue,
    Tauri publish, conditional menu push.
  - :func:`set_microphones` / :func:`set_autostart_enabled` /
    :func:`set_notifications_enabled` / :func:`set_hotkey` /
    :func:`refresh_config` — cached-field setters that invalidate the
    menu cache (lazily) and re-publish where needed.
  - :func:`invalidate_menu_cache_locked` — the LAZY cache invalidation
    under ``tray._menu_lock``.
  - elapsed-recording glue: :func:`format_elapsed`,
    :func:`on_elapsed_tick`, :func:`set_elapsed_timer_ref`,
    :func:`start_elapsed_timer`, :func:`cancel_elapsed_timer`.

The ``TrayIcon`` class keeps one-line delegate methods for each of
these so ``monkeypatch.setattr(TrayIcon.X, ...)`` (both class- and
instance-level) and bound-method identity keep working unchanged.

Logs go through the ``voice_typer.server.tray`` logger so log records
keep their pre-split attribution.
"""

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
    """Update tray icon state and tooltip.

    Short-circuit at the top — when ``state`` AND
    ``message`` are both unchanged, every downstream unit of work
    (menu-cache invalidation, elapsed-timer start/stop, icon
    redraw, event emit, menu push) is skipped. Callers that
    re-issue the same state (IPC reconnect replay,
    ``refresh_config`` with no actual change, ``_on_parakeet_cpu_fallback``
    re-fires) pay only a tuple-equality check.

    only invalidate the menu cache on TRANSCRIBING ⇄
    non-TRANSCRIBING (Force Cancel visibility flips); RECORDING ⇄
    IDLE only changes the icon. : RECORDING ⇄ IDLE start/stop
    the elapsed timer (: monotonic clock). ADR-0020 §6.5: push
    icon+tooltip to Tauri; on TRANSCRIBING change also push the menu.
    """
    if state == tray._state and message == tray._message:
        return
    prev_state = tray._state
    tray._state = state
    tray._message = message
    # RECORDING ⇄ non-RECORDING and TRANSCRIBING ⇄
    # non-TRANSCRIBING both invalidate the menu cache + push the
    # menu (the "Stop Dictation" label flips on RECORDING
    # enter/exit, "Force Cancel" appears on TRANSCRIBING
    # enter/exit). Transitions INSIDE the {RECORDING, TRANSCRIBING}
    # membership set (RECORDING → TRANSCRIBING) change nothing
    # label-visible, so no invalidation / publish fires.
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
    """Cache the mic device list + invalidate the menu cache.

    None/empty normalized to []. ADR-0020 §6.5: push to Tauri.

    Uses ``invalidate_menu_cache_locked`` (not the eager
    ``invalidate_menu_cache``) so the cache-validity flag is cleared
    under ``_menu_lock`` without forcing a pystray ``_update_menu``
    call — the Tauri publish path doesn't need the Win32 menu handle
    rebuilt, and on pystray the next right-click rebuilds lazily.
    """
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
# beside its main consumer below):
#
# Protects ``_cached_menu`` / ``_menu_cache_valid`` /
# ``_microphones`` (read+written by ``build_menu_for_tray`` on the
# pystray thread and by ``invalidate_menu_cache`` from background
# threads). On Windows, ``pystray.Icon._update_menu()`` calls
# ``DestroyMenu`` / ``CreatePopupMenu`` — not guaranteed
# thread-safe — so the lock serializes the rebuild.
#
# RLock (not Lock): ``invalidate_menu_cache`` acquires this lock
# and THEN calls ``tray._icon._update_menu()``. pystray's
# ``_update_menu`` iterates the icon's menu, and the menu was
# created as ``pystray.Menu(self._build_menu)`` — a single
# callable. pystray's ``Menu.items`` property INVOKES that
# callable when the menu is iterated, so ``_update_menu()``
# synchronously re-enters ``build_menu_for_tray`` on the SAME
# thread, which acquires ``_menu_lock`` again. With a plain
# ``Lock`` that is a self-deadlock: the dispatch-pool worker
# thread hangs forever holding the lock, and repeated
# ``set_tray_locale`` calls wedge all workers, so every IPC
# command (get_config etc.) times out at 15s while inline
# heartbeats keep the connection alive. An RLock lets the same
# thread re-enter while still serializing against OTHER threads
# (concurrent right-click builds on the pystray loop thread),
# preserving the FR-22 cross-thread guarantee. Mirrors the
# reentrancy rationale of ``_icon_lock`` in ``tray_publish``.
def invalidate_menu_cache_locked(tray: TrayIcon) -> None:
    """Clear ``_menu_cache_valid`` under ``_menu_lock`` without
    touching pystray.

    LAZY variant: the lazy setters (``set_microphones``,
    ``set_autostart_enabled``, ``set_notifications_enabled``,
    ``set_hotkey``, ``refresh_config``) mutate cached state and need
    to flag the menu cache as stale so the next right-click rebuilds.
    They previously wrote ``self._menu_cache_valid = False`` directly
    WITHOUT holding ``_menu_lock`` — racing a concurrent
    ``build_menu_for_tray`` (pystray right-click on the icon's loop
    thread) that had already observed the (stale) True flag and
    returned the cached tuple. The next right-click then rebuilt
    correctly, leaving a one-click staleness window.

    Holding the lock when clearing the flag closes that window: a
    concurrent ``build_menu_for_tray`` either finishes before we
    acquire the lock (and the NEXT build sees False → rebuilds) or
    waits until we release (and then sees False → rebuilds). The
    flag-clear is now happens-before the next cache check.

    This does NOT call ``_icon._update_menu()`` — the eager variant
    is reserved for explicit refresh actions because the Win32
    DestroyMenu/CreatePopupMenu round-trip is unnecessary when the
    Tauri host owns the native tray (``self._icon is None``) and
    the next pystray right-click rebuilds lazily anyway.
    """
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
    helper; no-op if helper missing — backward compat with _FakeTray)."""
    helper = getattr(tray, "_elapsed_timer_helper", None)
    if helper is not None:
        helper.start()


def cancel_elapsed_timer(tray: TrayIcon) -> None:
    """Cancel the elapsed-recording timer if running (idempotent)."""
    helper = getattr(tray, "_elapsed_timer_helper", None)
    if helper is not None:
        helper.cancel()
