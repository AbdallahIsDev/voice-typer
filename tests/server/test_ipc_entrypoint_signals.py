"""``voice_typer.server.ipc.entrypoint``."""

from __future__ import annotations

import io
import os
import socket
import sys
import time
from unittest.mock import MagicMock

import pytest
import voice_typer.server.app  # noqa: F401  (force-import for patch targets)
from voice_typer.server._paths import IPC_PORT
from voice_typer.server.ipc import entrypoint
from voice_typer.server.ipc.transport import _pick_available_port

from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    server,
)


class TestSignalHandlerWiring:
    """``main()`` registers a SIGUSR1 handler that triggers"""

    def test_sigint_triggers_graceful_shutdown(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Send SIGINT to the running entrypoint by mocking"""
        app_mock = MagicMock()
        app_mock.start.side_effect = KeyboardInterrupt()
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
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
        # Use --ws so the entrypoint takes the Tauri sidecar path.
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        # Mock sidecar_ws.run to raise KeyboardInterrupt (simulates
        import voice_typer.server.sidecar_ws as sidecar_ws_mod

        def _fake_ws_run(server):
            raise KeyboardInterrupt()

        monkeypatch.setattr(sidecar_ws_mod, "run", _fake_ws_run)

        # Prevent the WS startup daemon thread from actually running
        import threading as _threading

        _real_thread_init = _threading.Thread.__init__

        def _noop_thread_init(self, *args, **kwargs):
            kwargs["target"] = lambda *a, **kw: None
            _real_thread_init(self, *args, **kwargs)

        monkeypatch.setattr(_threading.Thread, "__init__", _noop_thread_init)

        # Disable faulthandler.enable so the test doesn't alter real
        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        monkeypatch.setattr(faulthandler, "dump_traceback_later", lambda **kw: None)

        # Spy on write_startup_diagnostic so we can assert it's NOT
        diagnostic_calls: list[str] = []
        monkeypatch.setattr(
            "voice_typer.server.ipc_diagnostics.write_startup_diagnostic",
            lambda label: diagnostic_calls.append(label),
        )

        with pytest.raises(KeyboardInterrupt):
            entrypoint.main()

        # The shutdown diagnostic was NOT written (KeyboardInterrupt is
        assert diagnostic_calls == [], (
            "main() must NOT write a startup diagnostic for "
            "KeyboardInterrupt (SIGINT), the except Exception block is "
            "narrowed from BaseException so SIGINT-driven shutdown "
            "propagates cleanly without masking the signal as a crash"
        )

    def test_sigusr1_handler_invokes_faulthandler_dump(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The signal handler ``main()`` registers for SIGUSR1 (POSIX)"""
        import signal

        if not hasattr(signal, "SIGUSR1"):
            pytest.skip("SIGUSR1 not available on this platform (Windows)")

        app_mock = MagicMock()
        app_mock.start.return_value = None  # clean shutdown
        monkeypatch.setattr("voice_typer.server.app.VoiceTyperApp", lambda: app_mock)
        monkeypatch.setattr("voice_typer.server.logging_setup._setup_logging", lambda: None)
        monkeypatch.setattr(
            "voice_typer.server.single_instance._ensure_single_instance",
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
        monkeypatch.setattr(sys, "argv", ["ipc_server", "--ws"])
        monkeypatch.delenv("TAURI_SIDECAR", raising=False)

        # Mock sidecar_ws.run so main() doesn't block.
        import voice_typer.server.sidecar_ws as sidecar_ws_mod

        monkeypatch.setattr(sidecar_ws_mod, "run", lambda server: 0)

        import faulthandler

        monkeypatch.setattr(faulthandler, "enable", lambda: None)
        # Capture dump_traceback_later calls so we can assert the handler
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

        # Run main(), it registers the SIGUSR1 handler.
        with pytest.raises(SystemExit):
            entrypoint.main()

        # The SIGUSR1 handler was registered.
        assert signal.SIGUSR1 in registered_handlers, "main() must register a handler for SIGUSR1 on POSIX"
        handler = registered_handlers[signal.SIGUSR1]
        assert callable(handler), "SIGUSR1 handler must be callable"

        # Invoke the handler directly (simulates SIGUSR1 delivery).
        handler(signal.SIGUSR1, None)  # type: ignore[arg-type]

        assert len(dump_calls) >= 1, (
            "the SIGUSR1 handler must invoke faulthandler.dump_traceback_later on signal delivery"
        )
        assert dump_calls[-1].get("timeout") == 1.0, (
            f"dump_traceback_later must be called with timeout=1.0; got {dump_calls[-1]}"
        )


class TestStdinEofTriggersShutdown:
    """when stdin reaches EOF, the legacy stdin/stdout IPC"""

    def test_stdin_eof_triggers_shutdown(self, server) -> None:
        """Close stdin (empty StringIO → immediate EOF) and assert:"""
        stdin = io.StringIO("")  # EOF immediately
        stdout = io.StringIO()
        server._running = True
        # Mock the disconnect hook so we can assert it was called.
        server._on_ipc_client_disconnect = MagicMock()

        # Must not raise.
        server._run(_stdin=stdin, _stdout=stdout)

        # No output (no commands processed).
        assert stdout.getvalue() == "", "stdin EOF before any command must produce no stdout output"

        # The shutdown hook (disconnect) was called once.
        server._on_ipc_client_disconnect.assert_called_once()
        args, _ = server._on_ipc_client_disconnect.call_args
        assert "stdin EOF" in args[0], (
            f"_on_ipc_client_disconnect reason must mention 'stdin EOF' (the shutdown trigger); got {args[0]!r}"
        )

    def test_stdin_eof_after_commands_still_triggers_shutdown(self, server) -> None:
        """After processing commands and reaching EOF, the disconnect"""
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


class TestPortBindingFallback:
    """``_pick_available_port`` (used by ``main()`` in standalone"""

    def test_port_binding_fallback(self) -> None:
        """Pre-bind a socket on ``IPC_PORT`` (9876), call"""
        # Pre-bind + listen on a socket at IPC_PORT to make it busy.
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if os.name != "nt":
            blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            blocker.bind(("127.0.0.1", IPC_PORT))
            blocker.listen(1)
        except OSError:
            # IPC_PORT is ALREADY occupied on this host (e.g. a real
            blocker.close()
            blocker = None
        try:
            port, sock = _pick_available_port(IPC_PORT, max_tries=1)
            try:
                # 1. The returned port is NOT the busy IPC_PORT.
                assert port != IPC_PORT, (
                    f"_pick_available_port must skip the busy port {IPC_PORT} and return a different port; got {port}"
                )

                # 2. The returned port is non-zero (a real port).
                assert port > 0, f"returned port must be a real non-zero port; got {port}"

                # 3. The returned socket is bound to the returned port.
                bound_port = sock.getsockname()[1]
                assert bound_port == port, (
                    f"returned socket must be bound to the returned port {port}; got {bound_port}"
                )

                sock.listen(1)
            finally:
                with __import__("contextlib").suppress(OSError):
                    sock.close()
        finally:
            if blocker is not None:
                with __import__("contextlib").suppress(OSError):
                    blocker.close()

    def test_port_binding_fallback_to_ephemeral_when_all_busy(self) -> None:
        """When ALL ports in the tried range are busy, the helper falls"""
        import contextlib

        max_tries = 5
        for _attempt in range(5):
            blockers: list[socket.socket] = []
            try:
                all_bound = True
                for offset in range(max_tries):
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    # No SO_REUSEADDR here either, a plain (exclusive)
                    try:
                        s.bind(("127.0.0.1", IPC_PORT + offset))
                        s.listen(1)
                        blockers.append(s)
                    except OSError:
                        # Port transiently held by a concurrent test —
                        s.close()
                        all_bound = False
                        break
                if not all_bound:
                    time.sleep(0.1)
                    continue

                port, sock = _pick_available_port(IPC_PORT, max_tries=max_tries)
                try:
                    # The returned port is non-zero (ephemeral fallback).
                    assert port > 0, f"ephemeral fallback must return a real non-zero port; got {port}"
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
                return  # success
            finally:
                for s in blockers:
                    with contextlib.suppress(OSError):
                        s.close()
        pytest.skip(
            f"could not pre-bind the full busy range [{IPC_PORT}.."
            f"{IPC_PORT + max_tries - 1}] after 5 attempts, a concurrent "
            f"process keeps holding one of the ports (environmental; "
            f"the all-bound path still verifies the fallback)"
        )
