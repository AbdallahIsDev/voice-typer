"""DJ-42: idle-footprint regression test for ``CrashRecovery._save_loop``.

The save-loop worker previously used ``queue.get(timeout=1.0)`` which
woke the worker every second even when no saves were queued. Each wake
cost a ``queue.Empty`` exception (cheap) but kept the process pinned at
1 Hz wakeups — preventing the kernel from promoting the process to a
deeper idle state and showing up as a constant ~0.1% CPU drain in
Task Manager / top.

DJ-42 bumps the timeout to 30 s. The ``None`` sentinel from
``shutdown()`` is what wakes the worker in the normal stop path — the
timeout only exists as a fallback for the rare ``queue.Full`` failure
mode where ``shutdown()``'s ``put_nowait(None)`` is suppressed. The
30 s fallback still recovers that rare path (the worker re-checks
``_stopped`` at the top of the loop) without penalising the common
idle case.

These tests pin the fix so a future revert (reducing the timeout back
to 1 s) fails loudly.
"""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Point config to a temp directory so the recovery file lands in tmp."""
    return tmp_config_dir


@pytest.fixture
def cr(recovery_dir):
    """Create a CrashRecovery instance with a temp dir + tear it down."""
    from voice_typer.server.crash_recovery import CrashRecovery

    inst = CrashRecovery(config_dir=recovery_dir)
    yield inst
    inst.shutdown()
    if inst._save_thread is not None:
        inst._save_thread.join(timeout=2.0)


# tests ──────────────────────────────────────────────────────────


class TestCrashRecoveryIdleFootprint:
    """DJ-42: the save loop must not wake more than once per 30s when idle."""

    def test_idle_loop_does_not_wake_every_second(self, cr):
        """When the save queue is empty, the worker must NOT wake every 1s.

        Approach: drain pending saves via ``flush()``, then patch
        ``_save_queue.get`` to count calls. Wait 2.5 s and assert the
        call count is 0 (the new 30 s timeout means the blocking
        ``get()`` is still in progress — no timeout fires).

        With the old 1 s timeout, this test would observe ~2-3 calls
        in the 2.5 s window.
        """
        # Drain any pending saves so the worker is truly idle.
        assert cr.flush(timeout=5.0) is True

        call_count = {"n": 0}
        original_get = cr._save_queue.get

        def counting_get(*args, **kwargs):
            call_count["n"] += 1
            return original_get(*args, **kwargs)

        # The worker is currently blocked inside its existing ``get()``
        # call — patching ``get`` on the queue instance does NOT
        # interrupt that in-flight call. The patch only takes effect on
        # the NEXT call (i.e., after the existing call returns). With
        # the new 30 s timeout, the existing call doesn't return within
        # our 2.5 s window → call_count stays at 0.
        with patch.object(cr._save_queue, "get", counting_get):
            time.sleep(2.5)

        # Old code (1 s timeout): ~2-3 calls.
        # New code (30 s timeout): 0 calls.
        assert call_count["n"] == 0, (
            f"DJ-42: save loop woke {call_count['n']} times in 2.5 s while idle — "
            f"the 1 s timeout was supposed to be bumped to 30 s. The old code "
            f"would produce ~2-3 wakes; the new code should produce 0."
        )

    def test_idle_loop_wakes_on_shutdown_sentinel(self, cr):
        """The ``None`` sentinel from ``shutdown()`` must wake the worker
        immediately — the longer 30 s timeout must NOT delay shutdown.

        This pins the contract that ``shutdown()`` remains responsive:
        the longer idle timeout is a fallback, not the primary wake
        mechanism.
        """
        # Drain pending saves.
        assert cr.flush(timeout=5.0) is True

        # shutdown() enqueues None and joins the worker with a 1 s
        # timeout. If the worker were using a 30 s blocking get without
        # the sentinel wake, the join would time out and the worker
        # would still be alive. With the sentinel, the worker exits
        # immediately.
        t0 = time.perf_counter()
        cr.shutdown()
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.5, (
            f"DJ-42: shutdown took {elapsed:.2f}s — the None sentinel should "
            f"wake the worker immediately, regardless of the 30 s idle timeout."
        )
        if cr._save_thread is not None:
            assert not cr._save_thread.is_alive(), (
                "DJ-42: worker thread is still alive after shutdown — the "
                "None sentinel did not wake it from the blocking get()."
            )

    def test_save_loop_uses_long_timeout(self, cr, monkeypatch):
        """The ``get()`` call inside ``_save_loop`` must use timeout >= 30 s.

        We can't easily inspect the timeout argument from outside, but
        we can verify it's NOT the old 1 s value by checking that the
        worker is still blocked in ``get()`` after a 2 s wait (with the
        old 1 s timeout, the worker would have cycled at least once).

        Implementation: patch ``queue.Queue.get`` to record the timeout
        argument it was called with, then trigger a fresh ``get()`` by
        enqueuing a sentinel. The recorded timeout must be >= 30.0.
        """
        # Drain pending saves.
        assert cr.flush(timeout=5.0) is True

        captured_timeouts: list[float | None] = []
        original_get = cr._save_queue.get

        def spy_get(block=True, timeout=None):
            captured_timeouts.append(timeout)
            return original_get(block=block, timeout=timeout)

        with patch.object(cr._save_queue, "get", spy_get):
            # Enqueue a save so the worker's current blocking get()
            # returns. The next iteration calls our patched get() with
            # the production timeout.
            cr.add("trigger", pasted=False)
            # Give the worker time to process the save and re-enter get().
            time.sleep(0.5)

        # The first captured call is the post-save get(); its timeout
        # must be the production 30 s, not the old 1 s.
        assert captured_timeouts, (
            "DJ-42: spy never observed a queue.get() call — the worker "
            "did not re-enter get() within the 0.5 s window. Test setup issue."
        )
        # The most recent call is the idle re-entry (after the save was
        # processed). That's the call whose timeout we want to verify.
        last_timeout = captured_timeouts[-1]
        assert last_timeout is not None, (
            f"DJ-42: queue.get() was called with timeout=None (blocking "
            f"forever) — that's a different fix than intended; expected "
            f"a 30 s fallback timeout. Captured: {captured_timeouts!r}"
        )
        assert last_timeout >= 30.0, (
            f"DJ-42: queue.get() called with timeout={last_timeout}s — "
            f"the old 1 s value would cause 1 Hz idle wakes. Expected "
            f">= 30 s. Captured: {captured_timeouts!r}"
        )
