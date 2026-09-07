"""System tray icon using pystray, with dynamic state and menu.

Phase 2 minimal right-click menu: Start Dictation (hotkey) / Models /
Restart / Quit. Left-click + "Open App" launches (or focuses) the
Electron app; all settings / history / templates live in the Electron
window only.

Module-split history — each concern lives in its own satellite module;
``TrayIcon`` below is a thin orchestrator of one-line delegates:
menu building + Tauri click dispatch → ``tray_menu.py``; types →
``tray_types.py``; icon rendering → ``tray_icon.py``; i18n →
``tray_i18n.py``; Wayland SNI detection → ``tray_wayland_detect.py``;
elapsed-timer core → ``tray_elapsed_timer.py``; window management →
``tray_window.py``; notifications → ``tray_notifications.py``;
lifecycle glue (bg-work wrap, host-ready republish, run, stop) →
``tray_lifecycle.py``; state mutations + cached-field setters +
elapsed glue → ``tray_state.py``; tooltip computation / Tauri publish /
pystray apply → ``tray_publish.py``.

Every public + private method signature is preserved; every extracted
method is a one-line delegate. No behavior change. Delegate methods are
kept on the class so monkeypatch.setattr + source-grep tests +
event_bus.subscribe/unsubscribe (bound-method equality) keep working.
``start()``, ``_launch_bg_work`` and ``_drain_pending`` stay physical
on the class — their bodies are source-pinned by tests (single bg-thread
spawn site + call-site counts, daemon-thread rationale, fallback
notification allowlist docstring contract).

Threading: ``start()`` creates the icon + launches background work on a
daemon thread (non-blocking). ``run()`` blocks the main thread with
``pystray.Icon.run()``. State updates from the background thread are
dispatched safely by pystray. Before ``run()`` starts, state /
notification calls are queued and flushed once the event loop is live.
"""

from __future__ import annotations

import logging
import threading

# Kept as an attribute patch point: tests do
# ``monkeypatch.setattr(tray_module.time, "monotonic", ...)``.
import time  # noqa: F401
from collections.abc import Callable

# PERF-COLDSTART-001: lazy import — pystray's xorg backend calls
# Xlib.display.Display() at module import time (~48 ms cold-start, fails
# without an X display). The proxy re-reads sys.modules on every access
# so monkeypatches of voice_typer.server.tray.pystray keep working.
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server._paths import APP_SLUG
from voice_typer.server.tray_elapsed_timer import ElapsedTimer  # noqa: F401
from voice_typer.server.tray_i18n import (  # noqa: E402,F401
    _TRAY_LABELS_EN,
    _TRAY_LABELS_ES,
    _TRAY_LABELS_LOCALES,
    _,
    _tray_locale,
    get_tray_locale,
    register_tray_labels,
    set_tray_locale,
)
from voice_typer.server.tray_icon import _make_icon
from voice_typer.server.tray_menu import (  # noqa: F401
    display_hotkey,
    wrap_callback,
)
from voice_typer.server.tray_publish import _APP_STATE_TO_ICON_NAME  # noqa: F401
from voice_typer.server.tray_types import AppState, TrayController
from voice_typer.server.tray_wayland_detect import (  # noqa: F401
    is_linux_wayland_without_sni,
)

pystray = lazy_module("pystray")

log = logging.getLogger(__name__)


