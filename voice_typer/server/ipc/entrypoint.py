"""Process entry point for the IPC server (``python -m ...ipc_server``).

Extracted from ``ipc_server.py``. This module owns the three
module-level functions that turn a CLI invocation into a running
:class:`IPCServer`:

  - :func:`_set_process_metadata` — Windows console title / AppUserModelID,
  - :func:`parse_ipc_args` — argparse + env-var side-effects,
  - :func:`main` — the actual subprocess entry point.

These were the last ~430 lines of the pre-split ``ipc_server.py``.
Extracting them lets ``ipc_server.py`` shrink to thin wiring (factory,
re-exports, IPCServer class composition).
"""

from __future__ import annotations

import os
import sys
from types import FrameType

# Re-exported by ``ipc_server.py`` so existing
# ``from voice_typer.server.ipc_server import main`` /
# ``... import parse_ipc_args`` callers keep working unchanged.
from voice_typer.server._paths import IPC_PORT
from voice_typer.server.ipc._helpers import log
from voice_typer.server.ipc.transport import _pick_available_port

# The stdin-IPC env-var gate is owned by ``ws_lifecycle`` (it's read
# inside ``LifecycleMixin.start``). Re-imported here so
# ``parse_ipc_args`` can set it from ``--allow-stdin`` without
# duplicating the constant.
from voice_typer.server.ipc.ws_lifecycle import _STDIN_IPC_ENV_VAR


def _set_process_metadata() -> None:
    """Set process-level metadata (console title, AppUserModelID, etc.).

    BRAND-METADATA: On Windows the Python backend appears as a generic
    pythonw.exe in Task Manager.  We call the platform helper to set
    the console title and AppUserModelID, which improves the process
    identity wherever the OS supports it.
    """
    from voice_typer.server.branding import APP_NAME
    from voice_typer.server.platform_utils import _set_windows_process_metadata

    _set_windows_process_metadata(APP_NAME)


