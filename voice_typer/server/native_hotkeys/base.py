"""Base class for native subprocess hotkey backends.

Split out from the original ``native_hotkeys.py`` god-file in Phase 4.5
().

This module owns:

- :data:`MAX_RESTART_ATTEMPTS`, :data:`RESTART_DELAY_BASE_SECONDS`,
  :data:`READY_TIMEOUT_SECONDS` — restart/backoff constants.
- :class:`SubprocessHotkeyBackend` — ABC that handles subprocess
  plumbing, parsing, matching, restart, and shutdown for all three
  platform backends (macOS / Windows / Linux).

The class derives from :class:`voice_typer.server.hotkeys.base.HotkeyBackend`
so the native backends are true ``HotkeyBackend`` implementations: the
shared interface methods (``set_on_release`` / ``set_toggle_on_keyup`` /
``set_tray``'s no-op default) are inherited instead of mirrored, while the
subprocess plumbing, restart, and multi-spec pooling surface stay defined
here. The import direction (native_hotkeys.base → hotkeys.base) is
acyclic: the ``hotkeys`` package's submodules never import
``native_hotkeys`` at module level (only ``hotkeys.factory`` does, inside
a function).

Patch-path compatibility: tests do
``monkeypatch.setattr(native_hotkeys, "is_macos", lambda: True)``
(and is_windows / is_linux).  For the patch to take effect on calls
made from *this* submodule, the references must resolve through the
package binding at call time.  We therefore call the predicates as
``_native_hotkeys_pkg.is_macos()`` etc. (where ``_native_hotkeys_pkg``
is the ``voice_typer.server.native_hotkeys`` module itself) so the
attribute lookup happens at call time and picks up the monkeypatched
binding, rather than capturing the function object at import time.
"""

import contextlib
import os
import signal
import subprocess
import threading
import time
from abc import abstractmethod
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar

from voice_typer.server import native_hotkeys as _native_hotkeys_pkg
from voice_typer.server.hotkeys.base import HotkeyBackend
from voice_typer.server.tray_hotkey import format_hotkey_label

from .binary_path import get_native_binary_path
from .modifiers import _canonical_modifier, _canonical_modifier_name_for_token
from .spec_parser import log, parse_hotkey_spec

# ─── Constants ─────────────────────────────────────────────────────────────

MAX_RESTART_ATTEMPTS = 5
RESTART_DELAY_BASE_SECONDS = 1.0  # 1, 2, 4, 8, 16s backoff
READY_TIMEOUT_SECONDS = 5.0

# liveness watchdog constants.
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


