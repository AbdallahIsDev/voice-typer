"""Regression tests for the keyring-call orphan/wedge/cooldown tracking."""

from __future__ import annotations

import logging
import threading
import time

import pytest
from voice_typer.server import credential_store


@pytest.fixture(autouse=True)
def _reset_keyring_state():
    """Reset the module-level orphan/wedge counters between tests."""
    with credential_store._keyring_state_lock:
        credential_store._backend._orphaned_thread_count = 0
        credential_store._backend._consecutive_timeouts = 0
        credential_store._backend._wedged_until = 0.0
    yield
    # Restore after the test too so a flaky run doesn't poison the suite.
    with credential_store._keyring_state_lock:
        credential_store._backend._orphaned_thread_count = 0
        credential_store._backend._consecutive_timeouts = 0
        credential_store._backend._wedged_until = 0.0


def _fast_timeout(monkeypatch, seconds: float = 0.05) -> None:
    """Shrink the per-call timeout so tests don't wait 5s each."""
    monkeypatch.setattr(credential_store, "_KEYRING_TIMEOUT_SECONDS", seconds)


def _fast_cooldown(monkeypatch, seconds: float = 0.1) -> None:
    """Shrink the wedge cooldown so we can test expiry quickly."""
    monkeypatch.setattr(credential_store, "_KEYRING_WEDGE_COOLDOWN_S", seconds)


class TestOrphanTracking:
    """``_orphaned_thread_count`` increments on timeout, decrements on finish."""

    def test_successful_call_leaves_no_orphans(self, monkeypatch):
        _fast_timeout(monkeypatch)
        # A fast-completing call, no orphan.
        result = credential_store._run_keyring_call(lambda: "ok")
        assert result == "ok"
        assert credential_store._backend._orphaned_thread_count == 0
        assert credential_store._backend._consecutive_timeouts == 0

    def test_timeout_increments_orphan_count(self, monkeypatch):
        _fast_timeout(monkeypatch)
        # A call that blocks longer than the timeout, should orphan.
        done = threading.Event()

        def slow_call() -> str:
            done.wait(timeout=2.0)  # Hold the thread open
            return "late"

        try:
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(slow_call)
            assert credential_store._backend._orphaned_thread_count == 1
            assert credential_store._backend._consecutive_timeouts == 1
        finally:
            # Let the orphan finish so it decrements the counter.
            done.set()
            # Wait for the orphan to decrement (poll, since we don't
            deadline = time.monotonic() + 2.0
            while credential_store._backend._orphaned_thread_count > 0 and time.monotonic() < deadline:
                time.sleep(0.01)
            assert credential_store._backend._orphaned_thread_count == 0, (
                "orphan thread did not decrement the counter on finish"
            )

    def test_non_timeout_exception_resets_consecutive_timeouts(self, monkeypatch):
        _fast_timeout(monkeypatch)
        # First call: timeout (consecutive=1).
        done = threading.Event()

        def slow_call() -> None:
            done.wait(timeout=2.0)

        try:
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(slow_call)
            assert credential_store._backend._consecutive_timeouts == 1

            # Second call: raises ValueError immediately, should reset
            with pytest.raises(ValueError):
                credential_store._run_keyring_call(_raise_value_error)
            assert credential_store._backend._consecutive_timeouts == 0
        finally:
            done.set()


def _raise_value_error() -> None:
    raise ValueError("backend blew up")


