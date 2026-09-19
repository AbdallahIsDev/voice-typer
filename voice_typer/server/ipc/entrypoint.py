"""Process entry point for the IPC server (``python -m ...ipc_server``).

Extracted from ``ipc_server.py``. This module owns the three
module-level functions that turn a CLI invocation into a running
:class:`IPCServer`:

  - :func:`_set_process_metadata`: Windows console title / AppUserModelID,
  - :func:`parse_ipc_args`: argparse + env-var side-effects,
  - :func:`main`: the actual subprocess entry point.

These were the last ~430 lines of the pre-split ``ipc_server.py``.
Extracting them lets ``ipc_server.py`` shrink to thin wiring (factory,
re-exports, IPCServer class composition).
"""

from __future__ import annotations

import os
import sys
from types import FrameType
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.app import VoiceTyperApp
    from voice_typer.server.ipc_server import IPCServer

# Re-exported by ``ipc_server.py`` so existing
from voice_typer.server.ipc._helpers import _STDIN_IPC_ENV_VAR, log
from voice_typer.server.tray_types import is_tauri_sidecar


def _ws_startup_thread_main(app: VoiceTyperApp) -> None:
    """Run ``app.start()`` on the ws-mode startup daemon thread, fail-fast.

    The ws (Tauri sidecar) branch of :func:`main` launches the full
    startup sequence (microphone enumeration, hotkey registration,
    background model load, autostart sync, tray alert subscriptions) on
    this daemon thread instead of the main thread, the main thread must
    stay free to block in :func:`sidecar_ws.run`.

    Failure semantics (crash-isolation design): if ``app.start()``
    raises, the process must NOT keep running as a half-dead sidecar
    (WS alive, no microphones/hotkeys/model, the Tauri UI would stay
    up forever in a broken state). It must exit with the canonical
    crash exit code so the Tauri supervisor sees the sidecar die
    (WS drop) and respawns it: the WS reader/writer/heartbeat paths
    trigger ``respawn`` on disconnect (``sidecar/ws/*.rs``), with
    backoff + the ``restart_counter.json`` circuit breaker
    (``sidecar/supervisor.rs``), and a cold-start ``Terminated``
    event routes to the same respawn (``sidecar/spawn.rs``).

    The exit uses ``os._exit`` rather than ``sys.exit``: on a
    non-main thread ``sys.exit`` only raises ``SystemExit`` inside
    that thread (silently ending the thread and leaving the process
    alive), whereas ``os._exit`` terminates the process immediately —
    the same mechanism the IPC lifecycle's hung-shutdown watchdog uses
    (``voice_typer/server/ipc/lifecycle.py``). The exit CODE mirrors
    the main path's ``sys.exit(EXIT_CRASH)`` crash semantics.

    Diagnostics: the crash is logged with a full traceback (FATAL)
    and a startup-error diagnostic is written via
    ``voice_typer.server.ipc_diagnostics.write_startup_diagnostic``
    (same helper the construction-failure and main-path
    ``app.start()``-failure paths use) so the traceback survives the
    pythonw.exe no-console environment.

    The crash is deliberately NOT swallowed and NOT retried here:
    bounded in-place retry would race the supervisor's own backoff
    and the restart circuit breaker; fail-fast + supervisor respawn
    is the single recovery authority.
    """
    try:
        app.start()
    except BaseException:
        # BaseException (not Exception) is intentional: a SystemExit /
        from voice_typer.__main__ import EXIT_CRASH
        from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

        log.exception("[FATAL] ws-mode app.start() raised, exiting so the Tauri supervisor respawns the sidecar")
        try:
            write_startup_diagnostic("ws app.start()")
        except Exception:
            # The diagnostic helper is internally failure-proof, but do not
            log.critical("[FATAL] write_startup_diagnostic failed during ws-mode crash handling", exc_info=True)
        log.critical("[FATAL] ws-mode sidecar exiting with crash code %d", EXIT_CRASH)
        os._exit(EXIT_CRASH)


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


