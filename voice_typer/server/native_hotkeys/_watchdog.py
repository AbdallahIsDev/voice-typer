"""Native hotkey backend — {name}."""

from __future__ import annotations

import logging
import time

from voice_typer.server.native_hotkeys._constants import (
    _WATCHDOG_PING_INTERVAL_SECONDS,
    _WATCHDOG_PONG_TIMEOUT_SECONDS,
    _WATCHDOG_RESPAWN_SECONDS,
)

log = logging.getLogger(__name__)


class _WatchdogMixin:
    def _watchdog_loop(self) -> None:
        """liveness watchdog for the native hotkey binary.

        Runs in a dedicated daemon thread.  Every ``_WATCHDOG_PING_INTERVAL_SECONDS``
        (30s) it writes ``PING\n`` to the binary's stdin and expects a
        ``PONG\n`` response within ``_WATCHDOG_PONG_TIMEOUT_SECONDS`` (5s).
        If no event AND no PONG has been received in
        ``_WATCHDOG_RESPAWN_SECONDS`` (60s), the binary is considered
        hung and the watchdog respawns it via ``stop()`` + ``start()``.

        Safety: until the binary has sent at least one PONG
        (``_pong_supported`` is False), the watchdog does NOT respawn
        on PONG absence — this prevents false-positive respawns for
        binaries that don't implement the PING/PONG protocol (which
        would otherwise respawn every 60s of idleness).  Once a PONG
        is observed, the binary is known to support the protocol and
        PONG absence becomes a reliable hung-binary signal.

        Tray notifications: if ``_on_watchdog_restart_callback`` is
        set (by the adapter), it's invoked with a human-readable
        message on each restart so the user is informed that their
        hotkey backend was unresponsive and has been restarted.
        """
        while not self._watchdog_stop_event.is_set():
            # Sleep for the PING interval, interruptible by stop().
            if self._watchdog_stop_event.wait(timeout=_WATCHDOG_PING_INTERVAL_SECONDS):
                return  # stop() was called
            if self._stop_event.is_set():
                return  # backend is shutting down
            # Only send PING if the process is alive.  If it's dead,
            # the reader thread's restart logic will handle it.
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
                # The reader thread will pick this up on the next loop.
                pass
            except Exception:
                log.debug(
                    "[NATIVE-HOTKEY] %s watchdog: failed to write PING",
                    self.platform_name,
                    exc_info=True,
                )

            # Wait briefly for a PONG response.  The actual PONG
            # handling happens in ``_handle_line`` on the reader
            # thread; we just sleep here so the watchdog doesn't
            # busy-loop.  The PONG timeout is enforced by the
            # respawn check below (which uses ``_last_pong_received_at``).
            if self._watchdog_stop_event.wait(timeout=_WATCHDOG_PONG_TIMEOUT_SECONDS):
                return
            if self._stop_event.is_set():
                return

            # respawn check — "no event AND no PONG for 60s".
            # Only enforce PONG absence if the binary is known to
            # support the PING/PONG protocol (``_pong_supported``).
            # Otherwise, an idle binary that doesn't implement PONG
            # would be respawned every 60s, which is a regression.
            now = time.time()
            event_stale = now - self._last_event_received_at > _WATCHDOG_RESPAWN_SECONDS
            pong_stale = self._pong_supported and (now - self._last_pong_received_at > _WATCHDOG_RESPAWN_SECONDS)
            if not (event_stale and pong_stale):
                continue

            # Binary is hung — respawn via stop() + start().
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
            # the reader thread and would re-enter ``stop()`` as a
            # no-op) and ``_watchdog_stop_event`` (which would kill
            # THIS thread).  We therefore clear ``_watchdog_stop_event``
            # BEFORE calling ``start()`` so the new spawn can start a
            # fresh watchdog.  The OLD watchdog (this thread) exits
            # after ``start()`` returns to avoid having two watchdogs.
            #
            # ``stop(shutdown=False)`` is used here because this
            # is a teardown-for-restart, NOT an app shutdown — latching
            # ``_shutdown_requested`` here would prevent the watchdog
            # from ever respawning (its own cleanup would disable it).
            # The subsequent ``_shutdown_requested`` check guards the
            # race where the main thread called ``stop(shutdown=True)``
            # concurrently between our cleanup ``stop()`` and our
            # ``start(cb)``: in that case the app is shutting down and
            # we must NOT resurrect the binary (it would orphan the
            # keyboard hook on Windows or evdev FDs on Linux).
            try:
                # Stash the callback so we can re-start after stop().
                cb = self._callback
                if cb is None:
                    log.error(
                        "[NATIVE-HOTKEY] %s watchdog: cannot respawn — no callback stashed",
                        self.platform_name,
                    )
                    return
                self.stop(shutdown=False)
                # if the main thread called ``stop(shutdown=True)``
                # while we were inside ``stop(shutdown=False)`` above
                # (or any time before this point), ``_shutdown_requested``
                # is now latched.  Do NOT resurrect the binary — the app
                # is shutting down and an orphaned native binary would
                # hold the keyboard hook (Windows) or evdev FDs (Linux)
                # after the parent has exited.  Once True, this flag is
                # NEVER cleared (see ``__init__``).
                if self._shutdown_requested:
                    log.info(
                        "[NATIVE-HOTKEY] %s watchdog: shutdown requested during "
                        "respawn cleanup; not resurrecting binary",
                        self.platform_name,
                    )
                    return
                # ``stop()`` set ``_watchdog_stop_event`` — clear it
                # so the new watchdog (spawned by ``start()`` via
                # ``_spawn_process``) can run.
                self._watchdog_stop_event.clear()
                self.start(cb)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] %s watchdog: respawn failed",
                    self.platform_name,
                )
                return
            # The new spawn started a new watchdog thread; this old
            # watchdog must exit to avoid double-monitoring.
            return