def parse_ipc_args() -> tuple[int | None, bool]:
    """Parse the IPC server CLI args ( extraction from ``main()``).

    Returns ``(port, ws_mode)`` where ``port`` is the ``--port N`` value
    (or ``None`` for stdin/stdout mode) and ``ws_mode`` is True when
    ``--ws`` was passed (Tauri sidecar WebSocket mode).

    Side effects:
        - Sets ``VOICE_TYPER_DEBUG=1`` env var when ``--debug`` is passed
          (must be set BEFORE ``_setup_logging()`` is called so the
          debug level is honoured by the log config).
        - Sets ``TAURI_SIDECAR=1`` env var when ``--ws`` is passed so
          downstream gates (heartbeat watchdog, single-instance mutex)
          know to defer to the Tauri host.

    Exits:
        - ``--help`` / ``--version`` exit via argparse (exit code 0).
        - Invalid combos (``--ws`` + ``--port``) or out-of-range ports
          exit via ``sys.exit(EXIT_BAD_ARGS)``.

    The args are parsed BEFORE the single-instance lock is acquired so
    ``--version`` works even when another instance is already running
    (mirrors ``voice_typer.__main__``).
    """
    import argparse
    import importlib.metadata
    import os

    from voice_typer.__main__ import EXIT_BAD_ARGS

    try:
        _pkg_version = importlib.metadata.version("voice-typer")
    except Exception:
        _pkg_version = "1.0.0"

    parser = argparse.ArgumentParser(
        prog="voice_typer.server.ipc_server",
        description="Voice Typer IPC server (spawned by Electron)",
        add_help=False,  # we add --help manually to avoid conflict with app
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help="TCP port to listen on (1..65535). If omitted, uses stdin/stdout IPC.",
    )
    parser.add_argument(
        "--ws",
        action="store_true",
        default=False,
        help=(
            "ADR-0020: run as a Tauri sidecar. Binds a localhost WebSocket "
            "server on an OS-assigned ephemeral port (127.0.0.1:0), prints "
            'a single {"event":"server_started","port":N} JSON line to '
            "stdout, then accepts WS connections authenticated by the "
            "VOICE_TYPER_IPC_TOKEN env var. Mutually exclusive with --port."
        ),
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
        "--allow-stdin",
        action="store_true",
        default=False,
        help=(
            "Explicitly enable the unauthenticated stdin/stdout "
            "IPC listener (sets VOICE_TYPER_ALLOW_STDIN_IPC=1). The "
            "stdin listener is gated off by default for security: "
            "stdin commands bypass the VOICE_TYPER_IPC_TOKEN handshake. "
            "Use this flag for development and testing only."
        ),
    )
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    if args.debug:
        os.environ["VOICE_TYPER_DEBUG"] = "1"
    # --allow-stdin sets the env var that ``IPCServer.start()``
    # checks before spawning the stdin listener. The env var (not the
    # CLI flag) is the canonical gate so direct-API users (tests,
    # ``IPCServer(app); server.start()``) can opt in without going
    # through ``main()`` / argparse.
    if args.allow_stdin:
        os.environ[_STDIN_IPC_ENV_VAR] = "1"
        log.info(
            "[IPC] --allow-stdin: %s=1 set (stdin listener will be spawned if _tcp_mode is False)",
            _STDIN_IPC_ENV_VAR,
        )
    port = args.port
    ws_mode = args.ws
    # ADR-0020 §2: --ws and --port are mutually exclusive. --ws binds
    # an OS-assigned ephemeral port and reports it via stdout; --port
    # binds a fixed port for the legacy Electron TCP path.
    if ws_mode and port is not None:
        print("--ws and --port are mutually exclusive", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)
    if port is not None and not (1 <= port <= 65535):
        print(f"Invalid port: {port} (must be 1..65535)", file=sys.stderr)
        sys.exit(EXIT_BAD_ARGS)
    # ADR-0020 §2 + §10: when running as a Tauri sidecar, set the
    # TAURI_SIDECAR=1 env var so downstream gates (heartbeat watchdog,
    # VoiceTyperSingleInstance mutex) know to disable themselves. The
    # Tauri host's single-instance plugin + supervisor replace
    # them. The env var is set here (rather than required to be set by
    # the host) so a `python -m voice_typer.server.ipc_server --ws`
    # invocation from a terminal also gets the right behavior.
    if ws_mode:
        os.environ["TAURI_SIDECAR"] = "1"
        log.info("[IPC] --ws mode enabled (TAURI_SIDECAR=1 env set)")
    return port, ws_mode


