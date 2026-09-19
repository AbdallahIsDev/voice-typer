"""DJ-42: idle-footprint regression test for ``CrashRecovery._save_loop``."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


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


class TestCrashRecoveryIdleFootprint:
    """DJ-42: the save loop must not wake more than once per 30s when idle."""

    def test_idle_loop_does_not_wake_every_second(self, cr):
        """When the save queue is empty, the worker must NOT wake every 1s."""
        # Drain any pending saves so the worker is truly idle.
        assert cr.flush(timeout=5.0) is True

        call_count = {"n": 0}
        original_get = cr._save_queue.get

        def counting_get(*args, **kwargs):
            call_count["n"] += 1
            return original_get(*args, **kwargs)

        # The worker is currently blocked inside its existing ``get()``
        with patch.object(cr._save_queue, "get", counting_get):
            time.sleep(2.5)

        # Old code (1 s timeout): ~2-3 calls.
        assert call_count["n"] == 0, (
            f"DJ-42: save loop woke {call_count['n']} times in 2.5 s while idle, "
            f"the 1 s timeout was supposed to be bumped to 30 s. The old code "
            f"would produce ~2-3 wakes; the new code should produce 0."
        )

    def test_idle_loop_wakes_on_shutdown_sentinel(self, cr):
        """
        The ``None`` sentinel from ``shutdown()`` must wake the worker
        This pins the contract that ``shutdown()`` remains responsive:
        """
        # Drain pending saves.
        assert cr.flush(timeout=5.0) is True

        t0 = time.perf_counter()
        cr.shutdown()
        elapsed = time.perf_counter() - t0

        assert elapsed < 1.5, (
            f"DJ-42: shutdown took {elapsed:.2f}s, the None sentinel should "
            f"wake the worker immediately, regardless of the 30 s idle timeout."
        )
        if cr._save_thread is not None:
            assert not cr._save_thread.is_alive(), (
                "DJ-42: worker thread is still alive after shutdown, the "
                "None sentinel did not wake it from the blocking get()."
            )

    def test_save_loop_uses_long_timeout(self, cr, monkeypatch):
        """The ``get()`` call inside ``_save_loop`` must use timeout >= 30 s."""
        # Drain pending saves.
        assert cr.flush(timeout=5.0) is True

        captured_timeouts: list[float | None] = []
        original_get = cr._save_queue.get

        def spy_get(block=True, timeout=None):
            captured_timeouts.append(timeout)
            return original_get(block=block, timeout=timeout)

        with patch.object(cr._save_queue, "get", spy_get):
            # Enqueue a save so the worker's current blocking get()
            cr.add("trigger", pasted=False)
            # Give the worker time to process the save and re-enter get().
            time.sleep(0.5)

        # The first captured call is the post-save get(); its timeout
        assert captured_timeouts, (
            "DJ-42: spy never observed a queue.get() call, the worker "
            "did not re-enter get() within the 0.5 s window. Test setup issue."
        )
        # The most recent call is the idle re-entry (after the save was
        last_timeout = captured_timeouts[-1]
        assert last_timeout is not None, (
            f"DJ-42: queue.get() was called with timeout=None (blocking "
            f"forever), that's a different fix than intended; expected "
            f"a 30 s fallback timeout. Captured: {captured_timeouts!r}"
        )
        assert last_timeout >= 30.0, (
            f"DJ-42: queue.get() called with timeout={last_timeout}s, "
            f"the old 1 s value would cause 1 Hz idle wakes. Expected "
            f">= 30 s. Captured: {captured_timeouts!r}"
        )
