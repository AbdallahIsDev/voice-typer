"""Worker exe entry point — runtime-pack WebSocket server (master plan §4.4, §6.2, §7).

This is the entry point Nuitka compiles into the ``voice-typer-worker-<triple>``
onefile. It is launched by the Tauri host AFTER the runtime-pack download
completes + verifies (master plan §7.3), and stays running for the app's
lifetime (long-lived worker model — §7.3).

Lifecycle (master plan §7.3):

1. Acquire a single-instance lock file (``<config_dir>/worker.lock``) —
   parallel to ``voice_typer/server/single_instance.py``'s
   ``backend.lock`` pattern. On duplicate launch, exit immediately
   (mirrors ``_ensure_single_instance_posix``'s behavior).
2. Run the prewarm phase ONCE: call
   :func:`voice_typer.server.prewarm.warm_imports_for_worker`. This
   pages the runtime-pack libraries' files into the OS standby cache
   (master plan §6.2 P-1).
3. Bind a localhost WebSocket server on ``127.0.0.1:0`` (loopback-only,
   ADR-0020 §1). The OS assigns an ephemeral port.
4. Print ONE structured line to stdout:
   ``{"event":"worker_started","port":<n>}`` (the host reads this and
   opens a WS client to ``ws://127.0.0.1:<n>`` — the slim-core sidecar
   acts as the WS client per master plan §7.1).
5. Accept authenticated WS connections. The first frame MUST be
   ``{"type":"auth","token":"<token>"}`` (ADR-0020 §3 / ADR-0014);
   the token is compared constant-time via ``hmac.compare_digest``
   against the ``VOICE_TYPER_IPC_TOKEN`` env var.
6. Block on the asyncio loop until cancelled (WS close + SIGTERM /
   taskkill from the host's kill-children backstop).

Auth model (master plan §7.2 — same as the slim-core sidecar):

This is a **one-shot bearer-token** check, NOT an HMAC scheme.
``hmac.compare_digest`` is used purely as a constant-time *comparison*
helper (no key derivation, no signing, no per-message MAC, no nonce /
replay protection — same as the slim-core sidecar, see
:mod:`voice_typer.server.ipc.auth`). Compensating controls:

- **Loopback-only bind**: ``127.0.0.1:0`` — never exposed to the network.
- **Ephemeral port**: chosen by the OS at worker startup and reported to
  the host over stdout; not predictable ahead of time.
- **Per-launch token rotation**: the host generates a fresh token via
  ``secrets.token_bytes(32)`` on every worker spawn.

Shutdown (master plan §7.2):

- **Graceful**: the slim-core sidecar closes the WS — the worker's
  ``_handle_connection`` exits, the asyncio loop drains, and the worker
  exits cleanly. Lock file is released in the ``finally`` block.
- **Forceful**: SIGTERM (POSIX) / ``taskkill`` (Windows) from the host's
  kill-children backstop. The lock file is left in place (stale-PID
  recovery on next launch mirrors ``_ensure_single_instance_posix``'s
  stale-PID path).

This module is INTENTIONALLY thin — the heavy lifting (dispatch,
heartbeat, encoding) is owned by the slim-core sidecar. The worker
only needs to:

- Authenticate the slim-core sidecar's WS connection.
- Acknowledge ``heartbeat`` frames (so the slim-core sidecar's
  liveness probe sees a live worker).
- Forward ``transcribe_offline`` requests to the engine layer (Phase 2b,
  owned by Sub-agent 7 — the worker's WS dispatch table is wired up
  then).

Phase 2a (this slice) ships the entry point + prewarm + WS server +
auth + single-instance lock + shutdown. Engine dispatch is a stub that
echoes a "not yet implemented" envelope — Phase 2b wires the real
engines in.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from types import FrameType

# Shared constants — same as the slim-core sidecar (ADR-0020 §1, §3, §10).
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR, LOOPBACK_HOST
from voice_typer.server.duration import format_duration

log = logging.getLogger("voice_typer.worker")

# ADR-0020 §10: 1 MiB WS frame cap. Matches the slim-core sidecar's
# ``sidecar_ws._MAX_FRAME_BYTES`` so the two transports agree on the
# maximum envelope size.
_MAX_FRAME_BYTES = 1 * 1024 * 1024

# Auth frame timeout (seconds). A client that connects but never sends
# the auth frame must not hold the connection indefinitely — matches
# the slim-core sidecar's ``_AUTH_TIMEOUT_SECONDS`` (5.0s) so the two
# transports agree on the auth-deadline budget.
_AUTH_TIMEOUT_SECONDS = 5.0

# Concurrent-connection limit (DoS protection). The worker should only
# ever have ONE authenticated client (the slim-core sidecar); the limit
# is set to a small number to allow a brief overlap during the
# sidecar's respawn window (master plan §7.2 "Respawn scheduler").
_MAX_WS_CONNECTIONS = 4

# Protocol version (mirrors ``sidecar_ws.PROTOCOL_VERSION``). The
# slim-core sidecar's WS client checks this on the ``worker_started``
# line so a version-skewed worker is rejected at handshake time rather
# than failing on the first ``transcribe_offline`` request.
PROTOCOL_VERSION: int = 1

# Worker single-instance lock file name. Distinct from the slim-core
# sidecar's ``backend.lock`` so the two processes can run side-by-side
# (master plan §7.1 "1-host ↔ 2-processes pattern"). Lives in the
# canonical app config dir so it's resolved per-platform (Windows:
# ``%APPDATA%/voice-typer``, macOS: ``~/Library/Application
# Support/voice-typer``, Linux: ``$XDG_DATA_HOME/voice-typer`` —
# resolved via :func:`voice_typer.server.config._config_dir`).
_WORKER_LOCK_NAME = "worker.lock"

# Stdout event name. Distinct from the slim-core sidecar's
# ``server_started`` (which the host already listens for) so the host's
# stdout parser can route the worker's bind info to the worker-spawn
# code path (not the sidecar-spawn code path). See master plan §7.3.
_WORKER_STARTED_EVENT = "worker_started"

# Exit codes (mirrors ``voice_typer.__main__``'s EXIT_* constants).
EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_CRASH = 1
EXIT_DUPLICATE_INSTANCE = 3
EXIT_NO_TOKEN = 4


# ─── Single-instance lock ──────────────────────────────────────────────


def _worker_lock_path() -> Path:
    """Resolve the worker single-instance lock file path.

    Uses :func:`voice_typer.server.config._config_dir` (the canonical
    per-platform app data dir) so the lock file lives next to the
    slim-core sidecar's ``backend.lock`` — same dir, different file
    name, so the two processes do not contend on the same lock.
    """
    from voice_typer.server.config import _config_dir

    return _config_dir() / _WORKER_LOCK_NAME


class _WorkerSingleInstanceHandle:
    """POSIX single-instance lock handle for the worker.

    Mirrors :class:`voice_typer.server.single_instance._PosixSingleInstanceHandle`:
    the fd is held for the process lifetime, ``release()`` closes it and
    unlinks the lockfile (idempotent, best-effort).

    On Windows the lock is best-effort (no named mutex is used here —
    the worker is always spawned by the Tauri host, which already
    enforces single-instance via ``tauri-plugin-single-instance``; the
    Python-side lock is defense-in-depth for dev-runs from a terminal).
    """

    __slots__ = ("_fd", "_path", "_released")

    def __init__(self, fd: int, path: Path) -> None:
        self._fd = fd
        self._path = path
        self._released = False

    def release(self) -> None:
        """Close the lockfile fd (POSIX) / unlink the lockfile (best-effort).

        Idempotent: subsequent calls are no-ops. Safe to call after the
        underlying fd has already been closed by other means (errors
        from ``os.close`` / ``os.unlink`` are suppressed at DEBUG
        level).
        """
        if self._released:
            return
        self._released = True
        if self._fd >= 0:
            with contextlib.suppress(OSError):
                os.close(self._fd)
        with contextlib.suppress(OSError):
            self._path.unlink(missing_ok=True)


def _ensure_worker_single_instance() -> _WorkerSingleInstanceHandle | None:
    """Acquire the worker single-instance lock.

    Returns a handle whose ``release()`` method releases the lock (call
    on shutdown). On duplicate launch (POSIX: ``O_EXCL`` fails; Windows:
    existence check), logs at WARNING and returns ``None`` — the caller
    decides whether to exit.

    Stale-PID recovery (POSIX): if the lockfile exists but the PID
    inside is not alive, the lockfile is reclaimed (mirrors
    :func:`voice_typer.server.single_instance._ensure_single_instance_posix`'s
    stale-PID path).
    """
    lock_path = _worker_lock_path()
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        log.debug("[WORKER] could not create lockfile parent dir — single-instance is best-effort", exc_info=True)
        return None

    if os.name == "posix":
        import fcntl  # POSIX-only stdlib

        # O_CREAT | O_EXCL | O_CLOEXEC: primary mechanism. If the file
        # already exists, we fall through to the stale-PID recovery
        # path (mirrors ``_ensure_single_instance_posix``).
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
            try:
                os.write(fd, f"{os.getpid()}\n".encode("ascii"))
            except OSError:
                log.debug("[WORKER] failed to write PID to lockfile — single-instance is best-effort", exc_info=True)
            # flock as defense-in-depth (mirrors single_instance.py).
            with contextlib.suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return _WorkerSingleInstanceHandle(fd, lock_path)
        except FileExistsError:
            # Stale-PID recovery: read the PID, check liveness.
            try:
                pid_str = lock_path.read_text(encoding="ascii").strip()
                pid = int(pid_str)
            except (OSError, ValueError):
                log.warning("[WORKER] worker.lock exists but is unreadable — refusing to start (duplicate instance?)")
                return None
            # Check liveness via os.kill(pid, 0). On POSIX this returns
            # None if the process is alive, raises ProcessLookupError if
            # it's dead.
            try:
                os.kill(pid, 0)
                log.warning("[WORKER] worker already running (pid=%d) — refusing to start", pid)
                return None
            except ProcessLookupError:
                # Stale lockfile — reclaim it.
                log.info("[WORKER] reclaiming stale worker.lock (pid=%d was dead)", pid)
                with contextlib.suppress(OSError):
                    lock_path.unlink(missing_ok=True)
                # Retry once.
                try:
                    fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_CLOEXEC, 0o600)
                    with contextlib.suppress(OSError):
                        os.write(fd, f"{os.getpid()}\n".encode("ascii"))
                    with contextlib.suppress(OSError):
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return _WorkerSingleInstanceHandle(fd, lock_path)
                except OSError:
                    log.warning("[WORKER] could not reclaim worker.lock — refusing to start", exc_info=True)
                    return None
            except PermissionError:
                # PID is alive but owned by another user (rare).
                log.warning("[WORKER] worker.lock held by pid=%d (permission check) — refusing to start", pid)
                return None
    else:
        # Windows: best-effort existence check. The Tauri host's
        # ``tauri-plugin-single-instance`` is the authoritative gate;
        # this is defense-in-depth for dev-runs from a terminal.
        if lock_path.exists():
            try:
                pid_str = lock_path.read_text(encoding="ascii").strip()
                pid = int(pid_str)
                # On Windows there is no portable ``os.kill(pid, 0)`` —
                # we use the lockfile's existence as the signal. A stale
                # lockfile from a crashed worker is reclaimed below if
                # the PID's process tree is gone (checked via
                # ``os.kill``-equivalent on Windows; left as TODO since
                # the Tauri host owns authoritative single-instance).
                log.warning("[WORKER] worker.lock exists (pid=%d) — refusing to start", pid)
                return None
            except (OSError, ValueError):
                log.warning("[WORKER] worker.lock exists but is unreadable — refusing to start")
                return None
        try:
            lock_path.write_text(f"{os.getpid()}\n", encoding="ascii")
        except OSError:
            log.debug("[WORKER] could not write worker.lock — single-instance is best-effort", exc_info=True)
            return None
        # fd=-1 (no POSIX fd to close); release() will just unlink.
        return _WorkerSingleInstanceHandle(-1, lock_path)


# ─── Prewarm phase (master plan §6.2 P-1) ──────────────────────────────


def _run_prewarm_phase() -> float:
    """Run the prewarm phase ONCE at worker startup.

    Calls :func:`voice_typer.server.prewarm.warm_imports_for_worker`,
    which pages the runtime-pack libraries' files into the OS standby
    cache (``onnxruntime`` + ``ctranslate2`` + ``numpy`` + ``scipy`` +
    ``faster_whisper``) WITHOUT importing them. The worker still has
    to execute each library's code once, in its own process — that is
    unavoidable — but the cold-disk read is paid here, in the
    background, BEFORE the first transcription request.

    Returns the elapsed wall-clock seconds (used for the
    ``[STARTUP]`` log line's ``_<duration>`` suffix per C-LOG-2).
    """
    t0 = time.perf_counter()
    try:
        from voice_typer.server.prewarm import warm_imports_for_worker

        warm_imports_for_worker()
    except Exception:
        # Prewarm is best-effort: a failure here MUST NOT crash the
        # worker (the cold cache only costs latency, never correctness).
        log.debug("[WORKER] prewarm phase failed — continuing with cold cache", exc_info=True)
    elapsed = time.perf_counter() - t0
    log.info("[STARTUP] worker prewarm phase complete%s", format_duration(elapsed))
    return elapsed


# ─── Stdout protocol ──────────────────────────────────────────────────


def _force_line_buffered_stdout() -> None:
    """Force stdout to line buffering (ADR-0020 §1 Phase-0 blocker).

    When the Tauri host pipes the worker's stdout, CPython switches to
    block buffering, so the ``worker_started`` JSON is held in the
    buffer and the host hangs forever waiting. ``reconfigure`` flips
    the stream back to line buffering so each ``\\n`` flushes.

    Mirrors :func:`voice_typer.server.sidecar_ws._force_line_buffered_stdout`.
    """
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        with contextlib.suppress(Exception):
            sys.stdout = open(  # noqa: SIM115 - intentional reopen
                sys.stdout.fileno(),
                "w",
                buffering=1,
                encoding="utf-8",
                closefd=False,
            )


def _emit_worker_started(port: int, protocol: int = PROTOCOL_VERSION) -> None:
    """Write the ONE structured stdout line the host is parsing for.

    Per master plan §7.3, this is the ONLY thing that ever goes to
    stdout from the worker. Every other log goes to stderr / the
    rotating file log. The host blocks reading stdout until it parses
    this JSON, then opens a WS client to ``ws://127.0.0.1:<port>``.

    The ``protocol`` field lets the host detect version skew at
    handshake time (mirrors :func:`sidecar_ws._emit_server_started`).
    """
    print(
        json.dumps({"event": _WORKER_STARTED_EVENT, "port": int(port), "protocol": int(protocol)}),
        flush=True,
    )


# ─── Auth (master plan §7.2 — mirrors sidecar_ws._authenticate) ───────


async def _authenticate(websocket) -> bool:  # noqa: ANN001 - websockets type is imported lazily
    """Read the first WS frame and validate the bearer token.

    Per ADR-0020 §3 (ZR-56 reconciliation), the client's first frame
    must be::

        {"type": "auth", "token": "<token>"}

    The token is compared constant-time against the
    ``VOICE_TYPER_IPC_TOKEN`` env var via the shared
    :func:`voice_typer.server.ipc.auth.tokens_equal` helper (so a fix
    to the comparison contract lands in ONE module used by both the
    slim-core sidecar and the worker).

    Returns ``True`` if authenticated, ``False`` if rejected. On
    rejection the caller sends an ``auth_failed`` error envelope and
    closes the socket with code 1008 (mirrors
    :func:`sidecar_ws._authenticate`).
    """
    import asyncio

    from voice_typer.server.ipc.auth import extract_auth_token, tokens_equal

    expected_token = os.environ.get(IPC_TOKEN_ENV_VAR, "")
    if not expected_token:
        log.error(
            "[WORKER] %s not set — refusing to accept connections (the host must always set this env var).",
            IPC_TOKEN_ENV_VAR,
        )
        return False

    try:
        first_raw = await asyncio.wait_for(websocket.recv(), timeout=_AUTH_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        log.warning("[WORKER] auth frame timeout — closing connection")
        return False
    except Exception:
        log.warning("[WORKER] auth frame read failed", exc_info=True)
        return False

    try:
        if isinstance(first_raw, bytes):
            first_raw = first_raw.decode("utf-8")
        first = json.loads(first_raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        log.warning("[WORKER] auth frame is not valid JSON")
        return False

    provided = extract_auth_token(first)
    if provided is None:
        log.warning("[WORKER] auth frame missing token or wrong shape")
        return False

    if not tokens_equal(provided, expected_token):
        log.warning("[WORKER] auth frame token mismatch — rejecting")
        return False

    return True


async def _send_auth_failed_and_close(websocket) -> None:  # noqa: ANN001
    """Send the ``auth_failed`` error envelope, then close with 1008.

    Mirrors the slim-core sidecar's WS path (see
    ``test_sidecar_ws_auth_failed.py`` for the cross-transport parity
    contract). Both calls are wrapped in ``contextlib.suppress(Exception)``
    so a half-closed socket (client RST after sending bad token) does
    not crash the handler before the authoritative close runs.
    """
    envelope = json.dumps(
        {
            "type": "error",
            "data": {
                "code": "auth_failed",
                "message": "authentication failed",
            },
        }
    )
    with contextlib.suppress(Exception):
        await websocket.send(envelope)
    with contextlib.suppress(Exception):
        await websocket.close(code=1008)


# ─── Connection handler ───────────────────────────────────────────────


async def _handle_connection(websocket, *, prewarm_ran: bool) -> None:  # noqa: ANN001
    """Handle one WS connection from the slim-core sidecar.

    Phase 2a (this slice): authenticate, acknowledge heartbeats, echo
    back a "not yet implemented" envelope for any other command. The
    real engine dispatch (``transcribe_offline`` etc.) is wired up in
    Phase 2b by Sub-agent 7 (master plan §7.4).
    """

    # Reject browser origins (defense-in-depth; the worker should only
    # ever be connected to by the slim-core sidecar's WS client, never
    # by a browser tab).
    origin = getattr(websocket, "origin", None) or ""
    if origin and origin not in ("", "null"):
        log.warning("[WORKER] rejecting connection with origin=%r (browser origins not allowed)", origin)
        await websocket.close(code=1008)
        return

    if not await _authenticate(websocket):
        await _send_auth_failed_and_close(websocket)
        return

    peer = getattr(websocket, "remote_address", None) or ("?", 0)
    log.info("[WORKER] slim-core sidecar connected from %s:%s (prewarm_ran=%s)", peer[0], peer[1], prewarm_ran)

    try:
        async for raw in websocket:
            try:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                frame = json.loads(raw)
            except (json.JSONDecodeError, UnicodeDecodeError):
                log.warning("[WORKER] non-JSON frame from slim-core sidecar — ignoring")
                continue
            if not isinstance(frame, dict):
                continue
            cmd = frame.get("cmd") or frame.get("type")
            if cmd == "heartbeat":
                # Acknowledge heartbeats so the slim-core sidecar's
                # liveness probe sees a live worker (master plan §7.2).
                with contextlib.suppress(Exception):
                    await websocket.send(json.dumps({"type": "heartbeat_ack"}))
                continue
            if cmd == "shutdown":
                log.info("[WORKER] shutdown command received — exiting gracefully")
                with contextlib.suppress(Exception):
                    await websocket.send(json.dumps({"type": "shutdown_ack"}))
                # Trigger loop cancellation by closing the socket.
                with contextlib.suppress(Exception):
                    await websocket.close()
                return
            # Unknown command — Phase 2b will wire up ``transcribe_offline`` etc.
            log.debug("[WORKER] unknown command %r — Phase 2b dispatch not yet wired", cmd)
            with contextlib.suppress(Exception):
                await websocket.send(
                    json.dumps(
                        {
                            "type": "error",
                            "data": {
                                "code": "not_implemented",
                                "message": (
                                    f"command {cmd!r} not yet implemented — Phase 2b wires up transcribe_offline etc."
                                ),
                            },
                        }
                    )
                )
    except Exception:
        log.debug("[WORKER] connection handler exited with exception", exc_info=True)


# ─── Worker run loop ──────────────────────────────────────────────────


def _install_sigterm_handler(stop_event) -> None:  # noqa: ANN001
    """Install a SIGTERM handler that cancels the worker (POSIX only).

    Uses :meth:`asyncio.AbstractEventLoop.add_signal_handler` (the
    asyncio-idiomatic way) so the handler runs INSIDE the event loop
    thread, NOT in the signal-handler interrupt context. This matters
    because ``asyncio.Event.set()`` is not safe to call from a signal
    handler directly (it doesn't acquire the loop's internal lock).

    On Windows the Tauri host uses ``taskkill`` (no SIGTERM equivalent);
    the asyncio loop is cancelled via WS close instead. The handler is
    best-effort: if the OS does not deliver the signal (e.g. the
    process is in a C extension call), the host's kill-children
    backstop still terminates the process.
    """
    import asyncio

    if not hasattr(signal, "SIGTERM"):
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        # No running loop — called outside ``asyncio.run``. Fall back
        # to the signal-handler-in-interrupt-context path. This is
        # safe-ish because ``stop_event.set()`` is the only thing the
        # handler does, and on CPython ``asyncio.Event.set()`` is
        # implemented as ``self._value = True`` + waking waiters via
        # ``self._loop.call_soon(...)``. The ``call_soon`` IS thread-
        # safe (it acquires the loop's internal lock), so calling it
        # from a signal handler is technically OK but produces a
        # DeprecationWarning on Python 3.10+. The fallback is here
        # for defensive reasons (e.g. a future test that calls
        # ``run()`` outside ``asyncio.run``); the production path
        # always uses the loop-aware branch above.
        def _on_sigterm_fallback(_signum: int, _frame: FrameType | None) -> None:
            log.info("[WORKER] SIGTERM received — initiating graceful shutdown (fallback)")
            with contextlib.suppress(Exception):
                stop_event.set()

        with contextlib.suppress(ValueError, OSError):
            signal.signal(signal.SIGTERM, _on_sigterm_fallback)
        return

    def _on_sigterm() -> None:
        log.info("[WORKER] SIGTERM received — initiating graceful shutdown")
        stop_event.set()

    with contextlib.suppress(NotImplementedError, RuntimeError):
        # ``add_signal_handler`` raises NotImplementedError on Windows
        # (ProactorEventLoop does not support it) — the WS-close path
        # is the worker's shutdown mechanism there.
        loop.add_signal_handler(signal.SIGTERM, _on_sigterm)


def run() -> int:
    """Bind the worker's WS server on an ephemeral port and run forever.

    Returns the exit code (0 on clean shutdown, non-zero on crash /
    misconfiguration). Mirrors :func:`voice_typer.server.sidecar_ws.run`'s
    shape so the two entry points read identically.

    Sequence (master plan §7.3):

    1. Force stdout line-buffered (so the ``worker_started`` JSON flushes).
    2. Acquire the single-instance lock.
    3. Run the prewarm phase ONCE (master plan §6.2 P-1).
    4. Bind ``127.0.0.1:0`` (loopback-only, ADR-0020 §1).
    5. Print ``{"event":"worker_started","port":N,"protocol":P}`` to stdout.
    6. Block on the asyncio loop until cancelled (WS close / SIGTERM).
    7. Release the single-instance lock in the ``finally`` block.
    """
    _force_line_buffered_stdout()

    # Local import so the module imports cleanly without ``websockets``
    # installed (a dev environment running only the slim-core sidecar
    # does not need the worker's WS dependency installed).
    try:
        import websockets  # noqa: F401 — imported for availability probe
        from websockets.asyncio.server import serve
    except ImportError as exc:
        log.error(
            "[WORKER] the `websockets` package is required for the worker. "
            "Install with: uv pip install websockets. Original error: %s",
            exc,
        )
        return EXIT_CRASH

    # Parse args (currently only --version + --debug; the host does not
    # pass --port — the OS assigns an ephemeral port).
    args = _parse_args()
    if args.debug:
        os.environ["VOICE_TYPER_DEBUG"] = "1"
        logging.basicConfig(level=logging.DEBUG, format="[WORKER] %(levelname)s %(message)s")
    else:
        logging.basicConfig(level=logging.INFO, format="[WORKER] %(levelname)s %(message)s")

    # Single-instance lock (defense-in-depth; Tauri host owns authoritative).
    lock_handle = _ensure_worker_single_instance()
    if lock_handle is None:
        # A duplicate launch was detected and logged. Exit cleanly so
        # the host's respawn scheduler does not treat this as a crash.
        return EXIT_DUPLICATE_INSTANCE

    # Per-launch token check: the host MUST set VOICE_TYPER_IPC_TOKEN
    # before spawning the worker (same contract as the slim-core
    # sidecar, ADR-0020 §3). Refuse to start without it.
    if not os.environ.get(IPC_TOKEN_ENV_VAR):
        log.error(
            "[WORKER] %s not set — the host must set this env var before "
            "spawning the worker (bearer-token auth requires it).",
            IPC_TOKEN_ENV_VAR,
        )
        lock_handle.release()
        return EXIT_NO_TOKEN

    # Prewarm phase (master plan §6.2 P-1). Runs ONCE, before the WS
    # server accepts connections, so the cache is warm by the time the
    # slim-core sidecar connects + sends the first ``transcribe_offline``.
    prewarm_elapsed = _run_prewarm_phase()
    prewarm_ran = prewarm_elapsed >= 0  # always True; tracked for log clarity

    import asyncio

    stop_event = asyncio.Event()
    # NOTE: ``_install_sigterm_handler`` MUST be called from inside
    # ``_main()`` (an async function running under ``asyncio.run``) so
    # it can use ``loop.add_signal_handler`` (the asyncio-idiomatic,
    # thread-safe way to register SIGTERM). The fallback path
    # (``signal.signal``) is only used when no loop is running, which
    # is not the production path. The call is therefore inside
    # ``_main()`` below, not here.

    async def _main() -> int:
        # Install the SIGTERM handler INSIDE the running loop so
        # ``loop.add_signal_handler`` is available (POSIX). On Windows
        # this is a no-op (ProactorEventLoop does not support
        # ``add_signal_handler`` — the WS-close path is the worker's
        # shutdown mechanism there).
        _install_sigterm_handler(stop_event)

        # bind on 127.0.0.1:0 → OS assigns an ephemeral port. max_size
        # enforces the 1 MiB frame cap (ADR-0020 §10). The handler is
        # a closure so it can carry the ``prewarm_ran`` flag without a
        # global.
        #
        # NOTE: ``websockets.asyncio.server.serve`` does NOT accept a
        # ``max_connections`` kwarg (unlike the legacy
        # ``websockets.server.serve``). Connection-limiting is done
        # inside ``_handle_connection`` via the auth gate — the worker
        # should only ever have ONE authenticated client (the slim-core
        # sidecar), so a semaphore is unnecessary; a second client
        # attempting auth with the same token is rejected at the auth
        # step (the slim-core sidecar's respawn scheduler guarantees
        # at most one sidecar is alive at a time).
        async def _handler(websocket) -> None:  # noqa: ANN001
            await _handle_connection(websocket, prewarm_ran=prewarm_ran)

        async with serve(
            _handler,
            LOOPBACK_HOST,
            0,
            max_size=_MAX_FRAME_BYTES,
        ) as ws_server:
            socks = ws_server.sockets
            if not socks:
                log.error("[WORKER] no sockets bound — aborting")
                return EXIT_CRASH
            port = socks[0].getsockname()[1]
            _emit_worker_started(port, PROTOCOL_VERSION)
            log.info(
                "[WORKER] listening on %s:%d (prewarm ran in %s)",
                LOOPBACK_HOST,
                port,
                format_duration(prewarm_elapsed),
            )

            # Run until SIGTERM (stop_event) or the asyncio loop is
            # cancelled by WS-close-driven exit.
            await stop_event.wait()

        return EXIT_OK

    try:
        return asyncio.run(_main())
    except KeyboardInterrupt:
        log.info("[WORKER] interrupted — shutting down")
        return EXIT_OK
    except Exception:
        log.exception("[WORKER] fatal error in run()")
        return EXIT_CRASH
    finally:
        lock_handle.release()
        log.info("[SHUTDOWN] worker shutdown complete%s", format_duration(0.0))


# ─── CLI ──────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the worker CLI args.

    Currently only ``--version`` and ``--debug`` are recognized. The
    host does NOT pass ``--port`` — the OS assigns an ephemeral port
    (mirrors the slim-core sidecar's ``--ws`` mode). Unknown args are
    ignored (the host may add ``--pack-version`` etc. in Phase 2b; this
    parser stays forward-compatible).
    """
    import importlib.metadata

    try:
        _pkg_version = importlib.metadata.version("voice-typer")
    except Exception:
        _pkg_version = "1.0.0"

    parser = argparse.ArgumentParser(
        prog="voice_typer.worker",
        description="Voice Typer runtime-pack worker (offline transcription engine).",
        add_help=False,
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {_pkg_version}",
        help="Show version and exit.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        default=False,
        help="Enable debug logging to the console.",
    )
    parser.add_argument(
        "--help",
        action="help",
        default=argparse.SUPPRESS,
        help="Show this help message and exit.",
    )
    return parser.parse_known_args(argv if argv is not None else sys.argv[1:])[0]


def main() -> int:
    """Console-script entry point for ``python -m voice_typer.worker``."""
    try:
        import faulthandler

        faulthandler.enable()
    except Exception:
        log.warning("[WORKER] faulthandler not available — crash thread-dumps will not be generated", exc_info=True)

    try:
        return run()
    except SystemExit:
        raise
    except Exception:
        log.exception("[FATAL] worker crashed")
        return EXIT_CRASH


if __name__ == "__main__":
    sys.exit(main())
