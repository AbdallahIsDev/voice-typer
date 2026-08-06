"""signal-handler wiring, stdin-EOF shutdown, and
port-binding fallback coverage for
``voice_typer.server.ipc.entrypoint``.

The entrypoint's ``main()`` function wires three signal-driven shutdown
surfaces:

  1. **Signal handler registration** — ``main()`` calls ``signal.signal``
     on POSIX to register a SIGUSR1 handler that invokes
     ``faulthandler.dump_traceback_later(timeout=1.0)`` for on-demand
     thread dumps. SIGINT/SIGTERM are NOT registered by ``main()``
     itself — they're handled by ``signal_handlers.install_signal_handlers``
     (invoked from ``app.start()``'s tray loop). When a SIGINT arrives,
     Python's default handler raises ``KeyboardInterrupt`` in the main
     thread; ``main()``'s ``except Exception`` block (narrowed from the
     pre-fix ``except BaseException``) does NOT swallow it, so the
     shutdown propagates cleanly to the OS.

  2. **Stdin EOF shutdown** — the legacy stdin/stdout IPC transport
     (``StdinRunnerMixin._run``) reads JSON lines from stdin. When stdin
     reaches EOF, the loop exits and calls
     ``_on_ipc_client_disconnect`` so the keyboard ownership is reset
     (a crashed CLI client doesn't leave the backend stuck in
     ``hotkey_capture`` state). ``main()`` itself sets
     ``server._tcp_mode = True`` so the stdin listener is NOT spawned
     in production — but the EOF→disconnect wiring is the canonical
     "stdin closed → shutdown" path the entrypoint exposes via the
     IPCServer composition.

  3. **Port binding fallback** — in standalone mode (no ``--port``, no
     ``--ws``), ``main()`` calls ``_pick_available_port(IPC_PORT)`` to
     auto-pick a free TCP port. The helper tries ports starting at
     ``IPC_PORT`` (9876) and increments; if every port in the range is
     busy, it falls back to an OS-assigned ephemeral port (``bind(("127.0.0.1", 0))``)
     so the function never fails.

Platform-qualified: the SIGINT test mocks ``app.start()`` to raise
``KeyboardInterrupt`` (what Python's default SIGINT handler produces) so
no real signal is delivered. The stdin-EOF test uses an ``io.StringIO``
so no real file descriptor is touched. The port-fallback test pre-binds
a real socket on the configured port (the only OS-level resource
touched — a loopback TCP socket, cleaned up in the test).

All other OS-level calls are mocked — no real signals, no real
subprocess, no real Win32 handles.
"""

from __future__ import annotations

import io
import socket
import sys
from unittest.mock import MagicMock

import pytest
import voice_typer.server.app  # noqa: F401  (force-import for patch targets)
from voice_typer.server._paths import IPC_PORT
from voice_typer.server.ipc import entrypoint
from voice_typer.server.ipc.transport import _pick_available_port

# Re-export the ``server`` fixture from the sibling conftest so the
# stdin-EOF test can construct an IPCServer backed by a MockApp without
# re-declaring the fixture. Mirrors ``tests/server/test_ipc_stdin_runner.py``.
from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    server,
)

# ── Signal handler wiring + SIGINT propagation ────────────────────────


