"""Adapter that wraps a native ``SubprocessHotkeyBackend`` to satisfy the"""

# ruff: noqa: E501 -- the state-transition ASCII table in the

import contextlib
import threading
from collections.abc import Callable

from voice_typer.server import hotkeys as _hotkeys_pkg
from voice_typer.server.branding import APP_NAME
from voice_typer.server.i18n import t as i18n_t
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
    """Adapter that wraps a ``SubprocessHotkeyBackend`` to satisfy the"""

    # State constants
    _STATE_NATIVE = "NATIVE"
    _STATE_FALLING_BACK = "FALLING_BACK"
    _STATE_FALLBACK = "FALLBACK"
    _STATE_FAILED = "FAILED"
    _STATE_STOPPED = "STOPPED"

    # retry interval for swapping back to native (5 minutes)
    _NATIVE_RETRY_INTERVAL_SECONDS = 300.0

    # this file doesn't need ``# type: ignore[attr-defined]`` markers.
    _tray: object | None = None

    def __init__(self, native_backend, role: str | None = None):
        # Don't call super().__init__ because we delegate hotkey_str
        self._role: str | None = role
        self._native = native_backend
        self.hotkey_str = native_backend.hotkey_str
        self._on_release_callback: Callable[[], None] | None = None
        self._callback: Callable[[], None] | None = None
        self._legacy: HotkeyBackend | None = None
        # state-change hook for the dispatcher. The
        self._on_state_change_callback: Callable[[str], None] | None = None
        self._state = self._STATE_NATIVE
        self._swap_lock = threading.Lock()
        self._native_retry_timer: threading.Timer | None = None
        self._permission_notification_shown = False
        # the public API surface, so no ``# type: ignore[assignment]``
        native_backend.set_error_callback(self._on_native_error)
        native_backend.set_permanent_failure_callback(self._on_native_permanent_failure)
        # wire WARN callback
        native_backend.set_warn_callback(self._on_native_warn)
        # Wire legacy attribute-style callback slots. The
        try:
            native_backend._on_error_callback = self._on_native_error
            native_backend._on_permanent_failure_callback = self._on_native_permanent_failure
        except (AttributeError, TypeError):
            # Some backends may disallow attribute assignment
            pass

    def start(self, callback: Callable[[], None]) -> None:
        self._callback = callback
        try:
            self._native.start(callback)
            with self._swap_lock:
                if self._state != self._STATE_STOPPED:
                    self._state = self._STATE_NATIVE
        except Exception as exc:
            log.warning("[HOTKEY] Native backend failed to start: %s, trying legacy", exc)
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
                pass
            # Reset the permission notification flag so a restart
            self._permission_notification_shown = False
            # Stop both backends, the inactive one is a no-op.
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

    def _on_native_warn(self, warn_message: str) -> None:
        """non-fatal degradation (e.g. SKIP_ACCESSIBILITY)."""
        log.warning("[HOTKEY] Native backend WARN: %s", warn_message)
        tray = self._get_tray()
        if tray is not None:
            try:
                short = warn_message if len(warn_message) <= 160 else warn_message[:157] + "..."
                # ``{appName}`` (C-BRAND-1). Format accepts either.
                tray.notify(
                    i18n_t(
                        "notify.native_adapter.warn_title",
                        app=APP_NAME,
                        appName=APP_NAME,
                    ),
                    short,
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for WARN")

    def _on_native_error(self, error_message: str) -> None:
        """Called by the native backend when it emits an ERROR: line."""
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

        # Get the tray from the app (best-effort, the adapter may be
        tray = self._get_tray()
        show_permission_notification(tray, error_message)

        # Open the OS permission UI (macOS System Settings / Linux pkexec)
        request_keyboard_permission(on_granted=self._on_permission_granted)

    def _on_permission_granted(self) -> None:
        """Called when the permission retry timer detects the permission"""
        log.info("[HOTKEY] Permission granted, restarting native backend")
        with contextlib.suppress(Exception):
            self._native.stop()
        # Stop the legacy backend BEFORE restarting native so
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
            # ``# type: ignore[arg-type]`` marker. ``_callback`` is
            cb = self._callback
            if cb is None:
                log.warning("[HOTKEY] Permission granted but no callback registered, skipping native restart")
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
                self._notify_state_change(self._STATE_NATIVE)
                return
        except Exception:
            log.exception("[HOTKEY] Native restart after permission grant failed")

    def _get_tray(self):
        """Best-effort: get the app's tray object for notifications."""
        # The HotkeyDispatcher stores itself on the adapter? No, but
        return getattr(self, "_tray", None)

    def _notify_state_change(self, state: str) -> None:
        """notify the dispatcher (if wired) that the active"""
        cb = getattr(self, "_on_state_change_callback", None)
        if cb is None:
            return
        try:
            cb(state)
        except Exception:
            log.debug("[HOTKEY] state-change callback (%s) raised", state, exc_info=True)

    def _on_native_permanent_failure(self) -> None:
        """Called when the native backend exhausts its 5 retries."""
        log.warning("[HOTKEY] Native backend permanently failed, swapping to legacy")
        self._swap_to_legacy()

    def _swap_to_legacy(self) -> None:
        """Replace the native backend with a legacy one."""
        with self._swap_lock:
            if self._state in (self._STATE_FALLBACK, self._STATE_FAILED, self._STATE_STOPPED):
                return  # Already swapped, given up, or stopped
            self._state = self._STATE_FALLING_BACK

        # If a recording is in progress (push-to-talk), fire the release
        if self._on_release_callback is not None:
            try:
                self._on_release_callback()
            except Exception:
                log.exception("[HOTKEY] on_release during swap raised")

        try:
            legacy = self._create_legacy_backend()
            # narrow ``self._callback`` to non-None (see
            cb = self._callback
            if cb is None:
                log.warning("[HOTKEY] Cannot swap to legacy, no callback registered")
                with contextlib.suppress(Exception):
                    legacy.stop()
                return
            legacy.start(cb)
            if self._on_release_callback is not None:
                legacy.set_on_release(self._on_release_callback)
            with self._swap_lock:
                if self._state == self._STATE_STOPPED:
                    # stop() was called during the swap, clean up
                    with contextlib.suppress(Exception):
                        legacy.stop()
                    return
                self._legacy = legacy
                # propagate _tray to legacy backend via the
                with contextlib.suppress(AttributeError, TypeError):
                    self._legacy.set_tray(self._tray)
                self._state = self._STATE_FALLBACK
            log.info("[HOTKEY] Successfully swapped to legacy backend")
            self._show_fallback_notification()
            # let the dispatcher un-pool the aux roles so they
            self._notify_state_change(self._STATE_FALLBACK)
            # Schedule a periodic retry of the native backend
            self._schedule_native_retry()
        except Exception as exc:
            log.error("[HOTKEY] Legacy backend also failed: %s, giving up", exc)
            with self._swap_lock:
                self._state = self._STATE_FAILED
            self._show_failure_notification(exc)

    def _create_legacy_backend(self) -> HotkeyBackend:
        """Instantiate the appropriate legacy backend for this platform."""
        if is_windows():
            return WindowsNativeHotkey(self.hotkey_str)
        if is_linux() and is_wayland_session():
            # Pass ``self._role`` so the legacy fallback on a
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
        """Attempt to swap back to the native backend."""
        with self._swap_lock:
            if self._state != self._STATE_FALLBACK:
                return  # Already recovered, failed, or stopped

        log.info("[HOTKEY] Retrying native backend...")
        # snapshot the legacy reference and stop it (frees any
        warm_spare = self._legacy
        try:
            if warm_spare is not None:
                with contextlib.suppress(Exception):
                    warm_spare.stop()
            self._native.stop()
            # narrow ``self._callback`` to non-None (see
            cb = self._callback
            if cb is None:
                log.warning("[HOTKEY] Native retry aborted, no callback registered")
                return
            self._native.start(cb)
            if self._native.is_alive():
                with self._swap_lock:
                    if self._state == self._STATE_STOPPED:
                        # stop() was called during retry, clean up
                        self._native.stop()
                        return
                    self._state = self._STATE_NATIVE
                    # Native succeeded, drop the warm spare; we don't
                    self._legacy = None
                if self._on_release_callback is not None:
                    self._native.set_on_release(self._on_release_callback)
                log.info("[HOTKEY] Native backend recovered, swapped back from legacy")
                self._show_recovery_notification()
                self._permission_notification_shown = False
                # let the dispatcher re-pool the aux roles
                self._notify_state_change(self._STATE_NATIVE)
                return
        except Exception as exc:
            log.warning("[HOTKEY] Native retry failed: %s, staying on legacy", exc)

        # Retry failed, restart the warm spare (or create a new legacy
        try:
            legacy = warm_spare if warm_spare is not None else self._create_legacy_backend()
            # ``cb`` is the narrowed callback from above, if
            if cb is None:
                log.warning("[HOTKEY] Cannot restart legacy, no callback registered")
                return
            legacy.start(cb)
            if self._on_release_callback is not None:
                legacy.set_on_release(self._on_release_callback)
            # (cont.): propagate _tray via the public
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
            log.error("[HOTKEY] Both native and legacy backends failed, hotkey dead")
            self._show_failure_notification(None)

    def _show_fallback_notification(self) -> None:
        """Notify the user that the hotkey is running in compatibility mode."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    i18n_t(
                        "notify.native_adapter.fallback_title",
                        app=APP_NAME,
                        appName=APP_NAME,
                    ),
                    i18n_t("notify.native_adapter.fallback_body"),
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for fallback notification")

    def _show_recovery_notification(self) -> None:
        """Notify the user that the native backend has recovered."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    i18n_t(
                        "notify.native_adapter.recovery_title",
                        app=APP_NAME,
                        appName=APP_NAME,
                    ),
                    i18n_t("notify.native_adapter.recovery_body"),
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for recovery notification")

    def _show_failure_notification(self, exc: Exception | None) -> None:
        """Notify the user that the hotkey is not working at all."""
        tray = self._get_tray()
        if tray is not None:
            try:
                tray.notify(
                    i18n_t(
                        "notify.native_adapter.failure_title",
                        app=APP_NAME,
                        appName=APP_NAME,
                    ),
                    i18n_t("notify.native_adapter.failure_body"),
                )
            except Exception:
                log.debug("[HOTKEY] tray.notify failed for failure notification")
