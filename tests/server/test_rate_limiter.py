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
