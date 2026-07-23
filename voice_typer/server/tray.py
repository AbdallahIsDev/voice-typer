"""System tray icon using pystray, with dynamic state and menu.

Phase 2: Minimal right-click menu:
- Toggle Dictation (hotkey)
- Models
- Restart
- Quit

Left-click + "Open App" launches the Electron app (or focuses it if already running).
All settings, history, templates, etc. live in the Electron window only.

CQ-004: This module is ~670 lines. It was considered for splitting but
kept as a single module because:
  - The TrayIcon class is a single cohesive unit (lifecycle + state + menu)
  - Splitting would create tight cross-file coupling (menu ↔ state ↔ notify)
  - The internal sections are clearly delineated with comment headers
  - Related logic (e.g. notification handling) stays together

Threading model:
- ``start()`` creates the icon and launches background work (model loading,
  hotkey registration, etc.) in a daemon thread.  It does NOT block.
- ``run()`` blocks the **main** thread with ``pystray.Icon.run()``.
  Call it from the main thread after ``start()``.
- State updates (icon, title, notifications) from the background thread are
  dispatched safely by pystray.
- Before ``run()`` starts, state / notification calls are queued and flushed
  once the event loop is live.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

# PERF-COLDSTART-001: lazy import — pystray's xorg backend calls
# Xlib.display.Display() at module import time, costing ~48 ms on the
# tray cold-start path and failing entirely without an X display
# (headless CI, bare Wayland). pystray is only used inside start() to
# build the Icon/Menu, so defer the real import to first attribute
# access. The proxy re-reads sys.modules on every access, so tests that
# monkeypatch voice_typer.server.tray.pystray (or sys.modules["pystray"])
# keep working unchanged. The ``from __future__ import annotations``
# above also stringifies the ``Optional[pystray.Icon]`` annotation in
# TrayIcon.__init__ so it no longer forces an eager import.
from voice_typer.server._lazy_import import lazy_module
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_linux
from voice_typer.server.tray_icon import _make_icon

# #13: menu building extracted to tray_menu.py (display_hotkey, wrap_callback,
# build_menu). tray.py now owns only pystray icon lifecycle + state queuing.
# build_menu is re-exported here so tests/test_e2e_smoke.py can assert
# ``tray.build_menu is tray_menu.build_menu``; the in-process menu is now
# assembled inline in TrayIcon._build_menu to surface Undo Last / Microphones /
# Settings / History / Help / conditional Force Cancel.
from voice_typer.server.tray_menu import (  # noqa: F401
    build_menu,
    display_hotkey,
    wrap_callback,
)

# ARCH-003: types extracted to tray_types.py; icon rendering to tray_icon.py
from voice_typer.server.tray_types import AppState, TrayController

pystray = lazy_module("pystray")

# TRAY-008: Localization for tray menu labels.
# Uses English as default. Wrap hardcoded strings with _() function.
# To add a new language, extend _TRAY_LABELS_LOCALES with a new locale
# dict and call set_tray_locale('es') from the IPC layer when the user
# changes the UI language in Settings.
_TRAY_LABELS_EN: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Toggle Dictation",
    "open_app": "Open App",
    "models": "Models",
    "restart": "Restart",
    "quit": "Quit",
    "about": "About",  # kept for potential in-app use
    "diagnostics": "Diagnostics",  # kept for potential in-app use
    "recording_active": "Recording active",
    "update_available": "Update Available",
    "version": "version",
    "force_cancel_transcription": "Cancel Transcription",
    # UX-1: Undo Last tray menu item.
    "undo_last": "Undo Last",
    # UX-2: Microphones submenu.
    "microphones": "Microphones",
    "more_microphones": "More microphones...",
    # UX-3: Force Cancel Stuck Transcription (renamed from Cancel Transcription
    # and made conditional on state == TRANSCRIBING).
    "force_cancel_stuck_transcription": "Force Cancel Stuck Transcription",
    # UX-33: Settings/History/Help quick shortcuts.
    "settings": "Settings...",
    "history": "History...",
    "help": "Help...",
    # UX-5: localized update-available notification body template.
    "update_available_body": "{app} {version} is available (you have {current})",
}

# TRAY-008: Spanish translations (proof of concept for tray i18n).
_TRAY_LABELS_ES: dict[str, str] = {
    "app_name": APP_NAME,
    "toggle_dictation": "Alternar Dictado",
    "open_app": "Abrir Aplicación",
    "models": "Modelos",
    "restart": "Reiniciar",
    "quit": "Salir",
    "about": "Acerca de",  # kept for potential in-app use
    "diagnostics": "Diagnósticos",  # kept for potential in-app use
    "recording_active": "Grabación activa",
    "update_available": "Actualización Disponible",
    "version": "versión",
    "force_cancel_transcription": "Cancelar Transcripción",
    "undo_last": "Deshacer Último",
    "microphones": "Micrófonos",
    "more_microphones": "Más micrófonos...",
    "force_cancel_stuck_transcription": "Forzar Cancelación de Transcripción Atascada",
    "settings": "Configuración...",
    "history": "Historial...",
    "help": "Ayuda...",
    "update_available_body": "{app} {version} está disponible (tienes {current})",
}

# TRAY-008: locale → label dict. Add new locales here.
_TRAY_LABELS_LOCALES: dict[str, dict[str, str]] = {
    "en": _TRAY_LABELS_EN,
    "es": _TRAY_LABELS_ES,
}

# TRAY-008: current tray locale (defaults to English).
_tray_locale: str = "en"


def set_tray_locale(locale: str) -> None:
    """TRAY-008: Set the tray menu locale.

    Called by the IPC layer when the user changes the UI language in
    Settings. Falls back to English if the locale is not supported.
    After calling this, the tray menu must be rebuilt for the new
    labels to take effect.
    """
    global _tray_locale
    _tray_locale = locale if locale in _TRAY_LABELS_LOCALES else "en"


def get_tray_locale() -> str:
    """TRAY-008: Return the current tray locale."""
    return _tray_locale


def register_tray_labels(locale: str, labels: dict[str, str]) -> None:
    """Register translated tray-menu labels for a locale, merging with existing.

    Updates ``_TRAY_LABELS_LOCALES`` so ``_()`` returns the pushed
    translations for keys present in ``labels``, falling back to English
    for missing keys. Re-registration merges new keys over the prior
    dict (so the renderer can push translations incrementally — e.g.
    one IPC call for ``open_app`` + ``quit``, a later one for
    ``toggle_dictation``).

    Called by the ``set_tray_locale`` IPC handler when the renderer
    pushes translated tray-menu strings for a locale.
    """
    existing = _TRAY_LABELS_LOCALES.get(locale, {})
    merged = {**existing, **labels}
    _TRAY_LABELS_LOCALES[locale] = merged


def _(key: str) -> str:
    """TRAY-008: Return the localized tray label for the given key.

    Looks up the key in the current locale's label dict, falling back
    to English, then to the key itself. This mirrors the i18n approach
    used in the Electron frontend (i18n.ts).
    """
    labels = _TRAY_LABELS_LOCALES.get(_tray_locale, _TRAY_LABELS_EN)
    if key in labels:
        return labels[key]
    # Fall back to English
    if key in _TRAY_LABELS_EN:
        return _TRAY_LABELS_EN[key]
    # Last resort: return the key itself
    return key


log = logging.getLogger(__name__)


# ARCH-003: types extracted to tray_types.py; icon rendering to tray_icon.py


# ADR-0020 §6.5: maps the internal ``AppState`` enum to the logical icon
# names accepted by the Tauri Rust host's ``tray_state`` listener
# (``src-tauri/src/tray.rs::load_tray_icon`` whitelists
# ``"idle" | "recording" | "transcribing" | "error"``). States without a
# dedicated tray-icon asset (``LOADING`` and ``CANCELLING``) fall back to
# a neighboring state so the Tauri tray icon still updates to something
# meaningful instead of staying frozen on the previous state's icon.
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
        # ARCH-045: set to True if pystray.Icon() raised OSError at start()
        # so callers can decide to skip tray-related operations.
        self._tray_unavailable: bool = False
        # PVT-G5-001: when the tray is unavailable (Wayland-without-SNI,
        # headless session, or VOICE_TYPER_NO_TRAY=1), ``run()`` blocks
        # the main thread on this Event instead of entering the pystray
        # event loop. ``stop()`` sets the event so ``run()`` returns and
        # the main thread can exit cleanly. Mirrors the
        # ``icon.stop()`` -> ``icon.run()`` return contract on the
        # pystray path.
        self._run_event: threading.Event = threading.Event()
        self._state = AppState.IDLE
        self._message = ""
        self._notifications_enabled = True
        # UX-2 (FIX-10): cached microphone list for the Microphones ▸
        # submenu. Populated by ``set_microphones`` (called from the
        # IPC layer when the device list changes) and invalidated by
        # ``set_microphones`` setting ``_menu_cache_valid = False``.
        # Empty list until the first push — the submenu always renders
        # (with a single ``More microphones...`` entry) so the user can
        # open the Settings page to pick a device even before the
        # backend has enumerated any.
        self._microphones: list[dict] = []
        # UX-11 (FIX-10): elapsed-recording timer. ``_recording_started_at``
        # is set when state transitions INTO RECORDING and cleared when
        # it transitions back to IDLE. ``_elapsed_timer`` is a daemon
        # ``threading.Timer`` that ticks every 1s to refresh the tray
        # tooltip with the current ``mm:ss`` (or ``h:mm:ss``) elapsed
        # time. Both are ``None`` when not recording.
        self._recording_started_at: float | None = None
        self._elapsed_timer: threading.Timer | None = None
        self._autostart_enabled = False
        # SK-b: parakeet_engine emits ``{"type": "parakeet_cpu_fallback"}``
        # when GPU transcription fails and it falls back to CPU. We
        # subscribe to that event (see ``_on_parakeet_cpu_fallback``) so
        # the tray tooltip can show a "(CPU fallback)" suffix — the
        # user can see at a glance why transcription is slower. The
        # user-facing toast is published separately by parakeet_engine
        # as a ``"notification"`` event, so we do NOT duplicate the
        # notification here.
        self._cpu_fallback_active: bool = False

        # G4-M-57: TRAY-015 update-checker dead code removed. The
        # previous ``_update_check_timer`` / ``_check_updates`` fields
        # and the ``start_update_checker`` / ``_do_update_check`` /
        # ``_schedule_update_check`` methods have been deleted — the
        # offline-first app does not phone home for updates, and the
        # feature had a broken disable toggle (the timer kept running
        # even when ``check_updates`` was False because the reschedule
        # call in the ``finally`` block ignored the flag). If update
        # checks are ever reintroduced, they MUST go through an explicit
        # consent gate (``check_updates: bool = False`` in Config with
        # an in-app opt-in dialog) and live in a dedicated module.

        # Pre-run state queue — flushed once pystray event loop is live
        self._pending_states: list[tuple[AppState, str]] = []
        self._pending_notifications: list[tuple[str, str]] = []
        self._queue_lock = threading.Lock()
        self._bg_work_fn: Callable | None = None
        self._bg_thread: threading.Thread | None = None
        self._hotkey: str = getattr(config, "hotkey", "<f2>") or "<f2>"

        # P4 #30: Menu cache
        self._cached_menu = None
        self._menu_cache_valid = False

    # ─── Public API ─────────────────────────────────────────────────────

    @property
    def state(self) -> AppState:
        """Return the current tray application state.

        Returns:
            The current AppState enum value (e.g. IDLE, RECORDING, PROCESSING).
        """
        return self._state

    def set_state(self, state: AppState, message: str = "") -> None:
        """Update tray icon state and tooltip.

        PERF-005: previously this invalidated the menu cache on every
        state change (recording start/stop), causing the full menu to
        be rebuilt.  The menu structure doesn't change on most state
        transitions — only the icon does. The exception is the
        TRANSCRIBING state, which gates the visibility of the
        ``Force Cancel Stuck Transcription`` menu item (UX-3): the
        cache is invalidated only on TRANSCRIBING ↔ non-TRANSCRIBING
        transitions so the item appears/disappears promptly.

        UX-11: RECORDING ↔ IDLE transitions also start/stop the
        elapsed-recording timer that refreshes the tooltip with the
        current ``mm:ss`` elapsed time.

        Args:
            state: The new AppState to set.
            message: Optional status message for the tray tooltip.
        """
        prev_state = self._state
        self._state = state
        self._message = message
        # UX-3: invalidate menu cache on TRANSCRIBING transitions so the
        # Force Cancel Stuck Transcription item appears/disappears. We
        # only invalidate when the TRANSCRIBING-ness actually changes
        # (not on every state transition) to preserve PERF-005's
        # optimization for the common RECORDING ⇄ IDLE path.
        transcribing_changed = (prev_state == AppState.TRANSCRIBING) != (state == AppState.TRANSCRIBING)
        if transcribing_changed:
            self._menu_cache_valid = False
        # UX-11: manage the elapsed-recording timer on RECORDING ⇄ IDLE.
        if state == AppState.RECORDING and prev_state != AppState.RECORDING:
            self._recording_started_at = time.time()
            self._start_elapsed_timer()
        elif state != AppState.RECORDING and prev_state == AppState.RECORDING:
            self._cancel_elapsed_timer()
            self._recording_started_at = None
        if self._icon:
            self._apply_state(state, message)
        else:
            with self._queue_lock:
                self._pending_states.append((state, message))

        # ADR-0020 §6.5: push icon + tooltip updates to the Tauri host so
        # the native tray icon reflects the new state. Always emitted —
        # even on RECORDING ⇄ IDLE (the icon color + tooltip suffix
        # change). The publish helper is a no-op on the Electron/pystray
        # runtime (guards on TAURI_SIDECAR=1).
        self._publish_tray_state()
        # When the TRANSCRIBING-ness changed, the menu structure changed
        # too (Force Cancel Stuck Transcription item appears/disappears)
        # — push the new menu model to the Tauri host. Non-TRANSCRIBING
        # transitions only change the icon/tooltip (handled by
        # ``_publish_tray_state`` above), so we don't waste a menu
        # rebuild on every RECORDING ⇄ IDLE toggle.
        if transcribing_changed:
            self._maybe_publish_tray_menu()

    def set_microphones(self, mics: list[dict] | None) -> None:
        """Cache the microphone device list and invalidate the menu cache.

        UX-2 (FIX-10): the cached list backs the Microphones ▸ submenu
        (``_build_microphones_submenu``). ``None`` and empty inputs are
        normalized to ``[]`` so callers never have to special-case a
        missing list. Invalidates the menu cache so the next right-click
        reflects the new device set.

        Args:
            mics: List of microphone device dicts (``{"id", "name",
                "default"}``), or ``None`` / ``[]`` for "no devices".
        """
        self._microphones = list(mics) if mics else []
        self._menu_cache_valid = False
        # ADR-0020 §6.5: push the new Microphones submenu to the Tauri
        # host. No-op on the Electron/pystray runtime.
        self._maybe_publish_tray_menu()

    def set_autostart_enabled(self, enabled: bool) -> None:
        """Update the cached autostart state.

        Args:
            enabled: Whether autostart is enabled.
        """
        self._autostart_enabled = enabled
        self._menu_cache_valid = False

    def set_notifications_enabled(self, enabled: bool) -> None:
        """Update the cached notifications state.

        Args:
            enabled: Whether notifications are enabled.
        """
        self._notifications_enabled = enabled
        self._menu_cache_valid = False

    def set_hotkey(self, hotkey: str) -> None:
        """Update the stored hotkey string for the next menu rebuild.

        Args:
            hotkey: The new hotkey string (e.g. '<f2>').
        """
        self._hotkey = hotkey
        self._menu_cache_valid = False
        # ADR-0020 §6.5: the hotkey appears in the "Toggle Dictation
        # (<hotkey>)" menu label and in the tooltip — push the updated
        # menu model + tooltip to the Tauri host. No-op on the
        # Electron/pystray runtime.
        self._maybe_publish_tray_menu()
        self._publish_tray_state()

    @staticmethod
    def _is_linux_wayland_without_sni() -> bool:
        """NEW-XPLAT-002: detect Linux Wayland without StatusNotifierItem.

        Returns True if ALL of the following are true:
          1. We're on Linux (sys.platform starts with "linux").
          2. The session is Wayland (XDG_SESSION_TYPE=wayland).
          3. No StatusNotifierItem watcher is registered on the D-Bus
             session bus.

        Detection of (3) is best-effort: we try to call the
        ``org.kde.StatusNotifierWatcher`` service via D-Bus.  If the
        call fails (service unknown, bus unavailable, dbus module
        missing), we assume SNI is not available — which matches the
        user's complaint that "the tray silently fails" on Sway/Hyprland.

        We DON'T try to detect specific compositors by name (Sway,
        Hyprland, etc.) because new compositors appear regularly and
        the SNI-availability check is the actual contract that
        matters.
        """
        import os

        if not is_linux():
            return False
        if os.environ.get("XDG_SESSION_TYPE") != "wayland":
            return False
        # Try to detect the StatusNotifierItem watcher service on D-Bus.
        try:
            import dbus  # type: ignore[import-untyped]
        except ImportError:
            # No dbus module — we can't detect SNI programmatically.
            # Conservative: assume SNI is NOT available (matches the
            # user's complaint of "silent failure" on minimal Wayland
            # setups that typically don't have python-dbus installed).
            log.debug(
                "[TRAY] Wayland session detected but python-dbus not installed; "
                "assuming StatusNotifierItem is unavailable."
            )
            return True
        try:
            bus = dbus.SessionBus()
            # The SNI watcher is the well-known name registered by
            # the compositor's tray (e.g. waybar, swaync, KDE's
            # plasma-workspace).  If it's not registered, the
            # NameHasOwner call returns False.
            proxy = bus.get_object(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
            )
            has_owner = bool(
                proxy.NameHasOwner(
                    "org.kde.StatusNotifierWatcher",
                    dbus_interface="org.freedesktop.DBus",
                )
            )
            if not has_owner:
                log.info(
                    "[TRAY] Wayland session detected and org.kde.StatusNotifierWatcher "
                    "is NOT registered on the D-Bus session bus. Tray will be skipped."
                )
                return True
            return False
        except Exception as exc:
            log.debug(
                "[TRAY] D-Bus check for StatusNotifierItem failed: %s — assuming SNI is unavailable.",
                exc,
            )
            return True

    def refresh_config(self, config) -> None:
        """Replace the cached Config reference and rebuild the menu.

        ARCH-043: tray held a reference to the original ``Config`` instance.
        IPC handlers like ``set_config`` call ``setattr`` on the live object,
        but if the caller ever swapped the *instance* (e.g. by reloading
        from disk), the tray kept rendering the stale object's attributes.
        Now callers can hand us the fresh instance + invalidate the menu.
        """
        self._config = config
        # Mirror set_hotkey(): pick up the new hotkey string immediately so
        # the next menu rebuild reflects the user's current setting.
        self._hotkey = getattr(config, "hotkey", self._hotkey) or self._hotkey
        self._menu_cache_valid = False
        # ADR-0020 §6.5: push the rebuilt menu model + tooltip to the
        # Tauri host. The config can affect the hotkey label, model name
        # in the tooltip, left-click default action, and Microphones
        # submenu (via ``tray_left_click_action``). No-op on the
        # Electron/pystray runtime.
        self._maybe_publish_tray_menu()
        self._publish_tray_state()

    def _wrap_bg_work(self, bg_work: Callable | None) -> Callable | None:
        """ADR-0020 §6.5: wrap ``bg_work`` so the initial tray menu is
        published to the Tauri host after background setup completes.

        ``bg_work`` (typically model preloading + hotkey registration)
        runs on a daemon thread from :meth:`start`. Once it finishes,
        the controller + config + microphone state are stable enough
        that the serialized menu model is meaningful — so we publish
        it to the Tauri host. Without this, the Tauri tray would show
        only the empty placeholder menu until the user changed a
        setting that triggered ``invalidate_menu_cache``.

        Returns ``None`` when ``bg_work`` is ``None`` (preserves the
        existing ``if self._bg_work_fn:`` guards in the three early-return
        paths of :meth:`start`). The wrapper runs the original ``bg_work``
        in a ``try/finally`` so the menu is published even if ``bg_work``
        raises (a failed preload shouldn't leave the tray menu frozen).
        """
        if bg_work is None:
            return None

        def _wrapped() -> None:
            try:
                bg_work()
            finally:
                # Best-effort: a failure here shouldn't mask the original
                # exception (if any). The ``finally`` ensures the menu
                # is pushed even when bg_work raised.
                try:
                    self._maybe_publish_tray_menu()
                    self._publish_tray_state()
                except Exception:
                    log.debug(
                        "[TRAY] post-bg_work tray publish failed",
                        exc_info=True,
                    )

        return _wrapped

    def start(self, bg_work: Callable | None = None) -> None:
        """Create the tray icon and start background work.

        ARCH-045: pystray.Icon() can raise OSError on Windows Server /
        headless sessions without a system tray (no explorer.exe, RDP
        with /admin, etc.). Previously this crashed the background
        thread silently. We now catch the OSError, log it, and notify
        the user that the tray is unavailable — the app keeps running
        so the hotkey + IPC server still work.

        NEW-XPLAT-002: on Linux Wayland compositors that don't implement
        the StatusNotifierItem D-Bus interface (Sway, Hyprland, dwl,
        river, bare wlroots), pystray has no tray to attach to.  We
        detect this case proactively and skip tray creation rather
        than letting pystray hang on ``icon.run()``.  The app remains
        usable via hotkey + IPC + Electron window.

        PVT-G5-001: honor the ``VOICE_TYPER_NO_TRAY=1`` environment
        variable to force the tray-unavailable path. Useful for
        headless CI, embedded deployments, and users who explicitly
        want the app to run without a tray icon (the hotkey + IPC
        server still work). Mirrors the Wayland-without-SNI fallback:
        we set ``_tray_unavailable = True`` and start ``bg_work`` on a
        daemon thread, then ``run()`` blocks the main thread on a
        ``threading.Event`` instead of entering the pystray event
        loop.
        """
        self._bg_work_fn = self._wrap_bg_work(bg_work)

        # SK-b: subscribe to ``parakeet_cpu_fallback`` events so the
        # tray can surface a "(CPU fallback)" status suffix. Idempotent
        # (set semantics) — safe to call multiple times. Subscribed
        # BEFORE the early-return paths below so the unavailable-path
        # tray still observes the event (the tooltip suffix is a no-op
        # when ``_icon`` is None, but the subscription is harmless and
        # keeps the wiring consistent across paths).
        try:
            from voice_typer.server import event_bus as _event_bus

            _event_bus.subscribe(self._on_parakeet_cpu_fallback)
        except Exception:
            log.debug(
                "[TRAY] could not subscribe to parakeet_cpu_fallback event",
                exc_info=True,
            )

        # PVT-G5-001: explicit opt-out via env var. The user (or the
        # test harness) sets ``VOICE_TYPER_NO_TRAY=1`` to skip tray
        # creation entirely. This mirrors the Wayland-without-SNI path
        # below: the app remains usable via hotkey + IPC + Electron
        # window, and ``run()`` blocks on a ``threading.Event`` rather
        # than raising RuntimeError.
        import os

        if os.environ.get("VOICE_TYPER_NO_TRAY") == "1":
            log.info(
                "[TRAY] VOICE_TYPER_NO_TRAY=1 set — skipping tray icon creation. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            if self._bg_work_fn:
                # RACE-008: daemon=True — see comment at the canonical
                # bg_thread spawn site in the Wayland branch below.
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # NEW-XPLAT-002: Linux Wayland without StatusNotifierItem.
        # pystray's GTK backend hangs forever on `icon.run()` when
        # there's no StatusNotifierItem service to register with.
        # Detect this case and skip tray creation entirely.
        if self._is_linux_wayland_without_sni():
            log.warning(
                "[TRAY] Linux Wayland session without StatusNotifierItem detected "
                "(common on Sway/Hyprland/dwl/river). Tray icon will not be created. "
                "The app remains usable via the global hotkey and the Electron window."
            )
            self._icon = None
            self._tray_unavailable = True
            # Still start background work so the app boots normally.
            # MINOR FIX: previously this path assigned a *different*
            # attribute name from the one used everywhere else (the
            # canonical name is ``self._bg_thread``).  Neither name
            # is referenced in stop() today, so no runtime failure —
            # but if stop() is ever extended to join the background
            # thread, the Wayland path's thread would be orphaned.
            # Use the canonical name for consistency.
            if self._bg_work_fn:
                # RACE-008: daemon=True is acceptable because bg_work
                # only does model preloading/hotkey registration — no
                # critical cleanup. The tray's stop() method joins with
                # timeout; on force-kill the OS reclaims the thread.
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # Phase 2: Build minimal menu
        menu = pystray.Menu(self._build_menu)

        try:
            self._icon = pystray.Icon(
                name="voice-typer",
                icon=_make_icon(AppState.IDLE),
                # PLAT-010: title serves as both tooltip AND accessible
                # name for screen readers. pystray does not expose a
                # separate accessible_name parameter — title is the
                # canonical way to set the a11y label on all backends
                # (Windows: NIF_TIP, macOS: accessibilityLabel, Linux:
                # AppIndicator tooltip). The title is kept non-empty
                # and localized via _() so screen readers announce the
                # app name correctly.
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
            # Still start background work so the app boots normally.
            if self._bg_work_fn:
                # RACE-008: daemon=True — see comment at the canonical
                # bg_thread spawn site above.
                self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
                self._bg_thread.start()
            return

        # Start background work
        if self._bg_work_fn:
            # RACE-008: daemon=True — see comment at the canonical
            # bg_thread spawn site above.
            self._bg_thread = threading.Thread(target=self._bg_work_fn, daemon=True)
            self._bg_thread.start()

        log.info("[TRAY] Tray icon created, background work started")

    def run(self) -> None:
        """Block the main thread with pystray's event loop.

        PVT-G5-001: when the tray is unavailable (Wayland-without-SNI,
        headless session, or ``VOICE_TYPER_NO_TRAY=1``), block on
        ``self._run_event`` instead of raising RuntimeError. The event
        is set by ``stop()`` so the main thread can exit cleanly. This
        keeps the app usable on platforms where pystray cannot run
        (the hotkey + IPC server + Electron window still work). The
        RuntimeError path is retained only when ``start()`` was never
        called (``_icon`` is None AND ``_tray_unavailable`` is False),
        which signals a programming error rather than an unsupported
        environment.
        """
        # PVT-G5-001: tray-unavailable path — block on the Event
        # instead of raising. This is the contract every caller of
        # ``tray.run()`` (notably ``app.py:VoiceTyperApp.start``)
        # relies on to keep the main thread alive while the IPC server
        # + hotkey backends run on daemon threads.
        if self._tray_unavailable and self._icon is None:
            # Flush queued state/notifications so callers that set
            # state before run() (e.g. app.start sets AppState.LOADING)
            # don't lose them. Best-effort — no icon means most
            # state/notify calls are no-ops, but the queue flush keeps
            # the queue from growing unbounded.
            with self._queue_lock:
                self._pending_states.clear()
                self._pending_notifications.clear()
            log.info(
                "[TRAY] Tray unavailable — main thread blocking on Event "
                "(stop() will release). Hotkey + IPC server still active."
            )
            self._run_event.wait()
            return

        if self._icon is None:
            raise RuntimeError("call start() before run()")

        # Flush queued state
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
        """Stop the tray icon and exit the event loop.

        PVT-G5-001: also release ``_run_event`` so the
        tray-unavailable ``run()`` path unblocks. Idempotent — safe to
        call when no icon exists (the Wayland/no-tray path) or when
        ``stop()`` has already been called.
        """
        if self._icon:
            self._icon.stop()
            self._icon = None
        # UX-11: cancel the elapsed-recording timer so it doesn't keep
        # firing after the tray is stopped. Idempotent (no-op if no
        # timer was running).
        self._cancel_elapsed_timer()
        # PVT-G5-001: release the main thread blocked in run() on the
        # tray-unavailable path. No-op if run() is on the pystray path
        # (the Event is never waited on there) or if stop() is called
        # before run() (the Event is already cleared; setting it is
        # harmless because run() will see _tray_unavailable and wait,
        # but the next stop() will release it).
        self._run_event.set()

        # SK-b: unsubscribe the parakeet_cpu_fallback handler so a
        # stopped tray doesn't keep receiving engine events. Safe to
        # call with a callback that was never registered (no-op via
        # ``set.discard``).
        try:
            from voice_typer.server import event_bus as _event_bus

            _event_bus.unsubscribe(self._on_parakeet_cpu_fallback)
        except Exception:
            log.debug(
                "[TRAY] could not unsubscribe parakeet_cpu_fallback event",
                exc_info=True,
            )

        log.info("[SHUTDOWN] Tray icon stopped")

    def notify(self, title: str, message: str) -> None:
        """Show a notification if notifications are enabled.

        TRAY-025 / TRAY-035: (removed) Notification re-display was
        previously stored and accessible via the tray menu; that menu
        item has been removed since the OS manages notification
        lifetime.
        """
        if not self._notifications_enabled:
            return
        if self._icon:
            self._do_notify(title, message)
        else:
            with self._queue_lock:
                self._pending_notifications.append((title, message))

    # ─── Internals ──────────────────────────────────────────────────────

    def _compute_tooltip(self, state: AppState, message: str) -> str:
        """Compute the tray tooltip string for the given state + message.

        Extracted from ``_apply_state`` so the Tauri-side ``tray_state``
        event (``_publish_tray_state``) re-uses the same formatting
        logic without duplicating it. Keeping both code paths in sync is
        critical: a divergence would mean the pystray tooltip and the
        Tauri tray tooltip show different strings for the same state.

        The tooltip layout is:
            ``<APP_NAME> — <message|state> [(CPU fallback)] [(mm:ss)] [<model>] (<hotkey>)``

        Each suffix is optional and only appended when its condition
        holds. ``APP_NAME`` is the localized app name; ``state.value``
        is used when no explicit ``message`` is provided (and the state
        is not IDLE — IDLE shows just the app name).
        """
        title = APP_NAME
        if message:
            title += f" — {message}"
        elif state != AppState.IDLE:
            title += f" — {state.value}"
        # SK-b: append "(CPU fallback)" suffix when parakeet_engine has
        # fallen back from CUDA to CPU. Published via the
        # ``parakeet_cpu_fallback`` event; see
        # ``_on_parakeet_cpu_fallback``.
        if self._cpu_fallback_active:
            title += " (CPU fallback)"
        # UX-11 (FIX-10): append elapsed ``mm:ss`` (or ``h:mm:ss``) when
        # recording so the user can see how long the current recording
        # has been running. Skipped for non-RECORDING states and when
        # ``_recording_started_at`` is None (e.g. before the first
        # RECORDING transition).
        if state == AppState.RECORDING and self._recording_started_at is not None:
            elapsed = time.time() - self._recording_started_at
            title += f" ({self._format_elapsed(elapsed)})"
        # TRAY-022: Include model name and hotkey in tooltip
        if self._config:
            model = getattr(self._config, "model_size", "")
            if model:
                title += f" [{model}]"
        hotkey = self._display_hotkey()
        if hotkey:
            title += f" ({hotkey})"
        return title

    def _publish_tray_state(self) -> None:
        """ADR-0020 §6.5: push icon + tooltip updates to the Tauri host.

        Mirrors ``_apply_state`` for the Tauri runtime: instead of
        mutating the pystray ``Icon`` object (which doesn't exist under
        Tauri), we emit a ``tray_state`` event the Rust host listens for
        (``src-tauri/src/tray.rs::create_tray`` registers a listener
        that calls ``tray.set_icon(...)`` and ``tray.set_tooltip(...)``
        with the payload).

        No-op on the Electron/pystray runtime —
        :func:`voice_typer.server.tray_menu.publish_tray_state` guards on
        ``TAURI_SIDECAR=1``. Safe to call headless — never touches
        pystray. Best-effort: errors are logged at debug level so a
        misbehaving event bus doesn't break the caller (typically
        ``set_state``, which is on the hot path for state transitions).
        """
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
        """Apply state to the live icon (safe from any thread)."""
        if not self._icon:
            return
        try:
            self._icon.icon = _make_icon(state)
        except OSError:
            # pystray Windows bug: DestroyIcon on stale handle (WinError 1402)
            # during rapid icon updates.  The stale handle prevents any future
            # icon updates from working, so clear it to let pystray re-create
            # the icon handle on the next call.
            #
            # CR-16: `_icon_handle` is a PRIVATE pystray attribute (not part
            # of the public API). pyproject.toml pins pystray to the 0.19
            # minor series (`>=0.19,<0.20`) so this workaround stays
            # effective.
            # TODO CR-16: file upstream issue for public reset_icon_handle() API
            # so we can drop the private-attr access.
            if hasattr(self._icon, "_icon_handle"):
                self._icon._icon_handle = None
        self._icon.title = self._compute_tooltip(state, message)

    # ─── UX-11 (FIX-10): elapsed-recording timer ──────────────────────

    @staticmethod
    def _format_elapsed(seconds: float) -> str:
        """Format ``seconds`` as ``mm:ss`` (under 1h) or ``h:mm:ss`` (1h+).

        Negative inputs are clamped to 0. Used by ``_apply_state`` to
        append the elapsed recording time to the tray tooltip.
        """
        total = max(0, int(seconds))
        hours, rem = divmod(total, 3600)
        minutes, secs = divmod(rem, 60)
        if hours > 0:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    def _start_elapsed_timer(self) -> None:
        """Start (or restart) the 1-second elapsed-recording tooltip timer.

        UX-11 (FIX-10): a daemon ``threading.Timer`` that ticks every
        1 second and re-applies the current state so the tray tooltip
        updates with the latest ``mm:ss``. The timer reschedules itself
        on each tick as long as the state is still RECORDING. Cancels
        any prior timer first so rapid RECORDING → RECORDING transitions
        (e.g. from a stop/restart race) don't leak overlapping timers.
        """
        self._cancel_elapsed_timer()

        def _tick() -> None:
            # Re-check state inside the tick: a stop() may have fired
            # between the timer being scheduled and now. If we're no
            # longer recording, just exit without rescheduling.
            if self._state != AppState.RECORDING:
                return
            try:
                if self._icon is not None:
                    self._apply_state(self._state, self._message)
                # ADR-0020 §6.5: under Tauri there is no pystray Icon
                # to refresh; emit a tray_state event instead so the
                # Rust host updates the native tooltip with the latest
                # elapsed time. No-op on the Electron/pystray runtime.
                self._publish_tray_state()
            except Exception:
                log.debug(
                    "[TRAY] elapsed-timer tick failed to refresh tooltip",
                    exc_info=True,
                )
            # Reschedule only if still recording. The check happens AFTER
            # _apply_state so a state change during the apply is caught.
            if self._state == AppState.RECORDING:
                t = threading.Timer(1.0, _tick)
                t.daemon = True
                self._elapsed_timer = t
                t.start()

        t = threading.Timer(1.0, _tick)
        t.daemon = True
        self._elapsed_timer = t
        t.start()

    def _cancel_elapsed_timer(self) -> None:
        """Cancel the elapsed-recording timer if running.

        Idempotent — safe to call when no timer exists (e.g. before the
        first RECORDING transition). Clears ``_elapsed_timer`` to ``None``
        so ``set_state`` assertions on ``_elapsed_timer is None`` work.
        """
        t = self._elapsed_timer
        self._elapsed_timer = None
        if t is not None:
            try:
                t.cancel()
            except Exception:
                log.debug("[TRAY] elapsed-timer cancel failed", exc_info=True)

    def _on_parakeet_cpu_fallback(self, event: dict) -> None:
        """SK-b: handle ``parakeet_cpu_fallback`` events from parakeet_engine.

        parakeet_engine publishes ``{"type": "parakeet_cpu_fallback",
        "data": {"device": "cpu", "reason": "..."}}`` when GPU
        transcription fails and it falls back to CPU. We mark
        ``_cpu_fallback_active`` so the next ``_apply_state`` call
        appends a "(CPU fallback)" suffix to the tooltip — the user can
        see at a glance why transcription is slower. The user-facing
        toast is already published separately as a ``"notification"``
        event by parakeet_engine, so we do NOT duplicate the
        notification here.

        Defensive: ignores malformed payloads (non-dict, missing
        ``type``). The event_bus subscriber contract is "callback gets
        a dict"; we still validate to be safe against a misbehaving
        publisher.
        """
        if not isinstance(event, dict):
            return
        if event.get("type") != "parakeet_cpu_fallback":
            return
        self._cpu_fallback_active = True
        # Re-apply the current state so the tooltip updates immediately
        # with the "(CPU fallback)" suffix. Best-effort — if the icon
        # is None (tray-unavailable path) ``_apply_state`` is a no-op.
        try:
            self._apply_state(self._state, self._message)
        except Exception:
            log.debug(
                "[TRAY] could not apply CPU-fallback state to tray icon",
                exc_info=True,
            )

    def notify_safety(self, title: str, message: str) -> None:
        """Show a notification that bypasses the notification toggle.

        RACE-022: guard _pending_notifications append with _queue_lock
        to prevent race with the flush in run().
        """
        if self._icon:
            self._do_notify(title, message)
        else:
            with self._queue_lock:
                self._pending_notifications.append((title, message))

    def _do_notify(self, title: str, message: str) -> None:
        """Send a notification through the icon."""
        try:
            self._icon.notify(message, title)
        except Exception as e:
            log.warning("[TRAY] Notification failed: %s", e)

    @staticmethod
    def _bring_electron_to_front() -> bool:
        """#13: Delegates to tray_window.bring_electron_to_front()."""
        from voice_typer.server.tray_window import bring_electron_to_front

        return bring_electron_to_front()

    def open_electron_window(self) -> None:
        """Open (or focus) the Electron dashboard window.

        #13: Delegates to tray_window.open_electron_window() which handles
        TCP push, Win32 focus, and Electron launch in order.
        """
        from voice_typer.server.tray_window import open_electron_window as _open

        _open()

    def invalidate_menu_cache(self) -> None:
        """Mark the menu cache as stale so it rebuilds on next right-click.

        ARCH-049: on Windows, pystray's ``_on_notify`` displays the menu via
        ``TrackPopupMenuEx`` with the STORED ``HMENU`` handle — it does NOT
        re-call the ``_build_menu`` callback on subsequent right-clicks because
        ``_update_menu()`` is only called during icon creation.  We must force
        pystray to rebuild its Win32 menu handle by calling
        ``_icon._update_menu()`` here, which triggers the ``_build_menu``
        callback and reads the latest config values.

        Thread safety: ``_update_menu()`` calls ``DestroyMenu`` /
        ``CreatePopupMenu`` / ``InsertMenuItem`` — all Win32 API calls that
        are thread-safe.  The old handle is destroyed and replaced atomically
        (CPython GIL protects the tuple assignment).  A concurrent right-click
        during the brief rebuild window simply shows the previous menu or
        nothing — the user can right-click again.
        """
        self._menu_cache_valid = False
        # ARCH-049: force pystray to rebuild its Win32 menu handle so the
        # next right-click reflects the current config state.
        if self._icon is not None:
            try:
                self._icon._update_menu()
            except Exception:
                log.debug("[TRAY] _icon._update_menu() failed", exc_info=True)
        # ADR-0020 §6.5: push serialized menu to Tauri sidecar host.
        self._maybe_publish_tray_menu()

    def _build_menu(self) -> tuple:
        """Build the tray menu with Models + Microphones submenus and quick shortcuts.

        Menu structure (UX-1/UX-2/UX-3/UX-33):
          - Open App (default/bold when ``tray_left_click_action == "open_app"``)
          - Toggle Dictation (default/bold when action == "toggle_dictation")
          - Undo Last                                  (UX-1)
          - Force Cancel Stuck Transcription           (UX-3, only when state == TRANSCRIBING)
          - --- separator ---
          - Models ▸
          - Microphones ▸                              (UX-2)
          - --- separator ---
          - Settings...                                (UX-33)
          - History...                                 (UX-33)
          - Help...                                    (UX-33)
          - --- separator ---
          - Restart
          - Quit

        The menu is cached on the TrayIcon instance (``_cached_menu``)
        and only rebuilt when ``_menu_cache_valid`` is False (set by
        ``set_microphones`` / ``set_hotkey`` / ``refresh_config`` /
        ``invalidate_menu_cache`` and on TRANSCRIBING state transitions
        via ``set_state``).

        About, Diagnostics, and Show Last Notification have been removed
        from the tray menu (they remain available in the Electron app).
        """
        if self._menu_cache_valid and self._cached_menu is not None:
            return self._cached_menu

        hotkey_str = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        hotkey_label = display_hotkey(hotkey_str)
        left_click = getattr(self._config, "tray_left_click_action", "open_app") or "open_app"
        dictation_default = left_click == "toggle_dictation"
        open_app_default = left_click == "open_app"

        items: list = []

        # Open App (first; default/bold depends on left_click_action).
        items.append(
            pystray.MenuItem(
                _("open_app"),
                wrap_callback(self.open_electron_window),
                default=open_app_default,
            )
        )
        # Toggle Dictation (with hotkey hint in the label).
        items.append(
            pystray.MenuItem(
                f"{_('toggle_dictation')} ({hotkey_label})",
                wrap_callback(self._controller.toggle_dictation),
                default=dictation_default,
            )
        )
        # UX-1: Undo Last — surfaces the previously-unreachable undo_last IPC.
        items.append(
            pystray.MenuItem(
                _("undo_last"),
                wrap_callback(self._controller.undo_last),
            )
        )
        # UX-3: Force Cancel Stuck Transcription — only rendered while
        # transcribing so the menu isn't cluttered when nothing is stuck.
        # The lambda is created (closure over self._controller.recording)
        # but NOT invoked during menu building, so a mock controller
        # without a ``recording`` attribute is safe.
        if self._state == AppState.TRANSCRIBING:
            items.append(
                pystray.MenuItem(
                    _("force_cancel_stuck_transcription"),
                    wrap_callback(
                        lambda: self._controller.recording._force_recover_from_stuck_transcription(force=True)
                    ),
                )
            )

        items.append(pystray.Menu.SEPARATOR)

        # Models submenu — built by tray_models.build_models_menu_items.
        models_sub = self._build_models_submenu()
        items.append(pystray.MenuItem(_("models"), pystray.Menu(*models_sub)))
        # UX-2: Microphones submenu — mirrors the Models submenu.
        mic_sub = self._build_microphones_submenu()
        items.append(pystray.MenuItem(_("microphones"), pystray.Menu(*mic_sub)))

        items.append(pystray.Menu.SEPARATOR)

        # UX-33: Settings / History / Help quick shortcuts. Each opens
        # the Electron window on the corresponding route via _open_page.
        for label_key, path in (
            ("settings", "/settings"),
            ("history", "/history"),
            ("help", "/about"),
        ):
            items.append(
                pystray.MenuItem(
                    _(label_key),
                    wrap_callback(lambda p=path: self._open_page(p)),
                )
            )

        items.append(pystray.Menu.SEPARATOR)

        # Restart + Quit.
        items.append(pystray.MenuItem(_("restart"), wrap_callback(self._controller.restart_app)))
        items.append(pystray.MenuItem(_("quit"), wrap_callback(self._confirm_quit_while_recording)))

        result = tuple(items)
        self._cached_menu = result
        self._menu_cache_valid = True
        return result

    def _maybe_publish_tray_menu(self) -> bool:
        """ADR-0020 §6.5 / §16: push the serialized tray menu to the Tauri
        sidecar host (no-op on the Electron/pystray runtime).

        Builds the model via :func:`build_tray_menu_model` (using the same
        controller callbacks as :meth:`_build_menu`) and emits it through
        :func:`publish_tray_menu`, which guards on ``TAURI_SIDECAR``.  Returns
        ``True`` if published.  Safe to call headless — never touches pystray.

        Note: under the Tauri runtime the pystray ``Icon`` is never created
        (the native tray is owned by the Rust host), so ``self._icon`` is
        ``None``. The earlier ``if self._icon is None: return False`` guard
        therefore short-circuited EVERY publish under Tauri — the
        ``tray_menu`` event never reached the Rust host and the tray menu
        stayed frozen at the empty placeholder. The guard is now removed;
        ``publish_tray_menu`` itself guards on ``TAURI_SIDECAR=1`` so the
        Electron runtime (where ``_icon`` IS set) is unaffected — the
        publish is a no-op there anyway.
        """
        from voice_typer.server.tray_menu import (
            build_tray_menu_model,
            publish_tray_menu,
        )

        controller = self._controller
        if controller is None:
            return False

        hotkey = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        left_click = getattr(self._config, "tray_left_click_action", "open_app") or "open_app"

        model, _id_map = build_tray_menu_model(
            hotkey=hotkey,
            toggle_dictation=controller.toggle_dictation,
            open_app=self.open_electron_window,
            repaste_last=getattr(controller, "repaste_last", lambda: None),
            force_cancel_transcription=lambda: controller.recording._force_recover_from_stuck_transcription(force=True),
            is_transcribing=lambda: (
                getattr(self._state, "name", "") == "TRANSCRIBING"
                or getattr(self._state, "value", "") == "TRANSCRIBING"
            ),
            restart_app=controller.restart_app,
            quit_app=self._confirm_quit_while_recording,
            build_models_submenu=self._build_models_submenu,
            left_click_action=left_click,
            microphones=getattr(controller, "_microphones", None),
            active_mic_id=getattr(controller, "active_microphone_id", None),
            on_select_mic=getattr(controller, "change_microphone", None),
            on_refresh_mics=getattr(controller, "refresh_microphones", None),
        )
        self._tray_id_map = _id_map
        return publish_tray_menu(model)

    def _open_page(self, path: str) -> None:
        """Publish a ``navigate`` event so the renderer opens ``path``.

        UX-33 (FIX-10): generalization of ``_open_models_page`` so any
        in-app route can be opened from the tray menu (Settings /
        History / Help). Does NOT open the Electron window itself —
        callers that need the window open (e.g. ``_open_models_page``)
        call ``open_electron_window`` first, then ``_open_page``.

        Args:
            path: The renderer route to navigate to (e.g. ``/settings``).
        """
        from voice_typer.server import event_bus

        try:
            event_bus.publish({"type": "navigate", "data": {"path": path}})
            log.info("[TRAY] Navigate push sent: %s", path)
        except Exception as e:
            log.warning("[TRAY] Failed to push navigate event for %s: %s", path, e)

    def _open_models_page(self) -> None:
        """Open the Electron window and navigate to the Models page.

        Called from the tray menu's "More models..." item. Opens/focuses
        the Electron window (same as open_electron_window) and then
        delegates to ``_open_page('/models')`` so the renderer navigates
        to the Models page instead of staying on whatever page was last
        open.
        """
        from voice_typer.server.tray_window import open_electron_window as _open

        _open()
        self._open_page("/models")

    def _build_microphones_submenu(self) -> list:
        """Build the Microphones ▸ submenu (UX-2).

        Renders one MenuItem per cached microphone (``self._microphones``),
        marking the active device (matching ``self._config.microphone``)
        with a ``• `` prefix. A trailing ``More microphones...`` item
        opens the Settings page (where the user can pick a device or
        refresh the list).

        Returns an empty list only if ``self._microphones`` is empty AND
        the ``More microphones...`` shortcut is somehow suppressed — in
        practice the shortcut is always appended so the submenu is never
        empty (the user can always reach the Settings page).
        """
        active_mic_id = str(getattr(self._config, "microphone", None) or "")
        items: list = []
        for mic in self._microphones:
            mic_id = str(mic.get("id", ""))
            mic_name = str(mic.get("name", mic_id)) or mic_id
            prefix = "• " if mic_id == active_mic_id else ""
            # Default-arg capture so each iteration's mic_id is bound
            # at lambda creation time (not lazily at call time).
            items.append(
                pystray.MenuItem(
                    f"{prefix}{mic_name}",
                    wrap_callback(lambda _id=mic_id: self._controller.change_microphone(_id)),
                )
            )
        if self._microphones:
            items.append(pystray.Menu.SEPARATOR)
        items.append(
            pystray.MenuItem(
                _("more_microphones"),
                wrap_callback(lambda: self._open_page("/settings")),
            )
        )
        return items

    def _build_models_submenu(self) -> list:
        """Build a list of model MenuItems — only cached models + More models link.

        #13: Fully delegates to tray_models.build_models_menu_items().

        ARCH-037: previously the menu builder re-parsed config.json from
        disk, which is stale under rapid config updates. We now pass
        the in-memory Config object via a config_provider callable so
        the menu always reflects the live state.
        """
        from voice_typer.server.config import _config_dir
        from voice_typer.server.tray_models import build_models_menu_items

        # ARCH-037: pass a config provider that returns the live Config
        # instance, so the menu doesn't read stale config.json from disk.
        config_provider = getattr(self, "_config", None)
        return build_models_menu_items(
            _config_dir,
            self._controller.change_model,
            wrap_callback,  # use the shared wrapper from tray_menu
            self._open_models_page,  # use models-page callback (opens + navigates)
            config_provider=config_provider,
        )

    def _display_hotkey(self) -> str:
        """Return the configured hotkey in a user-facing form.

        #13: delegates to tray_menu.display_hotkey() so the formatting
        logic is shared and testable without a TrayIcon instance.
        """
        hotkey = self._hotkey or getattr(self._config, "hotkey", "<f2>") or "<f2>"
        return display_hotkey(hotkey)

    # #13: _wrap moved to tray_menu.wrap_callback (shared helper).
    # Kept here as a static-method alias for backwards compatibility with
    # any code that calls TrayIcon._wrap(fn) directly.
    _wrap = staticmethod(wrap_callback)

    def _confirm_quit_while_recording(self) -> None:
        """Quit immediately, regardless of recording state.

        The old confirmation dialog was removed because crash recovery
        already protects in-flight transcriptions, and quit_app()
        handles discarding active recordings and waiting for
        transcription to finish (with timeout).
        """
        self._controller.quit_app()

    # G4-M-57: TRAY-015 periodic update checker removed.
    #
    # The previous implementation (``start_update_checker`` /
    # ``_do_update_check`` / ``_schedule_update_check`` /
    # ``_update_check_timer`` field) was dead code with a broken
    # disable toggle — the ``finally`` block in ``_do_update_check``
    # called ``_schedule_update_check`` unconditionally, so once the
    # timer was started it kept re-arming every 24 hours even when
    # ``check_updates`` was False. It also phoned home to GitHub
    # (api.github.com/repos/AbdallahIsDev/voice-typer/releases/latest)
    # on every run, which violates the offline-first promise and leaks
    # the user's IP + User-Agent to GitHub on every check.
    #
    # The whole feature is deleted. If update checks are ever
    # reintroduced, they MUST:
    #   1. Default to OFF (``check_updates: bool = False`` in Config).
    #   2. Be gated by an explicit in-app consent dialog.
    #   3. Live in a dedicated ``update_checker.py`` module so the tray
    #      stays focused on icon/menu state.
    #   4. Respect the ``check_updates`` flag at every reschedule.
