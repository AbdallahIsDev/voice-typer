"""Native hotkey core dispatch."""

from __future__ import annotations

import logging
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg
from voice_typer.server.hotkeys.base import HotkeyBackend
from voice_typer.server.native_hotkeys._constants import (
    READY_TIMEOUT_SECONDS,
)
from voice_typer.server.native_hotkeys._matching import _MatchingMixin
from voice_typer.server.native_hotkeys._reader import _ReaderMixin
from voice_typer.server.native_hotkeys._spawn import _SpawnMixin
from voice_typer.server.native_hotkeys._watchdog import _WatchdogMixin
from voice_typer.server.native_hotkeys.spec_parser import parse_hotkey_spec
from voice_typer.server.tray_hotkey import format_hotkey_label

log = logging.getLogger(__name__)


class SubprocessHotkeyBackend(_SpawnMixin, _ReaderMixin, _WatchdogMixin, _MatchingMixin, HotkeyBackend):
    """Base class for out-of-process native hotkey backends."""

    platform_name: str = "subprocess"
    supports_fn: bool = False

    # table-driven wire-protocol dispatch.
    _WIRE_HANDLERS: ClassVar[list[tuple[str, str, bool | None]]] = [
        ("MOD_DOWN:", "_on_modifier_event", True),
        ("MOD_UP:", "_on_modifier_event", False),
        ("KEY_DOWN:", "_on_key_event", True),
        ("KEY_UP:", "_on_key_event", False),
        ("FN_DOWN", "_on_fn_event", True),
        ("FN_UP", "_on_fn_event", False),
        # VERSION reporter: ``VERSION:<x.y.z>`` is emitted by the binary
        ("VERSION:", "_on_version_event", None),
        ("ERROR:", "_on_error_event", None),
        ("WARN:", "_on_warn_event", None),
    ]

    def __init__(self, hotkey_str: str, binary_path: Path | None = None):
        self.hotkey_str = hotkey_str
        self._parsed = parse_hotkey_spec(hotkey_str)
        self._on_release_callback: Callable[[], None] | None = None
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._failed = False
        self._error_message: str | None = None
        # Multi-spec pooling: extra matchers share this backend's
        self._extra_matchers: list[dict[str, Any]] = []
        # Delegation flag: when True, ``start()`` skips spawning a
        self._delegated: bool = False
        # accept an explicit ``binary_path`` from the
        self._binary_path: Path | None = (
            binary_path if binary_path is not None else _native_hotkeys_pkg.base.get_native_binary_path()
        )
        # restart lock + instance-level
        self._restart_lock = threading.Lock()
        self._restart_attempts = 0
        # Hotkey state tracking for matching
        self._held_modifiers: set[str] = set()
        self._fn_down: bool = False
        self._main_key_down: bool = False
        self._match_lock = threading.Lock()
        # VERSION reporter: the binary's reported wire-protocol version (set by
        self._binary_version: str | None = None
        # VERSION reporter: the manifest's expected version for this binary's
        self._expected_version: str | None = None
        # native log path: stable per-backend diagnostic log path passed
        self._native_log_path: Path | None = None
        # Toggle-mode flag: when True (set by HotkeyDispatcher for the main
        self._toggle_on_keyup: bool = False
        # optional callbacks invoked from _handle_line and
        self._on_error_callback: Callable[[str], None] | None = None
        self._on_permanent_failure_callback: Callable[[], None] | None = None
        # optional callback for WARN: lines.
        self._on_warn_callback: Callable[[str], None] | None = None
        # liveness watchdog state.  ``last_event_received_at``
        self._last_event_received_at: float = time.time()
        self._last_pong_received_at: float = 0.0
        # ``_pong_supported`` is set to True the first time a
        self._pong_supported: bool = False
        # watchdog thread + its own stop event.  The watchdog
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop_event = threading.Event()
        # optional callback for watchdog restart notifications.
        self._on_watchdog_restart_callback: Callable[[str], None] | None = None
        # callback used by ``start()``: stashed so the watchdog
        self._callback: Callable[[], None] | None = None
        # one-shot latch set by
        self._shutdown_requested: bool = False

    def set_error_callback(self, callback: Callable[[str], None]) -> None:
        """Register the ``ERROR:`` line handler invoked from ``_handle_line``."""
        self._on_error_callback = callback

    def set_permanent_failure_callback(self, callback: Callable[[], None]) -> None:
        """Register the permanent-failure handler (5 retries exhausted)."""
        self._on_permanent_failure_callback = callback

    def set_warn_callback(self, callback: Callable[[str], None]) -> None:
        """Register the ``WARN:`` line handler invoked from ``_handle_line``."""
        self._on_warn_callback = callback

    def add_extra_matcher(self, role: str, spec: str) -> None:
        """Register an additional ``(role, spec)`` pair to be matched"""
        parsed = parse_hotkey_spec(spec)
        if parsed is None:
            raise ValueError(f"Cannot parse hotkey spec: {spec!r}")
        for existing in self._extra_matchers:
            if existing["role"] == role:
                existing["parsed"] = parsed
                return
        self._extra_matchers.append(
            {
                "role": role,
                "parsed": parsed,
                "callback": None,
                "on_release_callback": None,
                "toggle_on_keyup": False,
            }
        )

    def remove_extra_matcher(self, role: str) -> None:
        """Remove the extra matcher registered for ``role`` (no-op if
        no matcher exists for that role)."""
        self._extra_matchers = [m for m in self._extra_matchers if m["role"] != role]

    def set_role_callback(self, role: str, callback: Callable[[], None] | None) -> None:
        """Set the press callback for ``role``. The primary spec's
        callback (role ``"dictation"``) is set via :meth:`start`."""
        if role == "dictation":
            self._callback = callback
            return
        for m in self._extra_matchers:
            if m["role"] == role:
                m["callback"] = callback
                return
        raise KeyError(f"Unknown role: {role!r} (register it via add_extra_matcher first)")

    def set_role_on_release(self, role: str, callback: Callable[[], None] | None) -> None:
        """Set the release callback for ``role``. The primary spec's
        release callback is set via :meth:`set_on_release`."""
        if role == "dictation":
            self._on_release_callback = callback
            return
        for m in self._extra_matchers:
            if m["role"] == role:
                m["on_release_callback"] = callback
                return
        raise KeyError(f"Unknown role: {role!r} (register it via add_extra_matcher first)")

    def set_role_toggle_on_keyup(self, role: str, value: bool) -> None:
        """Set the ``toggle_on_keyup`` flag for ``role``. The primary
        spec's flag is set via :meth:`set_toggle_on_keyup`."""
        if role == "dictation":
            self._toggle_on_keyup = value
            return
        for m in self._extra_matchers:
            if m["role"] == role:
                m["toggle_on_keyup"] = value
                return
        raise KeyError(f"Unknown role: {role!r} (register it via add_extra_matcher first)")

    def start(self, callback: Callable[[], None]) -> None:
        """Spawn the native binary and start parsing its stdout."""
        if self._delegated:
            self._callback = callback
            # Mark as ready so is_alive() reports True and callers
            self._ready_event.set()
            # DEBUG: the dispatcher's "[HOTKEY] ... pooled into shared
            log.debug(
                "[NATIVE-HOTKEY] %s backend is delegated (no subprocess); "
                "matching handled by the shared backend's extra matcher",
                self.platform_name,
            )
            return
        if self._parsed is None:
            raise ValueError(f"Cannot parse hotkey spec: {self.hotkey_str!r}")

        # Validate platform-specific constraints
        validation_error = self._validate_platform()
        if validation_error:
            raise ValueError(validation_error)

        if self._binary_path is None:
            raise FileNotFoundError(
                f"Native {self.platform_name} key-listener binary not found. "
                f"Set VOICE_TYPER_NATIVE_BINARY or rebuild the project."
            )

        log.info(
            "[NATIVE-HOTKEY] Starting %s backend (hotkey=%s)",
            self.platform_name,
            format_hotkey_label(self.hotkey_str),
        )

        self._callback = callback
        self._stop_event.clear()
        self._ready_event.clear()
        self._failed = False

        # Spawn the binary
        self._spawn_process()

        # ``_spawn_process`` may set ``_failed=True`` and
        if self._failed:
            self.stop(shutdown=False)
            msg = self._error_message or f"{self.platform_name} binary failed to start"
            raise RuntimeError(msg)

        # Wait for READY (or ERROR/early exit)
        if not self._ready_event.wait(timeout=READY_TIMEOUT_SECONDS):
            self._failed = True
            self._error_message = f"Timed out waiting for READY from {self.platform_name} binary"
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            self.stop(shutdown=False)
            raise RuntimeError(self._error_message)

        if self._failed:
            msg = self._error_message or f"{self.platform_name} binary failed to start"
            raise RuntimeError(msg)

    def stop(self, *, shutdown: bool = True) -> None:
        """Stop the binary cleanly."""
        if shutdown:
            # latch BEFORE the idempotency guard so a concurrent
            self._shutdown_requested = True
        if self._stop_event.is_set():
            return
        # Delegated backends own no subprocess / reader / watchdog —
        if self._delegated:
            self._stop_event.set()
            return
        log.info("[NATIVE-HOTKEY] Stopping %s backend", self.platform_name)
        self._stop_event.set()

        # signal the watchdog to exit BEFORE we kill the
        self._watchdog_stop_event.set()
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.0)
            self._watchdog_thread = None

        if self._process is not None:
            try:
                # Try graceful shutdown first
                try:
                    if _native_hotkeys_pkg.is_windows():
                        self._process.terminate()
                    else:
                        self._process.send_signal(signal.SIGTERM)
                except (OSError, ProcessLookupError):
                    # Process already gone between poll() and signal —
                    log.debug(
                        "[NATIVE-HOTKEY] terminate() raced process exit",
                        exc_info=True,
                    )
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    # Force kill
                    try:
                        self._process.kill()
                        self._process.wait(timeout=1.0)
                    except (subprocess.TimeoutExpired, OSError):
                        # Force-kill is itself best-effort; the process may
                        log.debug(
                            "[NATIVE-HOTKEY] force-kill after graceful timeout failed",
                            exc_info=True,
                        )
            finally:
                self._process = None

        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def is_alive(self) -> bool:
        """Return True if the backend is ready and not stopped."""
        if self._delegated:
            return self._ready_event.is_set() and not self._stop_event.is_set()
        return (
            self._process is not None
            and self._process.poll() is None
            and self._ready_event.is_set()
            and not self._stop_event.is_set()
        )

    def diagnose(self) -> str:
        """Return a human-readable diagnostic string."""
        binary = str(self._binary_path) if self._binary_path else "<not found>"
        alive = self._process is not None and self._process.poll() is None
        ready = self._ready_event.is_set()
        failed = self._failed
        return (
            f"{type(self).__name__} ({self.platform_name})\n"
            f"Hotkey: {self.hotkey_str}\n"
            f"Binary: {binary}\n"
            f"Process alive: {alive}\n"
            f"Ready: {ready}\n"
            f"Failed: {failed}\n"
            f"Error: {self._error_message or 'none'}"
        )

    def _validate_platform(self) -> str | None:
        """Return an error message if the hotkey is invalid for this platform,
        or None if valid. Subclasses must implement."""
        ...
