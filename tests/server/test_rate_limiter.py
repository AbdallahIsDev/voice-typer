"""Rate-limiter and flood-resistance tests."""

import threading
from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401
    mock_app,
    server,
)


class TestRateLimiter:
    """protects the IPC dispatcher from flood attacks."""

    def test_allows_messages_under_burst_limit(self):
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=10, sustained_per_sec=10, window=1.0)
        # All 10 messages within the same second should be allowed
        for _ in range(10):
            assert rl.allow(now=0.0) is True

    def test_rejects_messages_over_burst_limit(self):
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=10, sustained_per_sec=10, window=1.0)
        for _ in range(10):
            rl.allow(now=0.0)
        # 11th message in the same window should be rejected
        assert rl.allow(now=0.0) is False

    def test_window_slides_with_time(self):
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=5, sustained_per_sec=5, window=1.0)
        # Use up the budget at t=0
        for _ in range(5):
            assert rl.allow(now=0.0) is True
        # Rejected at t=0.5 (still within the 1.0s window)
        assert rl.allow(now=0.5) is False
        # Allowed at t=1.1 (window has slid past the t=0 timestamps)
        assert rl.allow(now=1.1) is True

    def test_sustained_rate_caps_burst(self):
        """Even if the burst limit is high, the sustained rate caps"""
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=5, window=1.0)
        # First 5 are allowed (sustained rate)
        for _ in range(5):
            assert rl.allow(now=0.0) is True
        # 6th in the same second is rejected despite burst being 200
        assert rl.allow(now=0.0) is False

    def test_allow_increments_rejected_count_atomically(self):
        """SEC-6 / YJ-61: ``allow()`` atomically increments"""
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=2, sustained_per_sec=2, window=1.0)
        # First 2 calls consume the budget.
        for _ in range(2):
            rl.allow(now=0.0)
        # Next 5 calls exceed the budget and must be rejected. SEC-6:
        for _ in range(5):
            assert rl.allow(now=0.0) is False
        assert rl.rejected_count == 5

    def test_thread_safe(self):
        """Multiple threads calling allow() concurrently should not"""
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=1000, sustained_per_sec=1000, window=1.0)
        accepted = []
        rejected = []
        lock = threading.Lock()

        def worker():
            for _ in range(100):
                ok = rl.allow()
                with lock:
                    if ok:
                        accepted.append(1)
                    else:
                        rejected.append(1)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Total should equal 10 * 100 = 1000 calls
        assert len(accepted) + len(rejected) == 1000
        # Accepted should never exceed burst (1000)
        assert len(accepted) <= 1000


# IPC DoS/flood test ─────────────────────────────────────────


class TestServerFloodResistance:
    """TEST-001: verify the IPC server can handle a flood of messages"""

    def test_flood_of_get_status_does_not_crash(self, server, mock_app):
        """Sending 1000 get_status messages in rapid succession should"""
        rejected = 0
        accepted = 0
        for i in range(1000):
            result = server._dispatch({"id": i, "type": "get_status"})
            if result.get("type") == "error" and "rate limit" in result.get("data", {}).get("message", ""):
                rejected += 1
            elif result.get("type") == "status":
                accepted += 1
        # The server should still be alive
        assert accepted + rejected == 1000
        # At least some should have been accepted (the first few before
        assert accepted > 0

    def test_flood_of_malformed_json_does_not_crash(self, server):
        """Malformed JSON lines should be rejected without crashing."""
        import io
        import json

        stdin = io.StringIO()
        stdout = io.StringIO()
        # 100 malformed JSON lines
        for _ in range(100):
            stdin.write('{"invalid": "json", missing_colon}\n')
        stdin.seek(0)
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)
        # Each line should produce an error response
        lines = stdout.getvalue().strip().split("\n")
        assert len(lines) == 100
        for line in lines:
            msg = json.loads(line)
            assert msg["type"] == "error"

    def test_large_limit_does_not_oom(self, server, mock_app):
        """A history request with limit=10^9 should be clamped, not OOM."""
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        result = server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": 10**9},
            }
        )
        # Should succeed (clamped to 500), not crash
        assert result["type"] == "history"
        mock_app.history_db.get_recent.assert_called_once_with(
            500, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )


# Per-command cost map coverage ────────────────────────────────────────


