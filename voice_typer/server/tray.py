"""System tray icon using pystray, with dynamic state and menu.

Phase 2 minimal right-click menu: Toggle Dictation (hotkey) / Models /
Restart / Quit. Left-click + "Open App" launches (or focuses) the
Electron app; all settings / history / templates live in the Electron
window only.

Module-split history — each concern lives in its own module;
``TrayIcon`` below is a thin orchestrator of one-line delegates:
  - menu building → ``tray_menu.py``
  - types → ``tray_types.py``
  - icon rendering → ``tray_icon.py``
  - i18n → ``tray_i18n.py``
  - Wayland SNI detection → ``tray_wayland_detect.py``
  - elapsed-recording timer → ``tray_elapsed_timer.py``
  - window management → ``tray_window.py``
  - notifications → ``tray_notifications.py``

Phase 4.5 spaghetti split: extracted the
window-management + notification methods to ``tray_window.py`` and
``tray_notifications.py``; this pass trims the verbose docstrings so the
class is a thin orchestrator (target ≤600 lines). Every public + private
method signature is preserved; every extracted method is a one-line
delegate. No behavior change. Delegate methods are kept on the class so
monkeypatch.setattr + source-grep tests + event_bus.subscribe/unsubscribe
(bound-method equality) keep working.

Threading: ``start()`` creates the icon + launches background work on a
daemon thread (non-blocking). ``run()`` blocks the main thread with
``pystray.Icon.run()``. State updates from the background thread are
dispatched safely by pystray. Before ``run()`` starts, state /
notification calls are queued and flushed once the event loop is live.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

# PERF-COLDSTART-001: lazy import — pystray's xorg backend calls
# Xlib.display.Display() at module import time (~48 ms cold-start, fails
# without an X display). The proxy re-reads sys.modules on every access
# so monkeypatches of voice_typer.server.tray.pystray keep working.
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server._paths import APP_SLUG
from voice_typer.server.branding import APP_NAME

# Re-exports (noqa: F401) for backward compat with tests/code that
# imports these symbols from voice_typer.server.tray directly.
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
    build_menu,
    display_hotkey,
    wrap_callback,
)
from voice_typer.server.tray_types import AppState, TrayController
from voice_typer.server.tray_wayland_detect import (  # noqa: F401
    is_linux_wayland_without_sni,
)

pystray = lazy_module("pystray")

log = logging.getLogger(__name__)


# ADR-0020 §6.5: maps internal AppState → logical icon name accepted by
# the Tauri Rust host's tray_state listener (whitelists
# "idle" | "recording" | "transcribing" | "error"). LOADING/CANCELLING
# fall back to a neighboring state (no dedicated asset).
_APP_STATE_TO_ICON_NAME: dict[AppState, str] = {
    AppState.IDLE: "idle",
    AppState.RECORDING: "recording",
    AppState.TRANSCRIBING: "transcribing",
    AppState.ERROR: "error",
    AppState.LOADING: "idle",
    AppState.CANCELLING: "error",
}


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
        # Protects ``_cached_menu`` / ``_menu_cache_valid`` /
        # ``_microphones`` (read+written by ``build_menu_for_tray`` on the
        # pystray thread and by ``invalidate_menu_cache`` from background
        # threads). On Windows, ``pystray.Icon._update_menu()`` calls
        # ``DestroyMenu`` / ``CreatePopupMenu`` — not guaranteed
        # thread-safe — so the lock serializes the rebuild.
        self._menu_lock = threading.Lock()
        # Protects ``self._icon`` access in ``_apply_state`` +
        # ``stop()``. Between ``self._icon.stop()`` returning and
        # ``self._icon = None`` executing, a concurrent ``_apply_state``
        # can read ``self._icon`` as non-None and then call
        # ``self._icon.icon = ...`` on a torn-down Icon — the documented
        # WinError 1402 trigger. RLock (not Lock) because ``_apply_state``
        # may re-enter through ``_compute_tooltip`` and any future
        # callback path that re-enters the icon's setter.
        self._icon_lock = threading.RLock()
        self._bg_work_fn: Callable | None = None
        self._bg_thread: threading.Thread | None = None
        self._hotkey: str = getattr(config, "hotkey", "<f2>") or "<f2>"
        self._cached_menu = None  # P4 #30: menu cache
        self._menu_cache_valid = False
        # Tauri-side ``id → callback`` map populated by
        # ``_maybe_publish_tray_menu`` (tray_menu.py). Read by
        # ``dispatch_tray_action`` to route a Tauri tray-click IPC back
        # to the right controller/window callback. Defaults to ``{}``
        # so ``dispatch_tray_action`` returns False (unknown item)
        # before the first menu publish lands.
        self._tray_id_map: dict[str, Callable] = {}
        # cache-skip — skip ``_make_icon`` redraw when
        # ``state == _last_applied_state``. The 1s elapsed-recording tick
        # calls ``_apply_state`` every second; pre-this
        # re-malloc'd a fresh PIL image + pystray icon handle on every
        # tick (and tickled the WinError 1402 stale-handle bug on Windows).
        # The tooltip (``self._icon.title``) is still updated unconditionally
        # so the elapsed ``mm:ss`` stays live.
        self._last_applied_state: AppState | None = None
        # last-published (icon_name, tooltip) tuple for publish
        # dedup. ``_publish_tray_state`` skips the emit entirely when
        # both fields match the cache; ``stop()`` clears it so a
        # restarted tray re-publishes its initial state. The cache key
        # is the FULL tuple (not just icon_name) so a tooltip-only
        # change still emits. Only set on a successful publish — a
        # failed publish is NOT cached so the next call retries.
        self._last_published: tuple[str, str] | None = None
        # serializes the check-then-publish-then-cache sequence in
        # ``_publish_tray_state`` so two concurrent callers (the 1s
        # elapsed-recording tick vs a state-change IPC) cannot both
        # pass the cache check and both emit. Held ONLY across the
        # tuple comparison + the event-bus publish, NOT across
        # ``_compute_tooltip`` or the icon-name lookup. A dedicated
        # Lock (not ``_icon_lock`` / ``_menu_lock``) so the publish
        # path isn't over-serialized against the icon-teardown or
        # menu-rebuild paths.
        self._publish_lock = threading.Lock()

    # ─── Public API ─────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        """Return the current tray application state."""
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
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
        if state == self._state and message == self._message:
            return
        prev_state = self._state
        self._state = state
        self._message = message
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
            self._menu_cache_valid = False
        if state == AppState.RECORDING and prev_state != AppState.RECORDING:
            self._recording_started_at = time.monotonic()
            self._start_elapsed_timer()
        elif state != AppState.RECORDING and prev_state == AppState.RECORDING:
            self._cancel_elapsed_timer()
            self._recording_started_at = None
        if self._icon:
            self._apply_state(state, message)
        else:
            with self._queue_lock:
                self._pending_states.append((state, message))
        self._publish_tray_state()
        if record_or_transcribe_changed:
            self._maybe_publish_tray_menu()

    def set_microphones(self, mics: list[dict] | None) -> None:
        """Cache the mic device list + invalidate the menu cache.

        None/empty normalized to []. ADR-0020 §6.5: push to Tauri.

        Uses ``_invalidate_menu_cache_locked`` (not the eager
        ``invalidate_menu_cache``) so the cache-validity flag is cleared
        under ``_menu_lock`` without forcing a pystray ``_update_menu``
        call — the Tauri publish path doesn't need the Win32 menu handle
        rebuilt, and on pystray the next right-click rebuilds lazily.
        """
        self._microphones = list(mics) if mics else []
        self._invalidate_menu_cache_locked()
        self._maybe_publish_tray_menu()

    def set_autostart_enabled(self, enabled: bool) -> None:
        """Update the cached autostart state."""
        self._autostart_enabled = enabled
        self._invalidate_menu_cache_locked()

    def set_notifications_enabled(self, enabled: bool) -> None:
        """Update the cached notifications state."""
        self._notifications_enabled = enabled
        self._invalidate_menu_cache_locked()

    def set_hotkey(self, hotkey: str) -> None:
        """Update the stored hotkey string for the next menu rebuild."""
        self._hotkey = hotkey
        self._invalidate_menu_cache_locked()
        self._maybe_publish_tray_menu()
        self._publish_tray_state()

    @staticmethod
    def _is_linux_wayland_without_sni() -> bool:
        """detect Linux Wayland without StatusNotifierItem."""
        return is_linux_wayland_without_sni()

    def refresh_config(self, config) -> None:
        """Replace the cached Config reference and rebuild the menu."""
        self._config = config
        self._hotkey = getattr(config, "hotkey", self._hotkey) or self._hotkey
        self._invalidate_menu_cache_locked()
        self._maybe_publish_tray_menu()
        self._publish_tray_state()

    def _wrap_bg_work(self, bg_work: Callable | None) -> Callable | None:
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
                    self._maybe_publish_tray_menu()
                    self._publish_tray_state()
                except Exception:
                    log.debug("[TRAY] post-bg_work tray publish failed", exc_info=True)

        return _wrapped

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
        except Exception:
            # Promote DEBUG → WARNING. The CPU-fallback
            # notification is safety-critical (alerts the user that a
            # model swap to CPU mode happened); silently swallowing the
            # subscribe failure at DEBUG hid cases where event_bus was
            # mis-imported or the handler signature drifted, leaving
            # users with no fallback alert.
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

        log.info("[TRAY] Tray icon created, background work started")

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

    def run(self) -> None:
        """Block the main thread with pystray's event loop.

        when the tray is unavailable, block on ``_run_event``
        (set by stop()) instead of raising. RuntimeError is retained only
        when start() was never called (programming error). On the
        unavailable path, drain pending queues every 60s (state is
        already published to Tauri via _publish_tray_state).
        """
        if self._tray_unavailable and self._icon is None:
            log.info(
                "[TRAY] Tray unavailable — main thread blocking on Event "
                "(stop() will release, pending queues drained every 60s). "
                "Hotkey + IPC server still active."
            )
            while not self._run_event.wait(timeout=60):
                self._drain_pending()
            return

        if self._icon is None:
            raise RuntimeError("call start() before run()")

        # Flush queued state + notifications.
        with self._queue_lock:
            for state, msg in self._pending_states:
                self._apply_state(state, msg)
            self._pending_states.clear()
        with self._queue_lock:
            for title, message in self._pending_notifications:
                self._do_notify(title, message)
            self._pending_notifications.clear()

        log.info("[TRAY] Tray event loop starting (main thread)")
        try:
            self._icon.run()
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
            self._icon = None
            self._tray_unavailable = True
            while not self._run_event.wait(timeout=60):
                self._drain_pending()

    def stop(self) -> None:
        """Stop the tray icon and exit the event loop (idempotent).

        release ``_run_event``. Unsubscribe
        parakeet_cpu_fallback (set.discard — safe if never registered).

        ``self._icon.stop()`` + ``self._icon = None`` are
        serialized by ``self._icon_lock`` so a concurrent
        ``_apply_state`` (e.g. from the 1s elapsed-recording tick or a
        state-change IPC) cannot read ``self._icon`` as non-None
        between ``stop()`` returning and the ``= None`` assignment
        landing — the documented WinError 1402 (torn-down Icon) race.
        ``_icon_lock`` is an RLock so a re-entrant callback from within
        ``Icon.stop()`` (if any backend ever invokes one) cannot
        self-deadlock.
        """
        # Hold the lock across the teardown pair so _apply_state's
        # re-check inside the lock is the authoritative guard.
        with self._icon_lock:
            if self._icon:
                self._icon.stop()
                self._icon = None
        self._cancel_elapsed_timer()
        self._run_event.set()
        # clear the icon-state cache so a restarted tray
        # redraws the icon on the first ``_apply_state`` (no stale cache).
        self._last_applied_state = None
        # clear the publish dedup cache so a restarted tray
        # re-publishes its initial state (no stale suppression).
        self._last_published = None

        try:
            from voice_typer.server import event_bus as _event_bus

            _event_bus.unsubscribe(self._on_parakeet_cpu_fallback)
        except Exception:
            log.debug("[TRAY] could not unsubscribe parakeet_cpu_fallback", exc_info=True)

        log.info("[SHUTDOWN] Tray icon stopped")

    # ─── Notifications (delegates to tray_notifications.py) ────────────

    def notify(self, title: str, message: str) -> None:
        """Show a notification if notifications are enabled (delegate).

         delegates to tray_notifications.notify. This
        body does NOT publish via the event bus (that path lives in the
        show_electron_notification IPC handler); the actual toast call
        ``self._icon.notify(message, title)`` lives in
        tray_notifications.do_notify (reached via _do_notify).
        """
        from voice_typer.server.tray_notifications import notify as _notify

        return _notify(self, title, message)

    def notify_safety(self, title: str, message: str) -> None:
        """Show a notification that bypasses the toggle (safety-critical).

        delegates to tray_notifications.notify_safety.
        """
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

    # ─── Internals: state + tooltip ────────────────────────────────────

    def _compute_tooltip(self, state: AppState, message: str) -> str:
        """Compute the tray tooltip: ``<APP_NAME> — <msg|state> [(CPU fallback)]
        [(mm:ss)] [<model>] (<hotkey>)``. Shared by _apply_state +
        _publish_tray_state so pystray + Tauri stay in sync."""
        title = APP_NAME
        if message:
            title += f" — {message}"
        elif state != AppState.IDLE:
            title += f" — {state.value}"
        if self._cpu_fallback_active:
            title += " (CPU fallback)"
        if state == AppState.RECORDING and self._recording_started_at is not None:
            elapsed = time.monotonic() - self._recording_started_at
            title += f" ({self._format_elapsed(elapsed)})"
        if self._config:  # model name
            model = getattr(self._config, "model_size", "")
            if model:
                title += f" [{model}]"
        hotkey = self._display_hotkey()  # hotkey
        if hotkey:
            title += f" ({hotkey})"
        # Win32 ``NOTIFYICONDATAW.szTip`` has a 128-char limit (127 +
        # NUL) — truncate to 127 chars (with a trailing ``…`` if
        # truncated) so the OS layer doesn't silently cut the tooltip.
        # ``…`` is a single codepoint (U+2026), so ``title[:126] + "…"``
        # is exactly 127 chars. Deterministic for the same input, so
        # the ``_last_published`` dedup tuple stays stable.
        if len(title) > 127:
            title = title[:126] + "…"
        return title

    def _publish_tray_state(self) -> None:
        """ADR-0020 §6.5: push icon+tooltip to Tauri (emit tray_state event
        instead of mutating pystray Icon). No-op on Electron/pystray.
        Best-effort (hot path).

        Suppress redundant publishes — the cache key is the
        FULL ``(icon_name, tooltip)`` tuple (not just icon_name), so a
        tooltip-only change still emits. A failed publish is NOT
        cached, so the next call retries (no silent drop)."""
        from voice_typer.server.tray_menu import publish_tray_state

        icon_name = _APP_STATE_TO_ICON_NAME.get(self._state, "idle")
        tooltip = self._compute_tooltip(self._state, self._message)
        # ``_publish_lock`` serializes the check-then-publish-then-cache
        # sequence so two concurrent callers (the 1s elapsed-recording
        # tick vs a state-change IPC) cannot both pass the cache check
        # and both emit. Held ONLY across the tuple comparison + the
        # publish (NOT across ``_compute_tooltip`` or the icon-name
        # lookup, which are pure and may run concurrently).
        with self._publish_lock:
            # identical last-published state → skip the emit
            # entirely (redundant tray_state events cause the Tauri
            # host to re-run tray.set_icon / tray.set_tooltip, which on
            # Windows is a DestroyIcon / LoadIcon round-trip per call).
            if self._last_published == (icon_name, tooltip):
                return
            try:
                ok = publish_tray_state(icon=icon_name, tooltip=tooltip)
            except Exception:
                log.debug(
                    "[TRAY] publish_tray_state failed (state=%s)",
                    self._state.value if hasattr(self._state, "value") else self._state,
                    exc_info=True,
                )
                # Do NOT cache a failed publish — the next call must
                # retry.
                return
            # Only cache a successful publish (best-effort
            # publish_tray_state returns False instead of raising on
            # the sidecar-disconnected path — a False return must NOT
            # suppress the next retry).
            if ok:
                self._last_published = (icon_name, tooltip)

    def _apply_state(self, state: AppState, message: str) -> None:
        """Apply state to the live icon (safe from any thread).

         skip the ``_make_icon`` redraw when
        ``state == self._last_applied_state`` — the icon PNG depends only
        on ``state``, not on the ``message`` / elapsed time. The 1s
        elapsed-recording tick re-enters here every second; the
        cache-skip avoids re-malloc'ing a fresh PIL image + pystray icon
        handle on every tick (and avoids tickling the WinError 1402
        stale-handle bug —  / ). The tooltip assignment is
        UNCONDITIONAL so the elapsed ``mm:ss`` stays live.

        The entire body is serialized by ``self._icon_lock`` so
        that a concurrent ``stop()`` cannot tear down ``self._icon``
        (``self._icon.stop()`` then ``self._icon = None``) between this
        method's ``if not self._icon: return`` check and the subsequent
        ``self._icon.icon = ...`` / ``self._icon.title = ...`` writes.
        Without the lock, the gap was the documented WinError 1402
        trigger (writing to a torn-down Icon). The caller's
        ``if self._icon:`` check (e.g. in ``set_state``) is racy on its
        own — the re-check inside the lock is the authoritative guard.
        """
        with self._icon_lock:
            if not self._icon:
                return
            # only redraw the icon on a state CHANGE.
            if state != self._last_applied_state:
                try:
                    self._icon.icon = _make_icon(state)
                except OSError as exc:
                    # pystray Windows DestroyIcon stale-handle
                    # bug (WinError 1402) during rapid icon updates — clear the
                    # private _icon_handle so pystray re-creates it next call
                    # (pystray pinned to >=0.19,<0.20 in pyproject.toml).
                    #
                    # if a future pystray release (0.20+) removes or
                    # renames the private ``_icon_handle`` attribute, the
                    # workaround becomes a silent no-op — the OSError is
                    # still raised on every icon update but the workaround
                    # can't fire, so WinError 1402 resurfaces for users with
                    # no diagnostic surface. Log a WARNING in that case so
                    # the silent workaround failure shows up in diagnostics
                    # (the regression test
                    # ``tests/test_pystray_icon_handle_regression.py`` guards
                    # this exact attribute via ``hasattr(pystray.Icon,
                    # "_icon_handle")``).
                    if hasattr(self._icon, "_icon_handle"):
                        self._icon._icon_handle = None
                    else:
                        log.warning(
                            "[TRAY] pystray.Icon no longer exposes the private "
                            "`_icon_handle` attribute — DestroyIcon workaround "
                            "disabled (OSError: %r). The tray will keep running "
                            "but rapid icon updates on Windows may hit WinError "
                            "1402. Replace the "
                            "private attribute access with a public "
                            "`reset_icon_handle()` API when upstream exposes "
                            "it, and bump pystray to the release that ships it.",
                            exc,
                        )
                self._last_applied_state = state
            # Tooltip is UNCONDITIONAL — elapsed mm:ss must stay live on the
            # 1s recording tick even when the icon was skipped.
            self._icon.title = self._compute_tooltip(state, message)

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

    # ─── elapsed-recording timer ──────────────────────

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Format seconds as mm:ss (under 1h) or h:mm:ss (1h+); delegates to
        ElapsedTimer.format_elapsed; kept for backward compat."""
        return ElapsedTimer.format_elapsed(seconds)

    def _on_elapsed_tick(self) -> None:
        """refresh tooltip with latest elapsed time (1s tick while
        RECORDING). Re-applies state to pystray Icon + publishes to Tauri."""
        if self._icon is not None:
            self._apply_state(self._state, self._message)
        self._publish_tray_state()

    def _set_elapsed_timer_ref(self, timer: threading.Thread | None) -> None:
        """Sync self._elapsed_timer with the helper's Timer."""
        self._elapsed_timer = timer

    def _start_elapsed_timer(self) -> None:
        """Start/restart the 1s elapsed-recording timer (delegates to ElapsedTimer
        helper; no-op if helper missing — backward compat with _FakeTray)."""
        helper = getattr(self, "_elapsed_timer_helper", None)
        if helper is not None:
            helper.start()

    def _cancel_elapsed_timer(self) -> None:
        """Cancel the elapsed-recording timer if running (idempotent)."""
        helper = getattr(self, "_elapsed_timer_helper", None)
        if helper is not None:
            helper.cancel()

    # ─── Window management (delegates to tray_window.py) ──────────────

    @staticmethod
    def _bring_electron_to_front() -> bool:
        """#13: Delegates to tray_window.bring_electron_to_front()."""
        from voice_typer.server.tray_window import bring_electron_to_front

        return bring_electron_to_front()

    def open_electron_window(self) -> None:
        """#13: Delegates to tray_window.open_electron_window()."""
        from voice_typer.server.tray_window import open_electron_window as _open

        _open()

    def _open_page(self, path: str) -> None:
        """Publish a navigate event so the renderer opens path (delegate).

         delegates to tray_window.open_page. Tests
        that monkeypatch.setattr(tray, "_open_page", fake) keep working."""
        from voice_typer.server.tray_window import open_page

        return open_page(path)

    def _open_models_page(self) -> None:
        """Open Electron window + navigate to /models (delegate).

         delegates to tray_window.open_models_page,
        which calls self._open_page('/models') so patch keeps working."""
        from voice_typer.server.tray_window import open_models_page

        return open_models_page(self)

    def _confirm_quit_while_recording(self) -> None:
        """Quit immediately, regardless of recording state (delegate).

         delegates to
        tray_window.confirm_quit_while_recording."""
        from voice_typer.server.tray_window import (
            confirm_quit_while_recording,
        )

        return confirm_quit_while_recording(self)

    # ─── Menu building (delegates to tray_menu.py) ─────────────────────

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale (delegate to tray_menu.invalidate_menu_cache).

        EAGER variant: also forces ``self._icon._update_menu()`` so the
        Win32 HMENU is rebuilt before the next right-click (pystray on
        Windows only invokes ``_build_menu`` at icon creation, so the
        cached HMENU would otherwise stay stale until restart). Reserve
        for explicit user-facing refresh actions (model download
        completed, autostart toggled from Settings, etc.).
        """
        from voice_typer.server.tray_menu import invalidate_menu_cache

        return invalidate_menu_cache(self)

    def _invalidate_menu_cache_locked(self) -> None:
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
        with self._menu_lock:
            self._menu_cache_valid = False

    def dispatch_tray_action(self, item_id: str) -> bool:
        """Dispatch a Tauri tray-click IPC to the registered callback.

        ADR-0020 §6.5 / §16: the Tauri Rust host emits a ``tray_click``
        IPC for every native menu item click; ``ipc_server.py`` calls
        this method with the item's ``id``. The ``id → callback`` map is
        populated by ``_maybe_publish_tray_menu`` (tray_menu.py) on
        every menu publish, so this method simply looks up the id and
        invokes the registered callback.

        Returns ``True`` if the id was found and the callback was
        invoked, ``False`` if the id is unknown (the IPC layer turns a
        False return into a ``server.unknown_tray_item`` error envelope).
        Before the first menu publish, ``self._tray_id_map`` is ``{}``
        (initialised in ``__init__``) so every click returns False — the
        Tauri host should publish the initial menu via
        ``_wrap_bg_work`` before any click can land.

        Callback exceptions are caught and logged so a single broken
        callback (e.g. a controller method that raises) doesn't take
        down the IPC server thread. The return value is still True on a
        known id — the click was *dispatched*, the callback's success is
        a separate concern (the renderer surfaces errors via toasts).
        """
        callback = self._tray_id_map.get(item_id)
        if callback is None:
            return False
        try:
            callback()
        except Exception:
            log.warning(
                "[TRAY] dispatch_tray_action callback raised for item_id=%r",
                item_id,
                exc_info=True,
            )
        return True

    def _build_menu(self) -> tuple:
        """Build the tray menu (Models + Microphones submenus + shortcuts).

         delegates to tray_menu.build_menu_for_tray.
        Lambdas consult self.* at CALL TIME so patches keep working."""
        from voice_typer.server.tray_menu import build_menu_for_tray

        return build_menu_for_tray(self)

    def _maybe_publish_tray_menu(self) -> bool:
        """ADR-0020 §6.5: push serialized tray menu to Tauri (delegate to
        tray_menu.maybe_publish_tray_menu). No-op on Electron/pystray."""
        from voice_typer.server.tray_menu import maybe_publish_tray_menu

        return maybe_publish_tray_menu(self)

    def _build_microphones_submenu(self) -> list:
        """Build the Microphones ▸ submenu — delegate."""
        from voice_typer.server.tray_menu import build_microphones_submenu

        return build_microphones_submenu(self)

    def _build_models_submenu(self) -> list:
        """Build a list of model MenuItems — cached models + More models link.

         delegates to tray_menu.build_models_submenu,
        which calls tray_models.build_models_menu_items for the actual
        MenuItem construction (: in-memory Config, not re-parsed).
        """
        from voice_typer.server.tray_menu import build_models_submenu

        # build_models_menu_items (tray_models.py) is the MenuItem builder.
        return build_models_submenu(self)

    def _display_hotkey(self) -> str:
        """Return the configured hotkey in a user-facing form (delegate)."""
        hotkey = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        return display_hotkey(hotkey)

    # #13: _wrap moved to tray_menu.wrap_callback; kept as static-method
    # alias for backwards compat with code calling TrayIcon._wrap(fn).
    _wrap = staticmethod(wrap_callback)

    # periodic update checker removed (dead code, broken
    # toggle, phoned home to GitHub). If reintroduced: default OFF, consent dialog, dedicated module.