class TestWedgeCooldown:
    """2 consecutive timeouts wedge the backend; subsequent calls short-circuit."""

    def test_single_timeout_does_not_wedge(self, monkeypatch):
        _fast_timeout(monkeypatch)
        done = threading.Event()

        def slow_call() -> None:
            done.wait(timeout=2.0)

        try:
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._run_keyring_call(slow_call)
            msg = str(exc_info.value)
            # First timeout, no wedge message.
            assert "wedged" not in msg, msg
            assert credential_store._backend._wedged_until == 0.0
        finally:
            done.set()

    def test_two_consecutive_timeouts_wedge(self, monkeypatch):
        _fast_timeout(monkeypatch)
        _fast_cooldown(monkeypatch)
        done1 = threading.Event()
        done2 = threading.Event()

        def slow_call_1() -> None:
            done1.wait(timeout=2.0)

        def slow_call_2() -> None:
            done2.wait(timeout=2.0)

        try:
            # 1st timeout, consecutive=1, no wedge.
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(slow_call_1)
            assert credential_store._backend._consecutive_timeouts == 1
            assert credential_store._backend._wedged_until == 0.0

            # 2nd consecutive timeout, wedge engaged.
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._run_keyring_call(slow_call_2)
            msg = str(exc_info.value)
            assert "orphaned threads: 2" in msg, msg
            assert "consecutive timeouts: 2" in msg, msg
            assert credential_store._backend._consecutive_timeouts == 2
            assert credential_store._backend._wedged_until > 0.0

            # 3rd call, short-circuits with the wedge message.
            with pytest.raises(TimeoutError) as exc_info:
                credential_store._run_keyring_call(lambda: "should not run")
            msg = str(exc_info.value)
            assert "wedged" in msg, msg
            assert "cooldown" in msg, msg
            # No new orphan was spawned (short-circuit path).
            assert credential_store._backend._orphaned_thread_count == 2
        finally:
            done1.set()
            done2.set()

    def test_wedge_logs_warning(self, monkeypatch, caplog):
        _fast_timeout(monkeypatch)
        _fast_cooldown(monkeypatch)
        done1 = threading.Event()
        done2 = threading.Event()

        def slow_call_1() -> None:
            done1.wait(timeout=2.0)

        def slow_call_2() -> None:
            done2.wait(timeout=2.0)

        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
                with pytest.raises(TimeoutError):
                    credential_store._run_keyring_call(slow_call_1)
                with pytest.raises(TimeoutError):
                    credential_store._run_keyring_call(slow_call_2)
            # The wedge message should appear in the WARNING logs.
            wedge_logs = [r for r in caplog.records if "wedged" in r.getMessage()]
            assert len(wedge_logs) >= 1, "expected a 'wedged' WARNING log on 2nd consecutive timeout"
        finally:
            done1.set()
            done2.set()

    def test_wedge_expires_and_resets(self, monkeypatch):
        _fast_timeout(monkeypatch)
        # Very short cooldown so it expires before the next call.
        monkeypatch.setattr(credential_store, "_KEYRING_WEDGE_COOLDOWN_S", 0.05)
        done1 = threading.Event()
        done2 = threading.Event()

        def slow_call_1() -> None:
            done1.wait(timeout=2.0)

        def slow_call_2() -> None:
            done2.wait(timeout=2.0)

        try:
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(slow_call_1)
            with pytest.raises(TimeoutError):
                credential_store._run_keyring_call(slow_call_2)
            assert credential_store._backend._wedged_until > 0.0
            assert credential_store._backend._consecutive_timeouts == 2

            # Wait for the cooldown to expire.
            time.sleep(0.1)

            # Next call: succeeds, wedge is cleared, consecutive reset.
            result = credential_store._run_keyring_call(lambda: "recovered")
            assert result == "recovered"
            assert credential_store._backend._wedged_until == 0.0
            assert credential_store._backend._consecutive_timeouts == 0
        finally:
            done1.set()
            done2.set()


class TestOrphanThresholdWarning:
    """A WARNING is logged when the orphan count exceeds the threshold."""

    def test_threshold_warning_fires(self, monkeypatch, caplog):
        _fast_timeout(monkeypatch)
        # Lower the threshold so we don't need to spawn 20 orphans.
        monkeypatch.setattr(credential_store, "_KEYRING_ORPHAN_WARN_THRESHOLD", 1)
        # Long cooldown so the wedge (which fires on the 2nd consecutive
        monkeypatch.setattr(credential_store, "_KEYRING_WEDGE_COOLDOWN_S", 60.0)

        done = threading.Event()

        def slow_call() -> None:
            done.wait(timeout=5.0)

        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.credential_store"):
                # Spawn 2 orphans, 1st: orphan_count=1, no threshold log
                for _ in range(2):
                    with pytest.raises(TimeoutError):
                        credential_store._run_keyring_call(slow_call)
            threshold_logs = [r for r in caplog.records if "orphaned keyring-io threads" in r.getMessage()]
            if not threshold_logs:
                # Bounded condition-wait, not a flaky single check: under
                import time as _time

                _deadline = _time.monotonic() + 2.0
                while _time.monotonic() < _deadline and not threshold_logs:
                    threshold_logs = [r for r in caplog.records if "orphaned keyring-io threads" in r.getMessage()]
                    if not threshold_logs:
                        _time.sleep(0.01)
            assert len(threshold_logs) >= 1, (
                "expected a threshold-exceeded WARNING when orphan count > threshold; "
                f"got logs: {[r.getMessage() for r in caplog.records]}"
            )
            assert credential_store._backend._orphaned_thread_count >= 2
        finally:
            done.set()
            # Wait for orphans to finish so they don't leak into other tests.
            deadline = time.monotonic() + 5.0
            while credential_store._backend._orphaned_thread_count > 0 and time.monotonic() < deadline:
                time.sleep(0.01)
