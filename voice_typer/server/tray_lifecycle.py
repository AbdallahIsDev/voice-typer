"""Tray lifecycle glue — extracted from ``tray.py``.

Owns the non-constructor lifecycle pieces of ``TrayIcon``:

  - :func:`wrap_bg_work` — ADR-0020 §6.5: wrap bg_work so the initial
    tray menu is published to Tauri after background setup.
  - :func:`subscribe_host_ready_republish` / :func:`on_host_ready` —
    Tauri-only listener that replays menu+state on every host
    (re)connect (covers the bg_work-vs-handshake publish race).
  - :func:`run` — block the main thread on pystray's event loop, or
    on ``_run_event`` when the tray is unavailable (60s pending-queue
    drain loop).
  - :func:`stop` — idempotent teardown (icon-lock serialized,
    elapsed-timer cancel, cache clears, event-bus unsubs).

``TrayIcon.start`` + ``TrayIcon._launch_bg_work`` stay physically on
the class in ``tray.py``: their bodies are source-pinned
(``self._bg_thread = threading.Thread`` single-spawn count +
``_launch_bg_work()`` call-site counts in tests/test_platform_and_config.py,
and the daemon-thread rationale in tests/regressions/test_platform_misc.py).

The ``TrayIcon`` class keeps one-line delegate methods for the
functions here so ``monkeypatch.setattr(TrayIcon.X, ...)`` and
bound-method identity (event_bus subscribe/unsubscribe pairs) keep
working unchanged.

Logs go through the ``voice_typer.server.tray`` logger so log records
keep their pre-split attribution.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.tray import TrayIcon

log = logging.getLogger("voice_typer.server.tray")


def wrap_bg_work(tray: TrayIcon, bg_work: Callable | None) -> Callable | None:
    """ADR-0020 §6.5: wrap bg_work so the initial tray menu is published
    to Tauri after background setup. Returns None when bg_work is None
    (preserves ``if self._bg_work_fn:`` guards). try/finally so the
    menu is published even if bg_work raises."""
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
    """Subscribe a listener that re-publishes the tray menu on host (re)connect.

    Idempotent via ``tray._host_ready_republish_subscribed`` — the
    registration now happens at TrayIcon CONSTRUCTION under Tauri
    (``start()`` never runs in ws-mode), and the pystray ``start()``
    path still calls it; the flag makes the second call a no-op so the
    replay listener is never double-registered.

    The ``tray_menu`` publish is fire-and-forget: ``publish_tray_menu``
    emits through ``event_bus`` and only subscribers registered AT THAT
    MOMENT receive it. The sidecar WS subscriber (``sidecar_ws.
    _install_subscriber``) installs per connection, so the one-shot
    publish from ``_wrap_bg_work``'s finally block races the first
    handshake — when bg_work finishes before the host authenticates,
    the event lands on an empty subscriber set, the Rust host keeps
    its placeholder menu forever, and nothing re-publishes.

    Subscribing to the sidecar's ``ready`` event (published AFTER the
    WS subscriber is installed — see sidecar_ws C-WS-1 ordering) turns
    every fresh host connection into a menu+state replay. This covers
    both the startup race and supervisor respawns/reconnects. Safe
    under Tauri only (guarded by the same ``TAURI_SIDECAR`` gate as
    ``publish_tray_menu``); on Electron this subscriber is never
    registered.
    """
    try:
        from voice_typer.server import event_bus as _event_bus

        _event_bus.subscribe(tray._on_host_ready)
    except Exception:
        log.warning(
            "[TRAY] could not subscribe host-ready republish — the Tauri tray "
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
        log.debug("[TRAY] host ready — tray menu + state re-published")
    except Exception:
        log.debug("[TRAY] host-ready tray republish failed", exc_info=True)


def run(tray: TrayIcon) -> None:
    """Block the main thread with pystray's event loop.

    when the tray is unavailable, block on ``_run_event``
    (set by stop()) instead of raising. RuntimeError is retained only
    when start() was never called (programming error). On the
    unavailable path, drain pending queues every 60s (state is
    already published to Tauri via _publish_tray_state).
    """
    if tray._tray_unavailable and tray._icon is None:
        log.info(
            "[TRAY] Tray unavailable — main thread blocking on Event "
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
        # succeeded - e.g. ``PermissionError: [WinError 5] Access
        # is denied`` when ``_create_window`` runs in a
        # restricted / non-interactive session (observed in the
        # ``voice-typer`` terminal run). ``start()`` only catches
        # the ``OSError`` from ``pystray.Icon(...)`` construction,
        # NOT failures inside the event loop, so the exception
        # previously propagated up through ``app.start()`` to
        # ``[FATAL] app.start() raised`` - the WHOLE backend (IPC
        # server, hotkeys, recorder) crashed. Degrade to the
        # tray-unavailable blocking path instead: the app stays
        # usable via hotkey + IPC server + Electron window, and
        # ``stop()`` releases the ``_run_event``.
        log.warning(
            "[TRAY] Tray event loop failed at runtime - degrading to "
            "tray-unavailable mode. Hotkey, IPC server, and Electron "
            "window continue to work; tray icon + notifications are "
            "disabled.",
            exc_info=True,
        )
        tray._icon = None
        tray._tray_unavailable = True
        while not tray._run_event.wait(timeout=60):
            tray._drain_pending()


def stop(tray: TrayIcon) -> None:
    """Stop the tray icon and exit the event loop (idempotent).

    release ``_run_event``. Unsubscribe
    parakeet_cpu_fallback (set.discard — safe if never registered).

    ``tray._icon.stop()`` + ``tray._icon = None`` are
    serialized by ``tray._icon_lock`` so a concurrent
    ``_apply_state`` (e.g. from the 1s elapsed-recording tick or a
    state-change IPC) cannot read ``tray._icon`` as non-None
    between ``stop()`` returning and the ``= None`` assignment
    landing — the documented WinError 1402 (torn-down Icon) race.
    ``_icon_lock`` is an RLock so a re-entrant callback from within
    ``Icon.stop()`` (if any backend ever invokes one) cannot
    self-deadlock.
    """
    # Hold the lock across the teardown pair so _apply_state's
    # re-check inside the lock is the authoritative guard.
    with tray._icon_lock:
        if tray._icon:
            tray._icon.stop()
            tray._icon = None
    tray._cancel_elapsed_timer()
    tray._run_event.set()
    # clear the icon-state cache so a restarted tray
    # redraws the icon on the first ``_apply_state`` (no stale cache).
    tray._last_applied_state = None
    # clear the publish dedup cache so a restarted tray
    # re-publishes its initial state (no stale suppression).
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
