"""Native hotkey backend — core class with shared state + public API.

MicrophoneDeviceWatcher inherits from platform-specific mixins defined
in leaf modules.  The class body here holds only the shared concerns:
state, lifecycle, public API, and the abstract platform hook.
"""

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
    """Base class for out-of-process native hotkey backends.

    Derives from :class:`voice_typer.server.hotkeys.base.HotkeyBackend`
    so a native backend can be used anywhere a ``HotkeyBackend`` is
    expected (the historical mirror of the interface is gone — the
    shared methods come from the ABC now).

    Subclasses just provide:
    - ``platform_name`` (used in log messages)
    - ``supports_fn`` (whether the FN key is observable on this platform)

    All the subprocess plumbing, parsing, matching, restart, and shutdown
    logic lives here.
    """

    platform_name: str = "subprocess"
    supports_fn: bool = False

    # table-driven wire-protocol dispatch.
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
        ("MOD_UP:", "_on_modifier_event", False),
        ("KEY_DOWN:", "_on_key_event", True),
        ("KEY_UP:", "_on_key_event", False),
        ("FN_DOWN", "_on_fn_event", True),
        ("FN_UP", "_on_fn_event", False),
        # VERSION reporter: ``VERSION:<x.y.z>`` is emitted by the binary
        # immediately after READY so the Python side can record the
        # binary's reported wire-protocol version. The factory compares
        # this against the manifest's ``version`` field and warns on
        # mismatch (see _on_version_event + factory.create_native_backend).
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
        # subprocess and event stream. Each entry is a dict with keys:
        # ``role`` (str), ``parsed`` (parsed spec dict | None),
        # ``callback`` (Callable | None), ``on_release_callback``
        # (Callable | None), ``toggle_on_keyup`` (bool).
        # The primary spec (``self.hotkey_str`` / ``self._parsed`` /
        # ``self._callback``) is role "dictation" and is always tried
        # first; extra matchers are tried in registration order.
        # This lets ``HotkeyDispatcher`` collapse the dictation / ESC /
        # repaste backends into ONE subprocess on platforms where the
        # native binary emits ALL keystroke events (Linux evdev,
        # Windows LL hook, macOS CGEventTap) — the binary takes the
        # dictation spec as argv[1] for its own validation /
        # suppression decisions, and the Python side dispatches each
        # wire event to the matching role's callback.
        self._extra_matchers: list[dict[str, Any]] = []
        # Delegation flag: when True, ``start()`` skips spawning a
        # subprocess and ``stop()`` / ``is_alive()`` short-circuit.
        # Set by ``HotkeyDispatcher`` on the ESC / repaste backends
        # when a shared backend's extra matcher handles their role.
        # The backend object still exists (for API compatibility with
        # code that expects a separate backend per role, and so the
        # test suite's ``_esc_backend is mock_backend`` assertions
        # keep working), but it does NOT spawn its own subprocess.
        self._delegated: bool = False
        # accept an explicit ``binary_path`` from the
        # factory so the SHA-256-verified binary discovered by
        # ``create_native_backend`` is the one we actually spawn —
        # previously the constructor re-discovered via
        # ``get_native_binary_path()``, discarding the factory's
        # verification result and re-running the search. ``binary_path``
        # is optional so existing tests that construct backends without
        # a factory still work; when it's None we fall back to
        # ``get_native_binary_path()`` to preserve the pre-fix behavior.
        # The factory (owned by another agent) needs a follow-up to
        # pass its verified ``binary`` here: see  cross-file
        # note in the assignment.
        self._binary_path: Path | None = (
            binary_path if binary_path is not None else _native_hotkeys_pkg.base.get_native_binary_path()
        )
        # restart lock + instance-level
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
        # VERSION reporter: the binary's reported wire-protocol version (set by
        # ``_on_version_event`` when a ``VERSION:<x.y.z>`` line arrives).
        # ``None`` until the binary emits VERSION — the factory uses
        # this to compare against the manifest's ``version`` field and
        # warn on mismatch.
        self._binary_version: str | None = None
        # VERSION reporter: the manifest's expected version for this binary's
        # filename, stashed by the factory so ``_on_version_event`` can
        # compare it against the binary's runtime-reported VERSION.
        # ``None`` means "manifest didn't have a version for this
        # binary" (e.g. tests that bypass the factory) — the
        # comparison is skipped in that case.
        self._expected_version: str | None = None
        # native log path: per-session diagnostic log path passed to the
        # binary via ``--log-file <path>`` (appended to the spawn command
        # in ``_spawn_process``; the binary also accepts it as positional
        # argv[2]). The binary writes timestamped diagnostic lines (init
        # steps, permission checks, device opens, hook installation,
        # warnings) to this file so support bundles can include a
        # native-side diagnostic trace alongside the Python-side log.
        # None until ``_compute_native_log_path`` resolves it lazily on
        # first spawn (so tests that construct backends without spawning
        # don't create log files); once resolved it is memoised so
        # watchdog respawns append to the same file.
        self._native_log_path: Path | None = None
        # Toggle-mode flag: when True (set by HotkeyDispatcher for the main
        # dictation hotkey in toggle mode), the toggle fires on key-UP
        # (release) instead of key-down. Prevents a press-and-hold from
        # starting and then immediately stopping recording.
        self._toggle_on_keyup: bool = False
        # optional callbacks invoked from _handle_line and
        # _reader_loop. Set by _NativeBackendAdapter (in hotkeys.py) so
        # the adapter can (a) show a permission notification on ERROR
        # and (b) swap to a legacy backend when the native binary dies
        # permanently. Both default to None (no-op) so the callbacks are
        # opt-in and don't affect tests that don't care about them.
        self._on_error_callback: Callable[[str], None] | None = None
        self._on_permanent_failure_callback: Callable[[], None] | None = None
        # optional callback for WARN: lines.
        self._on_warn_callback: Callable[[str], None] | None = None
        # liveness watchdog state.  ``last_event_received_at``
        # is updated in ``_handle_line`` on every recognised wire-protocol
        # event (READY, KEY_DOWN, MOD_DOWN, PONG, etc.).  ``last_pong_received_at``
        # is updated only when a ``PONG`` line is received.  The watchdog
        # thread reads these timestamps to decide whether the binary is
        # hung and needs to be respawned.
        self._last_event_received_at: float = time.time()
        self._last_pong_received_at: float = 0.0
        # ``_pong_supported`` is set to True the first time a
        # ``PONG`` line is received.  Until we've seen at least one PONG,
        # we don't know whether the binary supports the PING/PONG
        # protocol — respawning based on "no PONG" would be a false
        # positive for binaries that don't implement the protocol (which
        # would cause an infinite respawn loop on idle).  Once we've
        # seen a PONG, we know the binary supports the protocol and the
        # watchdog can safely respawn on PONG absence.
        self._pong_supported: bool = False
        # watchdog thread + its own stop event.  The watchdog
        # uses a separate stop event so it can be torn down independently
        # of the reader thread (the reader's ``_stop_event`` is shared,
        # but the watchdog needs to survive long enough to be joined in
        # ``stop()`` after the reader has exited).
        self._watchdog_thread: threading.Thread | None = None
        self._watchdog_stop_event = threading.Event()
        # optional callback for watchdog restart notifications.
        # Wired by the adapter to surface a tray notification when the
        # watchdog respawns the binary.  Defaults to None (no-op) so
        # tests that don't care about tray notifications aren't affected.
        self._on_watchdog_restart_callback: Callable[[str], None] | None = None
        # callback used by ``start()`` — stashed so the watchdog
        # can call ``self.start(self._callback)`` to respawn without
        # needing the caller to pass the callback again.
        self._callback: Callable[[], None] | None = None
        # one-shot latch set by
        # ``stop(shutdown=True)`` (the default for external callers —
        # main thread / app shutdown).  Once True, ``_watchdog_loop``
        # skips its respawn path (``stop()`` + ``start(cb)``) and exits
        # instead of resurrecting the binary.  Without this latch, the
        # watchdog's respawn sequence races a concurrent main-thread
        # ``stop()``: the main-thread ``stop()`` hits the idempotency
        # guard (``if self._stop_event.is_set(): return``) and is a
        # no-op, so the watchdog's subsequent ``start(cb)`` resurrects
        # an orphaned native binary that holds the keyboard hook
        # (Windows) or evdev FDs (Linux) after the app has shut down.
        # The flag is intentionally NEVER cleared — once shutdown is
        # requested, the binary must never be respawned by the
        # watchdog.  ``stop(shutdown=False)`` (used by the watchdog's
        # own cleanup and by ``start()``'s error-recovery paths) does
        # NOT set this flag, so legitimate respawns still work.
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
        """Register an additional ``(role, spec)`` pair to be matched
        against the same event stream as the primary spec.

        The ``role`` is an opaque string (e.g. ``"esc"``,
        ``"repaste"``) used to address the matcher in
        :meth:`set_role_callback` / :meth:`set_role_on_release` /
        :meth:`set_role_toggle_on_keyup`. Calling this with a
        ``role`` that already exists replaces the existing matcher's
        parsed spec (callbacks are preserved).

        Raises ``ValueError`` if ``spec`` cannot be parsed — the
        caller is expected to validate the spec before registering
        (mirroring the primary spec's parse-at-construction pattern).
        """
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
        """Spawn the native binary and start parsing its stdout.

        Delegated backends (``_delegated = True``) skip the spawn
        entirely — they exist only for API compatibility with code
        that expects a separate backend per role. The actual matching
        for a delegated role happens via an extra matcher on the
        shared (dictation) backend. The callback is still recorded
        so :meth:`is_alive` and the watchdog's respawn path see a
        "started" backend, but it is NEVER invoked (the shared
        backend's extra matcher handles dispatch).
        """
        if self._delegated:
            self._callback = callback
            # Mark as ready so is_alive() reports True and callers
            # (e.g. ``register_esc``'s post-start checks) see a
            # healthy backend. We do NOT set ``_process`` — the
            # delegated backend owns no subprocess.
            self._ready_event.set()
            # DEBUG: the dispatcher's "[HOTKEY] ... pooled into shared
            # backend" INFO line already covers this event.
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
        # return early (without spawning) when SHA-256 verification
        # fails OR the binary path is None. Check immediately so the
        # operator sees the precise error message ("binary failed
        # SHA-256 verification") instead of waiting for the
        # ``_ready_event`` timeout below to overwrite it with the
        # generic "Timed out waiting for READY" message.
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
        """Stop the binary cleanly.

        ``shutdown=True`` (the
        default for external callers — main thread / app shutdown)
        latches ``_shutdown_requested=True`` BEFORE the idempotency
        guard so a concurrent main-thread ``stop()`` that sees
        ``_stop_event`` already set (by the watchdog's own
        ``stop(shutdown=False)`` cleanup) still records the shutdown
        request.  Pre-fix, the main-thread ``stop()`` was a no-op
        (idempotency guard returned early) and never recorded the
        shutdown, so the watchdog's subsequent ``start(cb)`` resurrected
        an orphaned native binary (holding the keyboard hook on Windows
        or evdev FDs on Linux) after the app had begun shutdown.

        ``shutdown=False`` is used by the watchdog's own respawn
        cleanup (which is a teardown-for-restart, NOT an app shutdown)
        and by ``start()``'s internal error-recovery cleanup — neither
        should latch the shutdown flag, otherwise the watchdog could
        never respawn (its own cleanup would latch the flag) and a
        failed ``start()`` would permanently disable the watchdog.
        """
        if shutdown:
            # latch BEFORE the idempotency guard so a concurrent
            # main-thread stop() that sees ``_stop_event`` already set
            # (by the watchdog's cleanup stop) still records the
            # shutdown request.  Once True, this flag is NEVER cleared
            # — see the ``__init__`` comment for the rationale.
            self._shutdown_requested = True
        if self._stop_event.is_set():
            return
        # Delegated backends own no subprocess / reader / watchdog —
        # short-circuit after setting ``_stop_event`` so ``is_alive``
        # reports False and callers see a clean shutdown. The check
        # runs BEFORE the "Stopping" log: with ESC + repaste pooled
        # onto the shared dictation backend, three delegated stop()
        # calls otherwise logged ``Stopping <platform> backend`` three
        # times for ONE real teardown.
        if self._delegated:
            self._stop_event.set()
            return
        log.info("[NATIVE-HOTKEY] Stopping %s backend", self.platform_name)
        self._stop_event.set()

        # signal the watchdog to exit BEFORE we kill the
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
                        if _native_hotkeys_pkg.is_windows():
                            self._process.terminate()
                        else:
                            self._process.send_signal(signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        # Process already gone between poll() and signal —
                        # normal teardown race; log for the device-diagnosability trail.
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
                            # be stuck in an uninterruptible state. Log it.
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
        """Return True if the backend is ready and not stopped.

        For delegated backends (no subprocess), this returns True iff
        ``_ready_event`` is set and ``_stop_event`` is not — mirroring
        the contract callers expect from ``register_esc`` /
        ``register_repaste``'s post-start health check.
        """
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
