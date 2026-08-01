"""Tray notification handling — extracted from ``tray.py``.

(Phase 4.5 spaghetti split): the notification concern
was previously inlined on the ``TrayIcon`` class alongside pystray
lifecycle, state queuing, menu building, and Electron window
management. This module owns the four notification-related operations:

  - :func:`notify` — respect the user's notifications-enabled toggle
    before displaying.
  - :func:`notify_safety` — bypass the toggle (used for safety-critical
    messages that the user must see regardless of preference).
  - :func:`do_notify` — the low-level ``icon.notify(message, title)``
    call with exception swallowing (pystray can raise on Win32 toast
    failures; we don't want a notification failure to crash the tray).
  - :func:`on_parakeet_cpu_fallback` — event_bus callback that flips
    ``tray._cpu_fallback_active`` so the next ``_apply_state`` call
    appends a "(CPU fallback)" suffix to the tooltip.

The ``TrayIcon`` class keeps one-line delegate methods for each of
these so:

  - tests that do ``monkeypatch.setattr("voice_typer.server.tray.TrayIcon.X", ...)``
    still work (the symbol remains on the class).
  - source-grep tests like ``tests/test_notifications.py::TestNotifySafetyMethod``
    (which reads ``tray.py`` source for ``def notify_safety``) still pass.
  - the ``event_bus.subscribe(self._on_parakeet_cpu_fallback)`` /
    ``unsubscribe`` pair in ``TrayIcon.start`` / ``TrayIcon.stop`` keeps
    working — bound methods of the same instance + method are equal +
    hash equally, so ``set.discard`` finds the subscribed callback.

Threading: ``notify`` / ``notify_safety`` may be called from any
thread. Before the pystray event loop is live (``tray._icon`` is None),
notifications are appended to ``tray._pending_notifications`` under
``tray._queue_lock`` and flushed by ``TrayIcon.run``. The flush path
calls :func:`do_notify` directly.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray_notifications")


def notify(tray: TrayIcon, title: str, message: str) -> None:
    """Show a notification if notifications are enabled.

        Mirrors the original ``TrayIcon.notify`` contract: when the user
        has disabled notifications (``tray._notifications_enabled is
        False``), the call is a no-op. When the pystray ``Icon`` is not yet
        live (``tray._icon is None``), the (title, message) pair is
        appended to ``tray._pending_notifications`` under the queue lock;
        ``TrayIcon.run`` flushes the queue once the event loop starts.

    (removed) Notification re-display was
        previously stored and accessible via the tray menu; that menu item
        has been removed since the OS manages notification lifetime.
    """
    if not tray._notifications_enabled:
        return
    if tray._icon:
        do_notify(tray, title, message)
    else:
        with tray._queue_lock:
            tray._pending_notifications.append((title, message))


def notify_safety(tray: TrayIcon, title: str, message: str) -> None:
    """Show a notification that bypasses the notification toggle.

    Used for safety-critical messages that the user must see regardless
    of their ``show_notifications`` preference (e.g. crash recovery
    failure, model load error).

    RACE-022: guard ``_pending_notifications`` append with
    ``_queue_lock`` to prevent race with the flush in ``run()``.
    """
    if tray._icon:
        do_notify(tray, title, message)
    else:
        with tray._queue_lock:
            tray._pending_notifications.append((title, message))


def do_notify(tray: TrayIcon, title: str, message: str) -> None:
    """Send a notification through the icon.

    Low-level helper used by both :func:`notify` (when the toggle is
    on) and :func:`notify_safety` (always). Swallows exceptions from
    ``icon.notify`` — pystray can raise on Win32 toast failures
    (``WinError 1402`` stale handle, missing notify-icon area, etc.),
    and a notification failure must not crash the tray.
    """
    try:
        tray._icon.notify(message, title)
    except Exception as e:
        log.warning("[TRAY] Notification failed: %s", e)


def on_parakeet_cpu_fallback(tray: TrayIcon, event: dict) -> None:
    """SK-b: handle ``parakeet_cpu_fallback`` events from parakeet_engine.

    parakeet_engine publishes ``{"type": "parakeet_cpu_fallback",
    "data": {"device": "cpu", "reason": "..."}}`` when GPU transcription
    fails and it falls back to CPU. We mark ``tray._cpu_fallback_active``
    so the next ``_apply_state`` call appends a "(CPU fallback)" suffix
    to the tooltip — the user can see at a glance why transcription is
    slower. The user-facing toast is already published separately as a
    ``"notification"`` event by parakeet_engine, so we do NOT duplicate
    the notification here.

    Defensive: ignores malformed payloads (non-dict, missing ``type``).
    The event_bus subscriber contract is "callback gets a dict"; we
    still validate to be safe against a misbehaving publisher.
    """
    if not isinstance(event, dict):
        return
    if event.get("type") != "parakeet_cpu_fallback":
        return
    tray._cpu_fallback_active = True
    # Re-apply the current state so the tooltip updates immediately
    # with the "(CPU fallback)" suffix. Best-effort — if the icon is
    # None (tray-unavailable path) ``_apply_state`` is a no-op.
    #
    # Also publish the state to the Tauri/Electron side via
    # ``_publish_tray_state`` so the renderer's tray indicator picks
    # up the "(CPU fallback)" tooltip suffix immediately. Mirrors the
    # pattern used by ``_on_elapsed_tick`` (tray.py: ``_apply_state``
    # then ``_publish_tray_state``).
    try:
        tray._apply_state(tray._state, tray._message)
        tray._publish_tray_state()
    except Exception:
        log.debug(
            "[TRAY] could not apply CPU-fallback state to tray icon",
            exc_info=True,
        )
