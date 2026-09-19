"""The prior ``_RateLimiter.allow()`` implementation used a SINGLE deque"""

from __future__ import annotations

from voice_typer.server.ipc_server import (
    _RATE_LIMIT_BURST,
    _RATE_LIMIT_BURST_WINDOW_SECONDS,
    _RATE_LIMIT_SUSTAINED,
    _RATE_LIMIT_WINDOW_SECONDS,
    _RateLimiter,
)


class TestRateLimiterDualWindow:
    """IPC-4: burst (1s) and sustained (10s) are independent."""

    def test_slow_drip_trips_sustained_not_burst(self):
        """A client that sends 100 msgs/s for 7s (700 msgs in 10s"""
        rl = _RateLimiter(
            burst=_RATE_LIMIT_BURST,  # 200 per 1s
            sustained_per_sec=_RATE_LIMIT_SUSTAINED,  # 600 per 10s
            window=_RATE_LIMIT_WINDOW_SECONDS,  # 10.0
            burst_window=_RATE_LIMIT_BURST_WINDOW_SECONDS,  # 1.0
        )
        # Send 100 msgs/s for 7 seconds = 700 msgs total.
        accepted = 0
        rejected = 0
        for second in range(7):  # t=0, 1, 2, ..., 6
            for _ in range(100):
                if rl.allow(now=float(second)):
                    accepted += 1
                else:
                    rejected += 1
        # The first 600 are accepted (sustained deque fills to 600).
        assert accepted == 600, (
            f"expected 600 accepted (sustained cap), got {accepted}; "
            f"the sustained check is not catching the slow-drip attack"
        )
        assert rejected == 100, f"expected 100 rejected (700 - 600 sustained cap), got {rejected}"
        assert rl.rejected_count == 100

    def test_fast_burst_trips_burst_not_sustained(self):
        """A client that sends 201 msgs in 1s trips burst (200 cap)"""
        rl = _RateLimiter(
            burst=_RATE_LIMIT_BURST,
            sustained_per_sec=_RATE_LIMIT_SUSTAINED,
            window=_RATE_LIMIT_WINDOW_SECONDS,
            burst_window=_RATE_LIMIT_BURST_WINDOW_SECONDS,
        )
        accepted = 0
        rejected = 0
        # 201 msgs at t=0 (all in the same 1s burst window).
        for _ in range(201):
            if rl.allow(now=0.0):
                accepted += 1
            else:
                rejected += 1
        # First 200 accepted (burst deque fills to 200).
        assert accepted == 200, f"expected 200 accepted (burst cap), got {accepted}"
        assert rejected == 1, f"expected 1 rejected, got {rejected}"

    def test_sustained_check_reachable_with_production_config(self):
        """the production config (burst=200, sustained=600, window=10s,"""
        rl = _RateLimiter()  # production defaults
        # Send 60 msgs/s for 10s = 600 msgs (just under sustained).
        for second in range(10):
            for _ in range(60):
                assert rl.allow(now=float(second)) is True, (
                    f"msg at t={second} should be accepted (under both burst and sustained caps)"
                )
        # 601st msg at t=10.0, sustained deque has 600 entries (all
        assert rl.allow(now=10.0) is False, (
            "601st msg in 10s window must be rejected by sustained check (IPC-4: sustained is no longer dead code)"
        )
        assert rl.rejected_count == 1

    def test_burst_window_slides_independently(self):
        """The burst deque (1s) slides independently of the sustained"""
        rl = _RateLimiter(
            burst=10,
            sustained_per_sec=20,
            window=10.0,
            burst_window=1.0,
        )
        # Send 10 msgs at t=0 (fills burst deque to 10).
        for _ in range(10):
            assert rl.allow(now=0.0) is True
        # 11th at t=0.5, burst deque still has 10 (within 1s window).
        assert rl.allow(now=0.5) is False, "burst should reject (10 in 1s window)"
        # Wait 1.5s, burst deque slides past t=0 (cutoff = 1.5 - 1.0 = 0.5;
        assert rl.allow(now=1.5) is True, (
            "burst deque should have slid past t=0, allowing a new msg "
            "(sustained deque has 11 entries, well under 20 cap)"
        )
        # Sustained deque now has 11 entries (10 from t=0 + 1 from t=1.5).
        for _ in range(9):
            assert rl.allow(now=1.5) is True, (
                "burst deque has 1 entry (the prior t=1.5 msg), well under 10; sustained deque fills toward 20"
            )
        # 21st entry in sustained → reject.
        assert rl.allow(now=1.5) is False, "sustained should reject (20 entries in 10s window)"

    def test_reject_counter_independent_of_check_that_fired(self):
        """``rejected_count`` is incremented once per rejection,"""
        rl = _RateLimiter(burst=2, sustained_per_sec=3, window=10.0, burst_window=1.0)
        # 2 accepted at t=0 (burst fills).
        assert rl.allow(now=0.0) is True
        assert rl.allow(now=0.0) is True
        # 3rd at t=0, burst rejects (2 >= 2).
        assert rl.allow(now=0.0) is False
        # 4th at t=0, burst rejects again.
        assert rl.allow(now=0.0) is False
        assert rl.rejected_count == 2
        # At t=2.0 (burst deque slides past t=0), 3rd accepted
        assert rl.allow(now=2.0) is True
        # 4th at t=2.0, sustained rejects (3 >= 3).
        assert rl.allow(now=2.0) is False
        # Total rejections: 2 (burst) + 1 (sustained) = 3.
        assert rl.rejected_count == 3

    def test_burst_window_parameter_is_configurable(self):
        """The ``burst_window`` parameter lets tests use a smaller"""
        rl = _RateLimiter(burst=5, sustained_per_sec=100, window=10.0, burst_window=0.5)
        # 5 msgs at t=0, burst fills (5 in 0.5s window).
        for _ in range(5):
            assert rl.allow(now=0.0) is True
        # 6th at t=0.4, burst rejects (still in 0.5s window).
        assert rl.allow(now=0.4) is False
        # 7th at t=0.6, burst deque slides (cutoff = 0.6 - 0.5 = 0.1;
        assert rl.allow(now=0.6) is True
