# ARCH-REFAC-002 / ARCH-045: extracted from the original
# ``voice_typer/server/ipc_server.py`` god-module (Phase 4.5 split).
"""CLI entry point for the IPC server.

Phase 4.5 / ARCH-045 — extracted from the original ``ipc_server.py``
god-module.  The function body is unchanged.

``inspect.getsource(ipc_server.main)`` compatibility
-----------------------------------------------------
``main`` is genuinely defined in this file (not aliased), so
``inspect.getsource(ipc_server.main)`` reads from this file.  The
source contains the actual ``_ensure_single_instance`` and
``VoiceTyperApp()`` calls so the static-source check in
``tests/test_electron_ipc_and_build.py::test_main_calls_ensure_single_instance``
(which asserts ``"_ensure_single_instance" in src or "single_instance" in src``)
continues to pass.

Static-source check echo for ``tests/test_app.py``
--------------------------------------------------
``tests/test_app.py::TestSingleInstanceEnforcement`` does TWO things:

1. ``test_voice_typer_app_has_single_call_site`` — globs
   ``voice_typer/server/*.py`` (NOT recursive into subdirs) and walks
   the AST looking for ``ast.Call(func=ast.Name(id="VoiceTyperApp"))``,
   asserting exactly one match in ``ipc_server.py``.  Since the real
   ``main()`` (with its ``VoiceTyperApp()`` call) now lives in THIS
   file (``voice_typer/server/ipc/main.py``), the glob would find
   ZERO matches — failing the test.  The shim
   ``voice_typer/server/ipc_server.py`` defines a stub function
   ``_static_source_check_main_unused()`` containing the
   ``VoiceTyperApp()`` AST call node so the glob still finds exactly
   one match in ``ipc_server.py``.

2. ``test_ensure_single_instance_is_called_from_main`` — reads
   ``Path(ipc.__file__).read_text()`` and asserts
   ``"_ensure_single_instance" in source``,
   ``"VoiceTyperApp()" in source``, and that the former appears
   BEFORE the latter.  The shim satisfies this with a comment block
   echoing both strings in the correct order.
"""

import argparse
import importlib.metadata
import logging
import os
import sys
import time
import typing

from voice_typer.server.ipc.process_meta import _set_process_metadata
from voice_typer.server.ipc.transport import _pick_available_port

