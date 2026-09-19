"""Native hotkey backend, {name}."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, ClassVar

from voice_typer.server.native_hotkeys._constants import (
    MAX_RESTART_ATTEMPTS,
    RESTART_DELAY_BASE_SECONDS,
)

log = logging.getLogger(__name__)

# Known-good diagnostic lines emitted by the native binaries via their
_KNOWN_DIAGNOSTIC_MARKERS: tuple[str, ...] = (
    "starting; spec=",
    "stdin reader thread started (PING/PONG enabled)",
    "keyboard hook installed",
    "READY emitted; version=",
)


class _ReaderMixin:
    # Human-readable backend name used in log messages. Provided by the
    platform_name: str

    # Members provided by the composed ``SubprocessHotkeyBackend``
    _process: subprocess.Popen | None
    _stop_event: threading.Event
    _ready_event: threading.Event
    _restart_lock: threading.Lock
    _restart_attempts: int
    _failed: bool
    _error_message: str | None
    _binary_version: str | None
    _on_permanent_failure_callback: Callable[[], None] | None
    _on_error_callback: Callable[[str], None] | None
    _on_warn_callback: Callable[[str], None] | None
    _WIRE_HANDLERS: ClassVar[list[tuple[str, str, bool | None]]]

    if TYPE_CHECKING:
        # Method provided by ``_SpawnMixin`` in the composed MRO; a
        def _spawn_process(self) -> None: ...

    def _reader_loop(self) -> None:
        """Read lines from the binary's stdout and dispatch."""
        while not self._stop_event.is_set():
            if self._process is None or self._process.poll() is not None:
                # Process exited, decide whether to restart
                if self._stop_event.is_set():
                    return
                with self._restart_lock:
                    # Re-check under lock, another reader thread may have
                    if self._process is not None and self._process.poll() is None:
                        # Another thread is handling the restart; exit cleanly
                        return
                    self._restart_attempts += 1
                    attempts = self._restart_attempts
                    if attempts > MAX_RESTART_ATTEMPTS:
                        self._failed = True
                        self._error_message = f"{self.platform_name} binary crashed {attempts} times; giving up"
                        log.error("[NATIVE-HOTKEY] %s", self._error_message)
                        # notify the adapter so it can swap to a
                        if self._on_permanent_failure_callback is not None:
                            try:
                                self._on_permanent_failure_callback()
                            except Exception:
                                log.exception(
                                    "[NATIVE-HOTKEY] _on_permanent_failure_callback raised in %s backend",
                                    self.platform_name,
                                )
                        return
                    delay = RESTART_DELAY_BASE_SECONDS * (2 ** (attempts - 1))
                log.warning(
                    "[NATIVE-HOTKEY] %s binary exited (attempt %d/%d); restarting in %.1fs",
                    self.platform_name,
                    attempts,
                    MAX_RESTART_ATTEMPTS,
                    delay,
                )
                # Don't sleep with the GIL: use Event.wait for early cancel
                if self._stop_event.wait(timeout=delay):
                    return
                try:
                    self._spawn_process()
                except RuntimeError as exc:
                    self._failed = True
                    self._error_message = str(exc)
                    self._ready_event.set()
                    # Also notify the adapter on spawn failure (binary
                    if self._on_permanent_failure_callback is not None:
                        try:
                            self._on_permanent_failure_callback()
                        except Exception:
                            log.exception(
                                "[NATIVE-HOTKEY] _on_permanent_failure_callback raised in %s backend",
                                self.platform_name,
                            )
                    return
                # the new spawn creates its own reader thread (in
                return

            assert self._process is not None
            assert self._process.stdout is not None
            try:
                line_bytes = self._process.stdout.readline()
            except Exception:
                # Read failure (broken pipe / closed stream) is treated as
                log.debug(
                    "[NATIVE-HOTKEY] readline() failed on %s binary stdout, treating as EOF",
                    self.platform_name,
                    exc_info=True,
                )
                line_bytes = b""
            if not line_bytes:
                # EOF, process likely exited
                continue

            line = line_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                continue
            try:
                self._handle_line(line)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] Error handling line from %s binary: %r",
                    self.platform_name,
                    line,
                )

    def _handle_line(self, line: str) -> None:
        """Parse one wire-protocol line and dispatch to the hotkey matcher."""
        # PONG is tracked separately from generic events so the
        if line == "PONG":
            self._on_pong_event()
            return

        # update the general "last event received" timestamp
        self._last_event_received_at = time.time()

        # READY is an exact-match event (no payload) that resets the
        if line == "READY":
            self._on_ready_event()
            return

        # All remaining recognised events are dispatched via the table.
        for prefix, handler_name, down_flag in self._WIRE_HANDLERS:
            if line.startswith(prefix):
                payload = line[len(prefix) :]
                handler = getattr(self, handler_name)
                if down_flag is None:
                    handler(payload)
                else:
                    handler(payload, down=down_flag)
                return

        for marker in _KNOWN_DIAGNOSTIC_MARKERS:
            if marker in line:
                log.debug("[NATIVE-HOTKEY] %s binary diagnostic: %s", self.platform_name, line)
                return

        log.debug("[NATIVE-HOTKEY] Unrecognized line from %s: %r", self.platform_name, line)

    def _on_pong_event(self) -> None:
        """Handle a ``PONG`` wire-protocol line ()."""
        self._last_pong_received_at = time.time()
        self._pong_supported = True
        log.debug("[NATIVE-HOTKEY] %s binary sent PONG", self.platform_name)

    def _on_ready_event(self) -> None:
        """Handle a ``READY`` wire-protocol line."""
        self._ready_event.set()
        self._restart_attempts = 0
        # DEBUG: the dispatcher's "[HOTKEY] Registration OK" INFO line
        log.debug("[NATIVE-HOTKEY] %s binary is READY", self.platform_name)

    def _on_version_event(self, payload: str) -> None:
        """Handle a ``VERSION:<x.y.z>`` wire-protocol line (VERSION reporter)."""
        version = (payload or "").strip()
        if not version:
            log.debug(
                "[NATIVE-HOTKEY] %s binary sent empty VERSION line",
                self.platform_name,
            )
            return
        self._binary_version = version
        log.info(
            "[NATIVE-HOTKEY] %s binary reported VERSION: %s",
            self.platform_name,
            version,
        )
        expected = getattr(self, "_expected_version", None)
        if expected is None:
            # No manifest entry for this binary, skip the comparison.
            return
        if version != expected:
            log.warning(
                "[NATIVE-HOTKEY] %s binary VERSION mismatch: binary "
                "reported %s, manifest expected %s. The binary is still "
                "functional but may be out of sync with the Python side. "
                "Rebuild via scripts/build/compile_native.sh and re-run "
                "scripts/build/update_native_manifests.py.",
                self.platform_name,
                version,
                expected,
            )

    def _on_error_event(self, payload: str) -> None:
        """Handle an ``ERROR:<message>`` wire-protocol line."""
        self._failed = True
        self._error_message = payload
        log.error(
            "[NATIVE-HOTKEY] %s binary reported ERROR: %s",
            self.platform_name,
            self._error_message,
        )
        if self._on_error_callback is not None:
            try:
                self._on_error_callback(self._error_message)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] _on_error_callback raised in %s backend",
                    self.platform_name,
                )

    def _on_warn_event(self, payload: str) -> None:
        """Handle a ``WARN:<message>`` wire-protocol line ()."""
        log.warning(
            "[NATIVE-HOTKEY] %s binary reported WARN: %s",
            self.platform_name,
            payload,
        )
        if self._on_warn_callback is not None:
            try:
                self._on_warn_callback(payload)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] _on_warn_callback raised in %s backend",
                    self.platform_name,
                )
