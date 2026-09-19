"""Tray lifecycle (show/hide/stop)."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray")


def wrap_bg_work(tray: TrayIcon, bg_work: Callable | None) -> Callable | None:
    """ADR-0020 §6.5: wrap bg_work so the initial tray menu is published"""
    if bg_work is None:
        return None

    def _wrapped() -> None:
        try:
            bg_work()
        finally:
            try:
                tray._maybe_publish_tray_menu()
                tray._publish_tray_state()
            except Exception:
                log.debug("[TRAY] post-bg_work tray publish failed", exc_info=True)

    return _wrapped


def subscribe_host_ready_republish(tray: TrayIcon) -> None:
    """The ``tray_menu`` publish is fire-and-forget: ``publish_tray_menu``
    WS subscriber is installed: see sidecar_ws C-WS-1 ordering) turns
    """
    try:
        from voice_typer.server import event_bus as _event_bus

        _event_bus.subscribe(tray._on_host_ready)
    except Exception:
        log.warning(
            "[TRAY] could not subscribe host-ready republish, the Tauri tray "
            "menu may stay at its placeholder until the next state change",
            exc_info=True,
        )


def on_host_ready(tray: TrayIcon, event: dict) -> None:
    """Republish menu + state when the host connection signals ready."""
    if not isinstance(event, dict) or event.get("type") != "ready":
        return
    from voice_typer.server.tray_types import is_tauri_sidecar

    if not is_tauri_sidecar():
        return
    try:
        tray._maybe_publish_tray_menu()
        tray._publish_tray_state()
        log.debug("[TRAY] host ready, tray menu + state re-published")
    except Exception:
        log.debug("[TRAY] host-ready tray republish failed", exc_info=True)


def run(tray: TrayIcon) -> None:
    """Block the main thread with pystray's event loop."""
    if tray._tray_unavailable and tray._icon is None:
        try:
            from voice_typer.server.tray_types import is_tauri_sidecar as _is_sidecar
        except Exception:
            _is_sidecar = lambda: False  # noqa: E731
        if _is_sidecar():
            log.info(
                "[TRAY] TAURI_SIDECAR=1, native tray owned by Rust host; "
                "main thread blocking on Event "
                "(stop() will release, pending queues drained every 60s). "
                "Hotkey + IPC server still active."
            )
        else:
            log.info(
                "[TRAY] Tray unavailable, main thread blocking on Event "
                "(stop() will release, pending queues drained every 60s). "
                "Hotkey + IPC server still active."
            )
        while not tray._run_event.wait(timeout=60):
            tray._drain_pending()
        return

    if tray._icon is None:
        raise RuntimeError("call start() before run()")

    # Flush queued state + notifications.
    with tray._queue_lock:
        for state, msg in tray._pending_states:
            tray._apply_state(state, msg)
        tray._pending_states.clear()
    with tray._queue_lock:
        for title, message in tray._pending_notifications:
            tray._do_notify(title, message)
        tray._pending_notifications.clear()

    log.info("[TRAY] Tray icon created; event loop running (main thread)")
    try:
        tray._icon.run()
    except Exception:
        # pystray can fail at RUNTIME even though ``start()``
        log.warning(
            "[TRAY] Tray event loop failed at runtime - degrading to "
            "tray-unavailable mode. Hotkey, IPC server, and predecessor "
            "window continue to work; tray icon + notifications are "
            "disabled.",
            exc_info=True,
        )
        tray._icon = None
        tray._tray_unavailable = True
        while not tray._run_event.wait(timeout=60):
            tray._drain_pending()


def stop(tray: TrayIcon) -> None:
    """Stop the tray icon and exit the event loop (idempotent)."""
    # Hold the lock across the teardown pair so _apply_state's
    with tray._icon_lock:
        if tray._icon:
            tray._icon.stop()
            tray._icon = None
    tray._cancel_elapsed_timer()
    tray._run_event.set()
    # clear the icon-state cache so a restarted tray
    tray._last_applied_state = None
    # clear the publish dedup cache so a restarted tray
    tray._last_published = None

    try:
        from voice_typer.server import event_bus as _event_bus

        _event_bus.unsubscribe(tray._on_parakeet_cpu_fallback)
    except Exception:
        log.debug("[TRAY] could not unsubscribe parakeet_cpu_fallback", exc_info=True)

    try:
        from voice_typer.server import event_bus as _event_bus

        _event_bus.unsubscribe(tray._on_gpu_cpu_fallback)
    except Exception:
        log.debug("[TRAY] could not unsubscribe gpu_cpu_fallback", exc_info=True)

    try:
        from voice_typer.server import event_bus as _event_bus

        _event_bus.unsubscribe(tray._on_host_ready)
    except Exception:
        log.debug("[TRAY] could not unsubscribe host-ready republish", exc_info=True)

    log.info("[TRAY] Tray icon stopped")
