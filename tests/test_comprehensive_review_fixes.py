"""Targeted tests for Comprehensive Review (CR) fixes.

Each test class covers one CR finding. The tests are intentionally
focused — they verify the specific behavior change introduced by the
fix, not the full surface area (which is already covered by the
existing test suite).

per-process IPC rate limiter (must persist across reconnects).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from tests.fixtures.ipc_test_helpers import make_fake_sidecar_ws_server

# per-process rate limiter persists across reconnects ─────────


class TestPerProcessRateLimiter:
    """the IPC rate limiter must NOT reset on reconnect.

    Previously, ``_RateLimiter`` was instantiated fresh per TCP/WS
    connection. A local attacker could burst the 200-message budget,
    disconnect, reconnect, and burst again — bypassing the sustained
    cap. The fix: ONE ``_RateLimiter`` per ``IPCServer`` instance,
    lazily created and stored on the instance via
    ``_get_rate_limiter(server)``.
    """

    def test_get_rate_limiter_returns_same_instance_across_calls(self):
        """Repeated calls on the same server return the same limiter."""
        from voice_typer.server.ipc_server import _get_rate_limiter

        class FakeServer:
            pass

        server = FakeServer()
        rl1 = _get_rate_limiter(server)
        rl2 = _get_rate_limiter(server)
        assert rl1 is rl2, "limiter must be the same instance across calls"

    def test_get_rate_limiter_different_servers_get_different_limiters(self):
        """Different server instances get independent limiters (test isolation)."""
        from voice_typer.server.ipc_server import _get_rate_limiter

        class FakeServer:
            pass

        s1 = FakeServer()
        s2 = FakeServer()
        rl1 = _get_rate_limiter(s1)
        rl2 = _get_rate_limiter(s2)
        assert rl1 is not rl2, "different servers must get different limiters"

    def test_budget_persists_across_simulated_reconnect(self):
        """The 200-message burst budget must NOT reset when the connection
        drops and re-establishes (the attack scenario)."""
        from voice_typer.server.ipc_server import _get_rate_limiter

        class FakeServer:
            pass

        server = FakeServer()

        # Simulate first connection: exhaust the burst budget.
        rl1 = _get_rate_limiter(server)
        for _ in range(200):
            assert rl1.allow(now=0.0) is True
        # 201st message is rejected.
        assert rl1.allow(now=0.0) is False

        # Simulate reconnect: the server "re-fetches" the limiter.
        # fix: this must return the SAME instance with the budget
        # still exhausted — NOT a fresh limiter.
        rl_after_reconnect = _get_rate_limiter(server)
        assert rl_after_reconnect is rl1, "reconnect must reuse the same limiter"
        assert rl_after_reconnect.allow(now=0.0) is False, (
            "budget must persist across reconnect — attacker can no longer "
            "reset the 200-message burst by disconnecting and reconnecting"
        )

    def test_magic_mock_server_gets_real_rate_limiter(self):
        """Test doubles (MagicMock) must get a real _RateLimiter instance
        so the existing test suite (which uses MagicMock servers) keeps
        working without modification."""
        from voice_typer.server.ipc_server import _get_rate_limiter, _RateLimiter

        mock_server = MagicMock()
        rl = _get_rate_limiter(mock_server)
        assert isinstance(rl, _RateLimiter), "MagicMock server must get a real _RateLimiter, not a child MagicMock"
        # Subsequent calls return the stored instance (not a fresh one).
        rl2 = _get_rate_limiter(mock_server)
        assert rl is rl2, "MagicMock must reuse the stored limiter"

    def test_sidecar_ws_dispatch_uses_shared_limiter(self):
        """The WS dispatch path (sidecar_ws._make_dispatch) must use the
        per-server shared limiter, not a per-connection one."""
        from voice_typer.server import sidecar_ws
        from voice_typer.server.ipc_server import _RateLimiter

        server = make_fake_sidecar_ws_server()
        # The canonical fake defaults to ``{"ok": True}``; this test
        # asserts the empty-data result envelope, so override the
        # dispatch return value.
        server._dispatch.return_value = {"type": "result", "data": {}}
        dispatch = sidecar_ws._make_dispatch(server)

        # Send one frame — this should create+store the limiter on server.
        result = asyncio.run(dispatch({"type": "ping", "data": {}}, MagicMock()))
        assert result == {"type": "result", "data": {}}

        # The server must now have a real _RateLimiter stored.
        stored = getattr(server, "_rate_limiter_instance", None)
        assert isinstance(stored, _RateLimiter), "WS dispatch must store a real _RateLimiter on the server instance"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