class TestSignalHandlerWiring:
    """``main()`` registers a SIGUSR1 handler that triggers
    ``faulthandler.dump_traceback_later`` for on-demand thread dumps.

    SIGINT is NOT registered by ``main()`` (it's handled by
    ``signal_handlers.install_signal_handlers`` from inside
    ``app.start()``). When SIGINT arrives, Python's default handler
    raises ``KeyboardInterrupt``; ``main()``'s ``except Exception``
    block (narrowed from ``except BaseException``) does NOT swallow
    it, so the shutdown propagates cleanly.
    """

    def test_sigint_triggers_graceful_shutdown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Send SIGINT to the running entrypoint by mocking
        ``app.start()`` to raise ``KeyboardInterrupt`` (the exception
        Python's default SIGINT handler raises in the main thread).

        Asserts:

          1. ``main()`` does NOT swallow ``KeyboardInterrupt`` — it
             propagates out so the OS sees the signal-driven shutdown.
             Pre-fix (when the except block was ``except
             BaseException``), ``KeyboardInterrupt`` was swallowed and
             the entrypoint exited with code 0 instead of 130 (the
             conventional SIGINT exit code) — masking the signal-driven
             shutdown from the parent process.

          2. The shutdown diagnostic path (``write_startup_diagnostic``)
             is NOT triggered for a ``KeyboardInterrupt`` (it's reserved
             for ``Exception`` subclasses — ``KeyboardInterrupt`` is a
             ``BaseException`` but not an ``Exception``).

        No real signal is delivered — ``app.start()`` is mocked.
        """
        # Mock every heavy dependency so main() runs to the
        # app.start() call without touching real subsystems.
        app_mock = MagicMock()
        # app.start() raises KeyboardInterrupt — simulates SIGINT
        # arriving during the tray event loop.
        app_mock.start.side_effect = KeyboardInterrupt()
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.app._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.app._ensure_single_instance",
            lambda silent=False: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._set_process_metadata",
            lambda: None,
        )
        fake_server = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.providers.build_ipc_server",
            lambda app: fake_server,
        )
        # Use --port so the standalone electron-launch path is skipped.
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        # Disable faulthandler.enable so the test doesn't alter real
        # process state.
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda **kw: None)

        # Spy on write_startup_diagnostic so we can assert it's NOT
        # called for KeyboardInterrupt (the except Exception block is
        # skipped — KeyboardInterrupt is BaseException, not Exception).
        diagnostic_calls: list[str] = []
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda label: diagnostic_calls.append(label),
        )

        # main() must let KeyboardInterrupt propagate (not swallow it).
        with pytest.raises(KeyboardInterrupt):
            entrypoint.main()

        # The shutdown diagnostic was NOT written (KeyboardInterrupt is
        # not an Exception subclass — the except Exception block is
        # skipped).
        assert diagnostic_calls == [], (
            "main() must NOT write a startup diagnostic for "
            "KeyboardInterrupt (SIGINT) — the except Exception block is "
            "narrowed from BaseException so SIGINT-driven shutdown "
            "propagates cleanly without masking the signal as a crash"
        )

    def test_sigusr1_handler_invokes_faulthandler_dump(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The signal handler ``main()`` registers for SIGUSR1 (POSIX)
        invokes ``faulthandler.dump_traceback_later(timeout=1.0)`` for
        on-demand thread dumps.

        Captures the handler via a mocked ``signal.signal``, invokes it
        directly (simulating SIGUSR1 delivery), and asserts
        ``faulthandler.dump_traceback_later`` was called with
        ``timeout=1.0``.

        Platform-qualified: on Windows ``signal.SIGUSR1`` does not exist,
        so the ``hasattr(signal, "SIGUSR1")`` guard in ``main()`` skips
        the registration — the assertion is gated on ``hasattr`` so the
        test passes everywhere but only pins the wiring where it exists.

        No real signal is delivered — the handler is invoked directly.
        """
        import signal

        if not hasattr(signal, "SIGUSR1"):
            pytest.skip("SIGUSR1 not available on this platform (Windows)")

        app_mock = MagicMock()
        app_mock.start.return_value = None  # clean shutdown
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.app._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.app._ensure_single_instance",
            lambda silent=False: None,
        )
        monkeypatch.setattr(
            "voice_typer.server.ipc_server._set_process_metadata",
            lambda: None,
        )
        fake_server = MagicMock()
        monkeypatch.setattr(
            "voice_typer.server.providers.build_ipc_server",
            lambda app: fake_server,
        )
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--port", "9876"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        # Capture dump_traceback_later calls so we can assert the handler
        # fires it with timeout=1.0.
        dump_calls: list[dict] = []

        def _capture_dump(**kwargs):
            dump_calls.append(kwargs)

        monkeypatch.setattr(faulthandler, "dump_traceback_later", _capture_dump)

        # Capture the signal handler registered by main().
        registered_handlers: dict[int, object] = {}
        real_signal_signal = signal.signal

        def _capture_signal(signum, handler, *args, **kwargs):
            registered_handlers[signum] = handler
            # Install a no-op so the test doesn't lose SIGUSR1 control.
            return real_signal_signal(signum, lambda *a: None, *args, **kwargs)

        monkeypatch.setattr(signal, "signal", _capture_signal)

        # Run main() — it registers the SIGUSR1 handler.
        entrypoint.main()

        # The SIGUSR1 handler was registered.
        assert signal.SIGUSR1 in registered_handlers, (
            "main() must register a handler for SIGUSR1 on POSIX"
        )
        handler = registered_handlers[signal.SIGUSR1]
        assert callable(handler), "SIGUSR1 handler must be callable"

        # Invoke the handler directly (simulates SIGUSR1 delivery).
        handler(signal.SIGUSR1, None)  # type: ignore[arg-type]

        # faulthandler.dump_traceback_later was called with timeout=1.0.
        assert len(dump_calls) >= 1, (
            "the SIGUSR1 handler must invoke "
            "faulthandler.dump_traceback_later on signal delivery"
        )
        assert dump_calls[-1].get("timeout") == 1.0, (
            f"dump_traceback_later must be called with timeout=1.0; "
            f"got {dump_calls[-1]}"
        )


