"""#2 (Round 9): HotkeyDispatcher — extracted from VoiceTyperApp.

Owns global hotkey registration: dictation toggle hotkey, ESC cancel
hotkey, and repaste hotkey. Each hotkey gets its own HotkeyBackend
instance (Win32 native, pynput, or Wayland).

Previously this concern lived in VoiceTyperApp as ~100 LOC across:
    _register_hotkey, _register_esc_hotkey, _unregister_esc_hotkey,
    _register_repaste_hotkey, _restart_hotkey

All of those now live here. VoiceTyperApp keeps thin delegate methods
for back-compat with callers (settings window, tests).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from voice_typer.server.branding import APP_NAME
from voice_typer.server.hotkeys import HotkeyBackend, create_hotkey_backend
from voice_typer.server.keyboard_ownership import keyboard_ownership

log = logging.getLogger(__name__)


class HotkeyDispatcher:
    """Owns the three global hotkey backends (dictation / ESC / repaste).

    #2 (Round 9): extracted from VoiceTyperApp. The app passes itself
    (``app``) so HotkeyDispatcher can:
    - Read ``app.config`` (hotkey, recording_mode, esc_cancel_enabled, repaste_hotkey)
    - Call ``app.toggle_dictation`` / ``app._stop_dictation`` /
      ``app._cancel_dictation`` / ``app.repaste_last`` as hotkey callbacks
    - Call ``app.tray.notify`` on registration failure
    - Call ``app.tray.set_hotkey`` after a hotkey restart
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._hotkey_backend: Optional[HotkeyBackend] = None
        self._esc_backend: Optional[HotkeyBackend] = None
        self._repaste_backend: Optional[HotkeyBackend] = None

    # ── Backend accessors (for back-compat with tests that read them) ──

    @property
    def hotkey_backend(self) -> Optional[HotkeyBackend]:
        return self._hotkey_backend

    @property
    def esc_backend(self) -> Optional[HotkeyBackend]:
        return self._esc_backend

    @property
    def repaste_backend(self) -> Optional[HotkeyBackend]:
        return self._repaste_backend

    # ── Registration ───────────────────────────────────────────────────

    def register(self) -> None:
        """Register global hotkey using the platform-appropriate backend.

        UX-002: when registration fails (typically because another app
        has already claimed the same hotkey via Win32 ``RegisterHotKey``
        or X11 grab), surface a tray notification that names the hotkey
        so the user can pick a different one in Settings.
        """
        app = self._app
        hotkey_str = app.config.hotkey
        log.info("[HOTKEY] Registering: %r -> toggle_dictation", hotkey_str)

        try:
            self._hotkey_backend = create_hotkey_backend(hotkey_str)
            log.info("[HOTKEY] Backend created: %s", type(self._hotkey_backend).__name__)
            # GAP-2/GAP-4: give the backend a reference to the tray so
            # it can show permission/fallback/recovery notifications.
            # The _NativeBackendAdapter uses this for its notifications;
            # other backends ignore it.
            try:
                self._hotkey_backend._tray = app.tray  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
            self._hotkey_backend.start(self._make_dictation_callback())
            # P1: Push-to-talk mode -- set release callback
            if app.config.recording_mode == "push_to_talk":
                self._hotkey_backend.set_on_release(app._stop_dictation)
            log.info(
                "[HOTKEY] Registration OK (alive=%s, backend=%s)",
                self._hotkey_backend.is_alive(),
                type(self._hotkey_backend).__name__,
            )
        except Exception as exc:
            # UX-002: name the hotkey in the notification so the user
            # knows which one to rebind.  Common cause: another app
            # (Snipping Tool, GeForce Overlay, etc.) already claimed it.
            log.warning("[HOTKEY] Registration FAILED -- %s: %s", hotkey_str, exc)
            log.debug("Hotkey registration error", exc_info=True)
            app.tray.notify(
                APP_NAME,
                f"Hotkey {hotkey_str} could not be registered. "
                "It may be in use by another app. "
                "Use the tray menu to toggle dictation, or pick a different hotkey in Settings.",
            )

        # Feature: ESC to cancel -- register ESC hotkey when enabled
        if app.config.esc_cancel_enabled:
            self.register_esc()

        # Feature: Repaste hotkey
        if app.config.repaste_hotkey:
            self.register_repaste()

    def _make_dictation_callback(self):
        """Create a dictation hotkey callback that respects keyboard ownership.

        HOTKEY-FIX-001 (Round 1): the dictation callback previously called
        ``app.toggle_dictation`` directly with NO ownership check. This meant
        that pressing any key during a hotkey capture session (e.g. re-assigning
        the current hotkey, or capturing a new key like Tab) would immediately
        trigger recording — because the OS-level listener sees the same keypress
        the frontend capture handler sees, and there was no guard.

        This mirrors the ESC callback's ownership check (ARCH-ESC-001 at line
        ~142): if the frontend is in hotkey capture mode
        (``is_hotkey_capture_active()`` returns True), the dictation callback
        is a no-op. This fixes sub-tasks 2.4 (Race A) and 2.5 entirely.
        """
        def _dictation_callback() -> None:
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug(
                    "[HOTKEY] dictation ignored — frontend hotkey capture active"
                )
                return
            self._app.toggle_dictation()
        return _dictation_callback

    def _make_repaste_callback(self):
        """Create a repaste hotkey callback that respects keyboard ownership.

        HOTKEY-FIX-001 (Round 1): same defense-in-depth as the dictation
        callback. Prevents the repaste hotkey from firing during capture.
        """
        def _repaste_callback() -> None:
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug(
                    "[HOTKEY] repaste ignored — frontend hotkey capture active"
                )
                return
            self._app.repaste_last()
        return _repaste_callback

    def register_esc(self) -> None:
        """Register the ESC hotkey for cancelling dictation.

        ARCH-ESC-001: the ESC callback is wrapped to consult the
        KeyboardOwnership singleton. If the frontend is in hotkey
        capture mode (``is_hotkey_capture_active()`` returns True),
        the ESC callback is a no-op — the frontend owns the keyboard
        during capture. This is a defense-in-depth check on top of
        ``app._esc_cancel_paused`` (which is now an alias for the same
        state) and the recording controller's own
        ``recorder.recording`` check.
        """
        # Stop any existing backend first
        if self._esc_backend:
            try:
                self._esc_backend.stop()
            except Exception:
                pass
            self._esc_backend = None
        try:
            self._esc_backend = create_hotkey_backend("<esc>")

            def _esc_callback() -> None:
                # ARCH-ESC-001: centralized ownership check. The
                # KeyboardOwnership singleton is the single source of
                # truth for which subsystem owns the keyboard. If the
                # frontend is capturing a hotkey, the ESC callback is
                # a no-op — no matter what.
                if keyboard_ownership().is_hotkey_capture_active():
                    log.debug(
                        "[HOTKEY] ESC ignored — frontend hotkey capture active"
                    )
                    return
                self._app._cancel_dictation()

            self._esc_backend.start(_esc_callback)
            log.info("[HOTKEY] ESC cancel hotkey registered")
        except Exception:
            log.warning("[HOTKEY] ESC cancel hotkey registration failed")

    def unregister_esc(self) -> None:
        """Unregister the ESC hotkey."""
        if self._esc_backend:
            try:
                self._esc_backend.stop()
            except Exception:
                pass
            self._esc_backend = None
            log.info("[HOTKEY] ESC cancel hotkey unregistered")

    def register_repaste(self) -> None:
        """Register the repaste hotkey."""
        if self._repaste_backend:
            try:
                self._repaste_backend.stop()
            except Exception:
                pass
            self._repaste_backend = None
        if self._app.config.repaste_hotkey:
            try:
                self._repaste_backend = create_hotkey_backend(self._app.config.repaste_hotkey)
                self._repaste_backend.start(self._make_repaste_callback())
                log.info("[HOTKEY] Repaste hotkey registered: %s", self._app.config.repaste_hotkey)
            except Exception:
                log.warning("[HOTKEY] Repaste hotkey registration failed")

    def restart(self, hotkey: str) -> None:
        """Re-register the global hotkey after settings change."""
        app = self._app
        app.config.hotkey = hotkey
        app.config.save()
        if self._hotkey_backend:
            try:
                self._hotkey_backend.stop()
            except Exception:
                log.exception("[HOTKEY] Failed to stop previous backend")
            self._hotkey_backend = None
        self.register()
        app.tray.set_hotkey(app.config.hotkey)

    # ── Cleanup ────────────────────────────────────────────────────────

    def stop_all(self) -> None:
        """Stop all hotkey backends (called during app shutdown)."""
        for backend_attr in ("_hotkey_backend", "_esc_backend", "_repaste_backend"):
            backend = getattr(self, backend_attr)
            if backend is not None:
                try:
                    backend.stop()
                except Exception:
                    log.debug("[HOTKEY] Failed to stop %s", backend_attr, exc_info=True)
                setattr(self, backend_attr, None)