class TestRateLimiterCommandCosts:
    """Coverage for the per-command cost map (``COMMAND_COSTS``)."""

    def test_heartbeat_costs_one(self):
        """``heartbeat`` is explicitly listed at cost 1 so future"""
        from voice_typer.server.ipc.rate_limiter import COMMAND_COSTS
        from voice_typer.server.ipc_server import _RateLimiter

        assert COMMAND_COSTS["heartbeat"] == 1, (
            "heartbeat must be explicitly listed in COMMAND_COSTS at "
            "cost 1 so future DEFAULT_COST changes don't alter its "
            "rate-limit characteristics."
        )
        rl = _RateLimiter(burst=10, sustained_per_sec=10, window=1.0)
        # Heartbeat bypasses the limiter, returns True without recording.
        assert rl.allow(command="heartbeat", now=0.0) is True
        assert rl._burst_total == 0

    def test_download_model_costs_50(self):
        """``download_model`` consumes 50 of the 200-unit burst budget,"""
        from voice_typer.server.ipc.rate_limiter import COMMAND_COSTS
        from voice_typer.server.ipc_server import _RateLimiter

        assert COMMAND_COSTS["download_model"] == 50, (
            "download_model must cost 50 (was 10 pre-audit), large "
            "model downloads saturate the dispatcher thread pool and "
            "the disk long after the rate-limit window has slid past."
        )
        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0)
        # First 4 download_model calls: 4 * 50 = 200 == burst budget.
        for i in range(4):
            assert rl.allow(command="download_model", now=0.0) is True, (
                f"download_model call #{i + 1} should be accepted (cumulative cost {50 * (i + 1)} <= burst=200)."
            )
        # 5th would push total to 250 > 200 → rejected.
        assert rl.allow(command="download_model", now=0.0) is False, (
            "5th download_model in the same 1s window must be rejected (cumulative cost 250 > burst=200)."
        )
        # Verify the cost was actually consumed (running total matches
        assert rl._burst_total == 200, (
            "download_model's per-call cost (50) must be reflected in "
            "the limiter's running burst total after 4 accepted calls."
        )

    def test_unknown_command_uses_default_cost(self):
        """the count-based limiter: a caller that doesn't pass ``command``"""
        from voice_typer.server.ipc.rate_limiter import (
            COMMAND_COSTS,
            DEFAULT_COST,
        )
        from voice_typer.server.ipc_server import _RateLimiter

        # Sanity: "frobnicate" is not a known command.
        assert "frobnicate" not in COMMAND_COSTS, (
            "Test fixture sanity: 'frobnicate' should NOT be in "
            "COMMAND_COSTS, pick a different unknown-command name "
            "if this assertion ever fires."
        )
        rl = _RateLimiter(burst=10, sustained_per_sec=10, window=1.0)
        assert rl.allow(command="frobnicate", now=0.0) is True
        # DEFAULT_COST (1) unit consumed.
        assert rl._burst_total == DEFAULT_COST, (
            "Unknown commands must consume exactly DEFAULT_COST units; "
            "the running burst total must reflect DEFAULT_COST after "
            "one accepted call."
        )


class TestRateLimiterIntegrationWithTransports:
    """three transport chokepoints (TCP read loop, WS dispatch closure,"""

    def test_flood_of_download_model_rejected_by_rate_limit(self, server):
        """Flood 200 ``download_model`` commands through the stdin"""
        import io
        import json
        from unittest.mock import MagicMock

        # Mock _handle_download_model so the test doesn't actually try
        server._handle_download_model = MagicMock(return_value={"type": "result", "data": {"ok": True}})

        stdin = io.StringIO()
        for i in range(200):
            stdin.write(json.dumps({"id": i, "type": "download_model"}) + "\n")
        stdin.seek(0)
        stdout = io.StringIO()
        server._running = True
        server._run(_stdin=stdin, _stdout=stdout)

        accepted = 0
        rejected = 0
        for line in stdout.getvalue().strip().split("\n"):
            if not line:
                continue
            msg = json.loads(line)
            if msg.get("data", {}).get("code") == "client.rate_limited":
                rejected += 1
            else:
                accepted += 1

        assert accepted == 4, (
            "Expected exactly 4 download_model commands accepted "
            "(burst=200 / cost=50 = 4); got "
            f"{accepted}. The per-command cost map and the stdin "
            "rate-limit chokepoint must agree on download_model=50."
        )
        assert rejected == 196, (
            f"Expected exactly 196 download_model commands rejected with client.rate_limited; got {rejected}."
        )
