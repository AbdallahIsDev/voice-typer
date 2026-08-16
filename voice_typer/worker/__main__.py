"""Worker exe entry point — runtime-pack WebSocket server (master plan §4.4, §6.2, §7).

This is the entry point Nuitka compiles into the ``voice-typer-worker-<triple>``
onefile. It is launched by the Tauri host AFTER the runtime-pack download
completes + verifies (master plan §7.3), and stays running for the app's
lifetime (long-lived worker model — §7.3).

Lifecycle (master plan §7.3): acquire single-instance lock → run prewarm
phase ONCE → bind ``127.0.0.1:0`` (loopback-only, ADR-0020 §1) → emit
``{"event":"worker_started","port":N,"protocol":P}`` on stdout → accept
authenticated WS connections → block until graceful shutdown (shutdown
command or SIGTERM/taskkill from the host's kill-children backstop).

Auth (master plan §7.2): one-shot bearer-token check (NOT HMAC) via
:func:`voice_typer.server.ipc.auth.tokens_equal` (constant-time
``hmac.compare_digest`` comparison). Compensating controls: loopback-only
bind, OS-assigned ephemeral port, per-launch token rotation. See
:mod:`voice_typer.worker._auth` for the full contract.

Shutdown (master plan §7.2):

- **Graceful**: the slim-core sidecar sends ``{"cmd":"shutdown"}`` —
  the worker responds with ``shutdown_ack``, calls ``stop_event.set()``
  (which unblocks :func:`run_worker_server`'s ``await stop_event.wait()``),
  and closes the WS. The asyncio loop drains, ``run()`` returns
  ``EXIT_OK``, and the lock file is released in the ``finally`` block.
  The shutdown timer captures the wall-clock duration for the
  ``[SHUTDOWN] worker shutdown complete <duration>`` log line (C-LOG-2).
- **Forceful**: SIGTERM (POSIX) / ``taskkill`` (Windows) from the host's
  kill-children backstop. The SIGTERM handler also calls
  ``stop_event.set()`` (POSIX) so the same graceful code path runs; on
  Windows forceful kill skips the ``finally`` block, so the lockfile is
  left in place (stale-PID recovery on next launch mirrors
  ``_ensure_single_instance_posix``'s stale-PID path).

Module layout (E3 — wiring-only entry file, ≤ ~300 lines):

This module is wiring-only: parse args, set up logging, acquire the
single-instance lock, run the prewarm phase, delegate the WS server
lifecycle to :func:`run_worker_server`, release the lock in ``finally``.
Focused concerns live in:

- :mod:`voice_typer.worker._auth` — bearer-token handshake.
- :mod:`voice_typer.worker._single_instance` — POSIX flock + Windows
  best-effort + stale-PID recovery.
- :mod:`voice_typer.worker._ws_server` — WS server setup, connection
  handler, SIGTERM handler, prewarm phase, shutdown timer.

The heavy lifting (dispatch, heartbeat, encoding) is owned by the
slim-core sidecar. The worker only authenticates the sidecar's WS
connection, acknowledges ``heartbeat`` frames, and forwards
``transcribe_offline`` requests to the engine layer (Phase 2b — the
worker's WS dispatch table is wired up then).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

# Shared constants — same as the slim-core sidecar (ADR-0020 §1, §3, §10).
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR
from voice_typer.server.duration import format_duration

# Focused modules (E3 split — wiring-only entry file re-exports the
# public names so legacy imports like
# ``from voice_typer.worker import __main__ as worker_main`` still
# resolve, per E1 create-first wiring verification).
from voice_typer.worker._auth import (  # noqa: F401 — re-exported for back-compat
    _authenticate,
    _send_auth_failed_and_close,
)
from voice_typer.worker._single_instance import (  # noqa: F401 — re-exported for back-compat
    _ensure_worker_single_instance,
    _worker_lock_path,
    _WorkerSingleInstanceHandle,
)
from voice_typer.worker._ws_server import (  # noqa: F401 — re-exported for back-compat
    PROTOCOL_VERSION,
    _emit_worker_started,
    _force_line_buffered_stdout,
    _handle_connection,
    _install_sigterm_handler,
    _run_prewarm_phase,
    _ShutdownTimer,
    run_worker_server,
)

log = logging.getLogger("voice_typer.worker")

# Exit codes (mirrors ``voice_typer.__main__``'s EXIT_* constants).
EXIT_OK = 0
EXIT_BAD_ARGS = 2
EXIT_CRASH = 1
EXIT_DUPLICATE_INSTANCE = 3
EXIT_NO_TOKEN = 4


def run() -> int:
    """Bind the worker's WS server on an ephemeral port and run forever.

    Returns the exit code (0 on clean shutdown, non-zero on crash /
    misconfiguration). Mirrors :func:`voice_typer.server.sidecar_ws.run`'s
    shape so the two entry points read identically.

    Wiring-only: probe websockets → parse args → set VOICE_TYPER_DEBUG →
    configure canonical logging (C-LOG-1) → acquire single-instance lock
    → verify VOICE_TYPER_IPC_TOKEN → run prewarm phase → delegate to
    :func:`run_worker_server` → release lock + emit SHUTDOWN log line
    with measured duration (C-LOG-2) in ``finally``.
    """
    _force_line_buffered_stdout()

    # Local import so the module imports cleanly without ``websockets``
    # installed (a dev environment running only the slim-core sidecar
    # does not need the worker's WS dependency installed).
    try:
        import websockets  # noqa: F401 — imported for availability probe
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
        # ``VOICE_TYPER_DEBUG`` is read by many modules (e.g.
        # ``event_bus.py``, ``security/redaction.py``) — set it BEFORE
        # ``setup_logging`` so the per-module-level application picks up
        # the debug level for every ``voice_typer.*`` logger.
        os.environ["VOICE_TYPER_DEBUG"] = "1"

    # C-LOG-1: configure the canonical Voice Typer logging (file +
    # terminal formatters from ``voice_typer/server/log/formatters.py``)
    # so every ``log.*`` call follows ``YYYY-MM-DD  HH:MM:SS  LEVEL  msg``
    # (file) / ``HH:MM:SS  LEVEL  msg`` (terminal). The worker is a
    # standalone Nuitka-frozen onefile and must configure its own
    # logging — it cannot inherit the slim-core sidecar's setup. We call
    # the lower-level ``setup_logging`` (not ``logging_setup._setup_logging``)
    # because the wrapper also runs env-validation, HF_HOME setup, and
    # crash-handler install — none of which the worker needs (the
    # slim-core sidecar owns those concerns; the worker is a child).
    # ``process_name="worker"`` routes the worker to its OWN file (``worker.log``)
    # via :func:`voice_typer.server.log.get_log_file_path` — avoids the rotation race with ``voice-typer.log``.
    from voice_typer.server.config import _config_dir as _resolve_config_dir
    from voice_typer.server.log import (
        get_log_file_path as _get_log_file_path,
        setup_logging as _setup_worker_logging,
    )

    _worker_config_dir = _resolve_config_dir()
    _worker_session_id = _setup_worker_logging(
        _worker_config_dir, debug=args.debug, process_name="worker",
    )
    # C-LOG-1: the [STARTUP] logging initialized banner is the ONLY
    # sanctioned per-line occurrence of the session id — emitted once
    # per process so the session is greppable (``session=xxxxxxxx``)
    # without polluting every subsequent line. Mirrors
    # ``voice_typer/server/logging_setup.py``'s banner shape.
    _worker_json_mode = os.environ.get("VOICE_TYPER_LOG_JSON", "").lower() in ("1", "true", "yes")
    log.info(
        "[STARTUP] logging initialized: file=%s, level=%s, json=%s, debug=%s, quiet=%s, session=%s",
        _get_log_file_path(_worker_config_dir, process_name="worker"),
        logging.getLevelName(logging.getLogger("voice_typer").level),
        _worker_json_mode, args.debug, False, _worker_session_id,
    )

    # Single-instance lock (defense-in-depth; Tauri host owns authoritative).
    lock_handle = _ensure_worker_single_instance()
    if lock_handle is None:
        # Duplicate launch detected + logged — exit cleanly so the host's
        # respawn scheduler does not treat this as a crash.
        return EXIT_DUPLICATE_INSTANCE

    # Per-launch token check: the host MUST set VOICE_TYPER_IPC_TOKEN
    # before spawning the worker (ADR-0020 §3). Refuse to start without it.
    if not os.environ.get(IPC_TOKEN_ENV_VAR):
        log.error(
            "[WORKER] %s not set — the host must set this env var before "
            "spawning the worker (bearer-token auth requires it).",
            IPC_TOKEN_ENV_VAR,
        )
        lock_handle.release()
        return EXIT_NO_TOKEN

    # Prewarm phase (master plan §6.2 P-1). Runs ONCE before the WS
    # server accepts connections, so the cache is warm by the time the
    # slim-core sidecar connects + sends the first ``transcribe_offline``.
    prewarm_elapsed = _run_prewarm_phase()
    prewarm_ran = prewarm_elapsed >= 0  # always True; tracked for log clarity

    import asyncio

    stop_event = asyncio.Event()
    shutdown_timer = _ShutdownTimer()

    try:
        success = asyncio.run(
            run_worker_server(
                prewarm_elapsed=prewarm_elapsed,
                prewarm_ran=prewarm_ran,
                stop_event=stop_event,
                shutdown_timer=shutdown_timer,
            )
        )
        if not success:
            return EXIT_CRASH
        return EXIT_OK
    except KeyboardInterrupt:
        # Ctrl+C in a dev shell — treat as graceful shutdown so the
        # finally block's lock release runs (matches SIGTERM path).
        shutdown_timer.start()
        log.info("[WORKER] interrupted — shutting down")
        return EXIT_OK
    except Exception:
        log.exception("[WORKER] fatal error in run()")
        return EXIT_CRASH
    finally:
        lock_handle.release()
        log.info(
            "[SHUTDOWN] worker shutdown complete%s",
            format_duration(shutdown_timer.elapsed()),
        )


# ─── CLI ──────────────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the worker CLI args.

    Only ``--version`` and ``--debug`` are recognized. The host does NOT
    pass ``--port`` — the OS assigns an ephemeral port (mirrors the
    slim-core sidecar's ``--ws`` mode). Unknown args are ignored (the
    host may add ``--pack-version`` etc. in Phase 2b; this parser stays
    forward-compatible).
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