class TrayIcon:
    """Cross-platform system tray icon with Phase 2 minimal menu."""

    def __init__(
        self,
        controller: TrayController,
        config=None,
    ) -> None:
        self._controller = controller
        self._config = config  # reference to live Config object
        self._icon: pystray.Icon | None = None
        self._tray_unavailable: bool = False  # pystray.Icon() OSError
        # tray-unavailable run() blocks on this Event.
        self._run_event: threading.Event = threading.Event()
        self._state = AppState.IDLE
        self._message = ""
        self._notifications_enabled = True
        self._microphones: list[dict] = []  # mics submenu cache
        self._recording_started_at: float | None = None
        self._elapsed_timer: threading.Thread | None = None
        self._elapsed_timer_helper = ElapsedTimer(
            tick_callback=self._on_elapsed_tick,
            is_active=lambda: self._state == AppState.RECORDING,
            set_timer_ref=self._set_elapsed_timer_ref,
        )
        self._autostart_enabled = False
        self._cpu_fallback_active: bool = False
        # Pre-run state queue — flushed once the pystray event loop is live.
        self._pending_states: list[tuple[AppState, str]] = []
        self._pending_notifications: list[tuple[str, str]] = []
        self._queue_lock = threading.Lock()
        # Lock rationale lives beside each lock's consumers:
        # ``_menu_lock`` RLock re-entrancy → tray_state.invalidate_menu_cache_locked;
        # ``_icon_lock`` WinError-1402 serialization → tray_publish.apply_state;
        # publish dedup + dedicated Lock → tray_publish.publish_tray_state.
        self._menu_lock = threading.RLock()
        self._icon_lock = threading.RLock()
        self._bg_work_fn: Callable | None = None
        self._bg_thread: threading.Thread | None = None
        # ``<caps_lock>`` mirrors ``config.DEFAULT_HOTKEY`` (the
        # canonical default) — the legacy ``<f2>`` fallback would
        # display "F2" in tray tooltips while the app bound Caps Lock.
        self._hotkey: str = getattr(config, "hotkey", "<caps_lock>") or "<caps_lock>"
        self._cached_menu = None  # P4 #30: menu cache
        self._menu_cache_valid = False
        # Tauri-side ``id → callback`` map populated by
        # ``_maybe_publish_tray_menu`` (tray_menu.py); read by
        # ``dispatch_tray_action``. Defaults to ``{}`` so clicks that
        # land before the first menu publish return False (unknown item).
        self._tray_id_map: dict[str, Callable] = {}
        # Redraw/publish dedup caches; invariants documented at the
        # consuming code (tray_publish.apply_state caches the last
        # applied state; tray_publish.publish_tray_state caches the
        # last published ``(icon_name, tooltip)`` tuple under
        # ``_publish_lock``).
        self._last_applied_state: AppState | None = None
        self._last_published: tuple[str, str] | None = None
        self._publish_lock = threading.Lock()
        # Tauri runtime (TAURI_SIDECAR=1): the host-ready menu/state
        # replay subscriber MUST be registered at CONSTRUCTION time —
        # `start()` (which registers it on the pystray path) is never
        # called under Tauri (the pystray icon is skipped entirely), so
        # deferring registration there left the Rust host's tray menu
        # frozen at the empty placeholder after every sidecar spawn /
        # supervisor respawn (2026-08-30: "tray menu missing" after
        # tray-Restart). Idempotent via _host_ready_republish_subscribed.
        self._host_ready_republish_subscribed = False
        from voice_typer.server.tray_types import is_tauri_sidecar

        if is_tauri_sidecar():
            self._subscribe_host_ready_republish()

    # ─── Public API ─────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        """Return the current tray application state."""
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
        """Update tray icon state and tooltip (delegate to tray_state.set_state)."""
        from voice_typer.server.tray_state import set_state as _set_state

        return _set_state(self, state, message)

    def set_microphones(self, mics: list[dict] | None) -> None:
        """Cache the mic device list + invalidate the menu cache (delegate)."""
        from voice_typer.server.tray_state import set_microphones as _set_mics

        return _set_mics(self, mics)

    def set_autostart_enabled(self, enabled: bool) -> None:
        """Update the cached autostart state (delegate)."""
        from voice_typer.server.tray_state import set_autostart_enabled as _fn

        return _fn(self, enabled)

    def set_notifications_enabled(self, enabled: bool) -> None:
        """Update the cached notifications state (delegate)."""
        from voice_typer.server.tray_state import (
            set_notifications_enabled as _fn,
        )

        return _fn(self, enabled)

    def set_hotkey(self, hotkey: str) -> None:
        """Update the stored hotkey string for the next menu rebuild (delegate)."""
        from voice_typer.server.tray_state import set_hotkey as _fn

        return _fn(self, hotkey)

    @staticmethod
    def _is_linux_wayland_without_sni() -> bool:
        """detect Linux Wayland without StatusNotifierItem."""
        return is_linux_wayland_without_sni()

    def refresh_config(self, config) -> None:
        """Replace the cached Config reference and rebuild the menu (delegate)."""
        from voice_typer.server.tray_state import refresh_config as _fn

        return _fn(self, config)

    def _wrap_bg_work(self, bg_work: Callable | None) -> Callable | None:
        """Wrap bg_work so the initial tray menu publishes post-setup
        (delegate to tray_lifecycle.wrap_bg_work)."""
        from voice_typer.server.tray_lifecycle import wrap_bg_work as _fn

        return _fn(self, bg_work)

    def start(self, bg_work: Callable | None = None) -> None:
        """Create the tray icon and start background work (non-blocking).

        Three early-return paths skip tray creation but still launch
        bg_work on a daemon thread:  ``VOICE_TYPER_NO_TRAY=1``
        env var;  Linux Wayland without StatusNotifierItem
        (pystray GTK backend would hang on icon.run());
        pystray.Icon() raised OSError. On all three ``_tray_unavailable``
        is set True and run() blocks on ``_run_event``. Subscribe
        to parakeet_cpu_fallback BEFORE the early-return paths.
        """
        self._bg_work_fn = self._wrap_bg_work(bg_work)

        try:
            from voice_typer.server import event_bus as _event_bus

            _event_bus.subscribe(self._on_parakeet_cpu_fallback)
            _event_bus.subscribe(self._on_gpu_cpu_fallback)
        except Exception:
            # Promote DEBUG → WARNING: the CPU-fallback notification is
            # safety-critical (alerts the user that a model swap to CPU
            # mode happened); a swallowed subscribe failure left users
            # with no fallback alert.
            log.warning(
                "[TRAY] could not subscribe to parakeet_cpu_fallback — CPU-fallback alerts will NOT be surfaced",
                exc_info=True,
            )

        import os

        # explicit opt-out via env var.
        if os.environ.get("VOICE_TYPER_NO_TRAY") == "1":
            log.info(
                "[TRAY] VOICE_TYPER_NO_TRAY=1 set — skipping tray icon creation. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            self._launch_bg_work()  # shared launch helper
            return

        # Linux Wayland without StatusNotifierItem.
        if self._is_linux_wayland_without_sni():
            log.warning(
                "[TRAY] Linux Wayland session without StatusNotifierItem detected "
                "(common on Sway/Hyprland/dwl/river). Tray icon will not be created. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            self._launch_bg_work()  # shared launch helper
            return

        # Tauri sidecar runtime: the native tray is owned by the Rust host
        # (ADR-0020 §6.5 — created in src-tauri/src/tray.rs::create_tray and
        # driven by the ``tray_menu`` / ``tray_state`` WS events this process
        # publishes; see tray_menu.maybe_publish_tray_menu for the full
        # two-tray-icons / mis-routed-notifications rationale). Degrade to
        # the same unavailable-path used by headless hosts: no icon, bg_work
        # still launched, pending states drained by run()'s 60s loop, and
        # notifications re-routed to the host event bus (see
        # tray_notifications.do_notify).
        from voice_typer.server.tray_types import is_tauri_sidecar

        if is_tauri_sidecar():
            log.info(
                "[TRAY] TAURI_SIDECAR=1 — native tray is owned by the Rust host; "
                "skipping pystray icon creation. Menu/state reach the host via the "
                "tray_menu/tray_state events."
            )
            self._icon = None
            self._tray_unavailable = True
            self._subscribe_host_ready_republish()
            self._launch_bg_work()  # shared launch helper
            return

        menu = pystray.Menu(self._build_menu)
        try:
            self._icon = pystray.Icon(
                name=APP_SLUG,
                icon=_make_icon(AppState.IDLE),
                # title is both tooltip AND a11y name.
                title=_("app_name"),
                menu=menu,
            )
        except TypeError as e:
            raise RuntimeError(f"Failed to create tray icon (pystray Menu construction error): {e}") from e
        except OSError as e:
            # headless / Windows Server / no-explorer sessions.
            log.warning(
                "[TRAY] Could not create system tray icon (no tray available?). "
                "Hotkey and IPC server will continue to work, but tray menu "
                "and notifications are disabled. Original error: %s",
                e,
            )
            self._icon = None
            self._tray_unavailable = True
            self._launch_bg_work()  # shared launch helper
            return

        self._launch_bg_work()  # shared launch helper

        # DEBUG: the event-loop line below is the single INFO marker —
        # icon creation + loop start are one event.
        log.debug("[TRAY] Tray icon created, background work started")

    def _launch_bg_work(self) -> None:
        """Launch ``self._bg_work_fn`` on a daemon thread.

        Extracted from 4 near-duplicate ``if self._bg_work_fn:
        threading.Thread(...).start()`` blocks in ``start()``
        ( ``VOICE_TYPER_NO_TRAY=1``,  Wayland-
        without-SNI,  pystray ``OSError``, and the normal
        start path) so the launch shape (daemon thread + store on
        ``self._bg_thread``) lives in one place. No-op when
        ``_bg_work_fn`` is None (preserves the ``if self._bg_work_fn:``
        guards previously inlined at each call site).
        """
        if not self._bg_work_fn:
            return
        # daemon=True is acceptable because the background work
        # (microphone polling, prewarm status refresh, etc.) has no
        # critical cleanup: a force-kill leaves no locks or partial
        # files, and the OS reclaims the thread on process exit. The
        # main thread's pystray event loop + `stop()` handle orderly
        # shutdown independently.
        self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
        self._bg_thread.start()

    def _subscribe_host_ready_republish(self) -> None:
        """Subscribe a menu+state replay listener on host (re)connect (delegate)."""
        from voice_typer.server.tray_lifecycle import (
            subscribe_host_ready_republish as _fn,
        )

        return _fn(self)

    def _on_host_ready(self, event: dict) -> None:
        """Republish menu + state when the host connection signals ready (delegate)."""
        from voice_typer.server.tray_lifecycle import on_host_ready as _fn

        return _fn(self, event)

    def run(self) -> None:
        """Block the main thread with pystray's event loop (delegate to
        tray_lifecycle.run; unavailable path drains pending queues every 60s)."""
        from voice_typer.server.tray_lifecycle import run as _run

        return _run(self)

    def stop(self) -> None:
        """Stop the tray icon and exit the event loop, idempotently
        (delegate to tray_lifecycle.stop; teardown serialized by
        ``_icon_lock`` against concurrent ``_apply_state``)."""
        from voice_typer.server.tray_lifecycle import stop as _stop

        return _stop(self)

    # ─── Notifications (delegates to tray_notifications.py) ────────────

    def notify(self, title: str, message: str) -> None:
        """Show a notification if notifications are enabled (delegate).

        This body does NOT publish via the event bus (double-toast
        guard); that path lives in the show_electron_notification IPC
        handler. The pystray toast path lives in tray_notifications.
        """
        from voice_typer.server.tray_notifications import notify as _notify

        return _notify(self, title, message)

    def notify_safety(self, title: str, message: str) -> None:
        """Show a safety-critical notification bypassing the toggle (delegate)."""
        from voice_typer.server.tray_notifications import (
            notify_safety as _notify_safety,
        )

        return _notify_safety(self, title, message)

    def _do_notify(self, title: str, message: str) -> None:
        """Send a notification through the icon (delegate).

         delegates to tray_notifications.do_notify,
        which calls ``self._icon.notify(message, title)`` (pystray's
        native toast path — WinRT ToastNotification on Win10+).
        """
        from voice_typer.server.tray_notifications import do_notify as _do_notify

        return _do_notify(self, title, message)

    def _on_parakeet_cpu_fallback(self, event: dict) -> None:
        """Handle parakeet_cpu_fallback events (delegate)."""
        from voice_typer.server.tray_notifications import (
            on_parakeet_cpu_fallback as _on_fallback,
        )

        return _on_fallback(self, event)

    def _on_gpu_cpu_fallback(self, event: dict) -> None:
        """Handle gpu_cpu_fallback events (delegate)."""
        from voice_typer.server.tray_notifications import (
            on_gpu_cpu_fallback as _on_fallback,
        )

        return _on_fallback(self, event)

    def _drain_pending(self) -> None:
        """Drain pending state/notification queues (tray-unavailable run path).

        Called from run() every 60s; state already published to Tauri.

        the previous implementation silently dropped queued
        notifications on the tray-unavailable path (Linux Wayland
        without SNI / Windows-Server headless / ``VOICE_TYPER_NO_TRAY=1``
        / pystray.Icon() OSError fallback). The 60s drain was a no-op
        that cleared the queue without surfacing the notification, so
        a critical ``notify_safety`` (e.g. crash recovery failure,
        model load error) would never reach the user.

        The new path:
        1. Logs the notification at WARNING level with the full
           title + message. The Python rotating file logger is
           always available (it's a separate process from the
           pystray ICON subsystem), so the user can grep their log
           for the notification after-the-fact.
        2. Publishes a ``tray_fallback_notification`` event via the
           event bus so the Electron renderer can surface the
           notification as a toast. CROSS-LAYER GATE: the
           ACTUAL gate is the Tauri host's ``ALLOWED_EVENT_TYPES``
           slice at ``src-tauri/src/sidecar/ws.rs:80-150`` — the
           Tauri WS reader silently DROPS any inbound frame whose
           ``type`` is not in that slice (logged at
           ``[WS-READER] dropping unknown event type:``). Adding a
           ``tray_fallback_notification`` listener in the renderer
           ALONE is insufficient; the event name MUST also be added
           to the Rust ``ALLOWED_EVENT_TYPES`` slice (tracked in
           ). Until that ws.rs edit lands, this fallback
           surfaces only via the WARNING log above (fail-soft, not
           fail-silent). The test
           ``tests/test_tray_fallback_notification_allowlist.py``
           pins the published event-name literal so a future rename
           on the Python side without a matching ws.rs allowlist
           update is caught at CI time.
        3. Clears the queue (the dropped notification has been
           preserved via logs + Tauri channel — it cannot be lost).

        This is fail-safe: the call is wrapped in
        ``contextlib.suppress`` so a logging or event-bus failure
        cannot crash the tray's main loop.
        """
        with self._queue_lock:
            notifications = list(self._pending_notifications)
            self._pending_notifications.clear()
            self._pending_states.clear()
        if not notifications:
            return
        # Lazy import to keep tray.py startup fast (event_bus
        # imports are heavier than the icon-stub import).
        import contextlib

        from voice_typer.server import event_bus as _event_bus

        for title, message in notifications:
            log.warning(
                "[TRAY] Falling back to log+event for notification (tray unavailable): title=%r message=%r",
                title,
                message,
            )
            with contextlib.suppress(Exception):
                _event_bus.publish(
                    {
                        "type": "tray_fallback_notification",
                        "title": title,
                        "message": message,
                    }
                )

    # ─── Internals: state + tooltip (delegates to tray_publish.py) ─────

    def _compute_tooltip(self, state: AppState, message: str) -> str:
        """Compute the tray tooltip ``<APP_NAME> — <msg|state> …`` (delegate)."""
        from voice_typer.server.tray_publish import compute_tooltip as _fn

        return _fn(self, state, message)

    def _publish_tray_state(self) -> None:
        """ADR-0020 §6.5: push icon+tooltip to Tauri, deduped under
        ``_publish_lock`` (delegate to tray_publish.publish_tray_state)."""
        from voice_typer.server.tray_publish import publish_tray_state as _fn

        return _fn(self)

    def _apply_state(self, state: AppState, message: str) -> None:
        """Apply state to the live icon, serialized by ``_icon_lock``
        (delegate to tray_publish.apply_state)."""
        from voice_typer.server.tray_publish import apply_state as _fn

        return _fn(self, state, message)

    # ─── elapsed-recording timer (delegates to tray_state.py) ──────────

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """mm:ss (under 1h) / h:mm:ss (1h+) — delegate to tray_state.format_elapsed."""
        from voice_typer.server.tray_state import format_elapsed as _fmt

        return _fmt(seconds)

    def _on_elapsed_tick(self) -> None:
        """Refresh tooltip with latest elapsed time (1s tick while RECORDING)."""
        from voice_typer.server.tray_state import on_elapsed_tick as _fn

        return _fn(self)

    def _set_elapsed_timer_ref(self, timer: threading.Thread | None) -> None:
        from voice_typer.server.tray_state import set_elapsed_timer_ref as _fn

        return _fn(self, timer)

    def _start_elapsed_timer(self) -> None:
        from voice_typer.server.tray_state import start_elapsed_timer as _fn

        return _fn(self)

    def _cancel_elapsed_timer(self) -> None:
        from voice_typer.server.tray_state import cancel_elapsed_timer as _fn

        return _fn(self)

    # ─── Window management (delegates to tray_window.py) ──────────────

    @staticmethod
    def _bring_electron_to_front() -> bool:
        from voice_typer.server.tray_window import bring_electron_to_front

        return bring_electron_to_front()

    def open_electron_window(self) -> None:
        from voice_typer.server.tray_window import open_electron_window as _open

        _open()

    def _open_page(self, path: str) -> None:
        """Publish a navigate event so the renderer opens path.

        Tests that monkeypatch.setattr(tray, "_open_page", fake) keep
        working because the method stays on the class."""
        from voice_typer.server.tray_window import open_page

        return open_page(path)

    def _open_models_page(self) -> None:
        from voice_typer.server.tray_window import open_models_page

        return open_models_page(self)

    def _open_microphones_page(self) -> None:
        """Open the app window and navigate to the Microphone page.

        Wiring for the Tauri tray's "More microphones..." deep-link: the
        Microphones submenu stays useful (and reachable) even while the
        device list is momentarily empty. Composes the same primitives
        ``open_models_page`` uses — show/focus the window, then publish
        the ``navigate`` event for the ``microphone`` route. Monkeypatch
        friendly like its sibling (instance attributes are consulted at
        call time by the tray-menu id-map callbacks).
        """
        from voice_typer.server.tray_window import open_page

        self.open_electron_window()
        return open_page("/microphone")

    def _confirm_quit_while_recording(self) -> None:
        from voice_typer.server.tray_window import (
            confirm_quit_while_recording,
        )

        return confirm_quit_while_recording(self)

    # ─── Menu building (delegates to tray_menu.py) ─────────────────────

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale (EAGER variant — also forces
        ``self._icon._update_menu()``; reserved for explicit user-facing
        refresh actions). Delegate to tray_menu.invalidate_menu_cache;
        the lazy setters use ``_invalidate_menu_cache_locked`` instead."""
        from voice_typer.server.tray_menu import invalidate_menu_cache

        return invalidate_menu_cache(self)

    def _invalidate_menu_cache_locked(self) -> None:
        """Clear ``_menu_cache_valid`` under ``_menu_lock``, no pystray touch
        (delegate to tray_state.invalidate_menu_cache_locked)."""
        from voice_typer.server.tray_state import invalidate_menu_cache_locked as _fn

        return _fn(self)

    def dispatch_tray_action(self, item_id: str) -> bool:
        """Dispatch a Tauri tray-click IPC via ``_tray_id_map`` (delegate to
        tray_menu.dispatch_tray_action; unknown ids return False)."""
        from voice_typer.server.tray_menu import dispatch_tray_action as _dispatch

        return _dispatch(self, item_id)

    def _build_menu(self) -> tuple:
        """Build the tray menu (delegate to tray_menu.build_menu_for_tray).

        Lambdas consult self.* at CALL TIME so patches keep working."""
        from voice_typer.server.tray_menu import build_menu_for_tray

        return build_menu_for_tray(self)

    def _maybe_publish_tray_menu(self) -> bool:
        """ADR-0020 §6.5: push serialized tray menu to Tauri (delegate);
        no-op on Electron/pystray."""
        from voice_typer.server.tray_menu import maybe_publish_tray_menu

        return maybe_publish_tray_menu(self)

    def _build_microphones_submenu(self) -> list:
        """Build the Microphones ▸ submenu — delegate."""
        from voice_typer.server.tray_menu import build_microphones_submenu

        return build_microphones_submenu(self)

    def _build_models_submenu(self) -> list:
        """Build a list of model MenuItems — cached models + More models link."""
        from voice_typer.server.tray_menu import build_models_submenu

        # build_models_menu_items (tray_models.py) is the MenuItem builder.
        return build_models_submenu(self)

    def _display_hotkey(self) -> str:
        """Return the configured hotkey in a user-facing form (delegate)."""
        hotkey = self._hotkey or getattr(self._config, "hotkey", "<caps_lock>") or "<caps_lock>"
        return display_hotkey(hotkey)

    # #13: _wrap moved to tray_menu.wrap_callback; kept as static-method
    # alias for backwards compat with code calling TrayIcon._wrap(fn).
    _wrap = staticmethod(wrap_callback)

    # periodic update checker removed (dead code, broken
    # toggle, phoned home to GitHub). If reintroduced: default OFF, consent dialog, dedicated module.
