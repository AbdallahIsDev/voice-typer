"""``_dispatch``."""

from __future__ import annotations

import inspect
import io
import json
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer

from tests.fixtures.sidecar_ws_test_helpers import _make_fake_server
from tests.server.conftest import (  # noqa: F401  (fixture re-export)
    IPCServer,
    server,
)


class TestStdinRateLimiterGate:
    """per-process ``_RateLimiter`` BEFORE calling ``self._dispatch(msg)``."""

    def test_stdin_rejects_when_rate_limiter_returns_false(self, server) -> None:
        """stdin path must emit a ``client.rate_limited`` error envelope"""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        # Force the limiter to be created on the server instance so we
        limiter = _get_rate_limiter(server)
        # Replace ``_dispatch`` with a sentinel that asserts it was
        dispatch_called = []
        original_dispatch = server._dispatch

        def _should_not_be_called(msg):  # noqa: ARG001
            dispatch_called.append(msg)
            return original_dispatch(msg)

        server._dispatch = _should_not_be_called

        # Patch the limiter to always reject.
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"get_status","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        # The dispatch handler was NOT called (the gate short-circuited).
        assert dispatch_called == [], (
            "stdin path must NOT call _dispatch when the rate "
            "limiter rejects, the transport chokepoint must short-"
            "circuit before dispatch."
        )
        # The limiter was consulted exactly once (the stdin path's gate).
        limiter.allow.assert_called_once_with(command="get_status")
        # The error envelope was emitted on stdout.
        msg = json.loads(stdout.getvalue().strip())
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "client.rate_limited"
        assert msg["data"]["message"] == "rate limit exceeded; backing off"

    def test_stdin_dispatches_when_rate_limiter_returns_true(self, server) -> None:
        """When the rate limiter accepts (``allow() -> True``), the"""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        limiter = _get_rate_limiter(server)
        limiter.allow = MagicMock(return_value=True)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"get_status","id":7}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        # The limiter was consulted exactly once.
        limiter.allow.assert_called_once_with(command="get_status")
        # The dispatch response was emitted (NOT the rate_limited envelope).
        msg = json.loads(stdout.getvalue().strip())
        assert msg["id"] == 7
        assert msg["type"] == "status"

    def test_stdin_rate_limited_envelope_matches_tcp_ws_shape(self, server) -> None:
        """WS paths' envelope shape (``client.rate_limited`` + the same"""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        limiter = _get_rate_limiter(server)
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"download_model","id":99}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        msg = json.loads(stdout.getvalue().strip())
        # The envelope shape matches the TCP path's
        assert msg["type"] == "error"
        assert msg["data"]["code"] == "client.rate_limited"
        assert msg["data"]["message"] == "rate limit exceeded; backing off"
        limiter.allow.assert_called_once_with(command="download_model")

    def test_stdin_heartbeat_bypasses_rate_limiter(self, server) -> None:
        """The heartbeat command bypasses the rate limiter"""
        from voice_typer.server.ipc.rate_limiter import _get_rate_limiter

        limiter = _get_rate_limiter(server)
        # Spy on ``allow`` without overriding its behavior.
        limiter.allow = MagicMock(wraps=limiter.allow)  # type: ignore[method-assign]

        stdin = io.StringIO('{"type":"heartbeat","id":1}\n')
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        # The limiter was consulted (the stdin gate ran).
        limiter.allow.assert_called_once_with(command="heartbeat")
        out = stdout.getvalue().strip()
        assert out, "expected the heartbeat to be dispatched (bypass returns True)"