def main() -> None:
    """Create a ``VoiceTyperApp``, wrap it in an ``IPCServer``, and block.

    Designed as the subprocess entry point for an Electron frontend::

        python -m voice_typer.server.ipc_server          # stdin/stdout
        python -m voice_typer.server.ipc_server --port N  # TCP

    In TCP mode, stdout/stderr are NOT piped (Electron uses
    ``stdio: "inherit"``) so there is no pipe-backpressure issue
    during the heavy torch import.  Push events reach the frontend
    via TCP, and the terminal sees normal log output.
    """
    #  (privacy): tighten the process umask to ``0o077`` (owner-only)
    # at process startup so ALL files created by the sidecar — including
    # the history DB ``-wal`` / ``-shm`` sidecar files that SQLite creates
    # lazily on the first WAL-mode write — are owner-only by default.
    # Previously the chmod loop in ``history_db_internals/schema.py``
    # ran BEFORE the sidecar files existed, so they inherited the parent
    # shell's umask (typically ``0o022`` → files created ``0o644`` =
    # world-readable on multi-user POSIX). ``check_wal_mode`` re-runs
    # the chmod loop after PRAGMA WAL mode is set (closing the
    # creation-time race for the writer's first connection), but a
    # defense-in-depth umask at process startup covers ALL future
    # sidecar recreations (e.g. after a ``wal_checkpoint(TRUNCATE)``
    # drops the sidecars and they get recreated on the next write).
    # Done BEFORE any other subsystem init so every file the sidecar
    # creates benefits. Best-effort — ``os.umask`` always succeeds on
    # POSIX and is a no-op on Windows (which uses ACLs instead).
    if os.name == "posix":
        os.umask(0o077)

    # BRAND-METADATA: set process metadata early, before any subsystem
    # init, so the OS sees the correct identity from the start.
    _set_process_metadata()

    # import the standardized exit-code constant.
    # EXIT_BAD_ARGS is now used inside ``parse_ipc_args()`` (extracted
    # ); main() needs only EXIT_CRASH for the construction-failure
    # and app.start()-failure paths. Previously EXIT_CRASH was imported
    # but unused and the crash path called sys.exit with a raw literal.
    from voice_typer.__main__ import EXIT_CRASH
    # the ``sys.modules[_CANONICAL] = sys.modules["__main__"]``
    # registration hack that used to live at module level has been
    # removed.  See the  comment block above the mixin
    # imports for the rationale.

    # RACE-018: Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    try:
        import faulthandler

        faulthandler.enable()
        # Optional: register SIGUSR1 for on-demand thread dumps (POSIX only)
        import signal

        if hasattr(signal, "SIGUSR1"):
            # ``faulthandler.dump_traceback_later`` has the
            # signature ``(timeout: float, repeat: bool = False, ...)
            # -> None`` and does NOT match the ``signal.signal`` handler
            # protocol ``(signum: int, frame: FrameType | None) -> Any``.
            # Passing it directly would crash with TypeError the first
            # time the signal fires (missing ``timeout`` positional).
            # Wrap it in a closure that calls ``dump_traceback_later``
            # with a 1-second delay — the documented use case for
            # on-demand thread dumps from SIGUSR1.
            def _on_sigusr1(_signum: int, _frame: FrameType | None) -> None:
                faulthandler.dump_traceback_later(timeout=1.0)

            signal.signal(signal.SIGUSR1, _on_sigusr1)
    except (AttributeError, ValueError, OSError, RuntimeError):
        # Not available on all platforms (Windows lacks SIGUSR1;
        # ValueError/OSError if the signal can't be registered; RuntimeError
        # if faulthandler is already enabled). Previously a broad
        # ``except Exception: pass`` — narrowed so an unexpected import-time
        # bug surfaces instead of being silently swallowed.
        pass

    # parse arguments BEFORE acquiring the single-instance
    # lock, so ``--version`` works even when another instance is running
    # (mirrors voice_typer.__main__, which parses args before app.main()).
    # the argparse setup + validation + env-var side effects are
    # extracted to ``parse_ipc_args()`` above so ``main()`` no longer
    # mixes CLI parsing with app construction / transport dispatch.

    from voice_typer.server.app import VoiceTyperApp, _ensure_single_instance, _setup_logging

    port, ws_mode = parse_ipc_args()

    _setup_logging()

    # single-instance lock is acquired AFTER args are parsed
    # but BEFORE app construction (which stores the mutex handle).  The
    # lock is still taken for real launches (both standalone and --port IPC).
    #
    # ADR-0020 §12: under the Tauri sidecar path (TAURI_SIDECAR=1), the
    # Tauri host's `tauri-plugin-single-instance` plugin already enforces
    # single-instance via the OS's native mechanism (Win32 named mutex on
    # Windows, NSApplication activation on macOS, lockfile on Linux). The
    # Python-side `VoiceTyperSingleInstance` Win32 mutex (app.py:2086)
    # would double-lock on Windows and block the second-instance focus
    # path, so we skip it under Tauri.
    _tauri_sidecar = os.environ.get("TAURI_SIDECAR") == "1"
    if _tauri_sidecar:
        log.info("[IPC] TAURI_SIDECAR=1 — skipping Python-side single-instance mutex (Tauri host owns it)")
        _single_instance_mutex = None
    else:
        _single_instance_mutex = _ensure_single_instance(silent=True)

    # the os._exit monkey-patch that printed a stack trace
    # on every shutdown has been removed.

    try:
        app = VoiceTyperApp()
    except Exception:
        # Under pythonw.exe, _setup_logging() redirects stdout/stderr to
        # devnull, so ANY exception here is invisible to the user — they
        # only see "Python process exited: 1" + the misleading "Only one
        # instance" dialog from Electron.  Log the full traceback to both
        # the app's log file and a dedicated diagnostic file so debugging
        # is possible.
        log.exception("[FATAL] VoiceTyperApp() construction failed")
        #  the io.StringIO → traceback → _redact_text →
        # _secure_atomic_write → /tmp-fallback pattern is encapsulated
        # in ``ipc_diagnostics.write_startup_diagnostic`` so the
        # construction-failure and app.start()-failure paths share a
        # single source of truth (the two inline blocks had already
        # drifted once — 's overwrite-vs-append fix was applied to
        # only one). The helper preserves the historical
        # "Voice Typer startup failed at <time>" header so
        # ``tests/test_ipc_server_main_diagnostics.py``'s substring
        # assertions keep passing.
        from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

        write_startup_diagnostic("construction")
        # use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)

    # PLAT-HLEAK: store the mutex handle on the app instance so
    # quit() can CloseHandle it on shutdown
    app._mutex_handle = _single_instance_mutex

    # use the providers.build_ipc_server composition
    # root instead of constructing IPCServer directly.  Behavior is
    # identical today (build_ipc_server just calls IPCServer(app));
    # the factory exists so future wiring (logging, metrics, feature
    # flags, an alternate service implementation) lives in one place
    # rather than being threaded through this entry point.
    from voice_typer.server.providers import build_ipc_server

    server = build_ipc_server(app)
    #  ``main()`` NEVER uses the
    # unauthenticated stdin/stdout IPC path. The three launch modes are:
    #   1. ``--port N``        — explicit TCP, Electron connects over the
    #                            network with a session token.
    #   2. ``--ws``            — Tauri sidecar WebSocket (also
    #                            token-authenticated via env var).
    #   3. standalone (neither flag) — auto-pick a port, set a session
    #                            token, start TCP, and launch the
    #                            Electron frontend to connect back. The
    #                            Python process is the parent; stdin is
    #                            the user's terminal (or /dev/null when
    #                            launched by a desktop launcher).
    # In ALL three modes the stdin listener would be an unauthenticated
    # command channel: on Linux TIOCSTI injection is possible, and on
    # every platform an accidental paste of JSON into the terminal
    # triggers unintended IPC commands. We therefore set
    # ``_tcp_mode = True`` UNCONDITIONALLY before ``server.start()`` so
    # ``start()`` skips spawning the stdin listener thread. The standalone
    # path below still calls ``start_tcp()`` (the bound-socket overload)
    # after ``start()`` to begin accepting connections.
    server._tcp_mode = True
    server.start()
    # ADR-0020 §2: --ws mode starts the WebSocket sidecar server instead
    # of the TCP server. The WS server binds 127.0.0.1:0, prints the
    # `server_started` JSON to stdout, and accepts authenticated WS
    # connections from the Tauri Rust host. The TCP / standalone paths
    # below are unchanged for the Electron fallback.
    if ws_mode:
        from voice_typer.server import sidecar_ws

        log.info("[IPC] starting Tauri sidecar WebSocket server (sidecar_ws.run)")
        # ADR-0020 round-2 fix: do NOT call server.push({"type": "ready"})
        # here — in WS mode, server.push writes to the TCP _tcp_client
        # which is None (no TCP server started). The `ready` event is
        # emitted by sidecar_ws._handle_connection() via event_bus.publish
        # AFTER the first WS client authenticates, so the Tauri host
        # receives it over the WS connection.
        # sidecar_ws.run() blocks until the asyncio loop is cancelled
        # (SIGTERM from the host's kill_children backstop). Returns an
        # exit code; we propagate it.
        _ws_exit = sidecar_ws.run(server)
        if _ws_exit != 0:
            log.warning("[IPC] sidecar_ws.run exited with code %d", _ws_exit)
        sys.exit(_ws_exit)
    elif port is not None:
        server.start_tcp(port)
        log.info("[IPC] TCP mode on port %d — Electron should connect here", port)
    else:
        # P1-1.2: Standalone mode (no --port). The user ran VoiceTyper
        # from a terminal.  Auto-pick an available port, start the TCP
        # server, generate a session token, and launch the Electron
        # frontend so it connects back to us over TCP instead of
        # spawning its own Python backend.
        from voice_typer.server import electron_launcher

        standalone_port, standalone_sock = _pick_available_port(IPC_PORT)

        # Generate the session token and set it as an env var BEFORE
        # starting the TCP listener.  The _accept_tcp daemon thread reads
        # VOICE_TYPER_IPC_TOKEN at the top of its function; if we set it
        # after start_tcp(), the thread can read the env var before we
        # assign it, leaving expected_token empty and the connection
        # unauthenticated.
        ipc_token = electron_launcher.generate_session_token()
        os.environ["VOICE_TYPER_IPC_TOKEN"] = ipc_token

        # pass the BOUND socket through to start_tcp so there's
        # no race window between _pick_available_port's probe and the
        # real bind() in _accept_tcp.  The kernel guarantees no other
        # local process can claim the port between probe and listen.
        server.start_tcp((standalone_port, standalone_sock))
        log.info(
            "[IPC] standalone TCP mode on port %d — Electron will connect here",
            standalone_port,
        )

        # Launch Electron as a subprocess.  Pass the port + token via
        # env vars so Electron's main process detects them and connects
        # directly instead of spawning its own Python backend.
        electron_pid = electron_launcher.launch_electron_frontend(
            standalone_port,
            ipc_token,
        )
        if electron_pid is not None:
            # Track PID on the app instance so quit() can terminate
            # the subprocess during shutdown (P1-1.3).
            app._electron_pid = electron_pid
            # Also register with tray_window so its existing cleanup
            # path (which calls get_electron_pid()) still works.
            try:
                from voice_typer.server.tray_window import set_electron_pid

                set_electron_pid(electron_pid)
            except Exception:
                log.debug("[IPC] could not register Electron PID with tray_window", exc_info=True)
            log.info(
                "[STARTUP] Standalone mode — launched Electron (PID=%s) on port %d",
                electron_pid,
                standalone_port,
            )
        else:
            log.error(
                "[STARTUP] Standalone mode — failed to launch Electron; backend is running on port %d with no UI",
                standalone_port,
            )

    # Tell the frontend we're ready — Electron defers window creation until this.
    server.push({"type": "ready"})
    log.info("[IPC] entering app.start() (tray event loop)")
    try:
        app.start()  # blocks (tray event loop)
        # QUIT-CLEAN-001: keep shutdown quiet.  Only ``[QUIT] Quitting
        # Voice Typer...`` (from app.quit_app) and ``[SHUTDOWN]
        # Shutdown complete, exiting`` (from app.quit) should be at
        # INFO during a normal quit; everything else is internal
        # bookkeeping that the user doesn't need to see.
        log.debug("[IPC] Shutdown complete")
    except SystemExit as _se:
        # sys.exit() or os._exit() called from within pystray or runtime.
        # Catch it so we can log the cause, then re-raise.
        log.debug("[IPC] app.start() exited via sys.exit(%s)", _se.code)
        raise
    except Exception:
        #  (fix): was `except BaseException` which also caught
        # KeyboardInterrupt and GeneratorExit. Now catches only Exception
        # so Ctrl+C and SystemExit propagate normally to the finally block.
        log.exception("[FATAL] app.start() raised — shutting down")
        #  route through the shared diagnostic helper
        # (same as the construction-failure path above). The helper
        # preserves the historical
        # "\n--- app.start() failed at <time> ---\n" header and the
        #  overwrite-vs-append semantics so repeated relaunch
        # crashes don't grow ``startup-error.log`` without bound.
        from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

        write_startup_diagnostic("app.start()")
        # use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)
    else:
        pass
    finally:
        pass
    # Keep mutex alive by referencing it until exit
    _ = _single_instance_mutex


if __name__ == "__main__":
    main()
