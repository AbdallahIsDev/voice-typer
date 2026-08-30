"""Native hotkey backend — {name}."""

from __future__ import annotations

import logging
import time

from voice_typer.server.native_hotkeys._constants import (
    MAX_RESTART_ATTEMPTS,
    RESTART_DELAY_BASE_SECONDS,
)

log = logging.getLogger(__name__)


class _ReaderMixin:
    def _reader_loop(self) -> None:
        """Read lines from the binary's stdout and dispatch.

        the check-then-spawn sequence
        is guarded by ``_restart_lock`` and the old thread ``return``s after
        spawning a replacement (was: ``continue`` → fork-bomb). The attempt
        counter is the instance-level ``_restart_attempts`` (was: local
        ``attempts`` → per-thread, defeating the cap).
        """
        while not self._stop_event.is_set():
            if self._process is None or self._process.poll() is not None:
                # Process exited — decide whether to restart
                if self._stop_event.is_set():
                    return
                with self._restart_lock:
                    # Re-check under lock — another reader thread may have
                    # already restarted while we were waiting for the lock.
                    if self._process is not None and self._process.poll() is None:
                        # Another thread is handling the restart; exit cleanly
                        # so the new reader thread owns the new process.
                        return
                    self._restart_attempts += 1
                    attempts = self._restart_attempts
                    if attempts > MAX_RESTART_ATTEMPTS:
                        self._failed = True
                        self._error_message = f"{self.platform_name} binary crashed {attempts} times; giving up"
                        log.error("[NATIVE-HOTKEY] %s", self._error_message)
                        self._ready_event.set()  # unblock start() wait
                        # notify the adapter so it can swap to a
                        # legacy backend. The callback is invoked on the
                        # reader thread; adapters must be thread-safe.
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
                # Don't sleep with the GIL — use Event.wait for early cancel
                if self._stop_event.wait(timeout=delay):
                    return
                try:
                    self._spawn_process()
                except RuntimeError as exc:
                    self._failed = True
                    self._error_message = str(exc)
                    self._ready_event.set()
                    # Also notify the adapter on spawn failure (binary
                    # disappeared mid-restart, etc.)
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
                # ``_spawn_process``). The OLD thread (this one) MUST return
                # so it doesn't compete with the new reader for
                # ``self._process.stdout.readline()``. Pre-fix, the old
                # thread did ``continue`` and would race with the new
                # reader, causing out-of-order event processing (e.g.
                # ``MOD_DOWN:Ctrl`` and ``KEY_DOWN:V`` for a combo could be
                # processed by different threads).
                return

            assert self._process is not None
            assert self._process.stdout is not None
            try:
                line_bytes = self._process.stdout.readline()
            except Exception:
                line_bytes = b""
            if not line_bytes:
                # EOF — process likely exited
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
        """Parse one wire-protocol line and dispatch to the hotkey matcher.

        the dispatch is now table-driven via
        :data:`_WIRE_HANDLERS`. Adding a new event type = one table
        entry + one ``_on_*_event`` handler method, not a new branch
        in a 100-line if/elif chain.

        every recognised line (except ``PONG``) updates
        ``_last_event_received_at`` so the liveness watchdog can tell
        whether the binary is producing output.  ``PONG`` is tracked
        separately via ``_last_pong_received_at`` so the watchdog can
        distinguish "binary is alive and responding to PING" from
        "binary is alive but ignoring PING" (the latter is a strong
        signal of a stuck event loop).
        """
        # PONG is tracked separately from generic events so the
        # watchdog can apply the "no event AND no PONG" respawn rule.
        # PONG does NOT update ``_last_event_received_at``.
        if line == "PONG":
            self._on_pong_event()
            return

        # update the general "last event received" timestamp
        # for every other recognised line.  This includes READY,
        # ERROR:, WARN:, and all key/modifier events.  Unrecognised
        # lines also update the timestamp (any output means the binary
        # is alive).
        self._last_event_received_at = time.time()

        # READY is an exact-match event (no payload) that resets the
        # per-backend restart counter. Special-cased before the table
        # because the table dispatches via ``startswith``, and READY
        # has no payload to extract.
        if line == "READY":
            self._on_ready_event()
            return

        # All remaining recognised events are dispatched via the table.
        # The first matching prefix wins; entries are ordered so that
        # more-specific prefixes (e.g. ``MOD_DOWN:``) are tried before
        # less-specific ones. (No current entries shadow each other,
        # but the order is defensive against future additions.)
        for prefix, handler_name, down_flag in self._WIRE_HANDLERS:
            if line.startswith(prefix):
                payload = line[len(prefix) :]
                handler = getattr(self, handler_name)
                if down_flag is None:
                    handler(payload)
                else:
                    handler(payload, down=down_flag)
                return

        log.debug("[NATIVE-HOTKEY] Unrecognized line from %s: %r", self.platform_name, line)

    def _on_pong_event(self) -> None:
        """Handle a ``PONG`` wire-protocol line ().

        Updates ``_last_pong_received_at`` and latches
        ``_pong_supported`` on the first PONG so the liveness
        watchdog knows the binary implements the PING/PONG protocol.
        From then on, PONG absence is a reliable hung-binary signal.

        Does NOT update ``_last_event_received_at`` — PONG is a
        separate liveness signal so the watchdog can distinguish
        "alive and responding to PING" from "alive but ignoring PING"
        (the latter is a strong signal of a stuck event loop).
        """
        self._last_pong_received_at = time.time()
        self._pong_supported = True
        log.debug("[NATIVE-HOTKEY] %s binary sent PONG", self.platform_name)

    def _on_ready_event(self) -> None:
        """Handle a ``READY`` wire-protocol line.

        Sets ``_ready_event`` (unblocking ``start()``'s READY wait)
        and resets the per-backend restart counter () so a
        transient crash followed by recovery doesn't permanently count
        toward ``MAX_RESTART_ATTEMPTS``.
        """
        self._ready_event.set()
        self._restart_attempts = 0
        # DEBUG: the dispatcher's "[HOTKEY] Registration OK" INFO line
        # is emitted right after start() returns — a second INFO here
        # duplicated the same readiness event.
        log.debug("[NATIVE-HOTKEY] %s binary is READY", self.platform_name)

    def _on_version_event(self, payload: str) -> None:
        """Handle a ``VERSION:<x.y.z>`` wire-protocol line (VERSION reporter).

        Records the binary's reported wire-protocol version in
        ``_binary_version`` and (if the factory stashed an
        ``_expected_version`` from the manifest) compares the two,
        logging a WARNING on mismatch. The comparison is deferred to
        here (rather than done in the factory) because the binary only
        emits VERSION after READY, which happens after ``start()`` —
        the factory creates the backend but doesn't start it.

        Older binaries that don't emit VERSION leave ``_binary_version``
        as None; the factory's expected-version check is then a no-op
        (no comparison possible). A mismatch is a diagnostic signal
        only — the binary is still functional for the wire-protocol
        events we care about, so we don't fail the backend.

        ``payload`` is the version string (e.g. ``"1.0.0"``) with no
        surrounding whitespace; we strip defensively in case a binary
        emits ``VERSION: 1.0.0`` (with a space).
        """
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
            # No manifest entry for this binary — skip the comparison.
            # This is the case for tests that construct backends
            # directly without going through the factory, and for
            # dev-tree binaries whose filename isn't in the manifest.
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
        """Handle an ``ERROR:<message>`` wire-protocol line.

        Marks the backend as failed (``_failed = True``), stores the
        error message, logs at ERROR level, and unblocks ``start()``'s
        READY wait so callers see the failure promptly rather than
        timing out.

        also invokes ``_on_error_callback`` (if registered by
        the adapter) so the adapter can classify the error and
        potentially show a permission prompt. The callback is invoked
        on the reader thread; adapters must be thread-safe.
        """
        self._failed = True
        self._error_message = payload
        log.error(
            "[NATIVE-HOTKEY] %s binary reported ERROR: %s",
            self.platform_name,
            self._error_message,
        )
        self._ready_event.set()  # unblock start() wait
        if self._on_error_callback is not None:
            try:
                self._on_error_callback(self._error_message)
            except Exception:
                log.exception(
                    "[NATIVE-HOTKEY] _on_error_callback raised in %s backend",
                    self.platform_name,
                )

    def _on_warn_event(self, payload: str) -> None:
        """Handle a ``WARN:<message>`` wire-protocol line ().

        Non-fatal degradation: logs at WARNING level and invokes
        ``_on_warn_callback`` (if registered by the adapter) so the
        adapter can surface the warning to the user.
        """
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
