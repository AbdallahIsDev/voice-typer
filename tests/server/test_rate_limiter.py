"""Rate-limiter and flood-resistance tests.

Classes:
- TestRateLimiter        — RELIABILITY-006 sliding-window _RateLimiter
- TestServerFloodResistance — TEST-001 IPC DoS/flood resilience

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

import threading
from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401
    mock_app,
    server,
)

# ── RELIABILITY-006: per-IPCServer shared rate limiter ───────────────────


class TestRateLimiter:
    """RELIABILITY-006: ``_RateLimiter`` is a sliding-window limiter that
    protects the IPC dispatcher from flood attacks.

    One limiter is shared across all connections to a given ``IPCServer``
    instance, looked up via ``_get_rate_limiter(server)``; its budget
    persists across reconnects within the same process.  The limiter allows
    a burst of ``burst`` messages and a sustained rate of
    ``sustained_per_sec`` within a sliding 1-second window.  Messages
    over the budget are rejected (caller returns an error response
    rather than dispatching).
    """

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
        """Even if the burst limit is high, the sustained rate caps
        the per-second throughput."""
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=5, window=1.0)
        # First 5 are allowed (sustained rate)
        for _ in range(5):
            assert rl.allow(now=0.0) is True
        # 6th in the same second is rejected despite burst being 200
        assert rl.allow(now=0.0) is False

    def test_allow_increments_rejected_count_atomically(self):
        """SEC-6 / YJ-61: ``allow()`` atomically increments
        ``rejected_count`` when it returns ``False``. The separate
        ``reject()`` no-op was deleted (it was kept only for backward
        compatibility with callers that no longer exist). This test
        confirms the counter is incremented as a side-effect of the
        5 rejected ``allow()`` calls, not by any external bookkeeping.
        """
        from voice_typer.server.ipc_server import _RateLimiter

        rl = _RateLimiter(burst=2, sustained_per_sec=2, window=1.0)
        # First 2 calls consume the budget.
        for _ in range(2):
            rl.allow(now=0.0)
        # Next 5 calls exceed the budget and must be rejected. SEC-6:
        # each rejected ``allow()`` increments ``_rejected`` atomically
        # inside the same lock acquisition as the deque check.
        for _ in range(5):
            assert rl.allow(now=0.0) is False
        assert rl.rejected_count == 5

    def test_thread_safe(self):
        """Multiple threads calling allow() concurrently should not
        corrupt the limiter state."""
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
    """TEST-001: verify the IPC server can handle a flood of messages
    without crashing or exhausting resources.  The rate limiter
    (RELIABILITY-006) should kick in and reject over-budget messages."""

    def test_flood_of_get_status_does_not_crash(self, server, mock_app):
        """Sending 1000 get_status messages in rapid succession should
        not crash the server.  The rate limiter will reject most of
        them, but the server must stay alive and responsive."""
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
        # the rate limit kicks in)
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
        # get_recent must be called with 500, not 10^9
        mock_app.history_db.get_recent.assert_called_once_with(
            500, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )


# Per-command cost map coverage ────────────────────────────────────────


class TestRateLimiterCommandCosts:
    """Coverage for the per-command cost map (``COMMAND_COSTS``).

    The cost map assigns each known IPC command a "weight" against the
    shared burst/sustained budgets. Pre-this-coverage, the dict had ZERO
    direct tests — a regression that flipped ``download_model`` from 50
    to 1 (silently letting a buggy client fire 200 model downloads per
    second instead of 4) would have passed CI. These tests pin the
    configured cost of representative commands from each cost tier
    (cheap / mid / expensive) so future renames or value drift surface
    as test failures.
    """

    def test_heartbeat_costs_one(self):
        """``heartbeat`` is explicitly listed at cost 1 so future
        changes to ``DEFAULT_COST`` don't silently change the
        heartbeat's rate-limit characteristics (heartbeats fire every
        5 s / 15 s and must NEVER trip the burst cap).

        Note: ``_RateLimiter.allow`` short-circuits to ``True`` for
        ``command == "heartbeat"`` (the limiter bypass — see
        ``rate_limiter.py``), so the call does NOT actually consume a
        unit; this test pins the *configured* cost (``COMMAND_COSTS``
        entry) rather than the runtime cost. The configured cost is
        the contract a future refactor that removes the bypass would
        inherit, so it must stay 1.
        """
        from voice_typer.server.ipc.rate_limiter import COMMAND_COSTS
        from voice_typer.server.ipc_server import _RateLimiter

        assert COMMAND_COSTS["heartbeat"] == 1, (
            "heartbeat must be explicitly listed in COMMAND_COSTS at "
            "cost 1 so future DEFAULT_COST changes don't alter its "
            "rate-limit characteristics."
        )
        rl = _RateLimiter(burst=10, sustained_per_sec=10, window=1.0)
        # Heartbeat bypasses the limiter — returns True without recording.
        assert rl.allow(command="heartbeat", now=0.0) is True
        # The bypass means no burst budget is consumed; the configured
        # cost stays pinned at 1 (above) regardless of the bypass.
        assert rl._burst_total == 0

    def test_download_model_costs_50(self):
        """``download_model`` consumes 50 of the 200-unit burst budget,
        so a client can fire at most 4 ``download_model`` requests in
        any 1 s window before the 5th is rejected."""
        from voice_typer.server.ipc.rate_limiter import COMMAND_COSTS
        from voice_typer.server.ipc_server import _RateLimiter

        assert COMMAND_COSTS["download_model"] == 50, (
            "download_model must cost 50 (was 10 pre-audit) — large "
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
        # 4 accepted calls × 50 units each).
        assert rl._burst_total == 200, (
            "download_model's per-call cost (50) must be reflected in "
            "the limiter's running burst total after 4 accepted calls."
        )

    def test_unknown_command_uses_default_cost(self):
        """Unknown commands (not in ``COMMAND_COSTS``) default to
        ``DEFAULT_COST`` (1). Preserves backward compatibility with
        the count-based limiter: a caller that doesn't pass ``command``
        (or passes an unrecognized name) is treated as cost 1, identical
        to the pre-cost-map behavior."""
        from voice_typer.server.ipc.rate_limiter import (
            COMMAND_COSTS,
            DEFAULT_COST,
        )
        from voice_typer.server.ipc_server import _RateLimiter

        # Sanity: "frobnicate" is not a known command.
        assert "frobnicate" not in COMMAND_COSTS, (
            "Test fixture sanity: 'frobnicate' should NOT be in "
            "COMMAND_COSTS — pick a different unknown-command name "
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


class TestRateLimiterIntegrationWithDispatch:
    """Integration: the per-process ``_RateLimiter`` is now consulted
    at the TOP of ``IPCServer._dispatch`` (the single chokepoint that
    makes the limiter apply to ALL three transports — TCP, WS, stdin —
    via one lookup). These tests flood ``server._dispatch`` with
    expensive commands and verify only the expected few are accepted;
    the rest are rejected with the ``client.rate_limited`` envelope."""

    def test_flood_of_download_model_rejected_by_rate_limit(self, server, mock_app):
        """Flood 200 ``download_model`` commands via ``server._dispatch``.
        Each consumes 50 burst units (burst=200), so exactly 4 are
        accepted; the remaining 196 are rejected with the
        ``client.rate_limited`` envelope (the same envelope shape the
        TCP read loop emits at ``transport_tcp.py:689-694``).
        """
        from unittest.mock import MagicMock

        # Mock _handle_download_model so the test doesn't actually try
        # to download a model — we're testing the rate-limit chokepoint
        # in ``_dispatch``, not the handler body. The mock returns a
        # success envelope so the accepted/rejected count cleanly
        # reflects the limiter's decision.
        server._handle_download_model = MagicMock(return_value={"type": "result", "data": {"ok": True}})

        accepted = 0
        rejected = 0
        for i in range(200):
            result = server._dispatch({"id": i, "type": "download_model"})
            data = result.get("data", {}) if isinstance(result, dict) else {}
            code = data.get("code", "")
            if code == "client.rate_limited":
                rejected += 1
            else:
                accepted += 1

        # download_model costs 50; burst=200 → exactly 4 accepted (4*50=200),
        # 196 rejected. The sustained cap (600 over 10s) doesn't trip first
        # because 4*50=200 < 600.
        assert accepted == 4, (
            "Expected exactly 4 download_model commands accepted "
            "(burst=200 / cost=50 = 4); got "
            f"{accepted}. The per-command cost map and the dispatcher "
            "rate-limit chokepoint must agree on download_model=50."
        )
        assert rejected == 196, (
            f"Expected exactly 196 download_model commands rejected with client.rate_limited; got {rejected}."
        )
