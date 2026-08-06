"""#2 HotkeyDispatcher — extracted from VoiceTyperApp.

Owns global hotkey registration: dictation toggle hotkey, ESC cancel
hotkey, and repaste hotkey. Each hotkey gets its own HotkeyBackend
instance (Win32 native, pynput, or Wayland), unless an identical spec
is already tracked in ``_shared_backend_pool`` — in which case the
existing backend is reused (rare; e.g. two roles bound to the same key).

Previously this concern lived in VoiceTyperApp as ~100 LOC across:
    _register_hotkey, _register_esc_hotkey, _unregister_esc_hotkey,
    _register_repaste_hotkey, _restart_hotkey

All of those now live here. VoiceTyperApp keeps thin delegate methods
for back-compat with callers (settings window, tests).

TODO — full per-spec backend pooling (deferred; touches native binary
wire protocol)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
The current implementation pools the THREE ROLES (dictation / ESC /
repaste) into a single native subprocess via the ``_shared_backend``
extra-matcher mechanism (see class docstring). It ALSO tracks every
created backend by spec in ``_shared_backend_pool`` so two roles that
happen to share the same spec (rare) reuse the same backend instance.
``get_active_backend_count()`` exposes the size of that pool.

The FULL refactor (deferred because it touches the native binary's
wire protocol) is to extend the binary's command-line surface to
accept a list of ``(role, hotkey_spec)`` pairs (e.g. via a startup
handshake frame) and emit wire events tagged with the originating
role (e.g. ``EVENT role=esc KEY_UP <esc>``). This would let the
binary itself handle suppression for all three specs (eliminating the
macOS / Windows suppression limitation noted in the class docstring)
and would let a SINGLE native binary serve an arbitrary number of
distinct specs — collapsing the per-spec pool to one process even
when the specs differ. The ``_shared_backend_pool`` dict established
here is the Python-side tracking infrastructure that the full
refactor will repurpose: each ``HotkeyBackend`` entry would become a
``(role, spec)`` registration against the single shared binary rather
than a distinct subprocess.

Stepping stones (no wire-protocol change required):
  1. (DONE) Pool the three roles into one subprocess via extra
     matchers on the dictation backend (``_shared_backend``).
  2. (DONE — minimal) Track every created backend by spec in
     ``_shared_backend_pool`` so identical specs reuse a backend.
  3. (TODO) Add a ``remove_extra_matcher(role)`` API to the native
     adapter so roles can be torn down individually without stopping
     the shared subprocess.
  4. (TODO — wire protocol change) Extend the native binary to accept
     multiple ``(role, spec)`` pairs at startup and emit role-tagged
     events. Replace the extra-matcher shim with direct role dispatch.
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

    Architecture note — pooled subprocess (one process for all three roles)
    ----------------------------------------------------------------
    ``register`` creates the dictation backend via
    ``create_hotkey_backend(hotkey, role="dictation")`` and stashes it
    on ``self._shared_backend``. On platforms that select the native
    ``SubprocessHotkeyBackend`` (macOS / Windows / Linux), that backend
    owns the SINGLE native listener process. ``register_esc`` and
    ``register_repaste`` STILL call ``create_hotkey_backend`` (for API
    compatibility with code that asserts ``_esc_backend is mock_backend``)
    but the returned backends are marked ``_delegated=True`` — their
    ``start()`` skips spawning a subprocess, and the actual matching for
    ESC / repaste happens via extra matchers on the shared (dictation)
    backend's event stream. The native binary emits ALL keystroke
    events on stdout (it does not filter to the matched spec — the
    Python side does the matching), so one process is sufficient.

    Resource reduction: 1 native binary subprocess instead of 3, 1
    reader thread instead of 3, 1 watchdog thread instead of 3, 1 IPC
    pipe instead of 3, 1 TOCTOU-verify cycle instead of 3. On Linux
    this means 1× opens ``/dev/input/event*`` (was 3×); on Windows 1×
    WH_KEYBOARD_LL hook (was 3×); on macOS 1× CGEventTap + 1× NSEvent
    monitor (was 3× each).

    Known limitation (macOS / Windows suppression): the native binary
    uses argv[1] (the dictation spec) to decide which keystrokes to
    suppress via the CGEventTap (macOS) / WH_KEYBOARD_LL hook
    (Windows). Extra matchers' specs are NOT known to the binary, so
    their keystrokes are NOT suppressed. On Linux this is a non-issue
    (evdev is read-only, no suppression). On macOS / Windows, the
    keystroke for an extra matcher (e.g. ESC, repaste combo) will
    reach the foreground app. This is acceptable for ESC (foreground
    apps handle ESC themselves) but may cause double-paste for repaste
    combos (the foreground app sees the combo AND the Python-side
    repaste fires). A future session can extend the binary's
    command-line surface to accept multiple specs for suppression.

    Fallback: if the shared backend's native doesn't support extra
    matchers (e.g. legacy ``PynputHotkey`` / ``WaylandHotkey`` /
    ``WindowsNativeHotkey`` selected by the factory because the native
    binary is missing), pooling is silently skipped and the per-role
    subprocess model is used (3 subprocesses). This preserves the
    pre-refactor behavior on platforms without the native binary.

    Planned future refactor (deferred — touches native binary wire protocol)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    Extend the native binary's command-line surface to accept a list
    of ``(role, hotkey_spec)`` pairs (e.g. via a startup handshake
    frame) and emit wire events tagged with the originating role
    (e.g. ``EVENT role=esc KEY_UP <esc>``). This would let the binary
    itself handle suppression for all three specs (eliminating the
    macOS / Windows suppression limitation above) and simplify the
    Python-side dispatch (role tag on each event instead of running
    every matcher against every event). The current extra-matcher
    approach is the no-wire-protocol-change stepping stone toward
    that goal.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._hotkey_backend: HotkeyBackend | None = None
        self._esc_backend: HotkeyBackend | None = None
        self._repaste_backend: HotkeyBackend | None = None
        # Shared backend handle — the dictation backend, whose native
        # subprocess ALSO matches the ESC and repaste specs via extra
        # matchers (see :meth:`_pool_aux_into_shared`). On platforms
        # that select the native ``SubprocessHotkeyBackend`` this
        # collapses what was three subprocesses (dictation + ESC +
        # repaste) into ONE. The separate ``_esc_backend`` /
        # ``_repaste_backend`` instances still exist for API
        # compatibility (tests assert ``_esc_backend is mock_backend``)
        # but are marked ``_delegated=True`` so their ``start()`` skips
        # spawning — they own no subprocess, reader thread, or watchdog.
        # ``None`` until :meth:`_create_and_start_main_backend`
        # succeeds, and reset to ``None`` by :meth:`stop_all`.
        self._shared_backend: HotkeyBackend | None = None
        # Per-spec backend pool — tracks every live backend by its
        # hotkey spec so that two roles bound to the SAME spec (rare;
        # e.g. dictation and repaste both set to ``<f2>``) reuse one
        # backend instance instead of spawning a second native
        # subprocess. Keyed by the canonical hotkey spec string
        # (e.g. ``"<caps_lock>"``, ``"<esc>"``, ``"<ctrl>+<v>"``).
        # Populated by :meth:`_track_pooled_backend` after a backend's
        # ``start()`` succeeds; depopulated by
        # :meth:`_untrack_pooled_backend` when the backend is stopped
        # (so a stale entry is never returned). :meth:`stop_all`
        # clears the entire dict. ``get_active_backend_count()``
        # returns ``len(self._shared_backend_pool)`` — the number of
        # DISTINCT native subprocesses currently owned by this
        # dispatcher.
        #
        # NOTE: this is a MINIMAL pooling layer. The full refactor
        # (single native binary serving an arbitrary number of
        # ``(role, spec)`` pairs via a wire-protocol handshake) is
        # documented as a TODO in the module docstring. This dict is
        # the Python-side tracking infrastructure the full refactor
        # will repurpose.
        self._shared_backend_pool: dict[str, HotkeyBackend] = {}
        # Stashed ESC / repaste callbacks so :meth:`_repool_aux_into_shared`
        # can re-register them with a freshly-created shared backend
        # (e.g. after :meth:`restart` swaps the dictation backend).
        # Without these, a restart would leave the ESC / repaste extra
        # matchers on the OLD (stopped) shared backend and the roles
        # would silently stop firing until the next ``register_esc`` /
        # ``register_repaste`` call.
        self._esc_callback: Any = None
        self._repaste_callback: Any = None
        # track the last-registered ESC and repaste specs so
        # ``register()`` can skip the teardown+rebuild cycle when the
        # spec hasn't changed. Previously ``register()`` unconditionally
        # rebuilt both backends on every call (including the
        # ``restart()`` path that delegates back to ``register()``),
        # causing a brief window where ESC / repaste weren't available
        # plus unnecessary OS grab churn on Windows / macOS.
        self._esc_spec: str | None = None
        self._repaste_spec: str | None = None
        # re-entrancy guard for
        # :meth:`_handle_shared_native_state_changed`. Re-registering an
        # aux role may itself trigger a native-backend swap (e.g. the
        # role's own native subprocess also fails), which fires the hook
        # again — the flag breaks the recursion.
        self._resyncing_aux = False
        # threading.Event for atomic cross-
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
        # threading.Event for atomic cross-thread
        # ESC-cancel signaling. See _on_esc_release for the consumer side.
        self._esc_pending_capture_exit_event: threading.Event = threading.Event()
        # PTT safety timer. None when not armed (toggle mode,
        # or PTT mode but no recording in progress). Set by
        # ``_start_ptt_safety_timer`` and canceled by
        # ``_cancel_ptt_safety_timer`` (called from ``stop_all`` and on
        # the normal key-up stop). See ``_on_ptt_safety_timeout`` for the
        # callback.
        self._ptt_safety_timer: threading.Timer | None = None

    # ── Registration ───────────────────────────────────────────────────

    def register(self, skip_aux: bool = False) -> bool:
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
        #
        # AB-34: ``skip_aux=True`` (used by ``restart()``) skips the
        # aux-backend calls entirely — the ESC/repaste specs are
        # unchanged on a hotkey restart, so re-creating those
        # backends is wasted work that briefly leaves them dead.
        if not skip_aux:
            if app.config.esc_cancel_enabled:
                esc_already_alive = (
                    self._esc_backend is not None and self._esc_backend.is_alive() and self._esc_spec == "<esc>"
                )
                if not esc_already_alive:
                    self.register_esc()
            elif self._esc_backend is not None:
                # Untrack from the per-spec pool BEFORE stopping so the
                # count reflects the imminent teardown. ``stop()`` is
                # suppressed (may raise on a poisoned backend) but the
                # untracking is unconditional.
                self._untrack_pooled_backend(self._esc_backend)
                with contextlib.suppress(Exception):
                    self._esc_backend.stop()
                self._esc_backend = None
                self._esc_spec = None
                # Remove the pooled extra matcher from the shared backend
                # (which stays alive) so ESC stops cancelling dictation.
                self._remove_shared_extra_matcher("esc")
                self._esc_callback = None

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
                # Untrack from the per-spec pool BEFORE stopping (see
                # the ESC teardown path above for rationale).
                self._untrack_pooled_backend(self._repaste_backend)
                with contextlib.suppress(Exception):
                    self._repaste_backend.stop()
                self._repaste_backend = None
                self._repaste_spec = None
                # Remove the pooled extra matcher from the shared backend
                # (which stays alive) so the repaste hotkey stops firing
                # after ``repaste_hotkey`` is cleared in config.
                self._remove_shared_extra_matcher("repaste")
                self._repaste_callback = None

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

        Per-spec pool: if a backend with the same ``hotkey_str`` is
        already tracked in ``_shared_backend_pool`` and is still alive,
        it is returned as-is (no factory call, no second ``start()``).
        This collapses the rare case where two roles share the same spec
        (e.g. dictation and repaste both bound to ``<f2>``) into a
        single native subprocess. The backend is added to the pool
        AFTER ``start()`` succeeds so a failed start does not leave a
        stale entry.
        """
        app = self._app
        # Per-spec pool fast path: if a backend for this exact spec is
        # already alive, reuse it instead of spawning a second native
        # subprocess. ``is_alive()`` is the canonical liveness check
        # across all backend types (native subprocess, pynput listener,
        # Wayland socket). A dead pooled entry is purged below so the
        # next call re-creates fresh.
        pooled = self._shared_backend_pool.get(hotkey_str)
        if pooled is not None:
            if pooled.is_alive():
                log.info(
                    "[HOTKEY] Reusing pooled backend for spec %r "
                    "(active pool size=%d) — no new subprocess spawned",
                    hotkey_str,
                    len(self._shared_backend_pool),
                )
                # Re-install as the shared backend so any subsequent
                # aux pooling (ESC / repaste extra matchers) attaches
                # to this instance, then re-pool existing aux roles.
                self._shared_backend = pooled
                self._repool_aux_into_shared()
                return pooled
            # Stale entry — drop it so the factory path below can
            # install a fresh backend under the same key.
            self._shared_backend_pool.pop(hotkey_str, None)
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
        # wire the ``_NativeBackendAdapter``'s native↔legacy
        # state-change hook so the dispatcher can re-sync the pooled
        # ESC / repaste extra matchers when the shared backend's native
        # subprocess permanently fails and the adapter swaps to legacy.
        with contextlib.suppress(AttributeError, TypeError):
            new_backend._on_state_change_callback = (  # type: ignore[attr-defined]
                self._handle_shared_native_state_changed
        )
        # surface a tray notification when the user binds Caps
        # Lock on Wayland. The ``WaylandHotkey`` backend has no key-
        # suppression mechanism, so the OS will toggle caps state on
        # every press and the dictated text will be CAPITALIZED. The
        # factory already logged the same condition (see
        # ``factory.py``); here we ALSO surface it via the tray's
        # safety channel so the user actually sees it (logs are
        # invisible to most users). Done after ``create_hotkey_backend``
        # so the warning fires even if ``start()`` later raises.
        self._maybe_warn_wayland_caps_lock(hotkey_str)
        # PTT safety timeout — if a recording started via
        # push-to-talk exceeds 60s without a stop event, auto-stop and
        # surface a tray notification. The release callback is wired
        # below for PTT mode; this timer is a safety net in case the
        # release event is missed (e.g. focus loss, IME intercept, LL
        # hook race). See ``_start_ptt_safety_timer`` for details.
        if app.config.recording_mode == "push_to_talk":
            self._start_ptt_safety_timer()
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
        # Track in the per-spec pool AFTER start() succeeded so a
        # failed start does not leave a stale entry that would cause
        # a future ``register()`` to return a dead backend.
        self._track_pooled_backend(hotkey_str, new_backend)
        # Install as the shared backend and re-pool any aux backends
        # that were registered against the PREVIOUS shared backend
        # (e.g. after :meth:`restart` swaps the dictation backend).
        # ``_shared_backend`` is the single point of truth for "which
        # backend owns the live native subprocess that ESC / repaste
        # extra matchers are multiplexed onto".
        self._shared_backend = new_backend
        self._repool_aux_into_shared()
        return new_backend

    # ── Per-spec backend pool tracking ─────────────────────────────────

    def _track_pooled_backend(self, spec: str, backend: HotkeyBackend) -> None:
        """Record ``backend`` in ``_shared_backend_pool`` under ``spec``.

        Called AFTER a backend's ``start()`` succeeds so the pool only
        ever contains live backends. If an entry already exists for
        ``spec`` (e.g. a stale entry from a backend that's about to be
        stopped), it is overwritten — the caller has just installed a
        fresh backend for that spec.
        """
        self._shared_backend_pool[spec] = backend

    def _untrack_pooled_backend(self, backend: HotkeyBackend | None) -> None:
        """Remove ``backend`` from ``_shared_backend_pool`` by identity.

        Called when a backend is stopped (via :meth:`stop_all`,
        :meth:`restart`, :meth:`unregister_esc`, or the teardown paths
        in :meth:`register_esc` / :meth:`register_repaste` /
        :meth:`register`) so the pool never returns a dead backend.
        Identity comparison (``is``) is used instead of spec lookup
        because the same spec may have been re-registered under a new
        backend instance — we only want to drop the OLD instance.
        """
        if backend is None:
            return
        for spec, pooled in list(self._shared_backend_pool.items()):
            if pooled is backend:
                del self._shared_backend_pool[spec]
                log.debug(
                    "[HOTKEY] Untracked pooled backend for spec %r "
                    "(remaining pool size=%d)",
                    spec,
                    len(self._shared_backend_pool),
                )

    def get_active_backend_count(self) -> int:
        """Return the number of DISTINCT native backends currently
        tracked in ``_shared_backend_pool``.

        This is the count of live hotkey subprocesses owned by this
        dispatcher. On the full-pooling path (native
        ``SubprocessHotkeyBackend`` selected) with three DIFFERENT
        specs, this is 1 — the dictation backend's subprocess hosts
        the ESC and repaste extra matchers, and the ESC / repaste
        backends are delegated (no subprocess of their own). When
        pooling is unavailable (legacy backend) or specs collide, the
        count reflects the actual subprocess count.
        """
        # Purge any dead entries before reporting so the count reflects
        # currently-live backends. ``is_alive()`` is best-effort; a
        # backend that crashed between calls will be cleaned up here.
        for spec, pooled in list(self._shared_backend_pool.items()):
            if not pooled.is_alive():
                del self._shared_backend_pool[spec]
        return len(self._shared_backend_pool)

    # ── Multi-spec pooling helpers ────────────────────────────────────

    def _native_of(self, backend: HotkeyBackend | None) -> Any:
        """Return the wrapped ``SubprocessHotkeyBackend`` if ``backend``
        is a ``_NativeBackendAdapter``, else ``None``.

        The adapter (``voice_typer.server.hotkeys.native_adapter``)
        stores the native backend on ``self._native``. We access it
        via ``getattr`` so this method works for ANY backend that
        follows the same adapter pattern (and silently returns
        ``None`` for legacy backends like ``PynputHotkey`` /
        ``WaylandHotkey`` / ``WindowsNativeHotkey`` that don't support
        extra matchers — those fall back to the per-role subprocess
        model).

        This deliberately accesses a private attribute (``_native``)
        on a class owned by another module; the alternative (adding a
        public getter to ``_NativeBackendAdapter``) is out of scope
        for this refactor's owned-file list.
        """
        if backend is None:
            return None
        # BROKEN-3: when the backend is a ``_NativeBackendAdapter`` that
        # has swapped to its legacy fallback (the native subprocess
        # permanently failed) — or both died — the wrapped native object
        # is DEAD and no longer receives events. Report "no native" so
        # aux roles fall back to per-role subprocesses instead of
        # pooling onto the dead subprocess (which silently kills
        # ESC / repaste until restart).
        if getattr(backend, "_state", None) in ("FALLBACK", "FAILED"):
            return None
        native = getattr(backend, "_native", None)
        if native is None:
            return None
        # Duck-type: the native backend must support the pooling API.
        if not hasattr(native, "add_extra_matcher"):
            return None
        return native

    def _shared_native(self) -> Any:
        """Return the shared backend's native ``SubprocessHotkeyBackend``,
        or ``None`` if the shared backend is unset or doesn't support
        the pooling API (legacy backend in play)."""
        return self._native_of(self._shared_backend)

    def _pool_aux_into_shared(
        self,
        role: str,
        spec: str,
        callback: Any,
        aux_backend: HotkeyBackend | None,
    ) -> bool:
        """Register ``(role, spec, callback)`` as an extra matcher on
        the shared backend AND mark ``aux_backend`` as delegated (so
        its own ``start()`` skips spawning).

        Returns True if the role was pooled onto the shared backend;
        False if pooling is unavailable (no shared backend, or the
        shared backend's native doesn't support extra matchers) and
        the caller should fall back to the per-role subprocess model.

        Safe to call multiple times for the same role —
        :meth:`add_extra_matcher` is idempotent on ``role`` (replaces
        the parsed spec, preserves callbacks), and the
        ``set_role_*`` methods overwrite the previous value.
        """
        shared_native = self._shared_native()
        if shared_native is None:
            return False
        try:
            shared_native.add_extra_matcher(role, spec)
            shared_native.set_role_callback(role, callback)
            # Mark the aux backend as delegated so its start() skips
            # spawning a subprocess. The aux backend's own callback
            # (passed to start()) is NEVER invoked — the shared
            # backend's extra matcher handles dispatch.
            aux_native = self._native_of(aux_backend)
            if aux_native is not None:
                aux_native._delegated = True  # type: ignore[attr-defined]
            log.info(
                "[HOTKEY] Pooled %r into shared backend (spec=%r) — "
                "separate %r backend is delegated (no subprocess)",
                role,
                spec,
                role,
            )
            return True
        except Exception:
            log.debug(
                "[HOTKEY] Failed to pool %r into shared backend — "
                "falling back to per-role subprocess",
                role,
                exc_info=True,
            )
            return False

    def _repool_aux_into_shared(self) -> None:
        """Re-register any existing ESC / repaste extra matchers
        against the CURRENT shared backend.

        Called from :meth:`_create_and_start_main_backend` after a new
        shared backend is installed (e.g. by :meth:`restart` swapping
        the dictation backend). Without this, a restart would leave
        the ESC / repaste extra matchers on the OLD (stopped) shared
        backend and the roles would silently stop firing.

        Idempotent — safe to call when no aux backends are registered
        (no-op) or when the shared backend doesn't support pooling
        (no-op).
        """
        shared_native = self._shared_native()
        if shared_native is None:
            return
        if self._esc_spec is not None and self._esc_callback is not None:
            try:
                shared_native.add_extra_matcher("esc", self._esc_spec)
                shared_native.set_role_callback("esc", self._esc_callback)
            except Exception:
                log.debug("[HOTKEY] Failed to re-pool ESC after shared-backend swap", exc_info=True)
        if self._repaste_spec is not None and self._repaste_callback is not None:
            try:
                shared_native.add_extra_matcher("repaste", self._repaste_spec)
                shared_native.set_role_callback("repaste", self._repaste_callback)
            except Exception:
                log.debug("[HOTKEY] Failed to re-pool repaste after shared-backend swap", exc_info=True)

    def _remove_shared_extra_matcher(self, role: str) -> None:
        """Remove the pooled extra matcher ``role`` from the shared
        backend.

        Called from the DISABLE paths (:meth:`unregister_esc` and the
        ESC / repaste teardown branches in :meth:`register`) where the
        aux backend is stopped but the shared backend stays alive.
        Without this, the role keeps firing its callback (e.g. ESC
        keeps cancelling dictation after ``esc_cancel_enabled`` is
        turned off via settings).

        No-op when the role was never pooled (legacy per-role
        subprocess model, or no shared backend) —
         ``remove_extra_matcher`` is safe to call for an unknown role.
         """

        shared_native = self._shared_native()
        if shared_native is None:
            return
        with contextlib.suppress(Exception):
            shared_native.remove_extra_matcher(role)
    def _handle_shared_native_state_changed(self, state: str) -> None:
        """BROKEN-3: re-sync the aux (ESC / repaste) backends when the
        shared backend's ``_NativeBackendAdapter`` swaps native ↔ legacy.

        When the adapter's native subprocess permanently fails and it
        swaps to a legacy backend (``FALLBACK`` state), the pooled
        ``"esc"`` / ``"repaste"`` extra matchers live on the DEAD native
        — the legacy backend that actually receives events knows nothing
        about those roles, so the delegated aux backends silently stop
        firing. Re-registering the active aux roles re-runs the pooling
        decision: with ``_shared_native()`` now reporting ``None`` for a
        FALLBACK adapter (see :meth:`_native_of`), each role falls back
        to its own per-role subprocess and keeps working. On recovery
        back to ``NATIVE``, the same re-registration re-pools the roles
        onto the recovered native — avoiding a double-fire (per-role
        subprocess + extra matcher both matching).

        Guarded by ``_resyncing_aux`` so a recursive swap (the role's
        own native also failing, re-firing this hook from inside
        ``register_esc``) cannot loop forever.
        """
        if self._resyncing_aux:
            return
        self._resyncing_aux = True
        try:
            if self._esc_spec is not None and self._esc_callback is not None:
                self.register_esc()
            if self._repaste_spec is not None and self._repaste_callback is not None:
                self.register_repaste()
        except Exception:
            log.debug(
                "[HOTKEY] Aux role re-sync after shared-backend state=%r failed",
                state,
                exc_info=True,
            )
        finally:
            self._resyncing_aux = False

    def _maybe_warn_wayland_caps_lock(self, hotkey_str: str) -> None:
        """surface a tray notification if the user bound Caps Lock
        on Wayland. See ``factory.py`` for the matching log.warning.

        The factory detects the condition at register time and logs it;
        this method mirrors the warning via the tray's safety channel so
        the user actually sees it. Idempotent — calling it multiple times
        for the same hotkey re-surfaces the same notification, which is
        acceptable (the user may have dismissed the first one).
        """
        try:
            from voice_typer.server.platform_utils import is_wayland_session

            if not is_wayland_session():
                return
            if not hotkey_str or "caps_lock" not in hotkey_str.lower():
                return
            with contextlib.suppress(Exception):
                self._app.tray.notify_safety(
                    APP_NAME,
                    "On Wayland, Caps Lock cannot be suppressed — "
                    "your text will be capitalized. Bind Alt or a "
                    "function key instead, or remap Caps Lock via "
                    "your compositor's settings.",
                )
        except Exception:
            log.debug("[HOTKEY] _maybe_warn_wayland_caps_lock failed", exc_info=True)

    # PTT safety timeout. Push-to-talk starts recording on key-down
    # and stops on key-up. If the key-up event is missed (e.g. the LL hook
    # race, focus loss to a fullscreen app, IME intercept, or the listener
    # thread dying), the recording would run forever, filling disk and
    # confusing the user. This 60s safety timer auto-stops the recording
    # and surfaces a tray notification. The timer is armed in
    # ``_create_and_start_main_backend`` for PTT mode and canceled on the
    # normal stop path (``_cancel_ptt_safety_timer``). The timer is a
    # best-effort safety net — it does NOT replace the normal key-up
    # detection, it only catches the case where key-up was missed.
    _PTT_SAFETY_TIMEOUT_SECONDS: float = 60.0

    def _start_ptt_safety_timer(self) -> None:
        """Arm the 60s PTT safety timer. Called from
        ``_create_and_start_main_backend`` when PTT mode is active.

        The timer is stored on ``self._ptt_safety_timer`` and canceled by
        ``_cancel_ptt_safety_timer`` (called from ``stop_all`` and on the
        normal key-up stop). If the timer fires, it calls
        ``_on_ptt_safety_timeout`` which auto-stops dictation and surfaces
        a tray notification.
        """
        # cancel any existing timer (e.g. from a previous registration)
        self._cancel_ptt_safety_timer()
        try:
            timer = threading.Timer(
                self._PTT_SAFETY_TIMEOUT_SECONDS,
                self._on_ptt_safety_timeout,
            )
            timer.daemon = True
            timer.name = "PTT-Safety-Timeout"
            self._ptt_safety_timer = timer
            timer.start()
            log.debug(
                "[HOTKEY] PTT safety timer armed (%.0fs)",
                self._PTT_SAFETY_TIMEOUT_SECONDS,
            )
        except Exception:
            log.debug("[HOTKEY] Failed to arm PTT safety timer", exc_info=True)

    def _cancel_ptt_safety_timer(self) -> None:
        """Cancel the PTT safety timer if armed. Safe to call when no
        timer is active (no-op)."""
        timer = getattr(self, "_ptt_safety_timer", None)
        if timer is not None:
            timer.cancel()
            self._ptt_safety_timer = None

    def _on_ptt_safety_timeout(self) -> None:
        """fired by the PTT safety timer when a recording has
        run for 60s without a stop event. Auto-stops dictation and
        surfaces a tray notification so the user knows the release was
        missed.

        This is a safety net, not a replacement for normal key-up
        detection. The normal stop path (``set_on_release`` callback)
        cancels this timer; if the timer fires, it means the release
        event was lost.
        """
        log.warning(
            "[HOTKEY] PTT release event missed — auto-stopping recording after %.0fs safety timeout",
            self._PTT_SAFETY_TIMEOUT_SECONDS,
        )
        try:
            with contextlib.suppress(Exception):
                self._app._stop_dictation()
            with contextlib.suppress(Exception):
                self._app.tray.notify_safety(
                    APP_NAME,
                    "PTT release event missed — recording auto-stopped after 60s safety timeout.",
                )
        except Exception:
            log.exception("[HOTKEY] PTT safety timeout handler failed")

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

        Per-spec pool: the ESC backend is tracked in
        ``_shared_backend_pool`` under ``"<esc>"`` after ``start()``
        succeeds, and untracked when stopped. The fast-path reuse
        (returning the existing backend instead of calling the
        factory) is NOT implemented for ESC because the ESC callback
        differs from the dictation callback — reusing a dictation
        backend (rare case where the user bound dictation to ESC)
        would cause both callbacks to fire on the same keypress.
        The full refactor (see module docstring TODO) solves this
        via role-tagged wire events.
        """
        # Stop any existing backend first
        if self._esc_backend:
            self._untrack_pooled_backend(self._esc_backend)
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
                    # Route the release callback through the shared
                    # backend's extra matcher (role "esc") so a
                    # delegated ESC backend (no subprocess of its own)
                    # still receives key-up events. Falls back to the
                    # per-role ``_esc_backend`` when pooling is off
                    # (legacy backends).
                    shared_native = self._shared_native()
                    if shared_native is not None:
                        with contextlib.suppress(Exception):
                            shared_native.set_role_on_release("esc", self._on_esc_release)
                    if self._esc_backend is not None:
                        self._esc_backend.set_on_release(self._on_esc_release)
                    return
                self._app._cancel_dictation()

            # Stash the callback so :meth:`_repool_aux_into_shared`
            # can re-register it after a future shared-backend swap
            # (e.g. :meth:`restart` swaps the dictation backend).
            self._esc_callback = _esc_callback
            # Pool ESC into the shared backend (one subprocess for all
            # three roles). If pooling succeeds, the separate
            # ``_esc_backend`` is marked delegated and its ``start()``
            # skips spawning — the actual ESC matching happens via an
            # extra matcher on the shared (dictation) backend's
            # subprocess. If pooling fails (no shared backend, or the
            # shared backend is a legacy backend without extra-matchers
            # support), fall back to the per-role subprocess model.
            self._pool_aux_into_shared("esc", "<esc>", _esc_callback, self._esc_backend)
            self._esc_backend.start(_esc_callback)
            self._esc_spec = "<esc>"
            # Track in the per-spec pool AFTER start() succeeded so a
            # failed start does not leave a stale entry. See
            # :meth:`_track_pooled_backend` for the rationale.
            self._track_pooled_backend("<esc>", self._esc_backend)
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
                self._untrack_pooled_backend(self._esc_backend)
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
        # Also clear the shared backend's ESC release callback so
        # the extra matcher doesn't keep firing the release on every
        # ESC key-up. ``contextlib.suppress`` covers the case where
        # pooling is off (no shared native backend).
        shared_native = self._shared_native()
        if shared_native is not None:
            with contextlib.suppress(Exception):
                shared_native.set_role_on_release("esc", None)

    def unregister_esc(self) -> None:
        """Unregister the ESC hotkey."""
        if self._esc_backend:
            self._untrack_pooled_backend(self._esc_backend)
            with contextlib.suppress(Exception):
                self._esc_backend.stop()
            self._esc_backend = None
            self._esc_spec = None
            # Also remove the pooled "esc" extra matcher from the shared
            # backend. The delegated ESC backend's stop() only clears its
            # own (never-spawned) state — the shared backend stays alive,
            # so without this ESC keeps firing the cancel callback after
            # the hotkey is disabled (``esc_cancel_enabled`` toggle). No-op
            # in the legacy per-role subprocess model (role never pooled).
            self._remove_shared_extra_matcher("esc")
            # Clear the stashed callback so a later shared-backend swap
            # (``_repool_aux_into_shared``) can't re-register a disabled
            # role. ``register_esc`` re-stashes it on the next enable.
            self._esc_callback = None
            log.info("[HOTKEY] ESC cancel hotkey unregistered")

    def register_repaste(self) -> None:
        """Register the repaste hotkey."""
        if self._repaste_backend:
            self._untrack_pooled_backend(self._repaste_backend)
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
                _repaste_cb = self._make_repaste_callback()
                # Stash the callback so :meth:`_repool_aux_into_shared`
                # can re-register it after a shared-backend swap.
                self._repaste_callback = _repaste_cb
                # Pool repaste into the shared backend (one subprocess
                # for all three roles). See :meth:`register_esc` for
                # the full rationale. Falls back to the per-role
                # subprocess model when pooling is unavailable.
                self._pool_aux_into_shared(
                    "repaste",
                    self._app.config.repaste_hotkey,
                    _repaste_cb,
                    self._repaste_backend,
                )
                self._repaste_backend.start(_repaste_cb)
                self._repaste_spec = self._app.config.repaste_hotkey
                # Track in the per-spec pool AFTER start() succeeded
                # (see :meth:`_track_pooled_backend` for the rationale).
                self._track_pooled_backend(self._app.config.repaste_hotkey, self._repaste_backend)
                log.info("[HOTKEY] Repaste hotkey registered: %s", self._app.config.repaste_hotkey)
            except Exception:
                # null the failed backend reference so a
                # subsequent ``register()`` / ``register_repaste()``
                # doesn't try to ``stop()`` a partially-started backend.
                # ``stop()`` is safe to call on a partially-started
                # backend, so call it before nulling to release any OS
                # resources the partial start did acquire.
                if self._repaste_backend is not None:
                    self._untrack_pooled_backend(self._repaste_backend)
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
            # Untrack from the per-spec pool BEFORE stopping so the
            # count drops before the (possibly slow) stop() join. The
            # subsequent ``_create_and_start_main_backend`` call will
            # either reuse a DIFFERENT pooled backend (if the new spec
            # is also in the pool) or create a fresh one.
            self._untrack_pooled_backend(old_backend)
            try:
                old_backend.stop()
            except Exception:
                log.exception("[HOTKEY] Failed to stop previous backend before restart")
            self._hotkey_backend = None

        # AB-34 / T-1: ``register()`` ALSO calls ``register_esc()`` +
        # ``register_repaste()`` for first-time-setup convenience, but
        # ``restart()`` only swaps the MAIN dictation hotkey — the
        # ESC and repaste specs are unchanged, so re-creating those
        # backends would waste subprocess spawns / thread creation /
        # Win32 hook installs and briefly leave ESC dead during the
        # stop→start window. Inline the main-backend creation here
        # instead of delegating to ``register()``.
        try:
            new_backend = self._create_and_start_main_backend(hotkey)
            self._hotkey_backend = new_backend
            register_ok = True
        except Exception as exc:
            register_ok = False
            log.warning(
                "[HOTKEY] restart register failed for %r: %s",
                hotkey,
                exc,
            )
            # mirror ``register()``'s tray notification on failure so
            # the user sees which hotkey the OS rejected (PVT-G5-027
            # — users would otherwise have no idea why their settings
            # change silently rolled back).
            with contextlib.suppress(Exception):
                app.tray.notify(
                    APP_NAME,
                    f"Hotkey {hotkey} could not be registered. "
                    "It may be in use by another app. "
                    "Use the tray menu to toggle dictation, or pick a different hotkey in Settings.",
                )

        if register_ok:
            # new backend installed — old backend already stopped above.
            pass
        else:
            # registration failed. The OLD backend was already stopped,
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
        # Clear the stashed ESC / repaste callbacks and the shared
        # backend handle so a post-shutdown ``register()`` starts from
        # a clean slate. The extra matchers on the (now-stopped)
        # shared backend's native are torn down by the backend's own
        # ``stop()`` — we don't need to call ``remove_extra_matcher``
        # here because the native backend object is discarded.
        self._esc_callback = None
        self._repaste_callback = None
        self._shared_backend = None
        # Clear the per-spec pool so a post-shutdown ``register()``
        # starts from a clean slate. The backends themselves were
        # stopped (and untracked) by ``_stop_one_backend`` above; this
        # clears any entries that ``_stop_one_backend`` may have missed
        # (e.g. a backend that was in the pool but not assigned to any
        # of the three role attributes — defensive).
        self._shared_backend_pool.clear()
        # cancel any armed PTT safety timer so a hot-restart
        # or shutdown doesn't leave a dangling Timer that fires after
        # the dispatcher is torn down.
        self._cancel_ptt_safety_timer()

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
        # Untrack from the per-spec pool BEFORE stopping so the count
        # drops before the (possibly slow) stop() join. ``stop()`` is
        # best-effort below; the untracking is unconditional so a
        # poisoned backend doesn't linger in the pool.
        self._untrack_pooled_backend(backend)
        try:
            backend.stop()
        except Exception:
            log.debug("[HOTKEY] Failed to stop %s", backend_attr, exc_info=True)
        setattr(self, backend_attr, None)