# ── Stdin EOF triggers shutdown ───────────────────────────────────────


class TestStdinEofTriggersShutdown:
    """when stdin reaches EOF, the legacy stdin/stdout IPC
    transport (``StdinRunnerMixin._run``) exits its read loop and calls
    ``_on_ipc_client_disconnect`` so the keyboard ownership is reset
    (the canonical "stdin closed → shutdown" wiring the entrypoint
    exposes via the IPCServer composition).

    ``main()`` itself sets ``server._tcp_mode = True`` so the stdin
    listener is NOT spawned in production (the TCP transport is used
    instead). But the EOF→disconnect wiring is the entrypoint's
    contract for the stdin path — this test pins it so a future
    refactor that re-enables stdin mode doesn't silently drop the
    disconnect hook.

    No real file descriptor is touched — an ``io.StringIO`` simulates
    EOF.
    """

    def test_stdin_eof_triggers_shutdown(self, server) -> None:
        """Close stdin (empty StringIO → immediate EOF) and assert:

          1. ``_run`` exits cleanly (no exception).
          2. ``_on_ipc_client_disconnect`` is called exactly once with
             a reason string mentioning "stdin EOF" — the shutdown hook
             that resets keyboard ownership so a crashed CLI client
             doesn't leave the backend stuck.
          3. No output was written (no commands processed — EOF was
             immediate).
        """
        stdin = io.StringIO("")  # EOF immediately
        stdout = io.StringIO()
        server._running = True
        # Mock the disconnect hook so we can assert it was called.
        server._on_ipc_client_disconnect = MagicMock()

        # Must not raise.
        server._run(_stdin=stdin, _stdout=stdout)

        # No output (no commands processed).
        assert stdout.getvalue() == "", (
            "stdin EOF before any command must produce no stdout output"
        )

        # The shutdown hook (disconnect) was called once.
        server._on_ipc_client_disconnect.assert_called_once()
        args, _ = server._on_ipc_client_disconnect.call_args
        assert "stdin EOF" in args[0], (
            f"_on_ipc_client_disconnect reason must mention "
            f"'stdin EOF' (the shutdown trigger); got {args[0]!r}"
        )

    def test_stdin_eof_after_commands_still_triggers_shutdown(self, server) -> None:
        """After processing commands and reaching EOF, the disconnect
        hook still fires (once, at the end). This pins that the EOF
        shutdown wiring fires even when commands were processed
        successfully — a command-then-close sequence doesn't leave the
        disconnect hook un-fired."""
        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._on_ipc_client_disconnect = MagicMock()

        server._run(_stdin=stdin, _stdout=stdout)

        # The command was dispatched (status response on stdout).
        assert "status" in stdout.getvalue()

        # Disconnect was called after EOF.
        server._on_ipc_client_disconnect.assert_called_once()
        args, _ = server._on_ipc_client_disconnect.call_args
        assert "stdin EOF" in args[0]


# ── Port binding fallback ─────────────────────────────────────────────


