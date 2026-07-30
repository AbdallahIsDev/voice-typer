"""System tray icon using pystray, with dynamic state and menu.

Phase 2 minimal right-click menu: Toggle Dictation (hotkey) / Models /
Restart / Quit. Left-click + "Open App" launches (or focuses) the
Electron app; all settings / history / templates live in the Electron
window only.

CQ-004 / module-split history — each concern lives in its own module;
``TrayIcon`` below is a thin orchestrator of one-line delegates:
  - menu building → ``tray_menu.py`` (#13 / DT-FIX-9)
  - types → ``tray_types.py`` (ARCH-003)
  - icon rendering → ``tray_icon.py`` (ARCH-003)
  - i18n → ``tray_i18n.py`` (TRAY-008)
  - Wayland SNI detection → ``tray_wayland_detect.py``
  - elapsed-recording timer → ``tray_elapsed_timer.py``
  - window management → ``tray_window.py`` (DT-FIX-9 / DT-FIX-9b)
  - notifications → ``tray_notifications.py`` (DT-FIX-9 / DT-FIX-9b)

DT-FIX-9b (Phase 4.5 spaghetti split, DT-27): DT-FIX-9 extracted the
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
        self._tray_unavailable: bool = False  # ARCH-045: pystray.Icon() OSError
        # PVT-G5-001: tray-unavailable run() blocks on this Event.
        self._run_event: threading.Event = threading.Event()
        self._state = AppState.IDLE
        self._message = ""
        self._notifications_enabled = True
        self._microphones: list[dict] = []  # UX-2: mics submenu cache
        self._recording_started_at: float | None = None  # UX-11
        self._elapsed_timer: threading.Timer | None = None  # UX-11
        self._elapsed_timer_helper = ElapsedTimer(
            tick_callback=self._on_elapsed_tick,
            is_active=lambda: self._state == AppState.RECORDING,
            set_timer_ref=self._set_elapsed_timer_ref,
        )
        self._autostart_enabled = False
        self._cpu_fallback_active: bool = False  # SK-b
        # Pre-run state queue — flushed once the pystray event loop is live.
        self._pending_states: list[tuple[AppState, str]] = []
        self._pending_notifications: list[tuple[str, str]] = []
        self._queue_lock = threading.Lock()
        self._bg_work_fn: Callable | None = None
        self._bg_thread: threading.Thread | None = None
        self._hotkey: str = getattr(config, "hotkey", "<f2>") or "<f2>"
        self._cached_menu = None  # P4 #30: menu cache
        self._menu_cache_valid = False
        # AB-16 / DJ-36: cache-skip — skip ``_make_icon`` redraw when
        # ``state == _last_applied_state``. The 1s elapsed-recording tick
        # (UX-11) calls ``_apply_state`` every second; pre-AB-16 this
        # re-malloc'd a fresh PIL image + pystray icon handle on every
        # tick (and tickled the WinError 1402 stale-handle bug on Windows).
        # The tooltip (``self._icon.title``) is still updated unconditionally
        # so the elapsed ``mm:ss`` stays live.
        self._last_applied_state: AppState | None = None

    # ─── Public API ─────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        """Return the current tray application state."""
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
        """Update tray icon state and tooltip.

        PERF-005: only invalidate the menu cache on TRANSCRIBING ⇄
        non-TRANSCRIBING (Force Cancel visibility flips); RECORDING ⇄
        IDLE only changes the icon. UX-11: RECORDING ⇄ IDLE start/stop
        the elapsed timer (ER-54: monotonic clock). ADR-0020 §6.5: push
        icon+tooltip to Tauri; on TRANSCRIBING change also push the menu.
        """
        prev_state = self._state
        self._state = state
        self._message = message
        transcribing_changed = (prev_state == AppState.TRANSCRIBING) != (state == AppState.TRANSCRIBING)
        if transcribing_changed:
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
        if transcribing_changed:
            self._maybe_publish_tray_menu()

    def set_microphones(self, mics: list[dict] | None) -> None:
        """Cache the mic device list + invalidate the menu cache (UX-2).

        None/empty normalized to []. ADR-0020 §6.5: push to Tauri.
        """
        self._microphones = list(mics) if mics else []
        self._menu_cache_valid = False
        self._maybe_publish_tray_menu()

    def set_autostart_enabled(self, enabled: bool) -> None:
        """Update the cached autostart state."""
        self._autostart_enabled = enabled
        self._menu_cache_valid = False

    def set_notifications_enabled(self, enabled: bool) -> None:
        """Update the cached notifications state."""
        self._notifications_enabled = enabled
        self._menu_cache_valid = False

    def set_hotkey(self, hotkey: str) -> None:
        """Update the stored hotkey string for the next menu rebuild."""
        self._hotkey = hotkey
        self._menu_cache_valid = False
        self._maybe_publish_tray_menu()
        self._publish_tray_state()

    @staticmethod
    def _is_linux_wayland_without_sni() -> bool:
        """NEW-XPLAT-002: detect Linux Wayland without StatusNotifierItem."""
        return is_linux_wayland_without_sni()

    def refresh_config(self, config) -> None:
        """Replace the cached Config reference and rebuild the menu (ARCH-043)."""
        self._config = config
        self._hotkey = getattr(config, "hotkey", self._hotkey) or self._hotkey
        self._menu_cache_valid = False
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
        bg_work on a daemon thread: PVT-G5-001 ``VOICE_TYPER_NO_TRAY=1``
        env var; NEW-XPLAT-002 Linux Wayland without StatusNotifierItem
        (pystray GTK backend would hang on icon.run()); ARCH-045
        pystray.Icon() raised OSError. On all three ``_tray_unavailable``
        is set True and run() blocks on ``_run_event``. SK-b: subscribe
        to parakeet_cpu_fallback BEFORE the early-return paths.
        """
        self._bg_work_fn = self._wrap_bg_work(bg_work)

        try:
            from voice_typer.server import event_bus as _event_bus

            _event_bus.subscribe(self._on_parakeet_cpu_fallback)
        except Exception:
            log.debug("[TRAY] could not subscribe to parakeet_cpu_fallback", exc_info=True)

        import os

        # PVT-G5-001: explicit opt-out via env var.
        if os.environ.get("VOICE_TYPER_NO_TRAY") == "1":
            log.info(
                "[TRAY] VOICE_TYPER_NO_TRAY=1 set — skipping tray icon creation. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            if self._bg_work_fn:
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # NEW-XPLAT-002: Linux Wayland without StatusNotifierItem.
        if self._is_linux_wayland_without_sni():
            log.warning(
                "[TRAY] Linux Wayland session without StatusNotifierItem detected "
                "(common on Sway/Hyprland/dwl/river). Tray icon will not be created. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            if self._bg_work_fn:
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        menu = pystray.Menu(self._build_menu)
        try:
            self._icon = pystray.Icon(
                name="voice-typer",
                icon=_make_icon(AppState.IDLE),
                # PLAT-010: title is both tooltip AND a11y name.
                title=_("app_name"),
                menu=menu,
            )
        except TypeError as e:
            raise RuntimeError(f"Failed to create tray icon (pystray Menu construction error): {e}") from e
        except OSError as e:
            # ARCH-045: headless / Windows Server / no-explorer sessions.
            log.warning(
                "[TRAY] Could not create system tray icon (no tray available?). "
                "Hotkey and IPC server will continue to work, but tray menu "
                "and notifications are disabled. Original error: %s",
                e,
            )
            self._icon = None
            self._tray_unavailable = True
            if self._bg_work_fn:
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        if self._bg_work_fn:
            self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
            self._bg_thread.start()

        log.info("[TRAY] Tray icon created, background work started")

    def run(self) -> None:
        """Block the main thread with pystray's event loop.

        PVT-G5-001: when the tray is unavailable, block on ``_run_event``
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
        self._icon.run()

    def stop(self) -> None:
        """Stop the tray icon and exit the event loop (idempotent).

        PVT-G5-001: release ``_run_event``. SK-b: unsubscribe
        parakeet_cpu_fallback (set.discard — safe if never registered).
        """
        if self._icon:
            self._icon.stop()
            self._icon = None
        self._cancel_elapsed_timer()  # UX-11
        self._run_event.set()  # PVT-G5-001
        # AB-16 / DJ-36: clear the icon-state cache so a restarted tray
        # redraws the icon on the first ``_apply_state`` (no stale cache).
        self._last_applied_state = None

        try:
            from voice_typer.server import event_bus as _event_bus

            _event_bus.unsubscribe(self._on_parakeet_cpu_fallback)
        except Exception:
            log.debug("[TRAY] could not unsubscribe parakeet_cpu_fallback", exc_info=True)

        log.info("[SHUTDOWN] Tray icon stopped")

    # ─── Notifications (delegates to tray_notifications.py) ────────────

    def notify(self, title: str, message: str) -> None:
        """Show a notification if notifications are enabled (delegate).

        DT-FIX-9 / DT-FIX-9b: delegates to tray_notifications.notify. This
        body does NOT publish via the event bus (that path lives in the
        show_electron_notification IPC handler); the actual toast call
        ``self._icon.notify(message, title)`` lives in
        tray_notifications.do_notify (reached via _do_notify).
        """
        from voice_typer.server.tray_notifications import notify as _notify

        return _notify(self, title, message)

    def notify_safety(self, title: str, message: str) -> None:
        """Show a notification that bypasses the toggle (safety-critical).

        DT-FIX-9 / DT-FIX-9b: delegates to tray_notifications.notify_safety.
        """
        from voice_typer.server.tray_notifications import (
            notify_safety as _notify_safety,
        )

        return _notify_safety(self, title, message)

    def _do_notify(self, title: str, message: str) -> None:
        """Send a notification through the icon (delegate).

        DT-FIX-9 / DT-FIX-9b: delegates to tray_notifications.do_notify,
        which calls ``self._icon.notify(message, title)`` (pystray's
        native toast path — WinRT ToastNotification on Win10+).
        """
        from voice_typer.server.tray_notifications import do_notify as _do_notify

        return _do_notify(self, title, message)

    def _on_parakeet_cpu_fallback(self, event: dict) -> None:
        """SK-b: handle parakeet_cpu_fallback events (delegate)."""
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
        if self._cpu_fallback_active:  # SK-b
            title += " (CPU fallback)"
        if state == AppState.RECORDING and self._recording_started_at is not None:  # UX-11
            elapsed = time.monotonic() - self._recording_started_at  # ER-54
            title += f" ({self._format_elapsed(elapsed)})"
        if self._config:  # TRAY-022: model name
            model = getattr(self._config, "model_size", "")
            if model:
                title += f" [{model}]"
        hotkey = self._display_hotkey()  # TRAY-022: hotkey
        if hotkey:
            title += f" ({hotkey})"
        return title

    def _publish_tray_state(self) -> None:
        """ADR-0020 §6.5: push icon+tooltip to Tauri (emit tray_state event
        instead of mutating pystray Icon). No-op on Electron/pystray.
        Best-effort (hot path)."""
        from voice_typer.server.tray_menu import publish_tray_state

        icon_name = _APP_STATE_TO_ICON_NAME.get(self._state, "idle")
        tooltip = self._compute_tooltip(self._state, self._message)
        try:
            publish_tray_state(icon=icon_name, tooltip=tooltip)
        except Exception:
            log.debug(
                "[TRAY] publish_tray_state failed (state=%s)",
                self._state.value if hasattr(self._state, "value") else self._state,
                exc_info=True,
            )

    def _apply_state(self, state: AppState, message: str) -> None:
        """Apply state to the live icon (safe from any thread).

        AB-16 / DJ-36: skip the ``_make_icon`` redraw when
        ``state == self._last_applied_state`` — the icon PNG depends only
        on ``state``, not on the ``message`` / elapsed time. The 1s
        elapsed-recording tick (UX-11) re-enters here every second; the
        cache-skip avoids re-malloc'ing a fresh PIL image + pystray icon
        handle on every tick (and avoids tickling the WinError 1402
        stale-handle bug — CR-16 / GT-E1-8). The tooltip assignment is
        UNCONDITIONAL so the elapsed ``mm:ss`` stays live.
        """
        if not self._icon:
            return
        # AB-16 / DJ-36: only redraw the icon on a state CHANGE.
        if state != self._last_applied_state:
            try:
                self._icon.icon = _make_icon(state)
            except OSError as exc:
                # CR-16 / GT-E1-8: pystray Windows DestroyIcon stale-handle
                # bug (WinError 1402) during rapid icon updates — clear the
                # private _icon_handle so pystray re-creates it next call
                # (pystray pinned to >=0.19,<0.20 in pyproject.toml).
                #
                # S2-CR-71: if a future pystray release (0.20+) removes or
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
                        "1402. See S2-CR-71 / TODO S2-CR-16: replace the "
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

        AC-54: the previous implementation silently dropped queued
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
           notification as a toast (it already subscribes to
           ``tray_state`` for icon updates — adding a
           ``tray_fallback_notification`` channel is a single line
           in the renderer's `useAppStore`).
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

    # ─── UX-11 (FIX-10): elapsed-recording timer ──────────────────────

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Format seconds as mm:ss (under 1h) or h:mm:ss (1h+); delegates to
        ElapsedTimer.format_elapsed; kept for backward compat."""
        return ElapsedTimer.format_elapsed(seconds)

    def _on_elapsed_tick(self) -> None:
        """UX-11: refresh tooltip with latest elapsed time (1s tick while
        RECORDING). Re-applies state to pystray Icon + publishes to Tauri."""
        if self._icon is not None:
            self._apply_state(self._state, self._message)
        self._publish_tray_state()

    def _set_elapsed_timer_ref(self, timer: threading.Timer | None) -> None:
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

        DT-FIX-9 / DT-FIX-9b: delegates to tray_window.open_page. Tests
        that monkeypatch.setattr(tray, "_open_page", fake) keep working."""
        from voice_typer.server.tray_window import open_page

        return open_page(path)

    def _open_models_page(self) -> None:
        """Open Electron window + navigate to /models (delegate).

        DT-FIX-9 / DT-FIX-9b: delegates to tray_window.open_models_page,
        which calls self._open_page('/models') so patch keeps working."""
        from voice_typer.server.tray_window import open_models_page

        return open_models_page(self)

    def _confirm_quit_while_recording(self) -> None:
        """Quit immediately, regardless of recording state (delegate).

        DT-FIX-9 / DT-FIX-9b: delegates to
        tray_window.confirm_quit_while_recording."""
        from voice_typer.server.tray_window import (
            confirm_quit_while_recording,
        )

        return confirm_quit_while_recording(self)

    # ─── Menu building (delegates to tray_menu.py) ─────────────────────

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale (delegate to tray_menu.invalidate_menu_cache)."""
        from voice_typer.server.tray_menu import invalidate_menu_cache

        return invalidate_menu_cache(self)

    def _build_menu(self) -> tuple:
        """Build the tray menu (Models + Microphones submenus + shortcuts).

        DT-FIX-9 / DT-FIX-9b: delegates to tray_menu.build_menu_for_tray.
        Lambdas consult self.* at CALL TIME so patches keep working."""
        from voice_typer.server.tray_menu import build_menu_for_tray

        return build_menu_for_tray(self)

    def _maybe_publish_tray_menu(self) -> bool:
        """ADR-0020 §6.5: push serialized tray menu to Tauri (delegate to
        tray_menu.maybe_publish_tray_menu). No-op on Electron/pystray."""
        from voice_typer.server.tray_menu import maybe_publish_tray_menu

        return maybe_publish_tray_menu(self)

    def _build_microphones_submenu(self) -> list:
        """Build the Microphones ▸ submenu (UX-2) — delegate."""
        from voice_typer.server.tray_menu import build_microphones_submenu

        return build_microphones_submenu(self)

    def _build_models_submenu(self) -> list:
        """Build a list of model MenuItems — cached models + More models link.

        DT-FIX-9 / DT-FIX-9b: delegates to tray_menu.build_models_submenu,
        which calls tray_models.build_models_menu_items for the actual
        MenuItem construction (ARCH-037: in-memory Config, not re-parsed).
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

    # G4-M-57: TRAY-015 periodic update checker removed (dead code, broken
    # toggle, phoned home to GitHub). If reintroduced: default OFF, consent dialog, dedicated module.
