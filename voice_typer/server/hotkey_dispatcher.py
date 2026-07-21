"""#2 HotkeyDispatcher — extracted from VoiceTyperApp.

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

import contextlib
import logging
import threading
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.hotkeys import HotkeyBackend, create_hotkey_backend
from voice_typer.server.keyboard_ownership import keyboard_ownership

log = logging.getLogger(__name__)


class HotkeyDispatcher:
    """Owns the three global hotkey backends (dictation / ESC / repaste).

    #2 extracted from VoiceTyperApp. The app passes itself
    (``app``) so HotkeyDispatcher can:
    - Read ``app.config`` (hotkey, recording_mode, esc_cancel_enabled, repaste_hotkey)
    - Call ``app.toggle_dictation`` / ``app._stop_dictation`` /
      ``app._cancel_dictation`` / ``app.repaste_last`` as hotkey callbacks
    - Call ``app.tray.notify`` on registration failure
    - Call ``app.tray.set_hotkey`` after a hotkey restart
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._hotkey_backend: HotkeyBackend | None = None
        self._esc_backend: HotkeyBackend | None = None
        self._repaste_backend: HotkeyBackend | None = None
        # CR-85 + M-94 (combined): threading.Event for atomic cross-
        # thread access. Both sessions independently identified the
        # plain-bool race; session-2's attribute name
        # ``_esc_pending_capture_exit_event`` is adopted because it is
        # already used at ``ipc_server._on_ipc_client_disconnect``.
        # TODO Fix-A: ipc/server.py:1022 still does
        # ``_hotkeys._esc_pending_capture_exit = False`` — that line
        # targets the OLD attribute name. Session-4 deletes the dead
        # ``ipc/server.py`` parallel implementation entirely, which
        # eliminates the TODO. Until then, the OLD attribute is NOT
        # initialized here (so the ``= False`` write hits a missing
        # attribute and raises AttributeError — fail-loud is safer
        # than silently overwriting the Event).
        self._esc_pending_capture_exit_event: threading.Event = threading.Event()

    # ── Backend accessors (for back-compat with tests that read them) ──

    @property
    def hotkey_backend(self) -> HotkeyBackend | None:
        return self._hotkey_backend

    @property
    def esc_backend(self) -> HotkeyBackend | None:
        return self._esc_backend

    @property
    def repaste_backend(self) -> HotkeyBackend | None:
        return self._repaste_backend

    # ── Registration ───────────────────────────────────────────────────

    def register(self) -> bool:
        """Register global hotkey using the platform-appropriate backend.

        UX-002: when registration fails (typically because another app
        has already claimed the same hotkey via Win32 ``RegisterHotKey``
        or X11 grab), surface a tray notification that names the hotkey
        so the user can pick a different one in Settings.

        USER-REQUESTED FIX: in toggle mode, the dictation toggle fires on
        key-UP (release), not key-down, so a press-and-hold cannot start
        then immediately stop recording. This is wired via
        ``set_toggle_on_keyup(True)`` for the main dictation hotkey in
        toggle mode; push-to-talk keeps start-on-press / stop-on-release.

        CR-15 (atomic register): ``self._hotkey_backend`` is assigned the
        NEW backend only AFTER ``start()`` succeeds. If ``create_hotkey_backend``
        or ``start()`` raises, the OLD backend (if any) is left in place
        so the user is never left without a working hotkey. This is the
        building block ``restart()`` relies on for its atomicity.

        Returns:
            True if a new backend was successfully created, wired, and
            started (and assigned to ``self._hotkey_backend``); False if
            any step failed (the OLD backend, if any, is left running).
            Callers that ignore the return value (the historical
            contract) continue to work unchanged.
        """
        app = self._app
        hotkey_str = app.config.hotkey
        log.info("[HOTKEY] Registering: %r -> toggle_dictation", hotkey_str)

        success = False
        try:
            new_backend = self._create_and_start_main_backend(hotkey_str)
            # CR-15: assign only after start() succeeded. A failure
            # mid-way leaves the OLD backend in self._hotkey_backend.
            self._hotkey_backend = new_backend
            success = True
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

        return success

    def _create_and_start_main_backend(self, hotkey_str: str) -> HotkeyBackend:
        """Create, wire up, and start the main dictation hotkey backend.

        Shared by :meth:`register` (first-time setup) and :meth:`restart`
        (hot-swap). Returns the new backend on success; raises on failure
        so the caller can decide whether to install it as the active
        backend (atomic swap pattern).

        - ``create_hotkey_backend`` (factory) selects the best platform
          backend; can raise on spec parse errors or missing native
          binary paths.
        - ``start(callback)`` launches the listener thread; can raise if
          the OS rejects the hotkey (e.g. Win32 ``RegisterHotKey`` fails
          because another app already claimed it).

        Wiring applied to the new backend before ``start()``:
        - ``_tray`` attribute (GAP-2/GAP-4): so the backend can show
          permission / fallback / recovery notifications.
        - ``set_toggle_on_keyup(True)`` in toggle mode: so the toggle
          fires on key-UP and a press-and-hold cannot start-then-stop
          recording.
        - ``set_on_release(app._stop_dictation)`` in push-to-talk mode.
        """
        app = self._app
        new_backend = create_hotkey_backend(hotkey_str)
        log.info("[HOTKEY] Backend created: %s", type(new_backend).__name__)
        # GAP-2/GAP-4: give the backend a reference to the tray so
        # it can show permission/fallback/recovery notifications.
        # The _NativeBackendAdapter uses this for its notifications;
        # other backends ignore it.
        with contextlib.suppress(AttributeError, TypeError):
            new_backend._tray = app.tray  # type: ignore[attr-defined]
        # USER-REQUESTED FIX: in toggle mode, fire the toggle on key-up
        # (release) so holding the key never starts-then-stops recording.
        if app.config.recording_mode == "toggle":
            with contextlib.suppress(AttributeError, TypeError):
                new_backend.set_toggle_on_keyup(True)
        new_backend.start(self._make_dictation_callback())
        # P1: Push-to-talk mode -- set release callback
        if app.config.recording_mode == "push_to_talk":
            new_backend.set_on_release(app._stop_dictation)
        log.info(
            "[HOTKEY] Registration OK (alive=%s, backend=%s)",
            new_backend.is_alive(),
            type(new_backend).__name__,
        )
        return new_backend

    def _make_dictation_callback(self):
        """Create a dictation hotkey callback that respects keyboard ownership.

        HOTKEY-FIX-001: the dictation callback previously called
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
                log.debug("[HOTKEY] dictation ignored — frontend hotkey capture active")
                return
            self._app.toggle_dictation()

        return _dictation_callback

    def _make_repaste_callback(self):
        """Create a repaste hotkey callback that respects keyboard ownership.

        HOTKEY-FIX-001: same defense-in-depth as the dictation
        callback. Prevents the repaste hotkey from firing during capture.
        """

        def _repaste_callback() -> None:
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[HOTKEY] repaste ignored — frontend hotkey capture active")
                return
            self._app.repaste_last()

        return _repaste_callback

    def register_esc(self) -> None:
        """Register the ESC hotkey for cancelling dictation.

        ARCH-ESC-001: the ESC callback is wrapped to consult the
        KeyboardOwnership singleton. If the frontend is in hotkey
        capture mode (``is_hotkey_capture_active()`` returns True),
        the ESC callback defers to key-up instead of acting
        immediately on key-down. This matches how regular hotkey
        capture works (assignment happens on key-up / release).

        ESC-KEYUP-FIX: when the user presses ESC during hotkey
        capture, the key-down sets a pending flag and installs a
        release callback on the ESC backend. The actual ownership
        reset and ``hotkey_capture_cancel`` event are pushed on
        key-up, when the user releases the finger. This eliminates
        the "cancel on press" behavior the user reported as
        feeling unresponsive.
        """
        # Stop any existing backend first
        if self._esc_backend:
            with contextlib.suppress(Exception):
                self._esc_backend.stop()
            self._esc_backend = None

        # ESC-KEYUP-FIX / M-94 + CR-85 (combined): Event (initially
        # not-set, equivalent to the old ``False``) set on ESC key-down
        # during capture, cleared after the release callback fires on
        # key-up. ``threading.Event`` provides atomic ``is_set`` / ``set``
        # / ``clear`` so the 3 threads that touch this flag (ESC listener,
        # ESC release handler, IPC disconnect worker) cannot race on the
        # read-modify-write cycle that the plain bool exhibited.
        self._esc_pending_capture_exit_event.clear()

        try:
            self._esc_backend = create_hotkey_backend("<esc>")

            def _esc_callback() -> None:
                # ARCH-ESC-001: centralized ownership check.
                if keyboard_ownership().is_hotkey_capture_active():
                    log.info("[HOTKEY] ESC pressed during hotkey capture — waiting for key-up")
                    # ESC-KEYUP-FIX: set the pending flag and install
                    # a release callback. The actual cancel happens on
                    # key-up (release), not key-down (press).
                    # CR-85 + M-94 (combined): ``threading.Event.set()``
                    # is atomic — no race vs. a concurrent ``.clear()``
                    # from the IPC disconnect worker.
                    self._esc_pending_capture_exit_event.set()
                    if self._esc_backend is not None:
                        self._esc_backend.set_on_release(self._on_esc_release)
                    return
                self._app._cancel_dictation()

            self._esc_backend.start(_esc_callback)
            log.info("[HOTKEY] ESC cancel hotkey registered")
        except Exception:
            log.warning("[HOTKEY] ESC cancel hotkey registration failed")

    def _on_esc_release(self) -> None:
        """ESC-KEYUP-FIX: release callback fired on key-up.

        Installed by ``_esc_callback`` when ``is_hotkey_capture_active()``
        is True. On key-up, this resets keyboard ownership and pushes
        ``hotkey_capture_cancel`` so the frontend exits capture mode.

        The cancelRecording guard in HotkeyPicker.tsx
        (``if (!recordingRef.current) return;``) prevents duplicate
        ``onCaptureEnd`` calls when both this backend push AND the
        frontend's own DOM key-up handler fire for the same ESC release.

        M-94: the check-then-clear is still technically racy (a
        concurrent ``.set()`` from the ESC listener between the
        ``is_set()`` read and the ``clear()`` write would be lost),
        but ``threading.Event`` is the canonical primitive for this
        pattern and the race window is sub-microsecond — far shorter
        than the human reaction time between two ESC presses.  The
        previous plain-bool implementation had the SAME race window
        plus an additional race against the IPC disconnect worker
        (which ``= False``'d the bool without consulting the listener
        thread).  The Event eliminates the second race; the first is
        tolerable (a second ESC press within the same microsecond
        would re-arm the flag and the next release would fire the
        cancel again — idempotent via ``keyboard_ownership().reset()``).
        """
        # CR-85 + M-94 (combined): threading.Event.is_set() / .clear()
        if not self._esc_pending_capture_exit_event.is_set():
            return
        self._esc_pending_capture_exit_event.clear()

        log.info("[HOTKEY] ESC released during hotkey capture — canceling capture")

        # Reset keyboard ownership so subsequent keys
        # are no longer blocked by the capture check.
        keyboard_ownership().set_owner("normal", reason="esc released during capture")

        # Keep the legacy alias in sync with the canonical owner so readers
        # that still consult _esc_cancel_paused cannot see a stale "paused"
        # state. ESC-FIX-001 divergence fix: the alias was only cleared by a
        # frontend round-trip, so a missed IPC left ESC permanently dead.
        self._app._esc_cancel_paused = False

        # Push an event so the frontend exits capture mode.
        from voice_typer.server import event_bus

        event_bus.publish({"type": "hotkey_capture_cancel"})

        # Reset the release callback so it doesn't fire again
        # on the next ESC press during normal operation.
        if self._esc_backend is not None:
            with contextlib.suppress(Exception):
                self._esc_backend.set_on_release(None)

    def unregister_esc(self) -> None:
        """Unregister the ESC hotkey."""
        if self._esc_backend:
            with contextlib.suppress(Exception):
                self._esc_backend.stop()
            self._esc_backend = None
            log.info("[HOTKEY] ESC cancel hotkey unregistered")

    def register_repaste(self) -> None:
        """Register the repaste hotkey."""
        if self._repaste_backend:
            with contextlib.suppress(Exception):
                self._repaste_backend.stop()
            self._repaste_backend = None
        if self._app.config.repaste_hotkey:
            try:
                self._repaste_backend = create_hotkey_backend(self._app.config.repaste_hotkey)
                self._repaste_backend.start(self._make_repaste_callback())
                log.info("[HOTKEY] Repaste hotkey registered: %s", self._app.config.repaste_hotkey)
            except Exception:
                log.warning("[HOTKEY] Repaste hotkey registration failed")

    def restart(self, hotkey: str) -> None:
        """Re-register the global hotkey after settings change.

        CR-105: validate hotkey before mutating config.
        CR-15 (atomic restart): the NEW backend is brought up BEFORE
        the OLD one is stopped. If ``register()`` fails (e.g. the new
        hotkey spec is invalid, or the OS rejects it because another
        app already claimed it), the OLD backend is kept running so
        the user is never left without a working dictation hotkey.

        Atomicity is provided by :meth:`register` itself: it assigns
        ``self._hotkey_backend = new_backend`` only AFTER ``start()``
        succeeds, leaving the OLD backend in place on failure, and
        returns ``True``/``False`` to signal the outcome. This method
        uses that return value to decide whether to stop the OLD
        backend.

        UX-002: on failure, ``register()`` already shows the tray
        notification naming the rejected hotkey; we don't duplicate
        it here.
        """
        app = self._app
        from voice_typer.server.config_validators import _validate_hotkey

        validation_error = _validate_hotkey(hotkey)
        if validation_error is not None:
            log.warning("[HOTKEY] restart(%r) rejected: %s", hotkey, validation_error)
            with contextlib.suppress(Exception):
                app.tray.notify(
                    APP_NAME,
                    f"Hotkey {hotkey} is not valid: {validation_error}. Keeping the previous hotkey.",
                )
            return
        app.config.hotkey = hotkey
        if not app.config.save():
            log.warning("[HOTKEY] config.save() returned False — hotkey change may not persist")
            app.tray.notify(
                APP_NAME,
                "Failed to save hotkey to disk. Check disk space or permissions.",
            )

        old_backend = self._hotkey_backend

        # register() atomically installs a NEW backend on success
        # (assigning self._hotkey_backend = new_backend AFTER start()
        # succeeds) and leaves self._hotkey_backend UNCHANGED on
        # failure. Its return value signals the outcome.
        register_ok = self.register()

        if register_ok:
            # register() installed a new backend — stop the old one.
            if old_backend is not None:
                try:
                    old_backend.stop()
                except Exception:
                    log.exception("[HOTKEY] Failed to stop previous backend")
        else:
            # register() failed and left the OLD backend in place.
            # Do NOT stop it — the user keeps the working hotkey.
            log.warning(
                "[HOTKEY] restart did not install a new backend — keeping old backend running (old=%s)",
                type(old_backend).__name__ if old_backend is not None else None,
            )

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
