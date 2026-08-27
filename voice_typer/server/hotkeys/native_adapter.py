"""Adapter that wraps a native ``SubprocessHotkeyBackend`` to satisfy the
``HotkeyBackend`` interface expected by ``HotkeyDispatcher``.

Implements the  runtime fallback chain (native → legacy) and the
macOS Accessibility permission onboarding.  Split out from the
original ``hotkeys.py`` god-file in Phase 4.5 ().
"""

# ruff: noqa: E501 -- the state-transition ASCII table in the
# ``NativeHotkeyAdapter`` docstring below uses fixed-width box-drawing
# columns that intentionally exceed the 120-char line limit.

import contextlib
import threading
from collections.abc import Callable

from voice_typer.server import hotkeys as _hotkeys_pkg
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_wayland_session

from .base import HotkeyBackend, log
from .pynput_backend import PynputHotkey
from .wayland import WaylandHotkey
from .windows_native import WindowsNativeHotkey


# See pynput_backend.py for the rationale.
def is_windows() -> bool:
    return _hotkeys_pkg.is_windows()


def is_linux() -> bool:
    return _hotkeys_pkg.is_linux()


class _NativeBackendAdapter(HotkeyBackend):
    """Adapter that wraps a ``SubprocessHotkeyBackend`` to satisfy the
        ``HotkeyBackend`` interface expected by ``HotkeyDispatcher``.

        The wrapped native backend now inherits from
        ``HotkeyBackend`` directly (the historical "separate base class
        to avoid an import cycle" split is gone — the import direction is
        acyclic), so interface conformance comes for free. The adapter
        still earns its keep through the semantics the plain interface
        has no notion of: the native → legacy runtime fallback chain, the
        macOS Accessibility permission onboarding, and tray-notification
        propagation.

    (runtime fallback chain): when the native backend permanently
        fails (5 retries exhausted), the adapter transparently swaps to a
        legacy backend (``PynputHotkey`` / ``WindowsNativeHotkey`` /
        ``WaylandHotkey``) with the same callbacks. A 5-minute retry timer
        periodically attempts to swap back to the native backend; on
        success, the adapter swaps back and notifies the user.

    (macOS Accessibility onboarding): when the native backend
        emits an ``ERROR:`` line classified as a permission issue, the
        adapter shows a tray notification and (on macOS) opens System
        Settings → Accessibility. A 60-second permission retry timer
        polls for the permission being granted and, on success, restarts
        the native backend.

        State machine (5 states, 3 callback slots, 2 async timers):

            States: NATIVE, FALLING_BACK, FALLBACK, FAILED, STOPPED
            Callback slots (set on the native backend in __init__):
                - native._on_error_callback            -> _on_native_error
                - native._on_permanent_failure_callback-> _on_native_permanent_failure
                - _on_release_callback                 -> set via set_on_release
            Async timers:
                - 300s native-retry timer  (_native_retry_timer, this class)
                - 60s  permission-retry    (voice_typer.server.permissions,
                                             max 5 attempts)

            Quick diagram (omits self-loops, FAILED->STOPPED, and the
            permission-grant recovery path; see the full table below):

                NATIVE → FALLING_BACK → FALLBACK → (NATIVE on recovery, or FAILED)
                Any state → STOPPED on stop()

        State Transition Table:
        ┌──────────────┬──────────────────────────────────────┬──────────────────┬───────────────────────────────────────┐
        │ From         │ Event                                │ To               │ Side Effects                          │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ (init)       │ __init__                             │ NATIVE           │ Wire _on_error_callback &             │
        │              │                                      │                  │ _on_permanent_failure_callback        │
        │              │                                      │                  │ on native.                            │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ NATIVE       │ start() succeeds                     │ NATIVE           │ (self-loop; confirms state            │
        │              │                                      │                  │ under swap_lock)                      │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ NATIVE       │ start() raises OR                    │ FALLING_BACK     │ Via _swap_to_legacy(): fire           │
        │              │ _on_native_permanent_failure         │                  │ _on_release_callback, create &        │
        │              │ (native's 5 retries exhausted)       │                  │ start legacy backend.                 │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ FALLING_BACK │ legacy backend starts                │ FALLBACK         │ Assign _legacy, show fallback         │
        │              │ successfully                         │                  │ notification, schedule 300s           │
        │              │                                      │                  │ native retry timer.                   │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ FALLING_BACK │ legacy create/start raises           │ FAILED           │ Log error, show failure               │
        │              │                                      │                  │ notification.                         │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ FALLING_BACK │ stop() called during swap            │ STOPPED          │ Stop the just-created legacy          │
        │              │                                      │                  │ backend, return.                      │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ FALLBACK     │ 300s native retry timer fires,       │ NATIVE           │ Stop legacy, stop+start native,       │
        │              │ native restart succeeds              │                  │ set _on_release, show recovery        │
        │              │                                      │                  │ notification, reset perm flag.        │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ FALLBACK     │ 300s timer fires, native fails,      │ FALLBACK         │ (self-loop) Stop old legacy,          │
        │              │ legacy restarts                      │                  │ restart native (fails), new           │
        │              │                                      │                  │ legacy, schedule 300s retry.          │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ FALLBACK     │ 300s timer fires, both native        │ FAILED           │ Log "both backends failed",           │
        │              │ & legacy restart fail                │                  │ show failure notification.            │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ NATIVE,      │ 60s permission retry timer           │ NATIVE           │ Stop+start native, set                │
        │ FALLBACK, or │ fires, native restart succeeds       │                  │ _on_release, reset perm flag.         │
        │ FAILED       │                                      │                  │ NOTE: legacy NOT stopped when         │
        │              │                                      │                  │ transitioning from FALLBACK.          │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ NATIVE,      │ stop() called                        │ STOPPED          │ Cancel 300s timer & 60s perm          │
        │ FALLING_BACK,│                                      │                  │ retry, reset flag, stop legacy        │
        │ FALLBACK, or │                                      │                  │ & native. Idempotent no-op if         │
        │ FAILED       │                                      │                  │ already STOPPED.                      │
        ├──────────────┼──────────────────────────────────────┼──────────────────┼───────────────────────────────────────┤
        │ STOPPED      │ (none - terminal state)              │ STOPPED          │ No transitions out; stop() is         │
        │              │                                      │                  │ a no-op.                              │
        └──────────────┴──────────────────────────────────────┴──────────────────┴───────────────────────────────────────┘

        Key: STOPPED is terminal. FAILED is terminal except for stop() and the
        60s permission-grant recovery path (which restarts native directly).
        FALLING_BACK is a transient state held only while _swap_to_legacy() is
        between acquiring the swap_lock to set the state and re-acquiring it to
        install the legacy backend.
    """

    # State constants
    _STATE_NATIVE = "NATIVE"
    _STATE_FALLING_BACK = "FALLING_BACK"
    _STATE_FALLBACK = "FALLBACK"
    _STATE_FAILED = "FAILED"
    _STATE_STOPPED = "STOPPED"

    # retry interval for swapping back to native (5 minutes)
    _NATIVE_RETRY_INTERVAL_SECONDS = 300.0

    # declare the tray reference as a typed class attribute. The
    # adapter doesn't construct the tray — it's propagated from
    # ``hotkey_dispatcher.py`` (``adapter._tray = app.tray`` after
    # construction) and forwarded to the active legacy backend via
    # :meth:`HotkeyBackend.set_tray`. Typed here so the propagation in
    # this file doesn't need ``# type: ignore[attr-defined]`` markers.
    _tray: object | None = None

    def __init__(self, native_backend, role: str | None = None):
        # Don't call super().__init__ because we delegate hotkey_str
        # to the wrapped backend.
        # Store ``role`` so ``_create_legacy_backend`` can pass
        # it to ``WaylandHotkey`` when the native backend permanently
        # fails and the adapter swaps to legacy.
        self._role: str | None = role
        self._native = native_backend
        self.hotkey_str = native_backend.hotkey_str
        self._on_release_callback: Callable[[], None] | None = None
        self._callback: Callable[[], None] | None = None
        self._legacy: HotkeyBackend | None = None
        # state-change hook for the dispatcher. The
        # ``HotkeyDispatcher`` sets this to its
        # ``_handle_shared_native_state_changed`` method so the pooled
        # ESC / repaste extra matchers can be re-synced when the native
        # permanently fails (swap to legacy) or recovers (swap back).
        # ``None`` when no dispatcher is wired (e.g. in tests).
        self._on_state_change_callback: Callable[[str], None] | None = None
        self._state = self._STATE_NATIVE
        self._swap_lock = threading.Lock()
        self._native_retry_timer: threading.Timer | None = None
        self._permission_notification_shown = False
        # Wire up the native backend's error and permanent-failure
        # callbacks so we know when to (a) show a permission prompt
        # and (b) swap to the legacy backend.
        # use the public setters on ``SubprocessHotkeyBackend``
        # (``set_error_callback`` / ``set_permanent_failure_callback``
        # / ``set_warn_callback``) instead of reaching into the private
        # ``_on_*_callback`` attributes directly. The setters live on
        # the public API surface, so no ``# type: ignore[assignment]``
        # is needed and the callbacks remain an internal impl detail
        # of the native backend.
        native_backend.set_error_callback(self._on_native_error)
        native_backend.set_permanent_failure_callback(self._on_native_permanent_failure)
        # wire WARN callback
        native_backend.set_warn_callback(self._on_native_warn)
        # Wire legacy attribute-style callback slots. The
        # ``SubprocessHotkeyBackend`` historically used attribute
        # assignment (``backend._on_error_callback = cb``); the
        # refactor to ``set_error_callback`` is the canonical path,
        # but tests in ``tests/test_runtime_fallback.py`` (and any
        # external code that introspects the attribute) still read
        # the attribute. Mirroring the assignment here keeps both
        # the method-based setter and the attribute-based reader in
        # sync — defense-in-depth for the test surface.
        try:
            native_backend._on_error_callback = self._on_native_error
            native_backend._on_permanent_failure_callback = self._on_native_permanent_failure
        except (AttributeError, TypeError):
            # Some backends may disallow attribute assignment
            # (e.g. frozen dataclass). The method-based setter is
            # the authoritative wire — the attribute mirror is
            # best-effort for test introspection.
            pass

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        try:
            self._native.start(callback)
            with self._swap_lock:
                if self._state != self._STATE_STOPPED:
                    self._state = self._STATE_NATIVE
        except Exception as exc:
            log.warning("[HOTKEY] Native backend failed to start: %s — trying legacy", exc)
            self._swap_to_legacy()

    def set_on_release(self, callback: Callable[[], None] | None) -> None:
        self._on_release_callback = callback
        self._native.set_on_release(callback)
        if self._legacy is not None:
            self._legacy.set_on_release(callback)

    def set_toggle_on_keyup(self, value: bool) -> None:
        self._toggle_on_keyup = value
        with contextlib.suppress(AttributeError, TypeError):
            self._native.set_toggle_on_keyup(value)
        if self._legacy is not None:
            with contextlib.suppress(AttributeError, TypeError):
                self._legacy.set_toggle_on_keyup(value)

    def stop(self) -> None:
        with self._swap_lock:
            if self._state == self._STATE_STOPPED:
                return
            self._state = self._STATE_STOPPED
            # Cancel the native retry timer
            if self._native_retry_timer is not None:
                self._native_retry_timer.cancel()
                self._native_retry_timer = None
            # Cancel any pending permission retry
            try:
                from voice_typer.server.permissions import cancel_permission_retry

                cancel_permission_retry()
            except (ImportError, AttributeError):
                # ``cancel_permission_retry`` was added in a later
                # version of ``permissions.py``; on older checkouts
                # the import fails. Previously a broad
                # ``except Exception: pass``.
                pass
            # Reset the permission notification flag so a restart
            # can show it again.
            self._permission_notification_shown = False
            # Stop both backends — the inactive one is a no-op.
            # Stop the legacy first (it's the active one if swapping),
            # then the native.
            if self._legacy is not None:
                try:
                    self._legacy.stop()
                except Exception:
                    log.debug("[HOTKEY] Failed to stop legacy backend", exc_info=True)
                self._legacy = None
            try:
                self._native.stop()
            except Exception:
                log.debug("[HOTKEY] Failed to stop native backend", exc_info=True)

    def is_alive(self) -> bool:
        with self._swap_lock:
            state = self._state
        if state == self._STATE_NATIVE:
            return self._native.is_alive()
        if state == self._STATE_FALLBACK:
            return self._legacy is not None and self._legacy.is_alive()
        return False  # FAILED or STOPPED

    def diagnose(self) -> str:
        with self._swap_lock:
            state = self._state
        active = "native" if state == self._STATE_NATIVE else "legacy" if state == self._STATE_FALLBACK else "none"
        native_diag = self._native.diagnose()
        legacy_diag = self._legacy.diagnose() if self._legacy else "not started"
        return (
            f"_NativeBackendAdapter (state={state}, active={active})\n"
            f"Native backend:\n{native_diag}\n"
            f"Legacy backend:\n{legacy_diag}"
        )

    # permission error handling ────────────────────────────────

    def _on_native_warn(self, warn_message: str) -> None:
        """non-fatal degradation (e.g. SKIP_ACCESSIBILITY)."""
        log.warning("[HOTKEY] Native backend WARN: %s", warn_message)
        tray = self._get_tray()
        if tray is not None:
            try:
                short = warn_message if len(warn_message) <= 160 else warn_message[:157] + "..."
                tray.notify(f"{APP_NAME}: Native hotkey warning", short)
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for WARN")

    def _on_native_error(self, error_message: str) -> None:
        """Called by the native backend when it emits an ERROR: line.

        If the error is a permission issue (Accessibility on macOS,
        /dev/input on Linux), show a tray notification and open the OS
        permission UI. Other errors are handled by the startup fallback
        chain — no notification needed.
        """
        try:
            from voice_typer.server.permissions import (
                permission_error_is_permission_denied,
                request_keyboard_permission,
                show_permission_notification,
            )
        except ImportError:
            log.debug("[HOTKEY] permissions module not available")
            return

        if not permission_error_is_permission_denied(error_message):
            return

        # Show the notification at most once per session
        if self._permission_notification_shown:
            return
        self._permission_notification_shown = True

        # Get the tray from the app (best-effort — the adapter may be
        # used in tests without an app)
        tray = self._get_tray()
        show_permission_notification(tray, error_message)

        # Open the OS permission UI (macOS System Settings / Linux pkexec)
        # and schedule a retry timer that restarts the native backend
        # once permission is granted.
        request_keyboard_permission(on_granted=self._on_permission_granted)

    def _on_permission_granted(self) -> None:
        """Called when the permission retry timer detects the permission
        has been granted. Attempts to restart the native backend.

        Stops the legacy backend BEFORE restarting native.
        Previously the legacy backend was left running alongside the
        native backend after a permission-grant recovery — both
        backends would fire the same callback on the same keypress
        (double-toggle, double-ESC-cancel, double-repaste) until the
        next ``_retry_native`` cycle (~5 minutes later) cleaned it up.
        """
        log.info("[HOTKEY] Permission granted — restarting native backend")
        with contextlib.suppress(Exception):
            self._native.stop()
        # Stop the legacy backend BEFORE restarting native so
        # both backends aren't simultaneously alive (double-fire on
        # the same keypress). Snapshot under the swap_lock to avoid
        # racing with ``_swap_to_legacy`` / ``_retry_native`` which
        # also touch ``self._legacy``.
        with self._swap_lock:
            legacy_to_stop = self._legacy
            self._legacy = None
        if legacy_to_stop is not None:
            try:
                legacy_to_stop.stop()
            except Exception:
                log.debug(
                    "[HOTKEY] Failed to stop legacy backend during permission-grant recovery",
                    exc_info=True,
                )
        try:
            # narrow ``self._callback`` (typed
            # ``Callable[[], None] | None``) to the non-None local
            # ``cb`` so :meth:`HotkeyBackend.start`'s non-optional
            # ``callback`` parameter is satisfied without a
            # ``# type: ignore[arg-type]`` marker. ``_callback`` is
            # populated by :meth:`start` (above), which always runs
            # before the permission-retry timer fires — but the
            # static type can't see that, so we narrow here.
            cb = self._callback
            if cb is None:
                log.warning("[HOTKEY] Permission granted but no callback registered — skipping native restart")
                return
            self._native.start(cb)
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state != self._STATE_STOPPED:
                        self._state = self._STATE_NATIVE
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info("[HOTKEY] Native backend restarted after permission grant")
                self._permission_notification_shown = False
                # same re-pool signal as native recovery
                # (see ``_retry_native``).
                self._notify_state_change(self._STATE_NATIVE)
                return
        except Exception:
            log.exception("[HOTKEY] Native restart after permission grant failed")

    def _get_tray(self):
        """Best-effort: get the app's tray object for notifications.

        The adapter doesn't hold a reference to the app (to avoid a
        circular import). We look it up via the HotkeyDispatcher's
        ``_app`` attribute if the adapter was created by one. Returns
        None if no tray is available (e.g. in tests).
        """
        # The HotkeyDispatcher stores itself on the adapter? No — but
        # the adapter is stored on the dispatcher. We can't easily go
        # back up. For now, return None — the notification is still
        # logged, and the HotkeyDispatcher can override this by setting
        # ``adapter._tray = app.tray`` after construction.
        return getattr(self, "_tray", None)

    def _notify_state_change(self, state: str) -> None:
        """notify the dispatcher (if wired) that the active
        backend changed (native ↔ legacy swap). Best-effort — a
        misbehaving consumer must not break the swap state machine."""
        cb = getattr(self, "_on_state_change_callback", None)
        if cb is None:
            return
        try:
            cb(state)
        except Exception:
            log.debug("[HOTKEY] state-change callback (%s) raised", state, exc_info=True)

    # runtime fallback chain ───────────────────────────────────

    def _on_native_permanent_failure(self) -> None:
        """Called when the native backend exhausts its 5 retries."""
        log.warning("[HOTKEY] Native backend permanently failed — swapping to legacy")
        self._swap_to_legacy()

    def _swap_to_legacy(self) -> None:
        """Replace the native backend with a legacy one.

        Idempotent: if we've already swapped or stopped, do nothing.
        If the legacy backend also fails, set state to FAILED and show
        a tray notification.
        """
        with self._swap_lock:
            if self._state in (self._STATE_FALLBACK, self._STATE_FAILED, self._STATE_STOPPED):
                return  # Already swapped, given up, or stopped
            self._state = self._STATE_FALLING_BACK

        # If a recording is in progress (push-to-talk), fire the release
        # callback so it doesn't get stuck. The native backend (which
        # detected the press) is dead and can't detect the release.
        if self._on_release_callback is not None:
            try:
                self._on_release_callback()
            except Exception:
                log.exception("[HOTKEY] on_release during swap raised")

        try:
            legacy = self._create_legacy_backend()
            # narrow ``self._callback`` to non-None (see
            # ``_on_permission_granted`` for the rationale).
            cb = self._callback
            if cb is None:
                log.warning("[HOTKEY] Cannot swap to legacy — no callback registered")
                with contextlib.suppress(Exception):
                    legacy.stop()
                return
            legacy.start(cb)
            if self._on_release_callback is not None:
                legacy.set_on_release(self._on_release_callback)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    # stop() was called during the swap — clean up
                    with contextlib.suppress(Exception):
                        legacy.stop()
                    return
                self._legacy = legacy
                # propagate _tray to legacy backend via the
                # public ``set_tray`` API () instead of reaching
                # into the private ``_tray`` attribute directly.
                with contextlib.suppress(AttributeError, TypeError):
                    self._legacy.set_tray(self._tray)
                self._state = self._STATE_FALLBACK
            log.info("[HOTKEY] Successfully swapped to legacy backend")
            self._show_fallback_notification()
            # let the dispatcher un-pool the aux roles so they
            # don't stay delegated onto the now-dead native subprocess.
            self._notify_state_change(self._STATE_FALLBACK)
            # Schedule a periodic retry of the native backend
            self._schedule_native_retry()
        except Exception as exc:
            log.error("[HOTKEY] Legacy backend also failed: %s — giving up", exc)
            with self._swap_lock:
                self._state = self._STATE_FAILED
            self._show_failure_notification(exc)

    def _create_legacy_backend(self) -> HotkeyBackend:
        """Instantiate the appropriate legacy backend for this platform.

        Mirrors the fallback logic in ``create_hotkey_backend()`` for
        when the native binary is missing.
        """
        if is_windows():
            return WindowsNativeHotkey(self.hotkey_str)
        if is_linux() and is_wayland_session():
            # Pass ``self._role`` so the legacy fallback on a
            # Wayland session doesn't collide with other backends.
            return WaylandHotkey(self.hotkey_str, role=self._role)
        return PynputHotkey(self.hotkey_str)

    def _schedule_native_retry(self) -> None:
        """Schedule a periodic attempt to swap back to the native backend."""
        with self._swap_lock:
            if self._state != self._STATE_FALLBACK:
                return  # Already recovered, failed, or stopped

        # Cancel any existing timer
        if self._native_retry_timer is not None:
            self._native_retry_timer.cancel()

        timer = threading.Timer(
            self._NATIVE_RETRY_INTERVAL_SECONDS,
            self._retry_native,
        )
        timer.daemon = True
        timer.start()
        self._native_retry_timer = timer

    def _retry_native(self) -> None:
        """Attempt to swap back to the native backend.

                If the native backend restarts successfully, swap back and notify
                the user. If it fails, stay on legacy and schedule another retry.

        keep the stopped legacy backend as a "warm spare" while
                attempting the native restart. Previously ``_retry_native`` nulled
                ``self._legacy`` BEFORE attempting the native restart, so if the
                native failed to come back up the code had to construct a brand
                new legacy backend (``_create_legacy_backend()``) — during that
                construction window the hotkey was completely dead. By keeping
                the stopped legacy instance around, we can restart it directly
                (``legacy.start(...)``) on native failure, shrinking the dead
                window from "construct + start" to just "start". The warm spare
                is dropped (``self._legacy = None``) only after the native
                restart succeeds.
        """
        with self._swap_lock:
            if self._state != self._STATE_FALLBACK:
                return  # Already recovered, failed, or stopped

        log.info("[HOTKEY] Retrying native backend...")
        # snapshot the legacy reference and stop it (frees any
        # RegisterHotKey slot the native needs) WITHOUT nulling
        # ``self._legacy`` — keep it as a warm spare so we can restart
        # it quickly if the native restart fails.
        warm_spare = self._legacy
        try:
            if warm_spare is not None:
                with contextlib.suppress(Exception):
                    warm_spare.stop()
            self._native.stop()
            # narrow ``self._callback`` to non-None (see
            # ``_on_permission_granted`` for the rationale).
            cb = self._callback
            if cb is None:
                log.warning("[HOTKEY] Native retry aborted — no callback registered")
                return
            self._native.start(cb)
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state == self._STATE_STOPPED:
                        # stop() was called during retry — clean up
                        self._native.stop()
                        return
                    self._state = self._STATE_NATIVE
                    # Native succeeded — drop the warm spare; we don't
                    # need two backends alive.
                    self._legacy = None
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info("[HOTKEY] Native backend recovered — swapped back from legacy")
                self._show_recovery_notification()
                self._permission_notification_shown = False
                # let the dispatcher re-pool the aux roles
                # (the recovered native now matches the extra matchers
                # again — per-role subprocesses must be stopped to
                # avoid double-fire).
                self._notify_state_change(self._STATE_NATIVE)
                return
        except Exception as exc:
            log.warning("[HOTKEY] Native retry failed: %s — staying on legacy", exc)

        # Retry failed — restart the warm spare (or create a new legacy
        # backend if we never had one) and schedule another retry.
        # prefer restarting the existing warm_spare instance —
        # it's already constructed and its hotkey_str / state match the
        # adapter, so the restart is faster than constructing a new one.
        try:
            legacy = warm_spare if warm_spare is not None else self._create_legacy_backend()
            # ``cb`` is the narrowed callback from above — if
            # ``self._callback`` was None we already returned.
            if cb is None:
                log.warning("[HOTKEY] Cannot restart legacy — no callback registered")
                return
            legacy.start(cb)
            if self._on_release_callback is not None:
                legacy.set_on_release(self._on_release_callback)
            # (cont.): propagate _tray via the public
            # ``set_tray`` API () instead of the private attr.
            with contextlib.suppress(AttributeError, TypeError):
                legacy.set_tray(self._tray)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    legacy.stop()
                    return
                self._state = self._STATE_FALLBACK
                self._legacy = legacy
            self._schedule_native_retry()
        except Exception:
            with self._swap_lock:
                self._state = self._STATE_FAILED
            log.error("[HOTKEY] Both native and legacy backends failed — hotkey dead")
            self._show_failure_notification(None)

    # ── Notifications ───────────────────────────────────────────────────

    def _show_fallback_notification(self) -> None:
        """Notify the user that the hotkey is running in compatibility mode."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    f"{APP_NAME}: Compatibility mode",
                    "Hotkey is running in compatibility mode (reduced features). "
                    "Restart the app for full functionality.",
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for fallback notification")

    def _show_recovery_notification(self) -> None:
        """Notify the user that the native backend has recovered."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    f"{APP_NAME}: Full mode restored",
                    "Hotkey is running in full mode.",
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for recovery notification")

    def _show_failure_notification(self, exc: Exception | None) -> None:
        """Notify the user that the hotkey is not working at all."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    f"{APP_NAME}: Hotkey error",
                    "Hotkey is not working. Click to troubleshoot.",
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for failure notification")
