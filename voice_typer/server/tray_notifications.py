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
  - :func:`on_gpu_cpu_fallback` — same contract for the Whisper-family
    engine's ``gpu_cpu_fallback`` events; additionally shows the
    user-facing toast (the publisher there runs on the transcription
    thread right before a multi-second reload freeze, so the handler
    owns the message).

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

Dedup: :func:`notify` consults a small in-memory cache keyed by
``(title, message)`` with a 5-second TTL — within the TTL window a
second identical (title, message) pair is dropped silently. This
prevents notification storms when a state-change event fires many
times in quick succession (e.g. mic-unplug retries, model-load
restart loops). :func:`notify_safety` BYPASSES the cache — safety-
critical messages (crash recovery failure, model load error) must
always surface even if the same message was just shown.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from voice_typer.server.branding import APP_NAME
from voice_typer.server.tray_types import is_tauri_sidecar

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray_notifications")


# ─────────────────────────────────────────────────────────────────────────────
# Notification dedup cache.
#
# ``notify()`` can fire many times per second when the underlying state
# machine is in a tight retry loop (mic-unplug auto-switch, model-load
# restart, parakeet CPU-fallback re-application). Each call hits the
# platform notification daemon — on Windows this is a Win32 toast that
# queues visually; on macOS it's a UNUserNotificationCenter banner that
# stacks; on Linux it's a dbus ``org.freedesktop.Notifications`` call
# that some desktops (GNOME Shell) rate-limit by silently dropping.
#
# Dedup window: 5 seconds. Long enough to absorb a retry burst, short
# enough that a legitimately-recurring notification (e.g. "transcription
# saved" every 10 s during heavy dictation) still surfaces each time.
#
# The cache is module-level (not on TrayIcon) so all TrayIcon instances
# in the same process share it — there's only ever one tray icon, so
# sharing is the right default. The TTL is enforced lazily on lookup:
# an expired entry is treated as a miss and overwritten with the new
# timestamp. The cache is unbounded in theory but in practice holds at
# most a few dozen entries (one per distinct (title, message) pair the
# app emits), so no LRU eviction is needed.
# -----------------------------------------------------------------------------
_NOTIFY_DEDUP_TTL_SECONDS: float = 5.0
_notify_dedup_cache: dict[tuple[str, str], float] = {}


# pystray Win32 NOTIFYICONDATAW field limits (ctypes WCHAR arrays):
# ``szInfo`` is ``WCHAR * 256`` and ``szInfoTitle`` is ``WCHAR * 64``
# (verified against pystray 0.19 ``_util/win32.py``). Assigning a longer
# string raises ``ValueError: string too long (N, maximum length M)``,
# which ``do_notify`` swallows - so a notification whose message exceeds
# 256 chars is SILENTLY DROPPED on Windows (observed in the wild: the
# "previous session crashed" notification carried a 466-char message).
# macOS/Linux backends have their own limits; truncating at the Windows
# limits is safe for all backends and guarantees the toast is delivered.
_NOTIFY_MESSAGE_MAX_CHARS = 256
_NOTIFY_TITLE_MAX_CHARS = 64


def _truncate_notification(title: str, message: str) -> tuple[str, str]:
    """Truncate a notification (title, message) to fit the pystray
    Win32 NOTIFYICONDATAW struct limits.

    The truncation preserves the tail (``...last``) rather than the
    head so the most informative part of a long diagnostic message
    (e.g. the crash summary that trails the boilerplate) survives. If
    the message must be cut, an ellipsis is prepended so it is visible
    that content was elided.
    """
    if len(title) > _NOTIFY_TITLE_MAX_CHARS:
        title = "..." + title[-(_NOTIFY_TITLE_MAX_CHARS - 3) :]
    if len(message) > _NOTIFY_MESSAGE_MAX_CHARS:
        message = "..." + message[-(_NOTIFY_MESSAGE_MAX_CHARS - 3) :]
    return title, message