def _detach_process_group() -> bool:
    """Move the sidecar into its own POSIX process group (best-effort).

    The Tauri host spawns the frozen release sidecar as an ``externalBin``
    WITHOUT a pre-exec ``setsid``/``setpgid`` hook (pre_exec is not
    applied to spawned externalBin children in release mode), so the
    sidecar lands in the HOST's process group, a group-wide signal to
    the host (terminal Ctrl-C, group kill) would take the sidecar down
    before the cooperative shutdown handshake runs. The sidecar
    therefore detaches ITSELF: ``os.setpgid(0, 0)`` makes the process
    the leader of a fresh process group, isolating it (and the native
    listeners / prewarm / worker children it later spawns, they
    inherit the new group) from the host's group-wide signals. The
    host's lifecycle control is unaffected: the supervisor kills the
    sidecar BY PID (kill-tree), not by process group.

    POSIX only, a no-op on Windows (process groups are a POSIX
    concept; the Windows host uses Taskkill-style tree kills). The
    early-return guard also keeps the frozen dev-mode exe safe: the
    call is inert everywhere it is not applicable.

    Best-effort by design: any failure is logged at DEBUG and swallowed
    (a ``setpgid`` refusal: e.g. EACCES/EPERM in a sandboxed frozen
    environment, must never block sidecar startup; the sidecar then
    simply stays in the host's group, which is the pre-fix behavior).
    """
    if os.name != "posix":
        return False
    # Resolved via getattr: POSIX-only os attributes are absent from the
    setpgid = getattr(os, "setpgid", None)
    if setpgid is None:
        return False
    try:
        setpgid(0, 0)
    except OSError as exc:
        log.debug(
            "[IPC] process-group self-detach unavailable (%s), staying in the parent's group",
            exc,
        )
        return False
    getpgrp = getattr(os, "getpgrp", None)
    pgid = getpgrp() if getpgrp is not None else -1
    log.debug("[IPC] sidecar detached into its own process group (pgid=%d)", pgid)
    return True


def _version_requested(argv: list[str]) -> bool:
    """Return True when an argv token selects the ``--version`` action.

    Matches the exact ``--version`` flag AND unambiguous argparse prefix
    abbreviations (``--vers``: argparse's ``allow_abbrev`` default
    resolves them to the same action). Used to resolve the installed
    package version lazily: only a ``--version`` invocation pays the
    ``importlib.metadata`` dist-metadata scan.

    The prefix match requires a NON-EMPTY prefix: a bare ``--`` token
    (argparse's end-of-options marker, legitimately present when a
    host or launcher appends it) yields ``arg[2:] == ""``, and every
    string satisfies ``startswith("")``: without the guard the bare
    marker was misclassified as a version request and every such boot
    paid the metadata scan for nothing.
    """
    return any(arg == "--version" or (len(arg) > 2 and "version".startswith(arg[2:])) for arg in argv)


# Environment variable selecting the early server-started launch order
_EARLY_SERVER_STARTED_ENV_VAR = "VT_EARLY_SERVER_STARTED"


def _early_server_started_enabled() -> bool:
    """True when the early server-started launch order is selected.

    Read once per boot in :func:`main` (before the launch-order branch)
    so the flag has exactly one selection point; the ws transport
    learns the mode from the server marker attribute
    (``server._early_ws_bind``) rather than re-reading the environment.
    """
    return os.environ.get(_EARLY_SERVER_STARTED_ENV_VAR, "").strip().lower() in {"1", "true"}