class SubprocessHotkeyBackend(HotkeyBackend):
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
        self._binary_path: Path | None = binary_path if binary_path is not None else get_native_binary_path()
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
        # native log path: per-session diagnostic log path passed to the binary
        # via ``--log-file <path>`` (or as positional argv[2]). The
        # binary writes timestamped diagnostic lines (init steps,
        # permission checks, device opens, hook installation, warnings)
        # to this file so support bundles can include a native-side
        # diagnostic trace alongside the Python-side log. None until
        # ``_compute_native_log_path`` resolves it lazily on first
        # spawn (so tests that construct backends without spawning
        # don't create log files).
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

    # ── HotkeyBackend interface ──────────────────────────────────────
    #
    # ``set_on_release`` / ``set_toggle_on_keyup`` / ``set_tray`` are
    # INHERITED from ``HotkeyBackend`` (hotkeys/base.py) — the bodies
    # there assign the exact same instance attributes this class reads
    # (``_on_release_callback`` / ``_toggle_on_keyup``), and ``set_tray``
    # is the ABC's intentional no-op default (subprocess backends don't
    # emit tray notifications themselves; the adapter propagates the
    # tray reference to legacy backends). Only the methods with real
    # subprocess semantics (``start`` / ``stop`` / ``is_alive`` /
    # ``diagnose``) are defined below.

    # public setters for the / callbacks. Previously
    # the ``_NativeBackendAdapter`` reached into the private
    # ``_on_error_callback`` / ``_on_permanent_failure_callback`` /
    # ``_on_warn_callback`` attributes directly (with
    # ``# type: ignore[assignment]``) because no public API existed.
    # These setters expose the same wiring through the public surface so
    # the adapter doesn't need ``# type: ignore`` markers and the
    # callbacks remain an internal implementation detail of the backend.
    def set_error_callback(self, callback: Callable[[str], None]) -> None:
        """Register the ``ERROR:`` line handler invoked from ``_handle_line``."""
        self._on_error_callback = callback

    def set_permanent_failure_callback(self, callback: Callable[[], None]) -> None:
        """Register the permanent-failure handler (5 retries exhausted)."""
        self._on_permanent_failure_callback = callback

    def set_warn_callback(self, callback: Callable[[str], None]) -> None:
        """Register the ``WARN:`` line handler invoked from ``_handle_line``."""
        self._on_warn_callback = callback

    # ── Multi-spec pooling API ─────────────────────────────────────────
    #
    # The methods below let ``HotkeyDispatcher`` register additional
    # hotkey specs (ESC, repaste) against the SAME subprocess, so the
    # app spawns ONE native binary instead of three. The binary emits
    # ALL keystroke events on its stdout; the Python side runs each
    # registered matcher against the event stream and dispatches to
    # the matching role's callback. The primary spec
    # (``self.hotkey_str``) is role "dictation" and uses the existing
    # ``self._callback`` / ``self._on_release_callback`` /
    # ``self._toggle_on_keyup`` slots; extra matchers have their own
    # per-role slots stored in ``self._extra_matchers``.
    #
    # Known limitation (macOS / Windows suppression): the native binary
    # uses argv[1] (the primary dictation spec) to decide which
    # keystrokes to suppress via the CGEventTap (macOS) /
    # WH_KEYBOARD_LL hook (Windows). Extra matchers' specs are NOT
    # known to the binary, so their keystrokes are NOT suppressed.
    # On Linux this is a non-issue (evdev is read-only, no
    # suppression). On macOS / Windows, the keystroke for an extra
    # matcher (e.g. ESC, repaste combo) will reach the foreground
    # app. This is acceptable for ESC (foreground apps handle ESC
    # themselves) but may cause double-paste for repaste combos
    # (the foreground app sees the combo AND the Python-side
    # repaste fires). A future session can extend the binary's
    # command-line surface to accept multiple specs for suppression.

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

    # ── Platform-specific hooks ──────────────────────────────────────────

    @abstractmethod
    def _validate_platform(self) -> str | None:
        """Return an error message if the hotkey is invalid for this platform,
        or None if valid. Subclasses must implement."""
        ...

    # ── Native log path ─────────────────────────────────────────

    def _compute_native_log_path(self) -> Path | None:
        """Resolve the per-session diagnostic log path passed to the
        native binary via ``--log-file <path>``.

        The path is ``~/.voice-typer/logs/native-<backend>-<pid>.log``
        where ``<backend>`` is ``self.platform_name.lower()`` and
        ``<pid>`` is the current process's PID. The directory is created
        on first call (parents=True, exist_ok=True). The file itself is
        NOT created here — the native binary opens it with fopen("a").

        Returns ``None`` if the path can't be resolved (e.g. ``HOME``
        unset on POSIX, or ``USERPROFILE`` unset on Windows). In that
        case the binary is spawned without ``--log-file`` and its
        diagnostics go to stderr only (which the Python parent merges
        into stdout via STDERR=STDOUT).

        Memoised in ``self._native_log_path`` so the same path is reused
        across respawns (the binary appends, so all respawns of one
        backend land in the same file).
        """
        if self._native_log_path is not None:
            return self._native_log_path
        # Resolve the user's home directory. On POSIX this is ``$HOME``;
        # on Windows it's ``%USERPROFILE%`` (which ``Path.home`` reads
        # via ``os.path.expanduser``). Fall back to None on failure so
        # we don't crash the spawn just because logging can't be set up.
        try:
            home = Path.home()
        except (RuntimeError, OSError):
            return None
        if not str(home) or str(home) == ".":
            # ``Path.home()`` returns ``.`` when ``HOME`` is unset on
            # some POSIX systems — treat that as "no home available".
            return None
        log_dir = home / ".voice-typer" / "logs"
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Can't create the log dir (read-only home, sandbox, etc.).
            # Spawn without --log-file — the binary's stderr still goes
            # to the parent's merged stdout pipe.
            return None
        backend = (self.platform_name or "native").lower()
        path = log_dir / f"native-{backend}-{os.getpid()}.log"
        self._native_log_path = path
        return path

    # ── Process management ───────────────────────────────────────────────

    def _spawn_process(self) -> None:
        """Spawn the native binary with the hotkey spec as argv[1].

        re-verify the binary's SHA-256 against the
        manifest BEFORE every spawn. The factory's
        ``verify_native_binary_or_skip`` call at construction time
        covers the FIRST spawn, but the watchdog respawns the binary
        on liveness timeout (every ``_WATCHDOG_RESPAWN_SECONDS=60s``
        of inactivity) by calling ``stop()`` + ``start()`` →
        ``_spawn_process()`` WITHOUT going back through the factory.
        A TOCTOU window opens between the original verification and
        the respawn: an attacker swapping the binary on disk during
        that window achieves native-code execution as the user.
        Re-running the checksum at the top of ``_spawn_process`` closes
        the window for both the initial spawn and every respawn. On
        verification failure we set ``_failed=True`` and return early
        so the caller's ``_ready_event.wait(timeout=...)`` times out
        and raises a clear ``RuntimeError`` (rather than spawning an
        untrusted binary).

        TOCTOU mitigation between ``verify_native_binary_or_skip``
        and ``subprocess.Popen``. The previous code did::

            if not verify_native_binary_or_skip(self._binary_path):
                return
            subprocess.Popen([str(self._binary_path), ...])

        Between the verify and Popen, an attacker with write access to
        the binary path could swap the file on disk. The verify reads
        bytes via ``path.read_bytes()``; Popen re-resolves the path →
        execve, so a swap between the two achieves native-code
        execution as the user with a verified-clean path.

        Mitigation (POSIX only — see Windows limitation below):

        1. Open the file with ``os.open(path, O_RDONLY | O_CLOEXEC)``
           BEFORE the verify, pinning the inode at that moment.
        2. Run the existing SHA-256 verify (unchanged — uses
           ``path.read_bytes()`` so the existing tests' patch of
           ``verify_native_binary_or_skip`` continues to take effect).
        3. After verify, ``fstat`` the fd → capture
           ``(st_dev, st_ino, st_mtime_ns, st_size)``. This is the
           stat of the inode the fd pinned.
        4. Just before ``Popen``, ``os.stat`` the path and compare to
           the fstat. If the quartet differs, the file was swapped or
           modified between the os.open and the Popen — refuse to
           spawn.

        The fd does NOT need to be the same inode as what the verify
        read. If the file was swapped between os.open and verify, the
        os.stat check at step 4 catches it (path's stat ≠ fd's stat).
        If the file was swapped between verify and os.stat, the
        os.stat check catches it (path's stat ≠ fd's stat, because fd
        still pins the original inode). The only residual TOCTOU is
        between os.stat and the execve inside Popen — a sub-microsecond
        window.

        Residual TOCTOU (POSIX): an attacker who can win the race
        between os.stat and execve (e.g. via inotify + a tight rename
        loop) could still swap the binary. Closing this fully requires
        ``fexecve(fd, argv, envp)`` (exec from the fd, not the path),
        which is not exposed by ``subprocess.Popen`` and is not
        portable to older Linux kernels. The fd is closed AFTER
        ``Popen`` returns so the pinned inode stays referenced for the
        duration of the spawn (defense-in-depth: an attacker who
        unlinks+replaces the path can't reclaim the verified inode's
        disk blocks while the fd is open).

        Windows limitation: Windows does not have ``O_CLOEXEC`` and
        ``subprocess.Popen`` on Windows does not accept an open fd as
        argv[0] (it must be a path string). The TOCTOU window on
        Windows is the same as the pre- code (between verify
        and the ``CreateProcess`` call inside Popen). This is
        documented as a known limitation; the mitigation on Windows
        is the existing SHA-256 manifest gate (still in place) plus
        the assumption that the install dir is not writable by an
        untrusted user.
        """
        if self._binary_path is None:
            self._failed = True
            self._error_message = (
                f"Native {self.platform_name} key-listener binary not found. "
                f"Set VOICE_TYPER_NATIVE_BINARY or rebuild the project."
            )
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            return
        # Local import to avoid an import-cycle (binary_path.py imports
        # from .spec_parser at module load; base.py also imports from
        # .spec_parser — keeping this local avoids any chance of a
        # cycle if binary_path.py grows additional deps).
        from .binary_path import verify_native_binary_or_skip

        # on POSIX, open the file with O_RDONLY | O_CLOEXEC
        # BEFORE the verify so the fd pins the inode. The fd is held
        # across the verify and Popen so we can detect tampering via
        # a stat mismatch (path-stat vs fd-stat) just before Popen.
        # On Windows the fd-based check is skipped (see the docstring's
        # "Windows limitation" section).
        on_posix = _native_hotkeys_pkg.is_macos() or _native_hotkeys_pkg.is_linux()
        fd: int | None = None
        pinned_stat: tuple | None = None
        if on_posix:
            try:
                # ``O_CLOEXEC`` does not exist on Windows (pre-Python
                # 3.13); ``getattr`` resolves it to 0 there so tests
                # that monkeypatch a POSIX platform while running on
                # Windows take the fd-pinning path without raising
                # AttributeError.
                o_cloexec = getattr(os, "O_CLOEXEC", 0)
                fd = os.open(
                    str(self._binary_path),
                    os.O_RDONLY | o_cloexec,
                )
            except OSError as exc:
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary open failed "
                    f"during TOCTOU re-verify (path={self._binary_path}): {exc}"
                )
                log.error("[NATIVE-HOTKEY] %s", self._error_message)
                return

        if not verify_native_binary_or_skip(self._binary_path):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            self._failed = True
            self._error_message = (
                f"Native {self.platform_name} binary failed SHA-256 verification "
                f"on spawn/respawn (path={self._binary_path}). Refusing to spawn "
                f"an untrusted binary — falling back to the legacy backend."
            )
            log.error("[NATIVE-HOTKEY] %s", self._error_message)
            return

        # capture the pinned inode's stat for the pre-Popen
        # check. fstat reads metadata directly from the fd (no path
        # re-resolution), so this is the stat of the inode the fd
        # pinned at os.open time — unaffected by any later path swap.
        if fd is not None:
            try:
                st = os.fstat(fd)
                pinned_stat = (st.st_dev, st.st_ino, st.st_mtime_ns, st.st_size)
            except OSError as exc:
                with contextlib.suppress(OSError):
                    os.close(fd)
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary fstat failed "
                    f"during TOCTOU re-verify (path={self._binary_path}): {exc}"
                )
                log.error("[NATIVE-HOTKEY] %s", self._error_message)
                return

        # pre-Popen stat check. If the path's stat differs
        # from the pinned fd's stat, the file was swapped or modified
        # between os.open and now (which includes the verify window).
        # Refuse to spawn — this is the TOCTOU gate.
        if pinned_stat is not None:
            try:
                pst = os.stat(str(self._binary_path))
                path_stat = (pst.st_dev, pst.st_ino, pst.st_mtime_ns, pst.st_size)
            except OSError as exc:
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary stat failed pre-Popen (path={self._binary_path}): {exc}"
                )
                log.error("[NATIVE-HOTKEY] %s", self._error_message)
                return
            if path_stat != pinned_stat:
                if fd is not None:
                    with contextlib.suppress(OSError):
                        os.close(fd)
                self._failed = True
                self._error_message = (
                    f"Native {self.platform_name} binary stat changed "
                    f"between verify and Popen (path={self._binary_path}) — "
                    f"possible TOCTOU swap. Refusing to spawn an untrusted binary."
                )
                log.error("[NATIVE-HOTKEY] %s", self._error_message)
                return

        # Each backend instance spawns its OWN native listener process
        # here. ``HotkeyDispatcher`` creates three backends (dictation /
        # ESC / repaste), so three of these processes run at once on
        # native platforms — three reader threads, three IPC pipes, three
        # TOCTOU-verify + watchdog cycles. See the architecture note in
        # ``hotkey_dispatcher.HotkeyDispatcher`` for the planned
        # multiplexed-binary refactor that collapses all three roles into
        # a single process accepting multiple ``(role, spec)`` pairs.
        # TODO (future session): support a multi-spec command line so one
        # process can listen for several hotkeys and tag events by role.
        cmd = [str(self._binary_path), self.hotkey_str]
        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                # change stdin from DEVNULL to PIPE so the
                # liveness watchdog can write ``PING\n`` to the binary's
                # stdin.  The binary is expected to respond with ``PONG\n``;
                # if it doesn't implement the PING/PONG protocol, the
                # watchdog's ``_pong_supported`` flag stays False and
                # respawn-on-PONG-absence is suppressed (see
                # ``_watchdog_loop`` for the rationale).
                stdin=subprocess.PIPE,
                # No console window on Windows
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    if _native_hotkeys_pkg.is_windows() and hasattr(subprocess, "CREATE_NO_WINDOW")
                    else 0
                ),
                # Reset signal handlers in child so SIGTERM works cleanly
                start_new_session=on_posix,
            )
        except OSError as exc:
            self._failed = True
            self._error_message = f"Failed to spawn {self.platform_name} binary: {exc}"
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
            raise RuntimeError(self._error_message) from exc

        # close the pinned fd now that Popen has spawned.
        # The child has its own reference to the binary via the
        # execve, so the parent's fd is no longer needed. Closing
        # here avoids leaking fds across respawns.
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)

        # reset the liveness timestamps so the freshly-spawned
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

        # start (or restart) the liveness watchdog thread.
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

    # ── Wire-protocol event handlers ( table-driven dispatch) ────
    #
    # Each handler accepts a single positional ``payload: str`` (the
    # substring after the matching prefix; empty for exact-match
    # events like ``FN_DOWN``) and, for key/modifier/FN events, a
    # ``down: bool`` keyword arg. ``ERROR:`` / ``WARN:`` handlers
    # omit the ``down`` kwarg (no up/down semantics).

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

    # ── Liveness watchdog () ────────────────────────────────────

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

    # ── Hotkey matching ─────────────────────────────────────────────────

    def _on_fn_event(self, payload: str = "", *, down: bool) -> None:
        """Handle FN_DOWN / FN_UP. Used by the macOS backend only.

        ``payload`` is accepted for dispatch-table uniformity
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
                # auto-repeat filter: if the modifier is
                # already tracked as held, this MOD_DOWN is an OS
                # auto-repeat (not a fresh press). Skip the add (no-op
                # for the set) AND skip the ``_try_match`` call so a
                # modifier-only hotkey doesn't re-fire on every repeat.
                if canonical in self._held_modifiers:
                    return
                self._held_modifiers.add(canonical)
            else:
                self._held_modifiers.discard(canonical)
        # For modifier-only hotkeys (e.g. <alt> alone), the modifier
        # press itself is the trigger.
        if self._parsed and self._parsed["is_modifier_only"]:
            self._try_match(down)

    def _on_key_event(self, key_name: str, *, down: bool) -> None:
        """Handle KEY_DOWN / KEY_UP events.

        auto-repeat filter: the OS auto-repeats key-down
        events while a key is held (Windows WM_KEYDOWN repeats,
        Linux evdev emits value=2 repeats, macOS NSEvent .keyDown
        repeats). Without filtering, each repeat would re-call
        ``_try_match``, re-firing the hotkey callback on every repeat
        — for a toggle-mode hotkey that means toggling on/off every
        ~30ms while the key is held. We suppress the repeat by
        checking ``self._main_key_down`` BEFORE updating state — if
        the main key is already tracked as down, this KEY_DOWN is an
        OS auto-repeat (not a fresh press) and we return early.
        The not-down → down transition (the first KEY_DOWN after a
        KEY_UP or after init) is the only one that fires ``_try_match``.

        Known limitation: ``_main_key_down`` is a single boolean
        shared across all keys, not a per-key set. This means a
        KEY_DOWN:A followed by a KEY_DOWN:V (without KEY_UP:A) would
        suppress the V press. In practice this never happens because
        the OS only auto-repeats the most-recent key, and the wire
        protocol doesn't emit a new KEY_DOWN for a different key
        while the previous one is still held (the user must release
        first). If this assumption ever breaks, the fix is to track
        per-key down-state in a set, not a boolean.
        """
        with self._match_lock:
            if down:
                # auto-repeat filter — if the main key is
                # already tracked as down, this KEY_DOWN is an OS
                # auto-repeat. Skip the state update (no-op anyway)
                # AND skip the ``_try_match`` call so the hotkey
                # doesn't re-fire on every repeat.
                if self._main_key_down:
                    return
                self._main_key_down = True
            else:
                self._main_key_down = False
        self._try_match(down, key_name=key_name)

    def _try_match(self, down: bool, *, key_name: str | None = None) -> None:
        """Check if the current event matches any registered hotkey spec.

        The primary spec (``self._parsed``, role "dictation") is tried
        first; extra matchers (``self._extra_matchers``) are tried in
        registration order. The first matcher whose spec matches the
        current event fires its callback and short-circuits — at most
        ONE role fires per event. This prevents double-firing when two
        specs could both match (e.g. ``<ctrl>+v`` and ``<ctrl>+<shift>+v``
        would both match a Ctrl+Shift+V press if we didn't short-circuit;
        the more-specific matcher is whichever was registered first).

        Matching rules (per spec):
        - ``<fn>`` alone: matches FN_DOWN/FN_UP events
        - ``<modifier>`` alone (e.g. ``<alt>``): matches MOD_DOWN/MOD_UP of
          that modifier, with no other modifiers held
        - ``<caps_lock>`` alone: matches KEY_DOWN/KEY_UP of CapsLock
        - ``<key>`` alone (e.g. ``<f2>``): matches KEY_DOWN/KEY_UP of that key
          with no modifiers held
        - ``<modifier>+<key>`` (e.g. ``<ctrl>+<alt>+v``): matches KEY_DOWN/
          KEY_UP of the main key when ALL modifiers are currently held
        """
        # Primary matcher (role "dictation"). Uses the legacy
        # ``self._parsed`` / ``self._callback`` / etc. slots so existing
        # single-spec tests and the ``_NativeBackendAdapter`` are
        # unaffected.
        primary = {
            "role": "dictation",
            "parsed": self._parsed,
            "callback": getattr(self, "_callback", None),
            "on_release_callback": self._on_release_callback,
            "toggle_on_keyup": getattr(self, "_toggle_on_keyup", False),
        }
        if self._try_match_one(primary, down, key_name=key_name):
            return
        # Extra matchers (roles "esc", "repaste", etc.). Each is
        # independent — if the primary already fired, we skip them.
        for matcher in self._extra_matchers:
            if self._try_match_one(matcher, down, key_name=key_name):
                return

    def _try_match_one(
        self,
        matcher: dict[str, Any],
        down: bool,
        *,
        key_name: str | None = None,
    ) -> bool:
        """Try a single matcher against the current event.

        Returns True if the matcher matched (and fired its callback or
        deferred it to key-up via ``toggle_on_keyup``); False if the
        matcher did not match. The caller uses the return value to
        short-circuit further matchers (at most ONE role fires per
        event).

        ``matcher`` is a dict with keys: ``role``, ``parsed``,
        ``callback``, ``on_release_callback``, ``toggle_on_keyup``.
        """
        parsed = matcher["parsed"]
        if parsed is None:
            return False

        # FN-only hotkey
        if parsed["is_fn_only"]:
            if down:
                self._fire_callback_for(matcher)
            else:
                self._fire_on_release_for(matcher)
            return True

        # Modifier-only hotkey (e.g. <alt>, or <ctrl>+<alt>)
        if parsed["is_modifier_only"]:
            required = parsed["modifiers"]
            if "fn" in required:
                # Already handled by FN_DOWN/FN_UP above
                return False
            # Convert spec tokens to canonical modifier names
            required_canonical = set()
            for token in required:
                c = _canonical_modifier_name_for_token(token)
                if c is not None:
                    required_canonical.add(c)
            if not required_canonical:
                return False
            with self._match_lock:
                held = set(self._held_modifiers)
            # The hotkey is "these exact modifiers and no others"
            if held != required_canonical:
                return False
            if down:
                self._fire_callback_for(matcher)
            else:
                self._fire_on_release_for(matcher)
            return True

        # Regular hotkey (single key or combo)
        main_key = parsed["main_key"]
        if key_name != main_key:
            return False

        required_mods = parsed["modifiers"]
        with self._match_lock:
            held_mods = set(self._held_modifiers)
            # For FN-containing combos, add 'fn' to held_mods if FN is down
            if self._fn_down:
                held_mods.add("fn")

        # All required modifiers must be held
        if not required_mods.issubset(held_mods):
            return False

        # No extra modifiers should be held (unless they're required)
        # This prevents <ctrl>+v from firing when <ctrl>+<alt>+v is held
        extra = held_mods - required_mods
        if extra:
            return False

        toggle_on_keyup = bool(matcher.get("toggle_on_keyup", False))
        on_release = matcher.get("on_release_callback")
        if down:
            if on_release is not None:
                # Push-to-talk: start recording on press.
                self._fire_callback_for(matcher)
            elif toggle_on_keyup:
                # Toggle mode with toggle_on_keyup: defer the toggle to
                # key-up so holding the key cannot start-then-stop
                # recording. Do nothing here.
                pass
            else:
                # Legacy toggle (e.g. ESC, repaste): fire on press.
                self._fire_callback_for(matcher)
        else:
            if on_release is not None:
                # Push-to-talk: stop recording on release.
                self._fire_on_release_for(matcher)
            elif toggle_on_keyup:
                # Toggle mode: fire the toggle exactly once on key-up.
                self._fire_callback_for(matcher)
            # else: legacy toggle-on-keydown -> nothing to do on key-up.
        return True

    def _fire_callback(self) -> None:
        """Invoke the press callback (with exception shielding).

        Backwards-compat shim for the primary (dictation) role —
        delegates to :meth:`_fire_callback_for` with a primary-role
        matcher dict so the exception-shielding and log-message logic
        is shared between the primary and extra matchers.
        """
        primary = {
            "role": "dictation",
            "callback": getattr(self, "_callback", None),
        }
        self._fire_callback_for(primary)

    def _fire_callback_for(self, matcher: dict[str, Any]) -> None:
        """Invoke ``matcher["callback"]`` with exception shielding.

        Shared by the primary matcher (via :meth:`_fire_callback`) and
        the extra matchers. Logs the role so operators can attribute
        the callback invocation in the unified log.
        """
        cb = matcher.get("callback")
        if cb is None:
            return
        role = matcher.get("role", "dictation")
        try:
            cb()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Press callback raised in %s backend (role=%s)",
                self.platform_name,
                role,
            )

    def _fire_on_release(self) -> None:
        """Invoke the release callback (push-to-talk mode).

        Backwards-compat shim for the primary (dictation) role —
        delegates to :meth:`_fire_on_release_for`.
        """
        primary = {
            "role": "dictation",
            "on_release_callback": self._on_release_callback,
        }
        self._fire_on_release_for(primary)

    def _fire_on_release_for(self, matcher: dict[str, Any]) -> None:
        """Invoke ``matcher["on_release_callback"]`` with exception
        shielding. Shared by the primary and extra matchers."""
        cb = matcher.get("on_release_callback")
        if cb is None:
            return
        role = matcher.get("role", "dictation")
        try:
            cb()
        except Exception:
            log.exception(
                "[NATIVE-HOTKEY] Release callback raised in %s backend (role=%s)",
                self.platform_name,
                role,
            )
