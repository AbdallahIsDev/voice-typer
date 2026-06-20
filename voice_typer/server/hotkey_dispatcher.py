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

from voice_typer.server.hotkeys import HotkeyBackend, create_hotkey_backend

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
            self._hotkey_backend.start(app.toggle_dictation)
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
                "Voice Typer",
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

    def register_esc(self) -> None:
        """Register the ESC hotkey for cancelling dictation."""
        # Stop any existing backend first
        if self._esc_backend:
            try:
                self._esc_backend.stop()
            except Exception:
                pass
            self._esc_backend = None
        try:
            self._esc_backend = create_hotkey_backend("<esc>")
            self._esc_backend.start(self._app._cancel_dictation)
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
                self._repaste_backend.start(self._app.repaste_last)
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
