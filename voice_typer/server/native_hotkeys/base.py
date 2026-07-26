"""Base class for native subprocess hotkey backends.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
(ARCH-045).

This module owns:

- :data:`MAX_RESTART_ATTEMPTS`, :data:`RESTART_DELAY_BASE_SECONDS`,
  :data:`READY_TIMEOUT_SECONDS` — restart/backoff constants.
- :class:`SubprocessHotkeyBackend` — ABC that handles subprocess
  plumbing, parsing, matching, restart, and shutdown for all three
  platform backends (macOS / Windows / Linux).

Patch-path compatibility: tests do
``monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)``
(and is_windows / is_linux).  For the patch to take effect on calls
made from *this* submodule, the bare ``is_macos()`` references must
resolve to the package-level binding (which is what the patch
replaces).  We therefore expose them as thin wrappers that delegate
to the package's binding at call time, rather than capturing the
function object at import time.
"""

import signal
import subprocess
import threading
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg

from .binary_path import get_native_binary_path
from .modifiers import _canonical_modifier, _canonical_modifier_name_for_token
from .spec_parser import log, parse_hotkey_spec


# See factory.py / mac_backend.py / etc. for the rationale.
def is_windows() -> bool:
    return _native_hotkeys_pkg.is_windows()


def is_macos() -> bool:
    return _native_hotkeys_pkg.is_macos()


def is_linux() -> bool:
    return _native_hotkeys_pkg.is_linux()

# ─── Constants ─────────────────────────────────────────────────────────────

MAX_RESTART_ATTEMPTS = 5
RESTART_DELAY_BASE_SECONDS = 1.0  # 1, 2, 4, 8, 16s backoff
READY_TIMEOUT_SECONDS = 5.0

# G4-H-31: liveness watchdog constants.
#   * ``_WATCHDOG_PING_INTERVAL_SECONDS`` — how often the watchdog
#     writes ``PING\n`` to the binary's stdin.
#   * ``_WATCHDOG_PONG_TIMEOUT_SECONDS`` — how long the watchdog waits
#     for a ``PONG\n`` response before considering it missing.
#   * ``_WATCHDOG_RESPAWN_SECONDS`` — if no event AND no PONG has been
#     received in this window, the binary is considered hung and the
#     watchdog respawns it via ``stop()`` + ``start()``.
_WATCHDOG_PING_INTERVAL_SECONDS = 30.0
_WATCHDOG_PONG_TIMEOUT_SECONDS = 5.0
_WATCHDOG_RESPAWN_SECONDS = 60.0


# ─── Base class ────────────────────────────────────────────────────────────