class TestDispatchDoesNotCallRateLimiter:
    """``DispatcherMixin._dispatch`` source must NOT contain"""

    def test_dispatch_source_does_not_reference_rate_limiter(self) -> None:
        """
        The source of ``IPCServer._dispatch`` must not contain any
        Python statements must not call the limiter).
        """
        src = inspect.getsource(IPCServer._dispatch)
        # Strip comment-only lines (a line whose first non-whitespace
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "_get_rate_limiter" not in code_only, (
            "_dispatch must NOT call _get_rate_limiter, the "
            "rate limiter is enforced at the transport chokepoints "
            "(TCP / WS / stdin), not inside _dispatch. The double-call "
            "halves the effective burst budget and trips the sustained "
            "cap in half the expected time."
        )
        assert "rate_limiter.allow" not in code_only, (
            "_dispatch must NOT call rate_limiter.allow, the limiter is enforced at the transport chokepoints."
        )
        assert "RATE_LIMITED" not in code_only, (
            "_dispatch must NOT reference the RATE_LIMITED error "
            "code, the rate-limit error envelope is constructed at the "
            "transport chokepoints, not inside _dispatch."
        )

    def test_dispatcher_module_does_not_import_get_rate_limiter(self) -> None:
        """The ``dispatcher`` module must not import ``_get_rate_limiter``"""
        from voice_typer.server.ipc import dispatcher

        # The module-level ``_get_rate_limiter`` symbol must not be
        assert not hasattr(dispatcher, "_get_rate_limiter"), (
            "dispatcher module must NOT import _get_rate_limiter, "
            "the import is dead code after removing the limiter call "
            "from _dispatch."
        )

    def test_stdin_runner_module_imports_get_rate_limiter(self) -> None:
        """The ``stdin_runner`` module must import ``_get_rate_limiter``"""
        from voice_typer.server.ipc import stdin_runner

        assert hasattr(stdin_runner, "_get_rate_limiter"), (
            "stdin_runner module must import _get_rate_limiter, "
            "the stdin path now owns the rate-limiter gate (previously "
            "it had no gate)."
        )

    def test_stdin_run_source_contains_rate_limiter_gate(self) -> None:
        """The source of ``IPCServer._run`` (the stdin runner) must"""
        src = inspect.getsource(IPCServer._run)
        assert "_get_rate_limiter(self).allow" in src, (
            "stdin runner (_run) must call "
            "_get_rate_limiter(self).allow(command=...) before "
            "self._dispatch(msg), the stdin path now owns the "
            "rate-limiter gate."
        )
        assert "client.rate_limited" in src, (
            "stdin runner must emit the client.rate_limited "
            "error envelope when the limiter rejects, matching the "
            "TCP / WS path envelope shape."
        )


class TestWsPathSingleRateLimiterCall:
    """rate limiter exactly ONCE per dispatched command, at the transport"""

    def test_ws_dispatch_calls_limiter_once_per_command(self) -> None:
        """A single WS dispatch must consult ``rate_limiter.allow``"""
        import asyncio

        from voice_typer.server import sidecar_ws as sw

        ws_server = _make_fake_server()
        # Force the limiter to be created on the server instance.
        from voice_typer.server.ipc_server import _get_rate_limiter

        limiter = _get_rate_limiter(ws_server)
        limiter.allow = MagicMock(return_value=True)  # type: ignore[method-assign]

        dispatch = sw._make_dispatch(ws_server)
        asyncio.run(dispatch({"type": "get_status", "data": {}, "id": 1}, MagicMock()))

        # The limiter was called exactly ONCE, the WS chokepoint gate.
        assert limiter.allow.call_count == 1, (
            "WS path must call rate_limiter.allow exactly once "
            f"per dispatch (got {limiter.allow.call_count}). The "
            "double-call from _dispatch has been removed; the single "
            "call is the WS chokepoint gate."
        )
        limiter.allow.assert_called_once_with(command="get_status")

    def test_ws_dispatch_does_not_double_charge_on_reject(self) -> None:
        """When the WS chokepoint rejects, ``_dispatch`` is NOT called"""
        import asyncio

        from voice_typer.server import sidecar_ws as sw

        ws_server = _make_fake_server()
        ws_server._dispatch = MagicMock(return_value={"type": "result", "data": {}})

        from voice_typer.server.ipc_server import _get_rate_limiter

        limiter = _get_rate_limiter(ws_server)
        limiter.allow = MagicMock(return_value=False)  # type: ignore[method-assign]

        dispatch = sw._make_dispatch(ws_server)
        result = asyncio.run(dispatch({"type": "get_status", "data": {}, "id": 1}, MagicMock()))

        # The limiter was consulted exactly once (the chokepoint gate).
        assert limiter.allow.call_count == 1, (
            f"WS path must call rate_limiter.allow exactly once even on rejection (got {limiter.allow.call_count})."
        )
        # ``_dispatch`` was NOT called (the chokepoint short-circuited).
        ws_server._dispatch.assert_not_called()
        # The error envelope was returned.
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.rate_limited"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