def parse_ipc_args() -> tuple[int | None, bool]:
    """Parse the IPC server CLI args ( extraction from ``main()``).

    Returns ``(port, ws_mode)`` where ``port`` is always ``None``
    (``--port`` rejects with EXIT_BAD_ARGS; the stub stays because
    entrypoint tests pin the rejection, do not remove) and ``ws_mode``
    is True when ``--ws`` was passed (Tauri sidecar WebSocket mode).

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
    import os

    from voice_typer.__main__ import EXIT_BAD_ARGS

    # The installed-package version feeds ONLY the ``--version`` argparse
    _pkg_version = "1.0.0"
    if _version_requested(sys.argv[1:]):
        import importlib.metadata

        try:
            _pkg_version = importlib.metadata.version("voice-typer")
        except Exception:
            _pkg_version = "1.0.0"

    parser = argparse.ArgumentParser(
        prog="voice_typer.server.ipc_server",
        description="Voice Typer IPC server (spawned by the Tauri host)",
        add_help=False,  # we add --help manually to avoid conflict with app
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="N",
        help="REMOVED: the TCP transport was deleted. Use --ws for the Tauri sidecar.",
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
            "VOICE_TYPER_IPC_TOKEN env var."
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
    if args.allow_stdin:
        os.environ[_STDIN_IPC_ENV_VAR] = "1"
        log.info(
            "[IPC] --allow-stdin: %s=1 set (stdin listener will be spawned if _tcp_mode is False)",
            _STDIN_IPC_ENV_VAR,
        )
    port = args.port
    ws_mode = args.ws
    # TCP transport removed: --port is an unsupported leftover that
    if port is not None:
        print(
            "--port is no longer supported: the TCP IPC transport was removed. "
            "Use --ws for the Tauri sidecar WebSocket transport.",
            file=sys.stderr,
        )
        sys.exit(EXIT_BAD_ARGS)
    # ADR-0020 §2 + §10: when running as a Tauri sidecar, set the
    if ws_mode:
        os.environ["TAURI_SIDECAR"] = "1"
        log.info("[IPC] --ws mode enabled (TAURI_SIDECAR=1 env set)")
    return port, ws_mode


def main() -> None:
    """Create a ``VoiceTyperApp``, wrap it in an ``IPCServer``, and block.

    Designed as the subprocess entry point for the Tauri host::

        python -m voice_typer.server.ipc_server --ws  # Tauri sidecar WebSocket
    """
    #  (privacy): tighten the process umask to ``0o077`` (owner-only)
    if os.name == "posix":
        os.umask(0o077)

    # BRAND-METADATA: set process metadata early, before any subsystem
    _set_process_metadata()

    # POSIX process-group self-detach: the release-mode Tauri host
    _detach_process_group()

    # import the standardized exit-code constant.
    from voice_typer.__main__ import EXIT_CRASH
    # the ``sys.modules[_CANONICAL] = sys.modules["__main__"]``

    # RACE-018: Enable faulthandler for automatic thread-dump on SIGSEGV/SIGABRT.
    try:
        import faulthandler

        faulthandler.enable()
        # Optional: register SIGUSR1 for on-demand thread dumps (POSIX only)
        import signal

        if hasattr(signal, "SIGUSR1"):
            # ``faulthandler.dump_traceback_later`` has the
            def _on_sigusr1(_signum: int, _frame: FrameType | None) -> None:
                faulthandler.dump_traceback_later(timeout=1.0)

            signal.signal(signal.SIGUSR1, _on_sigusr1)
    except (AttributeError, ValueError, OSError, RuntimeError):
        # Not available on all platforms (Windows lacks SIGUSR1;
        pass

    # parse arguments BEFORE acquiring the single-instance

    # Import from the canonical homes (not via the app-module re-exports)
    from voice_typer.server.logging_setup import _setup_logging
    from voice_typer.server.single_instance import _ensure_single_instance

    port, ws_mode = parse_ipc_args()

    # Early server-started selection: read the env var ONCE here (the
    _ws_early_bind = _early_server_started_enabled() and ws_mode

    # ADR-0020 §12: under the Tauri sidecar path (TAURI_SIDECAR=1), the
    _tauri_sidecar = is_tauri_sidecar()

    # In standalone/terminal mode (``voice-typer`` from a terminal without
    while True:
        # Re-acquire the single-instance mutex each cycle.  The previous
        _single_instance_mutex = None if _tauri_sidecar else _ensure_single_instance(silent=True)

        # initialized`` banner (C-LOG-1).  ``setup_logging`` is idempotent
        _setup_logging()
        if _tauri_sidecar:
            log.info("[IPC] TAURI_SIDECAR=1, skipping Python-side single-instance mutex (Tauri host owns it)")

        # the os._exit monkey-patch that printed a stack trace

        if _ws_early_bind:
            # Bind-before-build: the WS listener binds and
            from typing import cast

            from voice_typer.server.providers import AppProtocol, build_ipc_server

            log.info(
                "[IPC] early server-started mode (%s=1), binding the WS listener "
                "before app construction; construction proceeds on the startup thread",
                _EARLY_SERVER_STARTED_ENV_VAR,
            )
            # ``cast`` (not a suppression): the app is DELIBERATELY
            server = build_ipc_server(cast(AppProtocol, None))
            # ``server.start()`` is DEFERRED to the startup thread: it
            server._tcp_mode = True
            # Marker read by ``sidecar_ws.run`` to install the bounded
            server._early_ws_bind = True
        else:
            try:
                # Single construction site (shared with the early-bind
                app = _construct_app_with_diagnostics()
            except Exception:
                # use the standardized exit code instead of raw 1.
                sys.exit(EXIT_CRASH)

            # PLAT-HLEAK: store the mutex handle on the app instance so
            app._mutex_handle = _single_instance_mutex

            # use the providers.build_ipc_server composition
            from voice_typer.server.providers import build_ipc_server

            server = build_ipc_server(app)
            #  ``main()`` NEVER uses the
            server._tcp_mode = True
            server.start()
        # ADR-0020 §2: --ws mode starts the WebSocket sidecar server instead
        if ws_mode:
            import threading

            from voice_typer.server import sidecar_ws

            # This branch exits via sys.exit() below and never reaches
            if _ws_early_bind:
                _ws_startup_thread = threading.Thread(
                    target=_ws_startup_thread_main_early,
                    args=(server, _single_instance_mutex),
                    name="ws-sidecar-startup",
                    daemon=True,
                )
            else:
                _ws_startup_thread = threading.Thread(
                    target=_ws_startup_thread_main,
                    args=(app,),
                    name="ws-sidecar-startup",
                    daemon=True,
                )
            _ws_startup_thread.start()
            log.info("[IPC] starting Tauri sidecar WebSocket server (sidecar_ws.run)")
            # ADR-0020 round-2 fix: do NOT call server.push({"type": "ready"})
            _ws_exit = sidecar_ws.run(server)
            if _ws_exit != 0:
                log.warning("[IPC] sidecar_ws.run exited with code %d", _ws_exit)
            sys.exit(_ws_exit)
        else:
            # TCP transport and predecessor standalone spawn were removed.
            log.error(
                "[IPC] No transport specified. The TCP transport was removed; "
                "run with --ws for the Tauri sidecar WebSocket transport."
            )
            from voice_typer.__main__ import EXIT_BAD_ARGS

            sys.exit(EXIT_BAD_ARGS)

        # Tell the frontend we're ready. (WS mode exits above via
        server.push({"type": "ready"})
        # DEBUG: the tray's own "[TRAY] Tray icon created; event loop
        log.debug("[IPC] entering app.start() (tray event loop)")
        try:
            app.start()  # blocks (tray event loop)
            # QUIT-CLEAN-001: keep shutdown quiet.  Only ``[QUIT] Quitting
            log.debug("[IPC] Shutdown complete")
        except SystemExit as _se:
            # sys.exit() or os._exit() called from within pystray or runtime.
            log.debug("[IPC] app.start() exited via sys.exit(%s)", _se.code)
            raise
        except Exception:
            #  (fix): was `except BaseException` which also caught
            log.exception("[FATAL] app.start() raised, shutting down")
            #  route through the shared diagnostic helper
            from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

            write_startup_diagnostic("app.start()")
            # use the standardized exit code instead of raw 1.
            sys.exit(EXIT_CRASH)
        else:
            pass
        finally:
            pass
        # ``app.start()`` returned: the tray loop was broken by
        if not vars(app).get("_in_place_restart", False):
            break
        log.info("[RESTART] In-place restart, re-initializing app in the same process")
        # Loop back: the next iteration re-acquires the single-instance
    _ = _single_instance_mutex


def _construct_app_with_diagnostics() -> VoiceTyperApp:
    """The SINGLE ``VoiceTyperApp()`` construction site.

    Called from exactly two launch-order branches (``main``'s default
    build-then-serve path and the early server-started ws-startup
    thread), the AST-level single-call-site invariant that
    ``tests/app/test_quit_restart.py`` pins is preserved here, and the
    single-instance mutex is always acquired (in ``main``) before
    either branch reaches this helper, so a duplicate process exits
    before any heavy import.

    Failure semantics (identical for both callers): log the full
    ``[FATAL] VoiceTyperApp() construction failed`` traceback, write
    the ``construction`` startup diagnostic (the io.StringIO →
    traceback → _redact_text → _secure_atomic_write → /tmp-fallback
    pattern encapsulated in ``ipc_diagnostics`` so the
    construction-failure and app.start()-failure paths share one
    source of truth), then RE-RAISE, each caller applies its own exit
    semantics (``sys.exit`` on the main path, ``os._exit`` on the
    daemon thread).
    """
    from voice_typer.server.app import VoiceTyperApp

    try:
        return VoiceTyperApp()
    except BaseException:
        log.exception("[FATAL] VoiceTyperApp() construction failed")
        try:
            from voice_typer.server.ipc_diagnostics import write_startup_diagnostic

            write_startup_diagnostic("construction")
        except Exception:
            # The diagnostic helper is internally failure-proof, but do
            log.critical(
                "[FATAL] write_startup_diagnostic failed during construction crash handling",
                exc_info=True,
            )
        raise


def _ws_startup_thread_main_early(server: IPCServer, mutex_handle) -> None:
    """Early server-started variant of the ws-startup thread body.

    Runs where :func:`_ws_startup_thread_main` runs (the ws-sidecar
    startup daemon thread) but OWNS the ``VoiceTyperApp()`` construction
    as well: in the early launch order ``main`` binds the WS listener
    (and emits ``server_started``) first, so the heavy construction —
    all builders, migration, and per-boot sweeps, proceeds here,
    behind the already-live listener, while the host's WS client can
    already authenticate and dispatch (pre-app frames are buffered by
    the transport, see ``sidecar_ws._wrap_dispatch_for_early_bind``).

    Sequence (order matters):

    1. Construct ``VoiceTyperApp()`` (the deferred heavy work).
    2. Late-bind the real app into the pre-bound server: swap in the
       app, rebuild the service over it (the service constructed over
       the deferred placeholder was never used, every pre-bind
       dispatch was buffered), and run ``server.start()`` (deferred
       from ``main`` because it touches ``self.app._ipc_server`` and
       hooks ``app.tray.set_state``: both need the real app).
    3. Raise ``server._early_bind_app_ready`` and flush the bounded
       pre-app dispatch buffer so the buffered frames execute against
       the fully-wired server.
    4. Delegate to :func:`_ws_startup_thread_main` (the existing
       fail-fast wrapper) for ``app.start()``.

    Failure semantics mirror the main-path construction handler: a
    crash logs the SAME ``[FATAL] VoiceTyperApp() construction failed``
    line, routes through ``write_startup_diagnostic("construction")``
    (identical phase label), and terminates the process with
    ``EXIT_CRASH``: via ``os._exit`` (this is a daemon thread;
    ``sys.exit`` would only end the thread and strand a WS-alive-but-
    empty backend) so the Tauri supervisor respawns the sidecar.
    ``BaseException`` (not ``Exception``) is caught for the same reason
    as :func:`_ws_startup_thread_main`.
    """
    from voice_typer.__main__ import EXIT_CRASH

    try:
        app = _construct_app_with_diagnostics()
    except BaseException:
        # The shared construction helper already logged the traceback and
        log.critical("[FATAL] early-bind sidecar exiting with crash code %d", EXIT_CRASH)
        os._exit(EXIT_CRASH)
        # Defensive return: ``os._exit`` never returns in production, but
        return

    # Late-bind the constructed app into the pre-bound server.
    app._mutex_handle = mutex_handle
    server.app = app
    from voice_typer.server.service import VoiceTyperService

    server.service = VoiceTyperService(app)
    server.start()
    server._early_bind_app_ready = True
    from voice_typer.server import sidecar_ws

    sidecar_ws.flush_early_dispatch_buffer(server)
    log.info(
        "[IPC] early server-started mode: app constructed + IPC server bound, "
        "buffered frames dispatched, startup sequence starting"
    )
    _ws_startup_thread_main(app)


if __name__ == "__main__":
    main()
