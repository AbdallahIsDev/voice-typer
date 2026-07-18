"""IPC-4: rate limiter ``sustained`` check is no longer dead code.

The prior ``_RateLimiter.allow()`` implementation used a SINGLE deque
for both the burst and sustained checks (both keyed off the same
``window`` parameter, default 10s). With ``burst=200`` and
``sustained=600`` over the same 10s deque, the burst check
(``len >= 200``) ALWAYS fired first, making the sustained check
(``len >= 600``) unreachable dead code.

The IPC-4 fix splits the limiter into TWO independent deques:

* ``_burst_timestamps`` — 1-second window (configurable via the new
  ``burst_window`` parameter, default ``_RATE_LIMIT_BURST_WINDOW_SECONDS
  = 1.0``).
* ``_sustained_timestamps`` — ``window``-second window (default 10s).

The two checks are now genuinely independent. A client can trip burst
(201 msgs in 1s) without tripping sustained (601 msgs in 10s), and
vice versa.

These tests pin the new behavior. The existing ``TestRateLimiter``
suite in ``tests/test_server.py`` covers the legacy single-window
contract (with ``window=1.0`` for both); this file covers the new
dual-window contract.
"""

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
        """A client that sends 100 msgs/s for 7s (700 msgs in 10s
        window) trips sustained (600 cap) but NOT burst (200/s cap).

        Pre-IPC-4: this client was NOT throttled — the single-deque
        impl never reached 200 in the 10s window until t=2s (200
        msgs), at which point burst fired first and the client was
        throttled on the 201st msg. The sustained check (600) was
        unreachable.

        Post-IPC-4: the burst deque (1s window) only sees the 100
        msgs from the current second, so burst (200) never trips.
        The sustained deque (10s window) accumulates 700 timestamps
        by t=7s, so sustained (600) trips on the 601st msg.
        """
        rl = _RateLimiter(
            burst=_RATE_LIMIT_BURST,  # 200 per 1s
            sustained_per_sec=_RATE_LIMIT_SUSTAINED,  # 600 per 10s
            window=_RATE_LIMIT_WINDOW_SECONDS,  # 10.0
            burst_window=_RATE_LIMIT_BURST_WINDOW_SECONDS,  # 1.0
        )
        # Send 100 msgs/s for 7 seconds = 700 msgs total.
        # At each second boundary, the burst deque (1s window) only
        # contains the 100 msgs from the current second — well under
        # the 200 burst cap.
        accepted = 0
        rejected = 0
        for second in range(7):  # t=0, 1, 2, ..., 6
            for _ in range(100):
                if rl.allow(now=float(second)):
                    accepted += 1
                else:
                    rejected += 1
        # The first 600 are accepted (sustained deque fills to 600).
        # The remaining 100 (601st through 700th) are rejected by
        # the sustained check.
        assert accepted == 600, (
            f"expected 600 accepted (sustained cap), got {accepted}; "
            f"the sustained check is not catching the slow-drip attack"
        )
        assert rejected == 100, f"expected 100 rejected (700 - 600 sustained cap), got {rejected}"
        assert rl.rejected_count == 100

    def test_fast_burst_trips_burst_not_sustained(self):
        """A client that sends 201 msgs in 1s trips burst (200 cap)
        but the sustained deque (10s window) only has 201 entries —
        well under the 600 sustained cap.

        This confirms the burst check is still active and catches
        fast-burst attacks even when sustained would not.
        """
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
        # 201st rejected by burst (200 >= 200).
        assert accepted == 200, f"expected 200 accepted (burst cap), got {accepted}"
        assert rejected == 1, f"expected 1 rejected, got {rejected}"

    def test_sustained_check_reachable_with_production_config(self):
        """Explicit test that the sustained check is REACHABLE with
        the production config (burst=200, sustained=600, window=10s,
        burst_window=1s).

        Pre-IPC-4, this test would have FAILED — the sustained check
        was dead code (burst always fired first). Post-IPC-4, the
        sustained check fires on the 601st msg in a 10s window even
        when no 1s window exceeds 200.
        """
        rl = _RateLimiter()  # production defaults
        # Send 60 msgs/s for 10s = 600 msgs (just under sustained).
        for second in range(10):
            for _ in range(60):
                assert rl.allow(now=float(second)) is True, (
                    f"msg at t={second} should be accepted (under both burst and sustained caps)"
                )
        # 601st msg at t=10.0 — sustained deque has 600 entries (all
        # within the 10s window: t=0.0 through t=9.0, all > 10.0 - 10.0
        # = 0.0; the t=0.0 timestamps are not < 0.0 so they stay).
        # Burst deque (1s window) only has the 60 from t=9.0
        # (t=0.0..8.0 evicted). 60 < 200, burst allows. 600 >= 600,
        # sustained rejects.
        assert rl.allow(now=10.0) is False, (
            "601st msg in 10s window must be rejected by sustained check (IPC-4: sustained is no longer dead code)"
        )
        assert rl.rejected_count == 1

    def test_burst_window_slides_independently(self):
        """The burst deque (1s) slides independently of the sustained
        deque (10s). A 5-second pause clears the burst deque but
        leaves most of the sustained deque populated.
        """
        rl = _RateLimiter(
            burst=10,
            sustained_per_sec=20,
            window=10.0,
            burst_window=1.0,
        )
        # Send 10 msgs at t=0 (fills burst deque to 10).
        for _ in range(10):
            assert rl.allow(now=0.0) is True
        # 11th at t=0.5 — burst deque still has 10 (within 1s window).
        assert rl.allow(now=0.5) is False, "burst should reject (10 in 1s window)"
        # Wait 1.5s — burst deque slides past t=0 (cutoff = 1.5 - 1.0 = 0.5;
        # t=0.0 < 0.5, evicted). Sustained deque (cutoff = 1.5 - 10 = -8.5)
        # still has all 10.
        assert rl.allow(now=1.5) is True, (
            "burst deque should have slid past t=0, allowing a new msg "
            "(sustained deque has 11 entries, well under 20 cap)"
        )
        # Sustained deque now has 11 entries (10 from t=0 + 1 from t=1.5).
        # Send 9 more at t=1.5 to fill sustained to 20.
        for _ in range(9):
            assert rl.allow(now=1.5) is True, (
                "burst deque has 1 entry (the prior t=1.5 msg), well under 10; sustained deque fills toward 20"
            )
        # 21st entry in sustained → reject.
        assert rl.allow(now=1.5) is False, "sustained should reject (20 entries in 10s window)"

    def test_reject_counter_independent_of_check_that_fired(self):
        """``rejected_count`` is incremented once per rejection,
        regardless of whether burst or sustained fired."""
        rl = _RateLimiter(burst=2, sustained_per_sec=3, window=10.0, burst_window=1.0)
        # 2 accepted at t=0 (burst fills).
        assert rl.allow(now=0.0) is True
        assert rl.allow(now=0.0) is True
        # 3rd at t=0 — burst rejects (2 >= 2).
        assert rl.allow(now=0.0) is False
        # 4th at t=0 — burst rejects again.
        assert rl.allow(now=0.0) is False
        assert rl.rejected_count == 2
        # At t=2.0 (burst deque slides past t=0), 3rd accepted
        # (sustained deque now has 3 entries: 2 from t=0 + 1 from t=2.0).
        assert rl.allow(now=2.0) is True
        # 4th at t=2.0 — sustained rejects (3 >= 3).
        assert rl.allow(now=2.0) is False
        # Total rejections: 2 (burst) + 1 (sustained) = 3.
        assert rl.rejected_count == 3

    def test_burst_window_parameter_is_configurable(self):
        """The ``burst_window`` parameter lets tests use a smaller
        burst window (e.g. 0.5s) without changing the sustained window.
        """
        rl = _RateLimiter(burst=5, sustained_per_sec=100, window=10.0, burst_window=0.5)
        # 5 msgs at t=0 — burst fills (5 in 0.5s window).
        for _ in range(5):
            assert rl.allow(now=0.0) is True
        # 6th at t=0.4 — burst rejects (still in 0.5s window).
        assert rl.allow(now=0.4) is False
        # 7th at t=0.6 — burst deque slides (cutoff = 0.6 - 0.5 = 0.1;
        # t=0.0 < 0.1, evicted). Allowed.
        assert rl.allow(now=0.6) is True
