"""Adapter that wraps a native ``SubprocessHotkeyBackend`` to satisfy the
``HotkeyBackend`` interface expected by ``HotkeyDispatcher``.

Implements the GAP-4 runtime fallback chain (native → legacy) and the
GAP-2 macOS Accessibility permission onboarding.  Split out from the
original ``hotkeys.py`` god-file in Phase 4.5 (ARCH-045).
"""

import contextlib
import os
import threading
from collections.abc import Callable

from voice_typer.server import hotkeys as _hotkeys_pkg
from voice_typer.server.branding import APP_NAME

from .base import HotkeyBackend, log
from .pynput_backend import PynputHotkey
from .wayland import WaylandHotkey
from .windows_native import WindowsNativeHotkey

# See pynput_backend.py for the rationale.
is_windows = lambda: _hotkeys_pkg.is_windows()
is_linux = lambda: _hotkeys_pkg.is_linux()


class _NativeBackendAdapter(HotkeyBackend):
    """Adapter that wraps a ``SubprocessHotkeyBackend`` to satisfy the
    ``HotkeyBackend`` interface expected by ``HotkeyDispatcher``.

    The native backends in ``native_hotkeys.py`` don't inherit from
    ``HotkeyBackend`` (they use a separate base class to avoid an import
    cycle). This adapter bridges the two.

    GAP-4 (runtime fallback chain): when the native backend permanently
    fails (5 retries exhausted), the adapter transparently swaps to a
    legacy backend (``PynputHotkey`` / ``WindowsNativeHotkey`` /
    ``WaylandHotkey``) with the same callbacks. A 5-minute retry timer
    periodically attempts to swap back to the native backend; on
    success, the adapter swaps back and notifies the user.

    GAP-2 (macOS Accessibility onboarding): when the native backend
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

    # GAP-4: retry interval for swapping back to native (5 minutes)
    _NATIVE_RETRY_INTERVAL_SECONDS = 300.0

    def __init__(self, native_backend):
        # Don't call super().__init__ because we delegate hotkey_str
        # to the wrapped backend.
        self._native = native_backend
        self.hotkey_str = native_backend.hotkey_str
        self._on_release_callback: Callable[[], None] | None = None
        self._callback: Callable[[], None] | None = None
        self._legacy: HotkeyBackend | None = None
        self._state = self._STATE_NATIVE
        self._swap_lock = threading.Lock()
        self._native_retry_timer: threading.Timer | None = None
        self._permission_notification_shown = False
        # Wire up the native backend's error and permanent-failure
        # callbacks so we know when to (a) show a permission prompt
        # and (b) swap to the legacy backend.
        native_backend._on_error_callback = self._on_native_error  # type: ignore[assignment]
        native_backend._on_permanent_failure_callback = (  # type: ignore[assignment]
            self._on_native_permanent_failure
        )

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
            except Exception:
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

    # ── GAP-2: permission error handling ────────────────────────────────

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
        """
        log.info("[HOTKEY] Permission granted — restarting native backend")
        with contextlib.suppress(Exception):
            self._native.stop()
        try:
            self._native.start(self._callback)  # type: ignore[arg-type]
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state != self._STATE_STOPPED:
                        self._state = self._STATE_NATIVE
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info("[HOTKEY] Native backend restarted after permission grant")
                self._permission_notification_shown = False
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

    # ── GAP-4: runtime fallback chain ───────────────────────────────────

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
            legacy.start(self._callback)  # type: ignore[arg-type]
            if self._on_release_callback is not None:
                legacy.set_on_release(self._on_release_callback)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    # stop() was called during the swap — clean up
                    with contextlib.suppress(Exception):
                        legacy.stop()
                    return
                self._legacy = legacy
                self._state = self._STATE_FALLBACK
            log.info("[HOTKEY] Successfully swapped to legacy backend")
            self._show_fallback_notification()
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
        if is_linux():
            wayland_display = os.environ.get("WAYLAND_DISPLAY", "")
            xdg_session = os.environ.get("XDG_SESSION_TYPE", "")
            if wayland_display or xdg_session == "wayland":
                return WaylandHotkey(self.hotkey_str)
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
        """
        with self._swap_lock:
            if self._state != self._STATE_FALLBACK:
                return  # Already recovered, failed, or stopped

        log.info("[HOTKEY] Retrying native backend...")
        try:
            # Stop the legacy backend first to free up any registered
            # hotkeys (e.g. RegisterHotKey on Windows).
            if self._legacy is not None:
                with contextlib.suppress(Exception):
                    self._legacy.stop()
                self._legacy = None
            self._native.stop()
            self._native.start(self._callback)  # type: ignore[arg-type]
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state == self._STATE_STOPPED:
                        # stop() was called during retry — clean up
                        self._native.stop()
                        return
                    self._state = self._STATE_NATIVE
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info("[HOTKEY] Native backend recovered — swapped back from legacy")
                self._show_recovery_notification()
                self._permission_notification_shown = False
                return
        except Exception as exc:
            log.warning("[HOTKEY] Native retry failed: %s — staying on legacy", exc)

        # Retry failed — restart the legacy backend and schedule another retry
        try:
            self._legacy = self._create_legacy_backend()
            self._legacy.start(self._callback)  # type: ignore[arg-type]
            if self._on_release_callback is not None:
                self._legacy.set_on_release(self._on_release_callback)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    self._legacy.stop()
                    return
                self._state = self._STATE_FALLBACK
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