class TestPortBindingFallback:
    """``_pick_available_port`` (used by ``main()`` in standalone
    mode) must fall back to an OS-assigned ephemeral port when the
    configured port range is busy, so the function never fails.

    The helper tries ports ``[IPC_PORT, IPC_PORT+1, ...]`` up to
    ``max_tries`` (default 100); if every port is busy, it binds to
    ``("127.0.0.1", 0)`` and lets the OS assign an ephemeral port.

    This test pre-binds a socket on ``IPC_PORT`` (9876) to simulate a
    busy port and verifies the helper returns a DIFFERENT port with a
    usable bound socket.
    """

    def test_port_binding_fallback(self) -> None:
        """Pre-bind a socket on ``IPC_PORT`` (9876), call
        ``_pick_available_port(IPC_PORT)``, and assert:

          1. The returned port is NOT ``IPC_PORT`` (the busy port was
             skipped).
          2. The returned socket is a real bound socket (``getsockname``
             returns the same port, ``listen`` succeeds).
          3. The returned port is non-zero (a real port, not the
             ephemeral-request sentinel).

        The pre-bound socket is closed in the test; the returned socket
        is closed in a ``finally`` so the test never leaks a file
        descriptor even on assertion failure.
        """
        # Pre-bind + listen on a socket at IPC_PORT to make it busy.
        # ``listen()`` is required: on Linux, ``SO_REUSEADDR`` allows two
        # UNLISTENING sockets to bind the same port, so a bare ``bind()``
        # does NOT simulate a busy port. An active listener makes the
        # helper's probe ``bind()`` fail with EADDRINUSE so the helper
        # skips to the next port (the fallback path under test).
        #
        # The blocker intentionally does NOT set SO_REUSEADDR: on
        # Windows, ``SO_REUSEADDR`` has inverse semantics (it lets a
        # second socket FORCIBLY bind a port already in use), so a
        # REUSEADDR blocker would not make the port busy for the probe.
        # A plain (exclusive) listening socket blocks the probe on BOTH
        # platforms.
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", IPC_PORT))
            blocker.listen(1)
        except OSError:
            # IPC_PORT is ALREADY occupied on this host (e.g. a real
            # backend process running, or another test holds it). That
            # is fine — the "port is busy" condition holds either way;
            # we just don't own the blocker.
            blocker.close()
            blocker = None
        try:
            port, sock = _pick_available_port(IPC_PORT, max_tries=1)
            try:
                # 1. The returned port is NOT the busy IPC_PORT.
                assert port != IPC_PORT, (
                    f"_pick_available_port must skip the busy port "
                    f"{IPC_PORT} and return a different port; got {port}"
                )

                # 2. The returned port is non-zero (a real port).
                assert port > 0, (
                    f"returned port must be a real non-zero port; "
                    f"got {port}"
                )

                # 3. The returned socket is bound to the returned port.
                bound_port = sock.getsockname()[1]
                assert bound_port == port, (
                    f"returned socket must be bound to the returned "
                    f"port {port}; got {bound_port}"
                )

                # 4. The socket is usable (listen succeeds — the
                # contract is "bound but not listening; caller calls
                # listen()").
                sock.listen(1)
            finally:
                with __import__("contextlib").suppress(OSError):
                    sock.close()
        finally:
            if blocker is not None:
                with __import__("contextlib").suppress(OSError):
                    blocker.close()

    def test_port_binding_fallback_to_ephemeral_when_all_busy(self) -> None:
        """When ALL ports in the tried range are busy, the helper falls
        back to an OS-assigned ephemeral port (``bind(("127.0.0.1", 0))``).

        Pre-binds ``max_tries`` sockets on the range
        ``[IPC_PORT..IPC_PORT+max_tries-1]`` so every port the helper
        tries is busy. Asserts the returned port is non-zero (the OS
        assigned an ephemeral port) and the socket is usable.

        The pre-bound sockets are closed in a ``finally`` so the test
        never leaks file descriptors.
        """
        import contextlib

        max_tries = 5
        blockers: list[socket.socket] = []
        try:
            for offset in range(max_tries):
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                # No SO_REUSEADDR here either — a plain (exclusive)
                # listening socket blocks the probe's REUSEADDR bind on
                # both Linux (listening port is never shareable) and
                # Windows (SO_REUSEADDR only allows double-binding when
                # BOTH sockets opt in).
                try:
                    s.bind(("127.0.0.1", IPC_PORT + offset))
                    s.listen(1)
                    blockers.append(s)
                except OSError:
                    # Port already busy (e.g. another test) — that's
                    # fine, the helper will skip it too.
                    s.close()

            port, sock = _pick_available_port(IPC_PORT, max_tries=max_tries)
            try:
                # The returned port is non-zero (ephemeral fallback).
                assert port > 0, (
                    f"ephemeral fallback must return a real non-zero "
                    f"port; got {port}"
                )
                # The returned port is NOT in the busy range (the
                # helper skipped all of them and asked the OS for an
                # ephemeral).
                assert port < IPC_PORT or port >= IPC_PORT + max_tries, (
                    f"ephemeral fallback port {port} must be outside "
                    f"the busy range [{IPC_PORT}..{IPC_PORT + max_tries - 1}]"
                )
                # The socket is bound to the returned port.
                assert sock.getsockname()[1] == port
                sock.listen(1)
            finally:
                with contextlib.suppress(OSError):
                    sock.close()
        finally:
            for s in blockers:
                with contextlib.suppress(OSError):
                    s.close()
