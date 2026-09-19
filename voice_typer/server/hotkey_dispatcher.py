"""#2 HotkeyDispatcher, extracted from VoiceTyperApp.

Owns global hotkey registration: dictation toggle hotkey, ESC cancel
hotkey, and repaste hotkey. Each hotkey gets its own HotkeyBackend
instance (Win32 native, pynput, or Wayland), unless an identical spec
is already tracked in ``_shared_backend_pool``, in which case the
existing backend is reused (rare; e.g. two roles bound to the same key).

Previously this concern lived in VoiceTyperApp as ~100 LOC across:
    _register_hotkey, _register_esc_hotkey, _unregister_esc_hotkey,
    _register_repaste_hotkey, _restart_hotkey

All of those now live here. VoiceTyperApp keeps thin delegate methods
for back-compat with callers (settings window, tests).

TODO, full per-spec backend pooling (deferred; touches native binary
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
distinct specs, collapsing the per-spec pool to one process even
when the specs differ. The ``_shared_backend_pool`` dict established
here is the Python-side tracking infrastructure that the full
refactor will repurpose: each ``HotkeyBackend`` entry would become a
``(role, spec)`` registration against the single shared binary rather
than a distinct subprocess.

Stepping stones (no wire-protocol change required):
  1. (DONE) Pool the three roles into one subprocess via extra
     matchers on the dictation backend (``_shared_backend``).
  2. (DONE, minimal) Track every created backend by spec in
     ``_shared_backend_pool`` so identical specs reuse a backend.
  3. (DONE) Role-based extra-matcher teardown:
     ``SubprocessHotkeyBackend.remove_extra_matcher(role)`` drops a
     single pooled matcher from the shared subprocess without a
     restart. The dispatcher wraps it as
     :meth:`_remove_shared_extra_matcher` and calls it from every
     disable / teardown path (``unregister_esc``, the ESC / repaste
     disable branches in :meth:`register`, the empty-config branch of
     :meth:`register_repaste`, and the pool-then-start failure paths)
     so a disabled role stops firing while the shared backend stays
     alive. Re-enabling a role re-adds the matcher via
     :meth:`_pool_aux_into_shared` (no matcher leak: ``add`` is
     idempotent on role, ``remove`` is a no-op for an unknown role).
  4. (TODO, wire protocol change) Extend the native binary to accept
     multiple ``(role, spec)`` pairs at startup and emit role-tagged
     events. Replace the extra-matcher shim with direct role dispatch.
     This is the remaining cross-layer work: it touches the native
     binary sources and requires host validation (C-TDEV). Until then
     the macOS / Windows suppression limitation in the class docstring
     stands.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import logging
import threading
import weakref
from typing import Any

from voice_typer.server.branding import APP_NAME
from voice_typer.server.config import DEFAULT_HOTKEY
from voice_typer.server.hotkeys import HotkeyBackend, create_hotkey_backend
from voice_typer.server.i18n import t as i18n_t
from voice_typer.server.keyboard_ownership import keyboard_ownership
from voice_typer.server.tray_hotkey import format_hotkey_label

log = logging.getLogger(__name__)

# Registry of dispatchers with a live PTT safety timer. Lets the test
_LIVE_PTT_TIMER_DISPATCHERS: weakref.WeakSet = weakref.WeakSet()


# Short human label for a backend, used in the ``Backend created`` /
_BACKEND_KIND_LABELS = {
    "_NativeBackendAdapter": "native",
    "WindowsNativeHotkey": "native-poll",
    "PynputHotkey": "pynput",
    "WaylandHotkey": "wayland",
}


def _backend_kind_label(backend) -> str:
    name = type(backend).__name__
    return _BACKEND_KIND_LABELS.get(name, name.lstrip("_"))


class HotkeyDispatcher:
    """Owns the three global hotkey backends (dictation / ESC / repaste).

    #2 extracted from VoiceTyperApp. The app passes itself
    (``app``) so HotkeyDispatcher can:
    - Read ``app.config`` (hotkey, recording_mode, esc_cancel_enabled, repaste_hotkey)
    - Call ``app.toggle_dictation`` / ``app._stop_dictation`` /
      ``app._cancel_dictation`` / ``app.repaste_last`` as hotkey callbacks
    - Call ``app.tray.notify`` on registration failure
    - Call ``app.tray.set_hotkey`` after a hotkey restart

    Architecture note, pooled subprocess (one process for all three roles)
    ----------------------------------------------------------------
    ``register`` creates the dictation backend via
    ``create_hotkey_backend(hotkey, role="dictation")`` and stashes it
    on ``self._shared_backend``. On platforms that select the native
    ``SubprocessHotkeyBackend`` (macOS / Windows / Linux), that backend
    owns the SINGLE native listener process. ``register_esc`` and
    ``register_repaste`` STILL call ``create_hotkey_backend`` (for API
    compatibility with code that asserts ``_esc_backend is mock_backend``)
    but the returned backends are marked ``_delegated=True``, their
    ``start()`` skips spawning a subprocess, and the actual matching for
    ESC / repaste happens via extra matchers on the shared (dictation)
    backend's event stream. The native binary emits ALL keystroke
    events on stdout (it does not filter to the matched spec, the
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

    Planned future refactor (deferred, touches native binary wire protocol)
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
        # Shared backend handle, the dictation backend, whose native
        self._shared_backend: HotkeyBackend | None = None
        # Per-spec backend pool, tracks every live backend by its
        self._shared_backend_pool: dict[str, HotkeyBackend] = {}
        # Stashed ESC / repaste callbacks so :meth:`_repool_aux_into_shared`
        self._esc_callback: Any = None
        self._repaste_callback: Any = None
        # track the last-registered ESC and repaste specs so
        self._esc_spec: str | None = None
        self._repaste_spec: str | None = None
        # re-entrancy guard for
        self._resyncing_aux = False
        # threading.Event for atomic cross-
        self._esc_pending_capture_exit_event: threading.Event = threading.Event()
        # PTT safety timer. None when not armed (toggle mode,
        self._ptt_safety_timer: threading.Timer | None = None

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
        from voice_typer.server.config_validators import _validate_hotkey

        validation_error = _validate_hotkey(hotkey_str)
        if validation_error is not None:
            log.warning(
                "[HOTKEY] configured hotkey %r rejected (%s), falling back to default <caps_lock>",
                hotkey_str,
                validation_error,
            )
            hotkey_str = DEFAULT_HOTKEY  # platform default (see config._default_hotkey_for_platform)
            app.config.hotkey = hotkey_str

        log.info("[HOTKEY] Registering: %s -> toggle_dictation", format_hotkey_label(hotkey_str))

        success = False
        try:
            new_backend = self._create_and_start_main_backend(hotkey_str)
            # assign only after start() succeeded. A failure
            self._hotkey_backend = new_backend
            success = True
        except Exception as exc:
            # name the hotkey in the notification so the user
            log.warning("[HOTKEY] Registration FAILED -- %s: %s", hotkey_str, exc)
            log.debug("Hotkey registration error", exc_info=True)
            app.tray.notify(
                APP_NAME,
                i18n_t("notify.hotkey_dispatcher.register_failed", hotkey=hotkey_str),
            )

        # Feature: ESC to cancel -- register ESC hotkey when enabled
        if not skip_aux:
            if app.config.esc_cancel_enabled:
                esc_already_alive = (
                    self._esc_backend is not None and self._esc_backend.is_alive() and self._esc_spec == "<esc>"
                )
                if not esc_already_alive:
                    self.register_esc()
            elif self._esc_backend is not None:
                # Untrack from the per-spec pool BEFORE stopping so the
                self._untrack_pooled_backend(self._esc_backend)
                with contextlib.suppress(Exception):
                    self._esc_backend.stop()
                self._esc_backend = None
                self._esc_spec = None
                # Remove the pooled extra matcher from the shared backend
                self._remove_shared_extra_matcher("esc")
                self._esc_callback = None

            # Feature: Repaste hotkey
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
                self._untrack_pooled_backend(self._repaste_backend)
                with contextlib.suppress(Exception):
                    self._repaste_backend.stop()
                self._repaste_backend = None
                self._repaste_spec = None
                # Remove the pooled extra matcher from the shared backend
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
        pooled = self._shared_backend_pool.get(hotkey_str)
        if pooled is not None:
            if pooled.is_alive():
                log.info(
                    "[HOTKEY] Reusing pooled backend for %s (active pool size=%d), no new subprocess spawned",
                    format_hotkey_label(hotkey_str),
                    len(self._shared_backend_pool),
                )
                # Re-install as the shared backend so any subsequent
                self._shared_backend = pooled
                self._repool_aux_into_shared()
                return pooled
            # Stale entry, drop it so the factory path below can
            self._shared_backend_pool.pop(hotkey_str, None)
        # pass role="dictation" so the WaylandHotkey backend (if
        new_backend = create_hotkey_backend(hotkey_str, role="dictation")
        log.debug("[HOTKEY] Backend created: %s", _backend_kind_label(new_backend))
        # give the backend a reference to the tray so
        with contextlib.suppress(AttributeError, TypeError):
            new_backend._tray = app.tray
        # wire the ``_NativeBackendAdapter``'s native↔legacy
        with contextlib.suppress(AttributeError, TypeError):
            new_backend._on_state_change_callback = self._handle_shared_native_state_changed
        # surface a tray notification when the user binds Caps
        self._maybe_warn_wayland_caps_lock(hotkey_str)
        # PTT safety timeout, if a recording started via
        if app.config.recording_mode == "push_to_talk":
            self._start_ptt_safety_timer()
        # USER-REQUESTED FIX: in toggle mode, fire the toggle on key-up
        if app.config.recording_mode == "toggle":
            with contextlib.suppress(AttributeError, TypeError):
                new_backend.set_toggle_on_keyup(True)
        new_backend.start(self._make_dictation_callback())
        # P1: Push-to-talk mode -- set release callback
        if app.config.recording_mode == "push_to_talk":
            new_backend.set_on_release(app._stop_dictation)
        log.info(
            "[HOTKEY] Registration OK: %s (backend=%s, alive=%s)",
            format_hotkey_label(hotkey_str),
            _backend_kind_label(new_backend),
            new_backend.is_alive(),
        )
        # Track in the per-spec pool AFTER start() succeeded so a
        self._track_pooled_backend(hotkey_str, new_backend)
        # Install as the shared backend and re-pool any aux backends
        self._shared_backend = new_backend
        self._repool_aux_into_shared()
        return new_backend

    def _track_pooled_backend(self, spec: str, backend: HotkeyBackend) -> None:
        """Record ``backend`` in ``_shared_backend_pool`` under ``spec``.

        Called AFTER a backend's ``start()`` succeeds so the pool only
        ever contains live backends. If an entry already exists for
        ``spec`` (e.g. a stale entry from a backend that's about to be
        stopped), it is overwritten, the caller has just installed a
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
        backend instance, we only want to drop the OLD instance.
        """
        if backend is None:
            return
        for spec, pooled in list(self._shared_backend_pool.items()):
            if pooled is backend:
                del self._shared_backend_pool[spec]
                log.debug(
                    "[HOTKEY] Untracked pooled backend for spec %r (remaining pool size=%d)",
                    spec,
                    len(self._shared_backend_pool),
                )

    def get_active_backend_count(self) -> int:
        """Return the number of DISTINCT native backends currently
        tracked in ``_shared_backend_pool``.

        This is the count of live hotkey subprocesses owned by this
        dispatcher. On the full-pooling path (native
        ``SubprocessHotkeyBackend`` selected) with three DIFFERENT
        specs, this is 1, the dictation backend's subprocess hosts
        the ESC and repaste extra matchers, and the ESC / repaste
        backends are delegated (no subprocess of their own). When
        pooling is unavailable (legacy backend) or specs collide, the
        count reflects the actual subprocess count.
        """
        # Purge any dead entries before reporting so the count reflects
        for spec, pooled in list(self._shared_backend_pool.items()):
            if not pooled.is_alive():
                del self._shared_backend_pool[spec]
        return len(self._shared_backend_pool)

    def _native_of(self, backend: HotkeyBackend | None) -> Any:
        """Return the wrapped ``SubprocessHotkeyBackend`` if ``backend``
        is a ``_NativeBackendAdapter``, else ``None``.

        The adapter (``voice_typer.server.hotkeys.native_adapter``)
        stores the native backend on ``self._native``. We access it
        via ``getattr`` so this method works for ANY backend that
        follows the same adapter pattern (and silently returns
        ``None`` for legacy backends like ``PynputHotkey`` /
        ``WaylandHotkey`` / ``WindowsNativeHotkey`` that don't support
        extra matchers, those fall back to the per-role subprocess
        model).

        This deliberately accesses a private attribute (``_native``)
        on a class owned by another module; the alternative (adding a
        public getter to ``_NativeBackendAdapter``) is out of scope
        for this refactor's owned-file list.
        """
        if backend is None:
            return None
        # BROKEN-3: when the backend is a ``_NativeBackendAdapter`` that
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
            aux_native = self._native_of(aux_backend)
            if aux_native is not None:
                aux_native._delegated = True
            # DEBUG: the caller's per-role "[HOTKEY] ... registered"
            log.debug(
                "[HOTKEY] Pooled %s into shared backend, separate %s backend is delegated (no subprocess)",
                format_hotkey_label(spec),
                role,
            )
            return True
        except Exception:
            # Partial install (e.g. add succeeded, set_role_callback
            with contextlib.suppress(Exception):
                shared_native.remove_extra_matcher(role)
            log.debug(
                "[HOTKEY] Failed to pool %s into shared backend, falling back to per-role subprocess",
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

        Idempotent, safe to call when no aux backends are registered
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
        backend without stopping the shared subprocess.

        Called from every role-teardown path:
        - :meth:`unregister_esc` (settings disable)
        - the ESC / repaste disable branches in :meth:`register`
        - the empty-config and validation-reject branches of
          :meth:`register_repaste`
        - the pool-then-start failure paths in :meth:`register_esc` /
          :meth:`register_repaste`

        The shared backend stays alive, only the role's matcher is
        dropped. Without this, the role keeps firing its callback
        (e.g. ESC keeps cancelling dictation after
        ``esc_cancel_enabled`` is turned off via settings).

        No-op when the role was never pooled (legacy per-role
        subprocess model, or no shared backend) —
        ``SubprocessHotkeyBackend.remove_extra_matcher`` is safe to
        call for an unknown role.
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
        , the legacy backend that actually receives events knows nothing
         about those roles, so the delegated aux backends silently stop
         firing. Re-registering the active aux roles re-runs the pooling
         decision: with ``_shared_native()`` now reporting ``None`` for a
         FALLBACK adapter (see :meth:`_native_of`), each role falls back
         to its own per-role subprocess and keeps working. On recovery
         back to ``NATIVE``, the same re-registration re-pools the roles
         onto the recovered native, avoiding a double-fire (per-role
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
        the user actually sees it. Idempotent, calling it multiple times
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
                    i18n_t("notify.hotkey_dispatcher.wayland_caps_lock"),
                )
        except Exception:
            log.debug("[HOTKEY] _maybe_warn_wayland_caps_lock failed", exc_info=True)

    # PTT safety timeout. Push-to-talk starts recording on key-down
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
            _LIVE_PTT_TIMER_DISPATCHERS.add(self)
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
        # Test-harness registry: no live timer remains on this
        _LIVE_PTT_TIMER_DISPATCHERS.discard(self)

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
            "[HOTKEY] PTT release event missed, auto-stopping recording after %.0fs safety timeout",
            self._PTT_SAFETY_TIMEOUT_SECONDS,
        )
        try:
            with contextlib.suppress(Exception):
                self._app._stop_dictation()
            with contextlib.suppress(Exception):
                self._app.tray.notify_safety(
                    APP_NAME,
                    i18n_t("notify.hotkey_dispatcher.ptt_release_missed"),
                )
        except Exception:
            log.exception("[HOTKEY] PTT safety timeout handler failed")

    def _make_dictation_callback(self):
        """Create a dictation hotkey callback that respects keyboard ownership.

        HOTKEY- the dictation callback previously called
        ``app.toggle_dictation`` directly with NO ownership check. This meant
        that pressing any key during a hotkey capture session (e.g. re-assigning
        the current hotkey, or capturing a new key like Tab) would immediately
        trigger recording: because the OS-level listener sees the same keypress
        the frontend capture handler sees, and there was no guard.

        This mirrors the ESC callback's ownership check ( at line
        ~142): if the frontend is in hotkey capture mode
        (``is_hotkey_capture_active()`` returns True), the dictation callback
        is a no-op. This fixes sub-tasks 2.4 (Race A) and 2.5 entirely.
        """

        def _dictation_callback() -> None:
            # guard against hotkey callbacks firing during
            if getattr(self._app, "_shutting_down", False):
                log.debug("[HOTKEY] dictation ignored, app shutting down")
                return
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[HOTKEY] dictation ignored, frontend hotkey capture active")
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
                log.debug("[HOTKEY] repaste ignored, app shutting down")
                return
            if keyboard_ownership().is_hotkey_capture_active():
                log.debug("[HOTKEY] repaste ignored, frontend hotkey capture active")
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
        differs from the dictation callback, reusing a dictation
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
        self._esc_pending_capture_exit_event.clear()

        try:
            # pass role="esc" so the WaylandHotkey backend (if
            self._esc_backend = create_hotkey_backend("<esc>", role="esc")
            # prefer the event-driven WM_HOTKEY message loop over
            with contextlib.suppress(AttributeError, TypeError):
                self._esc_backend._prefer_message_loop_first = True

            def _esc_callback() -> None:
                # shutdown guard (see _dictation_callback).
                if getattr(self._app, "_shutting_down", False):
                    log.debug("[HOTKEY] ESC ignored, app shutting down")
                    return
                # centralized ownership check.
                if keyboard_ownership().is_hotkey_capture_active():
                    log.info("[HOTKEY] ESC pressed during hotkey capture, waiting for key-up")
                    # ESC-KEYUP-FIX: set the pending flag and install
                    self._esc_pending_capture_exit_event.set()
                    # Route the release callback through the shared
                    shared_native = self._shared_native()
                    if shared_native is not None:
                        with contextlib.suppress(Exception):
                            shared_native.set_role_on_release("esc", self._on_esc_release)
                    if self._esc_backend is not None:
                        self._esc_backend.set_on_release(self._on_esc_release)
                    return
                self._app._cancel_dictation()

            # Stash the callback so :meth:`_repool_aux_into_shared`
            self._esc_callback = _esc_callback
            # Pool ESC into the shared backend (one subprocess for all
            _esc_pooled = self._pool_aux_into_shared("esc", "<esc>", _esc_callback, self._esc_backend)
            try:
                self._esc_backend.start(_esc_callback)
            except Exception:
                # Pool-then-start failure: if the extra matcher was
                if _esc_pooled:
                    self._remove_shared_extra_matcher("esc")
                    self._esc_callback = None
                raise
            self._esc_spec = "<esc>"
            # Track in the per-spec pool AFTER start() succeeded so a
            self._track_pooled_backend("<esc>", self._esc_backend)
            log.info(
                "[HOTKEY] ESC cancel registered%s",
                " (pooled into shared backend)" if _esc_pooled else "",
            )
        except Exception:
            # null the failed backend reference so a subsequent
            if self._esc_backend is not None:
                self._untrack_pooled_backend(self._esc_backend)
                with contextlib.suppress(Exception):
                    self._esc_backend.stop()
            self._esc_backend = None
            self._esc_spec = None
            log.warning("[HOTKEY] ESC cancel hotkey registration failed")
            # surface the failure to the user via the tray's
            with contextlib.suppress(Exception):
                self._app.tray.notify_safety(
                    APP_NAME,
                    i18n_t("notify.hotkey_dispatcher.esc_register_failed"),
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
        pattern and the race window is sub-microsecond, far shorter
        than the human reaction time between two ESC presses.  The
        previous plain-bool implementation had the SAME race window
        plus an additional race against the IPC disconnect worker
        (which ``= False``'d the bool without consulting the listener
        thread).  The Event eliminates the second race; the first is
        tolerable (a second ESC press within the same microsecond
        would re-arm the flag and the next release would fire the
        cancel again, idempotent via ``keyboard_ownership().reset()``).
        """
        #  + M-94 (combined): threading.Event.is_set() / .clear()
        if not self._esc_pending_capture_exit_event.is_set():
            return
        self._esc_pending_capture_exit_event.clear()

        log.info("[HOTKEY] ESC released during hotkey capture, canceling capture")

        # Reset keyboard ownership so subsequent keys
        keyboard_ownership().set_owner("normal", reason="esc released during capture")

        # Keep the legacy alias in sync with the canonical owner so readers
        self._app._esc_cancel_paused = False

        # Push an event so the frontend exits capture mode.
        from voice_typer.server import event_bus

        event_bus.publish({"type": "hotkey_capture_cancel"})

        # Reset the release callback so it doesn't fire again
        if self._esc_backend is not None:
            with contextlib.suppress(Exception):
                self._esc_backend.set_on_release(None)
        # Also clear the shared backend's ESC release callback so
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
            self._remove_shared_extra_matcher("esc")
            # Clear the stashed callback so a later shared-backend swap
            self._esc_callback = None
            log.info("[HOTKEY] ESC cancel hotkey unregistered")

    def register_repaste(self) -> None:
        """Register the repaste hotkey.

        Teardown contract: stopping a previous repaste backend never
        stops the shared dictation backend. When the new
        ``repaste_hotkey`` is empty (config cleared / rejected), the
        pooled extra matcher is removed from the shared backend so the
        old combo stops firing. Replacing a live repaste with a new
        spec re-uses the role-keyed ``add_extra_matcher`` path (no
        remove needed).
        """
        if self._repaste_backend:
            self._untrack_pooled_backend(self._repaste_backend)
            with contextlib.suppress(Exception):
                self._repaste_backend.stop()
            self._repaste_backend = None
            self._repaste_spec = None
        if not self._app.config.repaste_hotkey:
            # Empty config (cleared in Settings, or set_config wrote
            self._remove_shared_extra_matcher("repaste")
            self._repaste_callback = None
            return
        # validate the configured repaste hotkey BEFORE
        from voice_typer.server.config_validators import _validate_hotkey

        validation_error = _validate_hotkey(self._app.config.repaste_hotkey)
        if validation_error is not None:
            log.warning(
                "[HOTKEY] configured repaste_hotkey %r rejected (%s), "
                "disabling repaste (not resetting to <caps_lock> to avoid "
                "conflict with the main dictation hotkey)",
                self._app.config.repaste_hotkey,
                validation_error,
            )
            self._app.config.repaste_hotkey = ""
            # Same teardown as the empty-config branch: the previous
            self._remove_shared_extra_matcher("repaste")
            self._repaste_callback = None
            return
        try:
            # pass role="repaste" so the WaylandHotkey backend
            self._repaste_backend = create_hotkey_backend(self._app.config.repaste_hotkey, role="repaste")
            # same WM_HOTKEY-preference flag as the ESC backend
            with contextlib.suppress(AttributeError, TypeError):
                self._repaste_backend._prefer_message_loop_first = True
            _repaste_cb = self._make_repaste_callback()
            # Stash the callback so :meth:`_repool_aux_into_shared`
            self._repaste_callback = _repaste_cb
            # Pool repaste into the shared backend (one subprocess
            _repaste_pooled = self._pool_aux_into_shared(
                "repaste",
                self._app.config.repaste_hotkey,
                _repaste_cb,
                self._repaste_backend,
            )
            try:
                self._repaste_backend.start(_repaste_cb)
            except Exception:
                # Pool-then-start failure: remove the matcher already
                if _repaste_pooled:
                    self._remove_shared_extra_matcher("repaste")
                    self._repaste_callback = None
                raise
            self._repaste_spec = self._app.config.repaste_hotkey
            # Track in the per-spec pool AFTER start() succeeded
            self._track_pooled_backend(self._app.config.repaste_hotkey, self._repaste_backend)
            log.info(
                "[HOTKEY] Repaste registered: %s%s",
                format_hotkey_label(self._app.config.repaste_hotkey),
                " (pooled into shared backend)" if _repaste_pooled else "",
            )
        except Exception:
            # null the failed backend reference so a
            if self._repaste_backend is not None:
                self._untrack_pooled_backend(self._repaste_backend)
                with contextlib.suppress(Exception):
                    self._repaste_backend.stop()
            self._repaste_backend = None
            self._repaste_spec = None
            log.warning("[HOTKEY] Repaste hotkey registration failed")
            # surface the failure to the user via the tray's
            with contextlib.suppress(Exception):
                self._app.tray.notify_safety(
                    APP_NAME,
                    i18n_t("notify.hotkey_dispatcher.repaste_register_failed"),
                )

    def restart(self, hotkey: str) -> None:
        """Re-register the global hotkey after settings change.

        validate hotkey before mutating config.

        stop the OLD backend BEFORE starting the NEW one.
        Previously ``register()`` brought up the new backend first and
        the old backend was only stopped AFTER ``register()`` returned
        success, leaving a window where BOTH backends were running on
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
                    i18n_t(
                        "notify.hotkey_dispatcher.invalid_hotkey",
                        hotkey=hotkey,
                        validation_error=validation_error,
                    ),
                )
            return
        # capture the OLD hotkey spec BEFORE mutating
        old_hotkey_str = app.config.hotkey
        old_backend = self._hotkey_backend

        app.config.hotkey = hotkey
        if not app.config.save():
            log.warning("[HOTKEY] config.save() returned False, hotkey change may not persist")
            app.tray.notify(
                APP_NAME,
                i18n_t("notify.hotkey_dispatcher.save_failed"),
            )

        # stop the OLD backend BEFORE calling register()
        if old_backend is not None:
            # Untrack from the per-spec pool BEFORE stopping so the
            self._untrack_pooled_backend(old_backend)
            try:
                old_backend.stop()
            except Exception:
                log.exception("[HOTKEY] Failed to stop previous backend before restart")
            self._hotkey_backend = None

        # ``register()`` ALSO calls ``register_esc()`` +
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
            with contextlib.suppress(Exception):
                app.tray.notify(
                    APP_NAME,
                    i18n_t("notify.hotkey_dispatcher.register_failed", hotkey=hotkey),
                )

        if register_ok:
            # new backend installed, old backend already stopped above.
            pass
        else:
            # registration failed. The OLD backend was already stopped,
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
                        "[HOTKEY] Failed to restore previous backend (hotkey=%r), "
                        "user is left without a dictation hotkey",
                        old_hotkey_str,
                    )
                    with contextlib.suppress(Exception):
                        app.tray.notify(
                            APP_NAME,
                            i18n_t(
                                "notify.hotkey_dispatcher.restore_failed",
                                hotkey=old_hotkey_str,
                            ),
                        )
            else:
                # No OLD backend to restore, register() failure leaves
                log.warning("[HOTKEY] restart did not install a new backend, no previous backend to restore")

        app.tray.set_hotkey(app.config.hotkey)

    def stop_all(self) -> None:
        """Stop all hotkey backends (called during app shutdown).

        each backend's ``stop()`` runs in a worker thread under
        a hard 3s budget shared across all three backends. Previously
        ``stop_all`` called ``backend.stop()`` sequentially with no
        timeout, a single hung native backend (Win32
        ``UnregisterHotKey`` + listener-thread join, Wayland
        ``wl_display`` teardown, pynput listener join) could block the
        shutdown sequence for up to ~15s (3 backends × 5s join each).
        Backends that miss the 3s budget are leaked (their worker
        thread keeps running) and a warning is logged; every native
        listener thread is a daemon, so process exit still terminates
        it. ``stop()`` failures inside the budget are swallowed (logged
        at debug) so a poisoned backend doesn't abort the rest of
        shutdown, same contract as before.

        Implementation note: we do NOT use the ``with`` block on the
        ``ThreadPoolExecutor`` because ``__exit__`` calls
        ``shutdown(wait=True)`` which would block until every submitted
        future completes, defeating the 3s budget. Instead we call
        ``shutdown(wait=False, cancel_futures=True)`` so already-running
        workers are left to finish (or hang) in the background and the
        method returns as soon as ``concurrent.futures.wait`` does.
        """
        backend_attrs = ("_hotkey_backend", "_esc_backend", "_repaste_backend")
        live_attrs = [a for a in backend_attrs if getattr(self, a) is not None]
        if live_attrs:
            # NOT using ``with``: see docstring: ``__exit__`` would
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=len(live_attrs))
            try:
                futures = {pool.submit(self._stop_one_backend, a): a for a in live_attrs}
                done, not_done = concurrent.futures.wait(futures, timeout=3.0)
                for fut in not_done:
                    log.warning(
                        "[HOTKEY] %s did not stop within 3s budget, proceeding anyway",
                        futures[fut],
                    )
                # Surface any exception raised by a completed stop so
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
                # drops any not-yet-started submissions (defensive —
                pool.shutdown(wait=False, cancel_futures=True)
        # clear the spec trackers so a post-shutdown register()
        self._esc_spec = None
        self._repaste_spec = None
        # Clear the stashed ESC / repaste callbacks and the shared
        self._esc_callback = None
        self._repaste_callback = None
        self._shared_backend = None
        # of the three role attributes, defensive).
        self._shared_backend_pool.clear()
        # cancel any armed PTT safety timer so a hot-restart
        self._cancel_ptt_safety_timer()

    def _stop_one_backend(self, backend_attr: str) -> None:
        """stop a single backend and clear its attribute.

        Runs inside a ``concurrent.futures.ThreadPoolExecutor`` worker
        so a hung ``stop()`` cannot block the 3s budget in
        :meth:`stop_all`. ``stop()`` failures are swallowed (logged at
        debug) so a poisoned backend doesn't abort the rest of shutdown.
        The attribute is cleared UNCONDITIONALLY after ``stop()`` returns
        or raises, the post-stop code paths (and the test suite) treat
        ``None`` as "no backend", so leaving a partially-stopped backend
        in place would be worse than a clean None.
        """
        backend = getattr(self, backend_attr)
        if backend is None:
            return
        # Untrack from the per-spec pool BEFORE stopping so the count
        self._untrack_pooled_backend(backend)
        try:
            backend.stop()
        except Exception:
            log.debug("[HOTKEY] Failed to stop %s", backend_attr, exc_info=True)
        setattr(self, backend_attr, None)