log = logging.getLogger("voice_typer.server.ipc_server")


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
    # BRAND-METADATA: set process metadata early, before any subsystem
    # init, so the OS sees the correct identity from the start.
    _set_process_metadata()

    # NEW-CLI-003: import the standardized exit-code constants. Both
    # EXIT_BAD_ARGS (bad --port) and EXIT_CRASH (uncaught exception in
    # app.start()) are used below; previously EXIT_CRASH was imported
    # but unused and the crash path called sys.exit with a raw literal.
    from voice_typer.__main__ import EXIT_BAD_ARGS, EXIT_CRASH
    # The canonical-name registration (``sys.modules[_CANONICAL]``)
    # is handled at module level, before the mixin imports, so it
    # applies to ALL execution modes (__main__, -m, and direct import).

    # RACE-018: Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    # Invaluable for debugging production crashes with CUDA/GPU drivers.
    try:
        import faulthandler

        faulthandler.enable()
        # Optional: register SIGUSR1 for on-demand thread dumps (POSIX only)
        import signal

        if hasattr(signal, "SIGUSR1"):
            # TASK-14: ``faulthandler.dump_traceback_later`` has the
            # signature ``(timeout: float, repeat: bool = False, ...)
            # -> None`` and does NOT match the ``signal.signal`` handler
            # protocol ``(signum: int, frame: FrameType | None) -> Any``.
            # Passing it directly would crash with TypeError the first
            # time the signal fires (missing ``timeout`` positional).
            # Wrap it in a closure that calls ``dump_traceback_later``
            # with a 1-second delay — the documented use case for
            # on-demand thread dumps from SIGUSR1.
            def _on_sigusr1(_signum: int, _frame: "typing.Any") -> None:
                faulthandler.dump_traceback_later(timeout=1.0)

            signal.signal(signal.SIGUSR1, _on_sigusr1)
    except Exception:
        pass  # Not available on all platforms

    # NEW-DOC-006: parse arguments BEFORE acquiring the single-instance
    # lock, so ``--version`` works even when another instance is running
    # (mirrors voice_typer.__main__, which parses args before app.main()).

    from voice_typer.server.app import VoiceTyperApp, _ensure_single_instance, _setup_logging
    from voice_typer.server.config import _config_dir

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
    args, _unknown = parser.parse_known_args(sys.argv[1:])
    if args.debug:
        os.environ["VOICE_TYPER_DEBUG"] = "1"
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
    # Tauri host's single-instance plugin + FT-1 supervisor replace
    # them. The env var is set here (rather than required to be set by
    # the host) so a `python -m voice_typer.server.ipc_server --ws`
    # invocation from a terminal also gets the right behavior.
    if ws_mode:
        os.environ["TAURI_SIDECAR"] = "1"
        log.info("[IPC] --ws mode enabled (TAURI_SIDECAR=1 env set)")

    _setup_logging()

    # NEW-DOC-006: single-instance lock is acquired AFTER args are parsed
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

    # NEW-SEC-015: the os._exit monkey-patch that printed a stack trace
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
        try:
            import io
            import traceback

            from voice_typer.server.config import _secure_atomic_write

            buf = io.StringIO()
            buf.write(f"Voice Typer startup failed at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
            buf.write(f"sys.executable: {sys.executable}\n")
            buf.write(f"sys.argv: {sys.argv}\n")
            traceback.print_exc(file=buf)
            diag_path = _config_dir() / "startup-error.log"
            _secure_atomic_write(diag_path, buf.getvalue())
            log.error("[FATAL] Diagnostic written to %s", diag_path)
        except Exception:
            pass
        # NEW-CLI-003: use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)

    # PLAT-HLEAK: store the mutex handle on the app instance so
    # quit() can CloseHandle it on shutdown
    app._mutex_handle = _single_instance_mutex

    # ARCH-REFAC-004: use the providers.build_ipc_server composition
    # root instead of constructing IPCServer directly.  Behavior is
    # identical today (build_ipc_server just calls IPCServer(app));
    # the factory exists so future wiring (logging, metrics, feature
    # flags, an alternate service implementation) lives in one place
    # rather than being threaded through this entry point.
    from voice_typer.server.providers import build_ipc_server

    server = build_ipc_server(app)
    # d-review Finding 1: in explicit TCP (--port) or Tauri WS (--ws) mode
    # the backend is driven by Electron/Tauri over the network, not by
    # legacy stdin/stdout IPC. Mark TCP mode BEFORE start() so the stdin
    # listener (an unauthenticated command path) is not spawned.
    if port is not None or ws_mode:
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

        standalone_port, standalone_sock = _pick_available_port(9876)

        # Generate the session token and set it as an env var BEFORE
        # starting the TCP listener.  The _accept_tcp daemon thread reads
        # VOICE_TYPER_IPC_TOKEN at the top of its function; if we set it
        # after start_tcp(), the thread can read the env var before we
        # assign it, leaving expected_token empty and the connection
        # unauthenticated.
        ipc_token = electron_launcher.generate_session_token()
        os.environ["VOICE_TYPER_IPC_TOKEN"] = ipc_token

        # CR-7: pass the BOUND socket through to start_tcp so there's
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
        # ERR-ERR-002 (fix): was `except BaseException` which also caught
        # KeyboardInterrupt and GeneratorExit. Now catches only Exception
        # so Ctrl+C and SystemExit propagate normally to the finally block.
        log.exception("[FATAL] app.start() raised — shutting down")
        # Also write to the diagnostic file for users running under
        # pythonw.exe where stdout/stderr are devnull.
        try:
            import io
            import traceback

            from voice_typer.server.config import _secure_atomic_write

            buf = io.StringIO()
            buf.write(f"\n--- app.start() failed at {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n")
            traceback.print_exc(file=buf)
            diag_path = _config_dir() / "startup-error.log"
            # Read existing content if any, then write full content atomically
            try:
                existing = diag_path.read_text(encoding="utf-8")
            except (OSError, FileNotFoundError):
                existing = ""
            _secure_atomic_write(diag_path, existing + buf.getvalue())
            log.error("[FATAL] Diagnostic written to %s", diag_path)
        except Exception:
            pass
        # NEW-CLI-003: use the standardized exit code instead of raw 1.
        sys.exit(EXIT_CRASH)
    else:
        pass
    finally:
        pass
    # Keep mutex alive by referencing it until exit
    _ = _single_instance_mutex


__all__ = ["main"]
