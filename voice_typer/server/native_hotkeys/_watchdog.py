"""Native hotkey backend, {name}."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from voice_typer.server.native_hotkeys._constants import (
    _WATCHDOG_PING_INTERVAL_SECONDS,
    _WATCHDOG_PONG_TIMEOUT_SECONDS,
    _WATCHDOG_RESPAWN_SECONDS,
)

log = logging.getLogger(__name__)


class _WatchdogMixin:
    # Members provided by the composed ``SubprocessHotkeyBackend``
    platform_name: str
    _process: subprocess.Popen | None
    _stop_event: threading.Event
    _watchdog_stop_event: threading.Event
    _last_event_received_at: float
    _last_pong_received_at: float
    _pong_supported: bool
    _shutdown_requested: bool
    _callback: Callable[[], None] | None
    _on_watchdog_restart_callback: Callable[[str], None] | None

    if TYPE_CHECKING:
        # Methods provided by the composed ``SubprocessHotkeyBackend``
        def start(self, callback: Callable[[], None]) -> None: ...

        def stop(self, *, shutdown: bool = True) -> None: ...

    def _watchdog_loop(self) -> None:
        """liveness watchdog for the native hotkey binary."""
        while not self._watchdog_stop_event.is_set():
            # Sleep for the PING interval, interruptible by stop().
            if self._watchdog_stop_event.wait(timeout=_WATCHDOG_PING_INTERVAL_SECONDS):
                return  # stop() was called
            if self._stop_event.is_set():
                return  # backend is shutting down
            # Only send PING if the process is alive.  If it's dead,
            if self._process is None or self._process.poll() is not None:
                continue
            # Write PING\n to the binary's stdin (best-effort).
            try:
                stdin = self._process.stdin
                if stdin is not None:
                    stdin.write(b"PING\n")
                    stdin.flush()
            except (BrokenPipeError, OSError):
                # Process died between the poll() check and the write.
                pass
            except Exception:
                log.debug(
                    "[NATIVE-HOTKEY] %s watchdog: failed to write PING",
                    self.platform_name,
                    exc_info=True,
                )

            # Wait briefly for a PONG response.  The actual PONG
            if self._watchdog_stop_event.wait(timeout=_WATCHDOG_PONG_TIMEOUT_SECONDS):
                return
            if self._stop_event.is_set():
                return

            # respawn check, "no event AND no PONG for 60s".
            now = time.time()
            event_stale = now - self._last_event_received_at > _WATCHDOG_RESPAWN_SECONDS
            pong_stale = self._pong_supported and (now - self._last_pong_received_at > _WATCHDOG_RESPAWN_SECONDS)
            if not (event_stale and pong_stale):
                continue

            # Binary is hung, respawn via stop() + start().
            log.warning(
                "[NATIVE-HOTKEY] %s binary unresponsive (no events for %.1fs, no PONG for %.1fs); respawning",
                self.platform_name,
                now - self._last_event_received_at,
                (now - self._last_pong_received_at) if self._last_pong_received_at > 0 else float("inf"),
            )
            # Tray notification (if wired by the adapter).
            if self._on_watchdog_restart_callback is not None:
                try:
                    self._on_watchdog_restart_callback(
                        f"Native {self.platform_name} hotkey binary was unresponsive and has been restarted."
                    )
                except Exception:
                    log.exception(
                        "[NATIVE-HOTKEY] _on_watchdog_restart_callback raised in %s backend",
                        self.platform_name,
                    )
            # Respawn.  ``stop()`` sets ``_stop_event`` (which kills
            try:
                # Stash the callback so we can re-start after stop().
                cb = self._callback
                if cb is None:
                    log.error(
                        "[NATIVE-HOTKEY] %s watchdog: cannot respawn, no callback stashed",
                        self.platform_name,
                    )
                    return
                self.stop(shutdown=False)
                # if the main thread called ``stop(shutdown=True)``
                if self._shutdown_requested:
                    log.info(
                        "[NATIVE-HOTKEY] %s watchdog: shutdown requested during "
                        "respawn cleanup; not resurrecting binary",
                        self.platform_name,
                    )
                    return
                # ``stop()`` set ``_watchdog_stop_event``: clear it
                self._watchdog_stop_event.clear()
                self.start(cb)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] %s watchdog: respawn failed",
                    self.platform_name,
                )
                return
            # The new spawn started a new watchdog thread; this old
            return
