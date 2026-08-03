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

import concurrent.futures
import contextlib
import logging
import threading
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import DEFAULT_HOTKEY
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

    Architecture note — three backends, three native subprocesses
    ----------------------------------------------------------------
    ``register`` / ``register_esc`` / ``register_repaste`` each call
    ``create_hotkey_backend(spec, role=...)`` once (see the three
    call sites below). On platforms that select the native
    ``SubprocessHotkeyBackend`` (macOS / Windows / Linux), every
    backend spawns its OWN native listener process via
    ``subprocess.Popen`` (see ``native_hotkeys/base.py``). That is
    three long-lived OS processes, three reader threads, and three
    IPC pipes for what is conceptually one global-hotkey concern.

    Planned refactor (deferred — large + cross-cutting)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Collapse the three backends into ONE shared native binary that
    accepts a list of ``(role, hotkey_spec)`` pairs on the command
    line (or via a startup handshake frame) and emits wire events
    tagged with the originating role (e.g. ``EVENT role=esc KEY_UP
    <esc>``). ``HotkeyDispatcher`` would then own a single backend
    handle and dispatch each event to the matching callback
    (dictation / ESC / repaste) by role. This cuts the kernel
    overhead to one process / one reader thread / one pipe and lets
    the three platform binaries share a single TOCTOU-verified
    binary_path + a single watchdog.

    Why deferred: the change touches the native binary wire
    protocol (``_WIRE_HANDLERS`` in ``base.py``), the binary
    argument surface (``cmd = [binary, hotkey_str]``), the factory
    (``create_hotkey_backend``), all three platform backends, and
    the per-role Wayland socket naming — plus the restart /
    watchdog / TOCTOU-verify paths that currently run per-backend.
    It is a focused but wide refactor that needs its own session
    with the native binaries recompiled on all three platforms.

    TODO (future session): introduce a multiplexed
    ``SharedNativeHotkeyBackend`` that owns one process for all
    three roles; keep the current per-role backends as a fallback
    for platforms where the multiplexed binary is unavailable.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._hotkey_backend: HotkeyBackend | None = None
        self._esc_backend: HotkeyBackend | None = None
        self._repaste_backend: HotkeyBackend | None = None
        # track the last-registered ESC and repaste specs so
        # ``register()`` can skip the teardown+rebuild cycle when the
        # spec hasn't changed. Previously ``register()`` unconditionally
        # rebuilt both backends on every call (including the
        # ``restart()`` path that delegates back to ``register()``),
        # causing a brief window where ESC / repaste weren't available
        # plus unnecessary OS grab churn on Windows / macOS.
        self._esc_spec: str | None = None
        self._repaste_spec: str | None = None
        #  + M-94 (combined): threading.Event for atomic cross-
        # thread access. Both sessions independently identified the
        # plain-bool race; session-2's attribute name
        # ``_esc_pending_capture_exit_event`` is adopted because it is
        # already used at ``ipc_server._on_ipc_client_disconnect``.
        # the stale TODO Fix-A comment referencing the
        # non-existent ``ipc/server.py`` file has been deleted. The
        # OLD ``_esc_pending_capture_exit`` bool attribute was never
        # referenced anywhere in the codebase (the file was renamed
        # to ``ipc_server.py`` and the attribute was updated to the
        # Event form). The ``_esc_pending_capture_exit_event``
        # threading.Event is the sole, canonical implementation.
        #  + M-94: threading.Event for atomic cross-thread
        # ESC-cancel signaling. See _on_esc_release for the consumer side.
        self._esc_pending_capture_exit_event: threading.Event = threading.Event()

    # ── Registration ───────────────────────────────────────────────────

    def register(self) -> bool:
        """Register global hotkey using the platform-appropriate backend.

        when registration fails (typically because another app
        has already claimed the same hotkey via Win32 ``RegisterHotKey``
        or X11 grab), surface a tray notification that names the hotkey
        so the user can pick a different one in Settings.

        USER-REQUESTED FIX: in toggle mode, the dictation toggle fires on
        key-UP (release), not key-down, so a press-and-hold cannot start
        then immediately stop recording. This is wired via
        ``set_toggle_on_keyup(True)`` for the main dictation hotkey in
        toggle mode; push-to-talk keeps start-on-press / stop-on-release.

         (atomic register): ``self._hotkey_backend`` is assigned the
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

        #  (partial, session-4): validate the configured hotkey
        # before attempting to register it. Config.load() bypasses the
        # denylist, so a stale/hand-edited config could contain an
        # OS-reserved shortcut. On rejection, fall back to the platform
        # default so the user is never left without a working hotkey.
        from voice_typer.server.config_validators import _validate_hotkey

        validation_error = _validate_hotkey(hotkey_str)
        if validation_error is not None:
            log.warning(
                "[HOTKEY] configured hotkey %r rejected (%s) — falling back to default <caps_lock>",
                hotkey_str,
                validation_error,
            )
            hotkey_str = DEFAULT_HOTKEY  # platform default (see config._default_hotkey_for_platform)
            app.config.hotkey = hotkey_str

        log.info("[HOTKEY] Registering: %r -> toggle_dictation", hotkey_str)

        success = False
        try:
            new_backend = self._create_and_start_main_backend(hotkey_str)
            # assign only after start() succeeded. A failure
            # mid-way leaves the OLD backend in self._hotkey_backend.
            self._hotkey_backend = new_backend
            success = True
        except Exception as exc:
            # name the hotkey in the notification so the user
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
        # skip the teardown+rebuild if the ESC backend is
        # already alive with the same spec ("<esc>"). Previously
        # ``register()`` unconditionally called ``register_esc()``, which
        # stops and recreates the backend on every call — causing a
        # brief ESC-unavailable window and unnecessary OS grab churn.
        # When ESC is disabled, tear down any existing backend.
        if app.config.esc_cancel_enabled:
            esc_already_alive = (
                self._esc_backend is not None and self._esc_backend.is_alive() and self._esc_spec == "<esc>"
            )
            if not esc_already_alive:
                self.register_esc()
        elif self._esc_backend is not None:
            with contextlib.suppress(Exception):
                self._esc_backend.stop()
            self._esc_backend = None
            self._esc_spec = None

        # Feature: Repaste hotkey
        # skip the teardown+rebuild if the repaste backend is
        # already alive with the same spec. When repaste is disabled
        # (empty / None), tear down any existing backend.
        if app.config.repaste_hotkey:
            repaste_already_alive = (
                self._repaste_backend is not None
                and self._repaste_backend.is_alive()
                and self._repaste_spec == app.config.repaste_hotkey
            )
            if not repaste_already_alive:
                self.register_repaste()
        elif self._repaste_backend is not None:
            with contextlib.suppress(Exception):
                self._repaste_backend.stop()
            self._repaste_backend = None
            self._repaste_spec = None

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
        - ``_tray`` attribute (/): so the backend can show
          permission / fallback / recovery notifications.
        - ``set_toggle_on_keyup(True)`` in toggle mode: so the toggle
          fires on key-UP and a press-and-hold cannot start-then-stop
          recording.
        - ``set_on_release(app._stop_dictation)`` in push-to-talk mode.
        """
        app = self._app
        # pass role="dictation" so the WaylandHotkey backend (if
        # selected on a Wayland session) binds a per-backend socket
        # filename instead of colliding with the ESC / repaste backends.
        new_backend = create_hotkey_backend(hotkey_str, role="dictation")
        log.info("[HOTKEY] Backend created: %s", type(new_backend).__name__)
        # give the backend a reference to the tray so
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

        HOTKEY- the dictation callback previously called
        ``app.toggle_dictation`` directly with NO ownership check. This meant
        that pressing any key during a hotkey capture session (e.g. re-assigning
        the current hotkey, or capturing a new key like Tab) would immediately
        trigger recording — because the OS-level listener sees the same keypress
        the frontend capture handler sees, and there was no guard.

        This mirrors the ESC callback's ownership check ( at line
        ~142): if the frontend is in hotkey capture mode
        (``is_hotkey_capture_active()`` returns True), the dictation callback
        is a no-op. This fixes sub-tasks 2.4 (Race A) and 2.5 entirely.
        """

        def _dictation_callback() -> None:
            # guard against hotkey callbacks firing during
            # shutdown. The shutdown controller stops hotkey backends
            # with a 5s timeout each — if stop() times out, the listener
            # thread may still fire callbacks that call toggle_dictation()
            # → _start_dictation(), undoing cleanup and racing
            # recorder.stop()/discard().
            if getattr(self._app, "_shutting_down", False):
                log.debug("[HOTKEY] dictation ignored — app shutting down")
                return
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[HOTKEY] dictation ignored — frontend hotkey capture active")
                return
            self._app.toggle_dictation()

        return _dictation_callback

    def _make_repaste_callback(self):
        """Create a repaste hotkey callback that respects keyboard ownership.

        HOTKEY- same defense-in-depth as the dictation
        callback. Prevents the repaste hotkey from firing during capture.
        """

        def _repaste_callback() -> None:
            # shutdown guard (see _dictation_callback).
            if getattr(self._app, "_shutting_down", False):
                log.debug("[HOTKEY] repaste ignored — app shutting down")
                return
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[HOTKEY] repaste ignored — frontend hotkey capture active")
                return
            self._app.repaste_last()

        return _repaste_callback

    def register_esc(self) -> None:
        """Register the ESC hotkey for cancelling dictation.

        the ESC callback is wrapped to consult the
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
            self._esc_spec = None

        # ESC-KEYUP-FIX / M-94 +  (combined): Event (initially
        # not-set, equivalent to the old ``False``) set on ESC key-down
        # during capture, cleared after the release callback fires on
        # key-up. ``threading.Event`` provides atomic ``is_set`` / ``set``
        # / ``clear`` so the 3 threads that touch this flag (ESC listener,
        # ESC release handler, IPC disconnect worker) cannot race on the
        # read-modify-write cycle that the plain bool exhibited.
        self._esc_pending_capture_exit_event.clear()

        try:
            # pass role="esc" so the WaylandHotkey backend (if
            # selected on a Wayland session) binds a per-backend socket.
            self._esc_backend = create_hotkey_backend("<esc>", role="esc")
            # prefer the event-driven WM_HOTKEY message loop over
            # the per-keystroke WH_KEYBOARD_LL hook for the ESC backend.
            # Only the main dictation hotkey installs an LL hook (was 3).
            # If RegisterHotKey fails for ESC (some keys are reserved),
            # the backend falls back to the LL hook for ESC only — 2 hooks
            # instead of 3, still an improvement. suppress() so non-Windows
            # backends without ``_prefer_message_loop_first`` are skipped.
            with contextlib.suppress(AttributeError, TypeError):
                self._esc_backend._prefer_message_loop_first = True  # type: ignore[attr-defined]

            def _esc_callback() -> None:
                # shutdown guard (see _dictation_callback).
                if getattr(self._app, "_shutting_down", False):
                    log.debug("[HOTKEY] ESC ignored — app shutting down")
                    return
                # centralized ownership check.
                if keyboard_ownership().is_hotkey_capture_active():
                    log.info("[HOTKEY] ESC pressed during hotkey capture — waiting for key-up")
                    # ESC-KEYUP-FIX: set the pending flag and install
                    # a release callback. The actual cancel happens on
                    # key-up (release), not key-down (press).
                    #  + M-94 (combined): ``threading.Event.set()``
                    # is atomic — no race vs. a concurrent ``.clear()``
                    # from the IPC disconnect worker.
                    self._esc_pending_capture_exit_event.set()
                    if self._esc_backend is not None:
                        self._esc_backend.set_on_release(self._on_esc_release)
                    return
                self._app._cancel_dictation()

            self._esc_backend.start(_esc_callback)
            self._esc_spec = "<esc>"
            log.info("[HOTKEY] ESC cancel hotkey registered")
        except Exception:
            # null the failed backend reference so a subsequent
            # ``register()`` / ``register_esc()`` doesn't try to ``stop()``
            # a partially-started backend (which may have acquired OS
            # resources via ``create_hotkey_backend`` even if ``start()``
            # raised). ``stop()`` is safe to call on a partially-started
            # backend (it suppresses AttributeError / OSError on missing
            # listener threads), so call it before nulling to release any
            # resources the partial start did acquire.
            if self._esc_backend is not None:
                with contextlib.suppress(Exception):
                    self._esc_backend.stop()
            self._esc_backend = None
            self._esc_spec = None
            log.warning("[HOTKEY] ESC cancel hotkey registration failed")
            # surface the failure to the user via the tray's
            # safety channel (bypasses the notification toggle) so they
            # know ESC cancel is unavailable. Previously this branch
            # only emitted a ``log.warning`` — the user had no signal
            # until they pressed ESC and nothing happened.
            with contextlib.suppress(Exception):
                self._app.tray.notify_safety(
                    APP_NAME,
                    "ESC cancel hotkey could not be registered. Another app may have claimed it.",
                )

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
        #  + M-94 (combined): threading.Event.is_set() / .clear()
        if not self._esc_pending_capture_exit_event.is_set():
            return
        self._esc_pending_capture_exit_event.clear()

        log.info("[HOTKEY] ESC released during hotkey capture — canceling capture")

        # Reset keyboard ownership so subsequent keys
        # are no longer blocked by the capture check.
        keyboard_ownership().set_owner("normal", reason="esc released during capture")

        # Keep the legacy alias in sync with the canonical owner so readers
        # that still consult _esc_cancel_paused cannot see a stale "paused"
        # state. ESC- divergence fix: the alias was only cleared by a
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
            self._esc_spec = None
            log.info("[HOTKEY] ESC cancel hotkey unregistered")

    def register_repaste(self) -> None:
        """Register the repaste hotkey."""
        if self._repaste_backend:
            with contextlib.suppress(Exception):
                self._repaste_backend.stop()
            self._repaste_backend = None
            self._repaste_spec = None
        if self._app.config.repaste_hotkey:
            # validate the configured repaste hotkey BEFORE
            # attempting to register it. ``Config.load()`` bypasses the
            # denylist, so a stale/hand-edited config could contain an
            # OS-reserved shortcut (e.g. ``<win>+<l>``) or — after
            # ``<caps_lock>+<v>`` (caps_lock is now correctly
            # rejected by Stage 5 as a non-modifier key in a multi-
            # non-modifier combo, instead of being silently accepted
            # because it was incorrectly listed as a modifier). On
            # rejection, DISABLE repaste (set ``repaste_hotkey=""``)
            # rather than resetting to the default ``<caps_lock>``,
            # which would conflict with the main dictation hotkey.
            from voice_typer.server.config_validators import _validate_hotkey

            validation_error = _validate_hotkey(self._app.config.repaste_hotkey)
            if validation_error is not None:
                log.warning(
                    "[HOTKEY] configured repaste_hotkey %r rejected (%s) — "
                    "disabling repaste (not resetting to <caps_lock> to avoid "
                    "conflict with the main dictation hotkey)",
                    self._app.config.repaste_hotkey,
                    validation_error,
                )
                self._app.config.repaste_hotkey = ""
                return
            try:
                # pass role="repaste" so the WaylandHotkey backend
                # (if selected on a Wayland session) binds a per-backend socket.
                self._repaste_backend = create_hotkey_backend(self._app.config.repaste_hotkey, role="repaste")
                # same WM_HOTKEY-preference flag as the ESC backend
                # (see register_esc for the full rationale).
                with contextlib.suppress(AttributeError, TypeError):
                    self._repaste_backend._prefer_message_loop_first = True  # type: ignore[attr-defined]
                self._repaste_backend.start(self._make_repaste_callback())
                self._repaste_spec = self._app.config.repaste_hotkey
                log.info("[HOTKEY] Repaste hotkey registered: %s", self._app.config.repaste_hotkey)
            except Exception:
                # null the failed backend reference so a
                # subsequent ``register()`` / ``register_repaste()``
                # doesn't try to ``stop()`` a partially-started backend.
                # ``stop()`` is safe to call on a partially-started
                # backend, so call it before nulling to release any OS
                # resources the partial start did acquire.
                if self._repaste_backend is not None:
                    with contextlib.suppress(Exception):
                        self._repaste_backend.stop()
                self._repaste_backend = None
                self._repaste_spec = None
                log.warning("[HOTKEY] Repaste hotkey registration failed")
                # surface the failure to the user via the tray's
                # safety channel. Mirrors the ESC path: a silent
                # ``log.warning`` left the user with no way to know the
                # repaste hotkey was unavailable.
                with contextlib.suppress(Exception):
                    self._app.tray.notify_safety(
                        APP_NAME,
                        "Repaste hotkey could not be registered. Another app may have claimed it.",
                    )

    def restart(self, hotkey: str) -> None:
        """Re-register the global hotkey after settings change.

        validate hotkey before mutating config.

        stop the OLD backend BEFORE starting the NEW one.
        Previously ``register()`` brought up the new backend first and
        the old backend was only stopped AFTER ``register()`` returned
        success — leaving a window where BOTH backends were running on
        platforms that permit multiple global-hotkey registrations
        (pynput on Linux/X11, Wayland). Both fired the dictation
        callback on the same keypress → double-toggle. Stopping the
        old backend first eliminates the window.

        Fallback restore on failure: if ``register()`` fails (e.g. the
        new hotkey spec is invalid or the OS rejects it because
        another app claimed it), the OLD backend's hotkey spec is
        restored to ``app.config.hotkey`` and a fresh backend is
        created with the OLD spec so the user is never left without a
        working dictation hotkey. This preserves the  user-facing
        contract ("restart failure keeps the previous hotkey working")
        while eliminating the double-backend window.

        on failure, ``register()`` already shows the tray
        notification naming the rejected hotkey; we don't duplicate
        it here. If fallback restore ALSO fails, the user is left
        without a hotkey and a separate ERROR-level log line is
        emitted so operators can diagnose.
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
        # capture the OLD hotkey spec BEFORE mutating
        # config so we can restore it (and recreate a backend with
        # the OLD spec) if register() fails.
        old_hotkey_str = app.config.hotkey
        old_backend = self._hotkey_backend

        app.config.hotkey = hotkey
        if not app.config.save():
            log.warning("[HOTKEY] config.save() returned False — hotkey change may not persist")
            app.tray.notify(
                APP_NAME,
                "Failed to save hotkey to disk. Check disk space or permissions.",
            )

        # stop the OLD backend BEFORE calling register()
        # so there is no window where both old and new backends are
        # running. The OLD backend's listener thread is joined (best-
        # effort) so its callback can no longer fire on the old spec.
        # ``self._hotkey_backend`` is cleared so register() starts
        # from a clean slate; on success it installs the new backend.
        if old_backend is not None:
            try:
                old_backend.stop()
            except Exception:
                log.exception("[HOTKEY] Failed to stop previous backend before restart")
            self._hotkey_backend = None

        # register() atomically installs a NEW backend on success
        # (assigning self._hotkey_backend = new_backend AFTER start()
        # succeeds) and leaves self._hotkey_backend UNCHANGED on
        # failure. Its return value signals the outcome.
        register_ok = self.register()

        if register_ok:
            # register() installed a new backend — old backend already
            # stopped above. Nothing more to do.
            pass
        else:
            # register() failed. The OLD backend was already stopped,
            # so we must restore it by re-creating a backend with the
            # OLD hotkey spec. Revert config so subsequent calls (and
            # the tray.set_hotkey below) reflect the OLD spec.
            if old_backend is not None:
                log.warning(
                    "[HOTKEY] restart failed; restoring previous hotkey %r",
                    old_hotkey_str,
                )
                app.config.hotkey = old_hotkey_str
                with contextlib.suppress(Exception):
                    app.config.save()
                try:
                    self._hotkey_backend = self._create_and_start_main_backend(old_hotkey_str)
                except Exception:
                    log.exception(
                        "[HOTKEY] Failed to restore previous backend (hotkey=%r) — "
                        "user is left without a dictation hotkey",
                        old_hotkey_str,
                    )
                    with contextlib.suppress(Exception):
                        app.tray.notify(
                            APP_NAME,
                            f"Could not restore the previous hotkey {old_hotkey_str}. "
                            "Open Settings to rebind a hotkey.",
                        )
            else:
                # No OLD backend to restore — register() failure leaves
                # _hotkey_backend as None (first-time registration that
                # failed). register() already showed the tray notify.
                log.warning("[HOTKEY] restart did not install a new backend — no previous backend to restore")

        app.tray.set_hotkey(app.config.hotkey)

    # ── Cleanup ────────────────────────────────────────────────────────

    def stop_all(self) -> None:
        """Stop all hotkey backends (called during app shutdown).

        each backend's ``stop()`` runs in a worker thread under
        a hard 3s budget shared across all three backends. Previously
        ``stop_all`` called ``backend.stop()`` sequentially with no
        timeout — a single hung native backend (Win32
        ``UnregisterHotKey`` + listener-thread join, Wayland
        ``wl_display`` teardown, pynput listener join) could block the
        shutdown sequence for up to ~15s (3 backends × 5s join each).
        Backends that miss the 3s budget are leaked (their worker
        thread keeps running) and a warning is logged; every native
        listener thread is a daemon, so process exit still terminates
        it. ``stop()`` failures inside the budget are swallowed (logged
        at debug) so a poisoned backend doesn't abort the rest of
        shutdown — same contract as before.

        Implementation note: we do NOT use the ``with`` block on the
        ``ThreadPoolExecutor`` because ``__exit__`` calls
        ``shutdown(wait=True)`` which would block until every submitted
        future completes — defeating the 3s budget. Instead we call
        ``shutdown(wait=False, cancel_futures=True)`` so already-running
        workers are left to finish (or hang) in the background and the
        method returns as soon as ``concurrent.futures.wait`` does.
        """
        backend_attrs = ("_hotkey_backend", "_esc_backend", "_repaste_backend")
        live_attrs = [a for a in backend_attrs if getattr(self, a) is not None]
        if live_attrs:
            # NOT using ``with`` — see docstring: ``__exit__`` would
            # block on ``shutdown(wait=True)`` and defeat the budget.
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(live_attrs))
            try:
                futures = {pool.submit(self._stop_one_backend, a): a for a in live_attrs}
                done, not_done = concurrent.futures.wait(futures, timeout=3.0)
                for fut in not_done:
                    log.warning(
                        "[HOTKEY] %s did not stop within 3s budget — proceeding anyway",
                        futures[fut],
                    )
                # Surface any exception raised by a completed stop so
                # operators can diagnose poisoned backends (debug-level
                # — the user-visible contract is "stop_all never raises"
                # and that is preserved by swallowing here).
                for fut in done:
                    exc = fut.exception()
                    if exc is not None:
                        log.debug(
                            "[HOTKEY] %s stop() raised: %r",
                            futures[fut],
                            exc,
                            exc_info=True,
                        )
            finally:
                # wait=False: do NOT block on still-running workers
                # (that would defeat the 3s budget). cancel_futures=True
                # drops any not-yet-started submissions (defensive —
                # with max_workers==len(live_attrs) every submission
                # starts immediately, so this is a no-op in practice).
                pool.shutdown(wait=False, cancel_futures=True)
        # clear the spec trackers so a post-shutdown register()
        # call (e.g. from a test or a hot restart) does NOT skip the
        # rebuild under the "same spec" fast-path.
        self._esc_spec = None
        self._repaste_spec = None

    def _stop_one_backend(self, backend_attr: str) -> None:
        """stop a single backend and clear its attribute.

        Runs inside a ``concurrent.futures.ThreadPoolExecutor`` worker
        so a hung ``stop()`` cannot block the 3s budget in
        :meth:`stop_all`. ``stop()`` failures are swallowed (logged at
        debug) so a poisoned backend doesn't abort the rest of shutdown.
        The attribute is cleared UNCONDITIONALLY after ``stop()`` returns
        or raises — the post-stop code paths (and the test suite) treat
        ``None`` as "no backend", so leaving a partially-stopped backend
        in place would be worse than a clean None.
        """
        backend = getattr(self, backend_attr)
        if backend is None:
            return
        try:
            backend.stop()
        except Exception:
            log.debug("[HOTKEY] Failed to stop %s", backend_attr, exc_info=True)
        setattr(self, backend_attr, None)