class SubprocessHotkeyBackend(ABC):
    """Base class for out-of-process native hotkey backends.

    Subclasses just provide:
    - ``platform_name`` (used in log messages)
    - ``supports_fn`` (whether the FN key is observable on this platform)

    All the subprocess plumbing, parsing, matching, restart, and shutdown
    logic lives here.
    """

    platform_name: str = "subprocess"
    supports_fn: bool = False

    # ZR-65: table-driven wire-protocol dispatch.
    #
    # Each entry is ``(prefix, handler_method_name, down_flag)``:
    #   - ``prefix``    — the wire-protocol line prefix to match
    #                     (``startswith``).
    #   - ``handler``   — name of the ``_on_*_event`` method that
    #                     handles the event. Each handler accepts a
    #                     single positional ``payload: str`` (the
    #                     substring after the prefix; empty for
    #                     exact-match events like ``FN_DOWN``).
    #   - ``down_flag`` — ``True`` / ``False`` for key/modifier/FN
    #                     events (passed as the ``down=`` kwarg), or
    #                     ``None`` for events with no up/down
    #                     semantics (``ERROR:``, ``WARN:``).
    #
    # Adding a new event type = one table entry + one ``_on_*_event``
    # handler method, not a new branch in a 100-line if/elif chain.
    #
    # ``PONG`` and ``READY`` are intentionally NOT in this table:
    #   - ``PONG`` is special-cased in ``_handle_line`` BEFORE the
    #     ``_last_event_received_at`` update so the watchdog can tell
    #     "alive and responding to PING" from "alive but ignoring PING"
    #     (see ``_on_pong_event``).
    #   - ``READY`` is special-cased AFTER the timestamp update because
    #     it is an exact-match event (no payload) and resets the
    #     per-backend restart counter (see ``_on_ready_event``).
    _WIRE_HANDLERS: ClassVar[list[tuple[str, str, bool | None]]] = [
        ("MOD_DOWN:", "_on_modifier_event", True),
        ("MOD_UP:",   "_on_modifier_event", False),
        ("KEY_DOWN:", "_on_key_event",      True),
        ("KEY_UP:",   "_on_key_event",      False),
        ("FN_DOWN",   "_on_fn_event",       True),
        ("FN_UP",     "_on_fn_event",       False),
        ("ERROR:",    "_on_error_event",    None),
        ("WARN:",     "_on_warn_event",     None),
    ]

    def __init__(self, hotkey_str: str):
        self.hotkey_str = hotkey_str
        self._parsed = parse_hotkey_spec(hotkey_str)
        self._on_release_callback: Callable[[], None] | None = None
        self._process: subprocess.Popen | None = None
        self._reader_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._ready_event = threading.Event()
        self._failed = False
        self._error_message: str | None = None
        self._binary_path: Path | None = get_native_binary_path()
        # CR-007 (IMPROVE-mode run, 2026-07-21): restart lock + instance-level
        # attempt counter. Pre-fix, ``_reader_loop`` used a LOCAL ``attempts``
        # counter and the old thread did ``continue`` after spawning a
        # replacement, causing a fork-bomb (after 5 crashes the backend could
        # have 2^5 ≈ 32 orphaned reader threads + processes). Post-fix, the
        # check-then-spawn sequence is guarded by ``_restart_lock`` so only
        # one thread performs a restart; the old thread ``return``s after
        # spawning so the new reader thread owns the new process; and
        # ``_restart_attempts`` is an instance variable reset to 0 on a
        # successful READY (so the cap is per-backend, not per-thread).
        self._restart_lock = threading.Lock()
        self._restart_attempts = 0
        # Hotkey state tracking for matching
        self._held_modifiers: set[str] = set()
        self._fn_down: bool = False
        self._main_key_down: bool = False
        self._match_lock = threading.Lock()
        # Toggle-mode flag: when True (set by HotkeyDispatcher for the main
        # dictation hotkey in toggle mode), the toggle fires on key-UP
        # (release) instead of key-down. Prevents a press-and-hold from
        # starting and then immediately stopping recording.
        self._toggle_on_keyup: bool = False
        # GAP-2/GAP-4: optional callbacks invoked from _handle_line and
        # _reader_loop. Set by _NativeBackendAdapter (in hotkeys.py) so
        # the adapter can (a) show a permission notification on ERROR
        # and (b) swap to a legacy backend when the native binary dies
        # permanently. Both default to None (no-op) so the callbacks are
        # opt-in and don't affect tests that don't care about them.
        self._on_error_callback: Callable[[str], None] | None = None
        self._on_permanent_failure_callback: Callable[[], None] | None = None
        # CR-143: optional callback for WARN: lines.
        self._on_warn_callback: Callable[[str], None] | None = None
        # G4-H-31: liveness watchdog state.  ``last_event_received_at``
        # is updated in ``_handle_line`` on every recognised wire-protocol
        # event (READY, KEY_DOWN, MOD_DOWN, PONG, etc.).  ``last_pong_received_at``
        # is updated only when a ``PONG`` line is received.  The watchdog
        # thread reads these timestamps to decide whether the binary is
        # hung and needs to be respawned.
        self._last_event_received_at: float = time.time()
        self._last_pong_received_at: float = 0.0
        # G4-H-31: ``_pong_supported`` is set to True the first time a
        # ``PONG`` line is received.  Until we've seen at least one PONG,
        # we don't know whether the binary supports the PING/PONG
        # protocol — respawning based on "no PONG" would be a false
        # positive for binaries that don't implement the protocol (which
        # would cause an infinite respawn loop on idle).  Once we've
        # seen a PONG, we know the binary supports the protocol and the
        # watchdog can safely respawn on PONG absence.
        self._pong_supported: bool = False
        # G4-H-31: watchdog thread + its own stop event.  The watchdog
        # uses a separate stop event so it can be torn down independently
        # of the reader thread (the reader's ``_stop_event`` is shared,
        # but the watchdog needs to survive long enough to be joined in
        # ``stop()`` after the reader has exited).
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop_event = threading.Event()
        # G4-H-31: optional callback for watchdog restart notifications.
        # Wired by the adapter to surface a tray notification when the
        # watchdog respawns the binary.  Defaults to None (no-op) so
        # tests that don't care about tray notifications aren't affected.
        self._on_watchdog_restart_callback: Callable[[str], None] | None = None
        # G4-H-31: callback used by ``start()`` — stashed so the watchdog
        # can call ``self.start(self._callback)`` to respawn without
        # needing the caller to pass the callback again.
        self._callback: Callable[[], None] | None = None

    # ── HotkeyBackend interface (compatible with hotkeys.HotkeyBackend) ──

    def set_on_release(self, callback: Callable[[], None] | None) -> None:
        """Set the callback for key release (push-to-talk mode)."""
        self._on_release_callback = callback

    def set_toggle_on_keyup(self, value: bool) -> None:
        """In toggle mode, fire the toggle on key-up (release) instead of
        key-down. Set True by HotkeyDispatcher for the main dictation
        hotkey so a press-and-hold cannot start-then-stop recording.
        """
        self._toggle_on_keyup = value

    def start(self, callback: Callable[[], None]) -> None:
        """Spawn the native binary and start parsing its stdout."""
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
            "[NATIVE-HOTKEY] Starting %s backend (binary=%s, hotkey=%r)",
            self.platform_name,
            self._binary_path,
            self.hotkey_str,
        )

        self._callback = callback
        self._stop_event.clear()
        self._ready_event.clear()
        self._failed = False

        # Spawn the binary
        self._spawn_process()

        # Wait for READY (or ERROR/early exit)
        if not self._ready_event.wait(timeout=READY_TIMEOUT_SECONDS):
            self._failed = True
            self._error_message = f"Timed out waiting for READY from {self.platform_name} binary"
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            self.stop()
            raise RuntimeError(self._error_message)

        if self._failed:
            msg = self._error_message or f"{self.platform_name} binary failed to start"
            raise RuntimeError(msg)

    def stop(self) -> None:
        """Stop the binary cleanly."""
        if self._stop_event.is_set():
            return
        log.info("[NATIVE-HOTKEY] Stopping %s backend", self.platform_name)
        self._stop_event.set()

        # G4-H-31: signal the watchdog to exit BEFORE we kill the
        # process so it doesn't try to write PING to a dead stdin or
        # race the reader thread's restart logic.  The watchdog is a
        # daemon thread, so even if the join times out it won't block
        # process exit.
        self._watchdog_stop_event.set()
        if self._watchdog_thread is not None and self._watchdog_thread.is_alive():
            self._watchdog_thread.join(timeout=1.0)
            self._watchdog_thread = None

        if self._process is not None:
            try:
                if self._process.poll() is None:  # still running
                    # Try graceful shutdown first
                    try:
                        if is_windows():
                            self._process.terminate()
                        else:
                            self._process.send_signal(signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass
                    try:
                        self._process.wait(timeout=2.0)
                    except subprocess.TimeoutExpired:
                        # Force kill
                        try:
                            self._process.kill()
                            self._process.wait(timeout=1.0)
                        except (subprocess.TimeoutExpired, OSError):
                            pass
            finally:
                self._process = None

        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

    def is_alive(self) -> bool:
        """Return True if the binary is running and READY was received."""
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

    # ── Platform-specific hooks ──────────────────────────────────────────

    @abstractmethod
    def _validate_platform(self) -> str | None:
        """Return an error message if the hotkey is invalid for this platform,
        or None if valid. Subclasses must implement."""
        ...

    # ── Process management ───────────────────────────────────────────────

    def _spawn_process(self) -> None:
        """Spawn the native binary with the hotkey spec as argv[1]."""
        cmd = [str(self._binary_path), self.hotkey_str]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # G4-H-31: change stdin from DEVNULL to PIPE so the
                # liveness watchdog can write ``PING\n`` to the binary's
                # stdin.  The binary is expected to respond with ``PONG\n``;
                # if it doesn't implement the PING/PONG protocol, the
                # watchdog's ``_pong_supported`` flag stays False and
                # respawn-on-PONG-absence is suppressed (see
                # ``_watchdog_loop`` for the rationale).
                stdin=subprocess.PIPE,
                # No console window on Windows
                creationflags=(
                    subprocess.CREATE_NO_WINDOW if is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW") else 0
                ),
                # Reset signal handlers in child so SIGTERM works cleanly
                start_new_session=is_macos() or is_linux(),
            )
        except OSError as exc:
            self._failed = True
            self._error_message = f"Failed to spawn {self.platform_name} binary: {exc}"
            raise RuntimeError(self._error_message) from exc

        # G4-H-31: reset the liveness timestamps so the freshly-spawned
        # binary gets a full 60s grace period before the watchdog
        # considers it hung.  ``_pong_supported`` is NOT reset here —
        # once we've seen a PONG from any spawn of this binary, we know
        # it supports the protocol and future spawns should too.
        self._last_event_received_at = time.time()
        self._last_pong_received_at = 0.0

        # Start the reader thread
        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name=f"{self.platform_name}-hotkey-reader",
            daemon=True,
        )
        self._reader_thread.start()

        # G4-H-31: start (or restart) the liveness watchdog thread.
        # The watchdog writes ``PING\n`` to the binary's stdin every
        # 30s and respawns the binary if it stops responding.  We use
        # a dedicated ``_watchdog_stop_event`` (separate from
        # ``_stop_event``) so the watchdog can be torn down
        # independently in ``stop()`` after the reader has exited.
        if self._watchdog_thread is None or not self._watchdog_thread.is_alive():
            self._watchdog_stop_event.clear()
            self._watchdog_thread = threading.Thread(
                target=self._watchdog_loop,
                name=f"{self.platform_name}-hotkey-watchdog",
                daemon=True,
            )
            self._watchdog_thread.start()

    def _reader_loop(self) -> None:
        """Read lines from the binary's stdout and dispatch.

        CR-007 (IMPROVE-mode run, 2026-07-21): the check-then-spawn sequence
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
                        # GAP-4: notify the adapter so it can swap to a
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
                # CR-007: the new spawn creates its own reader thread (in
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

        ZR-65: the dispatch is now table-driven via
        :data:`_WIRE_HANDLERS`. Adding a new event type = one table
        entry + one ``_on_*_event`` handler method, not a new branch
        in a 100-line if/elif chain.

        G4-H-31: every recognised line (except ``PONG``) updates
        ``_last_event_received_at`` so the liveness watchdog can tell
        whether the binary is producing output.  ``PONG`` is tracked
        separately via ``_last_pong_received_at`` so the watchdog can
        distinguish "binary is alive and responding to PING" from
        "binary is alive but ignoring PING" (the latter is a strong
        signal of a stuck event loop).
        """
        # G4-H-31: PONG is tracked separately from generic events so the
        # watchdog can apply the "no event AND no PONG" respawn rule.
        # PONG does NOT update ``_last_event_received_at``.
        if line == "PONG":
            self._on_pong_event()
            return

        # G4-H-31: update the general "last event received" timestamp
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

    # ── Wire-protocol event handlers (ZR-65 table-driven dispatch) ────
    #
    # Each handler accepts a single positional ``payload: str`` (the
    # substring after the matching prefix; empty for exact-match
    # events like ``FN_DOWN``) and, for key/modifier/FN events, a
    # ``down: bool`` keyword arg. ``ERROR:`` / ``WARN:`` handlers
    # omit the ``down`` kwarg (no up/down semantics).

    def _on_pong_event(self) -> None:
        """Handle a ``PONG`` wire-protocol line (G4-H-31).

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
        and resets the per-backend restart counter (CR-007) so a
        transient crash followed by recovery doesn't permanently count
        toward ``MAX_RESTART_ATTEMPTS``.
        """
        self._ready_event.set()
        self._restart_attempts = 0
        log.info("[NATIVE-HOTKEY] %s binary is READY", self.platform_name)

    def _on_error_event(self, payload: str) -> None:
        """Handle an ``ERROR:<message>`` wire-protocol line.

        Marks the backend as failed (``_failed = True``), stores the
        error message, logs at ERROR level, and unblocks ``start()``'s
        READY wait so callers see the failure promptly rather than
        timing out.

        GAP-2: also invokes ``_on_error_callback`` (if registered by
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
        """Handle a ``WARN:<message>`` wire-protocol line (CR-143).

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

    # ── Liveness watchdog (G4-H-31) ────────────────────────────────────

    def _watchdog_loop(self) -> None:
        """G4-H-31: liveness watchdog for the native hotkey binary.

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

            # G4-H-31: respawn check — "no event AND no PONG for 60s".
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
            try:
                # Stash the callback so we can re-start after stop().
                cb = self._callback
                if cb is None:
                    log.error(
                        "[NATIVE-HOTKEY] %s watchdog: cannot respawn — no callback stashed",
                        self.platform_name,
                    )
                    return
                self.stop()
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

    # ── Hotkey matching ─────────────────────────────────────────────────

    def _on_fn_event(self, payload: str = "", *, down: bool) -> None:
        """Handle FN_DOWN / FN_UP. Used by the macOS backend only.

        ZR-65: ``payload`` is accepted for dispatch-table uniformity
        but ignored — ``FN_DOWN`` / ``FN_UP`` are exact-match events
        with no payload (the prefix IS the line).
        """
        del payload  # unused — kept for dispatch-table signature parity
        with self._match_lock:
            self._fn_down = down
        self._try_match(down)

    def _on_modifier_event(self, mod_name: str, *, down: bool) -> None:
        """Handle MOD_DOWN / MOD_UP events.

        ``mod_name`` is one of: Ctrl, Shift, Alt, Cmd (macOS), Win
        (Windows), Super (Linux). We normalize all of these to lowercase
        'ctrl', 'shift', 'alt', 'cmd'.
        """
        canonical = _canonical_modifier(mod_name)
        if canonical is None:
            return
        with self._match_lock:
            if down:
                self._held_modifiers.add(canonical)
            else:
                self._held_modifiers.discard(canonical)
        # For modifier-only hotkeys (e.g. <alt> alone), the modifier
        # press itself is the trigger.
        if self._parsed and self._parsed["is_modifier_only"]:
            self._try_match(down)

    def _on_key_event(self, key_name: str, *, down: bool) -> None:
        """Handle KEY_DOWN / KEY_UP events."""
        with self._match_lock:
            if down:
                self._main_key_down = True
            else:
                self._main_key_down = False
        self._try_match(down, key_name=key_name)

    def _try_match(self, down: bool, *, key_name: str | None = None) -> None:
        """Check if the current event matches the registered hotkey.

        Matching rules:
        - ``<fn>`` alone: matches FN_DOWN/FN_UP events
        - ``<modifier>`` alone (e.g. ``<alt>``): matches MOD_DOWN/MOD_UP of
          that modifier, with no other modifiers held
        - ``<caps_lock>`` alone: matches KEY_DOWN/KEY_UP of CapsLock
        - ``<key>`` alone (e.g. ``<f2>``): matches KEY_DOWN/KEY_UP of that key
          with no modifiers held
        - ``<modifier>+<key>`` (e.g. ``<ctrl>+<alt>+v``): matches KEY_DOWN/
          KEY_UP of the main key when ALL modifiers are currently held
        """
        if self._parsed is None:
            return
        parsed = self._parsed

        # FN-only hotkey
        if parsed["is_fn_only"]:
            if down:
                self._fire_callback()
            else:
                self._fire_on_release()
            return

        # Modifier-only hotkey (e.g. <alt>, or <ctrl>+<alt>)
        if parsed["is_modifier_only"]:
            required = parsed["modifiers"]
            if "fn" in required:
                # Already handled by FN_DOWN/FN_UP above
                return
            # Convert spec tokens to canonical modifier names
            required_canonical = set()
            for token in required:
                c = _canonical_modifier_name_for_token(token)
                if c is not None:
                    required_canonical.add(c)
            if not required_canonical:
                return
            with self._match_lock:
                held = set(self._held_modifiers)
            # The hotkey is "these exact modifiers and no others"
            if held != required_canonical:
                return
            if down:
                self._fire_callback()
            else:
                self._fire_on_release()
            return

        # Regular hotkey (single key or combo)
        main_key = parsed["main_key"]
        if key_name != main_key:
            return

        required_mods = parsed["modifiers"]
        with self._match_lock:
            held_mods = set(self._held_modifiers)
            # For FN-containing combos, add 'fn' to held_mods if FN is down
            if self._fn_down:
                held_mods.add("fn")

        # All required modifiers must be held
        if not required_mods.issubset(held_mods):
            return

        # No extra modifiers should be held (unless they're required)
        # This prevents <ctrl>+v from firing when <ctrl>+<alt>+v is held
        extra = held_mods - required_mods
        if extra:
            return

        if down:
            if self._on_release_callback is not None:
                # Push-to-talk: start recording on press.
                self._fire_callback()
            elif getattr(self, "_toggle_on_keyup", False):
                # Toggle mode with toggle_on_keyup: defer the toggle to
                # key-up so holding the key cannot start-then-stop
                # recording. Do nothing here.
                pass
            else:
                # Legacy toggle (e.g. ESC, repaste): fire on press.
                self._fire_callback()
        else:
            if self._on_release_callback is not None:
                # Push-to-talk: stop recording on release.
                self._fire_on_release()
            elif getattr(self, "_toggle_on_keyup", False):
                # Toggle mode: fire the toggle exactly once on key-up.
                # Holding the key (no key-up) never toggles, so a
                # press-and-hold cannot start-then-stop recording.
                self._fire_callback()
            # else: legacy toggle-on-keydown -> nothing to do on key-up.

    def _fire_callback(self) -> None:
        """Invoke the press callback (with exception shielding)."""
        cb = getattr(self, "_callback", None)
        if cb is None:
            return
        try:
            cb()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Press callback raised in %s backend",
                self.platform_name,
            )

    def _fire_on_release(self) -> None:
        """Invoke the release callback (push-to-talk mode)."""
        if self._on_release_callback is None:
            return
        try:
            self._on_release_callback()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Release callback raised in %s backend",
                self.platform_name,
            )