def _notify_dedup_seen(title: str, message: str) -> bool:
    """Return True if (title, message) was shown within the TTL window.

    Records the current monotonic timestamp on a miss so the next call
    within the TTL returns True. On a hit, leaves the timestamp
    unchanged (the original emit time, not the last lookup time, drives
    expiry — so a repeated storm of identical notifications stops
    surfacing for the full 5 s after the FIRST one, not after the LAST).
    """
    key = (title, message)
    now = time.monotonic()
    seen_at = _notify_dedup_cache.get(key)
    if seen_at is not None and (now - seen_at) < _NOTIFY_DEDUP_TTL_SECONDS:
        return True
    _notify_dedup_cache[key] = now
    return False


def clear_notify_dedup_cache() -> None:
    """Clear the notification dedup cache.

    Primarily for tests so each test starts with an empty cache.
    Production code should not call this — the TTL is the correct
    invalidation mechanism.
    """
    _notify_dedup_cache.clear()


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

    (dedup) A second identical (title, message) pair within a 5-second
        window is dropped silently to prevent notification storms from
        state-machine retry loops (mic-unplug, model-load restart, etc.).
        :func:`notify_safety` bypasses this cache for safety-critical
        messages.
    """
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
        # Under Tauri there is no pystray icon — ``do_notify`` detects that
        # combination and routes the toast through the host ``notification``
        # event instead of queueing it for a pystray flush that would never
        # come (the Tauri runtime never creates an icon).
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

    (dedup) This path BYPASSES the dedup cache by design — a
        safety-critical message must always surface even if the same
        message was just shown. The assumption is that safety-critical
        events are rare (crash recovery failure, model load error) and
        the cost of a duplicate toast is far lower than the cost of a
        missed one.
    """
    if tray._icon or is_tauri_sidecar():
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

    The message/title are truncated to the pystray Win32
    ``NOTIFYICONDATAW`` limits (256 / 64 WCHARs) BEFORE the call so an
    over-long message doesn't raise ``ValueError: string too long``
    inside ``icon.notify`` and get silently dropped by the
    ``except Exception`` below — the user never saw the toast at all.
    """
    title, message = _truncate_notification(title, message)
    # Tauri sidecar runtime: there is no pystray icon (the native tray is
    # owned by the Rust host — see tray.py start()'s TAURI_SIDECAR gate),
    # so route the toast through the ``notification`` event. The Rust host
    # (src-tauri/src/host_events.rs) listens for it and shows the native
    # toast via tauri-plugin-notification. The payload shape mirrors the
    # ``show_electron_notification`` IPC publisher (system_handlers.py):
    # ``{"title": ..., "message": ...}``.
    if tray._icon is None and is_tauri_sidecar():
        _publish_notification_event(title, message)
        return
    try:
        tray._icon.notify(message, title)
    except Exception as e:
        log.warning("[TRAY] Notification failed: %s", e)


def _publish_notification_event(title: str, message: str) -> None:
    """Publish a ``notification`` event for the Tauri host to render.

    Best-effort: a publish failure is logged and swallowed — a toast must
    never crash the tray (same contract as the pystray path above).
    """
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
    """Handle ``parakeet_cpu_fallback`` events from parakeet_engine.

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


_GPU_CPU_FALLBACK_MESSAGE = (
    "GPU transcription failed — switching to CPU. The next transcription may take up to a minute."
)


def on_gpu_cpu_fallback(tray: TrayIcon, event: dict) -> None:
    """Handle ``gpu_cpu_fallback`` events from transcription_fallback.

    The Whisper-family engine publishes
    ``{"type": "gpu_cpu_fallback", "data": {"device": "cpu",
    "reason": "..."}}`` (same payload shape as the parakeet engine's
    ``parakeet_cpu_fallback``) when GPU transcription fails and it tears
    down + reloads the model on CPU. Unlike the parakeet path — where
    the engine itself publishes the toast as a separate ``notification``
    event — here the tray handler owns the user-facing message, because
    the publisher runs on the transcription thread right before a
    synchronous 5-50s reload freeze.

    Mirrors :func:`on_parakeet_cpu_fallback`: marks
    ``tray._cpu_fallback_active`` so the tooltip gains the
    "(CPU fallback)" suffix, re-applies state, and additionally shows
    the toast through :func:`notify` (respects the notifications toggle
    and dedup — this is informational, not safety-critical: the app
    keeps transcribing).

    Defensive: ignores malformed payloads (non-dict, wrong ``type``).
    """
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
