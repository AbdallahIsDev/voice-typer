"""UE-21 / UE-11-F7 / UE-11-F8 / UE-11-F9: tests for ``_timeout_utils``."""

from __future__ import annotations

import contextlib
import threading
import time

import pytest
from voice_typer.server import _timeout_utils as _tu
from voice_typer.server._timeout_utils import (
    _DE11_GRACE_PERIOD_SECONDS,
    _TIMEOUT,
    SHUTDOWN_WATCHDOG_TIMEOUT_S,
    TIMEOUT,
    _run_parallel_with_timeout,
    _run_with_timeout,
    join_leaked_workers,
)


@pytest.fixture(autouse=True)
def _reset_leaked_workers():
    """Clear ``_LEAKED_WORKERS`` before and after each test."""
    with _tu._LEAKED_WORKERS_LOCK:
        snapshot = list(_tu._LEAKED_WORKERS)
        _tu._LEAKED_WORKERS.clear()
    yield
    with _tu._LEAKED_WORKERS_LOCK:
        leftover = list(_tu._LEAKED_WORKERS)
        _tu._LEAKED_WORKERS.clear()
        _tu._LEAKED_WORKERS.extend(snapshot)
    # Best-effort join of leftover test threads so they don't linger.
    for t in leftover:
        if t.is_alive():
            with contextlib.suppress(Exception):
                t.join(timeout=0.5)


class TestLeakedWorkerRegistry:
    """UE-21 / UE-11-F7: ``_run_with_timeout`` tracks leaked workers."""

    def test_run_with_timeout_adds_leaked_worker_on_timeout(self):
        """When the worker doesn't finish in *timeout*, it's appended"""
        blocker = threading.Event()

        def _blocking():
            blocker.wait(timeout=5.0)

        try:
            result = _run_with_timeout("test-blocked", _blocking, timeout=0.1)
        finally:
            blocker.set()  # let the worker exit so it doesn't linger

        assert result is TIMEOUT, "must return TIMEOUT on timeout"
        # The worker must have been registered for best-effort join.
        with _tu._LEAKED_WORKERS_LOCK:
            assert len(_tu._LEAKED_WORKERS) == 1, "leaked worker must be appended to _LEAKED_WORKERS on TIMEOUT"
            assert _tu._LEAKED_WORKERS[0].name == "cleanup-test-blocked"

    def test_run_with_timeout_does_not_add_worker_on_success(self):
        """A worker that finishes in time is NOT added to the registry."""
        result = _run_with_timeout("test-ok", lambda: "ok", timeout=1.0)
        assert result == "ok"
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == [], "successful worker must not be added to _LEAKED_WORKERS"

    def test_run_with_timeout_does_not_add_worker_on_exception(self):
        """A worker that raises is NOT added to the registry (the"""

        def _raising():
            raise RuntimeError("boom")

        with pytest.raises(RuntimeError, match="boom"):
            _run_with_timeout("test-raise", _raising, timeout=1.0)
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == [], "worker that raised must not be added to _LEAKED_WORKERS"

    def test_run_with_timeout_returns_none_when_func_returns_none(self):
        """A worker that returns ``None`` is NOT a timeout, the"""
        result = _run_with_timeout("test-none", lambda: None, timeout=1.0)
        assert result is None
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == []


class TestJoinLeakedWorkers:
    """UE-21 / UE-11-F7: ``join_leaked_workers`` drains the registry."""

    def test_join_leaked_workers_returns_zero_on_empty_registry(self):
        """An empty registry returns 0 immediately."""
        assert join_leaked_workers(timeout=1.0) == 0

    def test_join_leaked_workers_prunes_already_dead_workers(self):
        """Workers that already exited are pruned without waiting."""
        # Manually append a dead thread to the registry.
        dead_t = threading.Thread(target=lambda: None, daemon=True)
        dead_t.start()
        dead_t.join(timeout=1.0)
        assert not dead_t.is_alive()
        with _tu._LEAKED_WORKERS_LOCK:
            _tu._LEAKED_WORKERS.append(dead_t)

        remaining = join_leaked_workers(timeout=1.0)
        assert remaining == 0, "dead worker must be pruned"
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == [], "dead worker must be removed"

    def test_join_leaked_workers_joins_alive_workers(self):
        """A leaked worker that's still alive gets joined (within the"""
        blocker = threading.Event()

        def _blocking():
            blocker.wait(timeout=5.0)

        t = threading.Thread(target=_blocking, daemon=True, name="test-leak")
        t.start()
        with _tu._LEAKED_WORKERS_LOCK:
            _tu._LEAKED_WORKERS.append(t)

        # Unblock the worker shortly so join_leaked_workers can reap it.
        def _unblock():
            time.sleep(0.1)
            blocker.set()

        unblock_thread = threading.Thread(target=_unblock, daemon=True)
        unblock_thread.start()

        remaining = join_leaked_workers(timeout=2.0)
        assert remaining == 0, f"worker should have been joined + pruned; remaining={remaining}"
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == [], "joined worker must be pruned"
        assert not t.is_alive(), "worker must have exited"
        unblock_thread.join(timeout=1.0)

    def test_join_leaked_workers_returns_count_of_still_alive(self):
        """registry; the return value is the count of remaining alive"""
        blocker = threading.Event()

        def _blocking():
            blocker.wait(timeout=30.0)

        t = threading.Thread(target=_blocking, daemon=True, name="test-stuck")
        t.start()
        with _tu._LEAKED_WORKERS_LOCK:
            _tu._LEAKED_WORKERS.append(t)
        try:
            remaining = join_leaked_workers(timeout=0.1)
            assert remaining == 1, "stuck worker must remain in registry; remaining should be 1"
            with _tu._LEAKED_WORKERS_LOCK:
                assert len(_tu._LEAKED_WORKERS) == 1, "stuck worker must still be in registry"
        finally:
            blocker.set()
            t.join(timeout=2.0)

    def test_join_leaked_workers_negative_timeout_clamped_to_zero(self):
        """A negative timeout is clamped to 0 (just prunes dead threads)."""
        dead_t = threading.Thread(target=lambda: None, daemon=True)
        dead_t.start()
        dead_t.join(timeout=1.0)
        with _tu._LEAKED_WORKERS_LOCK:
            _tu._LEAKED_WORKERS.append(dead_t)

        # Negative timeout → clamped to 0 → just prunes dead threads.
        remaining = join_leaked_workers(timeout=-1.0)
        assert remaining == 0
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == []

    def test_join_leaked_workers_zero_timeout_just_prunes(self):
        """``timeout=0`` skips the join phase but still prunes dead threads."""
        dead_t = threading.Thread(target=lambda: None, daemon=True)
        dead_t.start()
        dead_t.join(timeout=1.0)
        with _tu._LEAKED_WORKERS_LOCK:
            _tu._LEAKED_WORKERS.append(dead_t)

        remaining = join_leaked_workers(timeout=0.0)
        assert remaining == 0
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == []

    def test_leaked_worker_removed_after_it_eventually_finishes(self):
        """A leaked worker that eventually finishes (between two"""
        blocker = threading.Event()

        def _blocking():
            blocker.wait(timeout=5.0)

        t = threading.Thread(target=_blocking, daemon=True, name="test-eventual")
        t.start()
        with _tu._LEAKED_WORKERS_LOCK:
            _tu._LEAKED_WORKERS.append(t)

        # First call: worker still alive, stays in registry.
        remaining1 = join_leaked_workers(timeout=0.05)
        assert remaining1 == 1, "stuck worker should still be alive"
        # Now let it finish.
        blocker.set()
        t.join(timeout=2.0)
        assert not t.is_alive()
        # Second call: worker is now dead, pruned.
        remaining2 = join_leaked_workers(timeout=1.0)
        assert remaining2 == 0, "finished worker must be pruned"
        with _tu._LEAKED_WORKERS_LOCK:
            assert _tu._LEAKED_WORKERS == []


class TestLeakedWorkerRegistryThreadSafety:
    """UE-21 / UE-11-F7: the registry is guarded by a lock."""

    def test_concurrent_timeouts_all_register(self):
        """Multiple concurrent ``_run_with_timeout`` calls that time out"""
        blocker = threading.Event()
        n = 8

        def _blocking():
            blocker.wait(timeout=5.0)

        def _one():
            _run_with_timeout("test-concurrent", _blocking, timeout=0.05)

        threads = [threading.Thread(target=_one, daemon=True) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        try:
            with _tu._LEAKED_WORKERS_LOCK:
                assert len(_tu._LEAKED_WORKERS) == n, (
                    f"all {n} leaked workers must be registered; got {len(_tu._LEAKED_WORKERS)}"
                )
        finally:
            blocker.set()
            # Drain so the test doesn't leak threads.
            join_leaked_workers(timeout=2.0)

    def test_concurrent_append_and_join_no_race(self):
        """Concurrent ``_run_with_timeout`` (appending) +"""
        blocker = threading.Event()
        errors: list[Exception] = []

        def _appender():
            try:
                for _ in range(10):

                    def _blocking():
                        blocker.wait(timeout=5.0)

                    _run_with_timeout("test-race", _blocking, timeout=0.01)
            except Exception as e:
                errors.append(e)

        def _drainer():
            try:
                for _ in range(20):
                    join_leaked_workers(timeout=0.0)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=_appender, daemon=True),
            threading.Thread(target=_appender, daemon=True),
            threading.Thread(target=_drainer, daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
        try:
            assert errors == [], f"concurrent ops raised: {errors}"
        finally:
            blocker.set()
            join_leaked_workers(timeout=2.0)


class TestRunParallelWithTimeoutDuplicateDesc:
    """UE-11-F8: ``_run_parallel_with_timeout`` rejects duplicate descs."""

    def test_duplicate_desc_raises_value_error(self):
        """Two items with the same ``desc`` raise ``ValueError`` (not"""
        items = [
            ("dup", lambda: 1, 1.0),
            ("dup", lambda: 2, 1.0),
        ]
        with pytest.raises(ValueError, match="duplicate descriptions"):
            _run_parallel_with_timeout(items)

    def test_unique_descs_succeed(self):
        """Unique descs work normally (regression: the guard shouldn't"""
        items = [
            ("a", lambda: 1, 1.0),
            ("b", lambda: 2, 1.0),
            ("c", lambda: 3, 1.0),
        ]
        results = _run_parallel_with_timeout(items)
        # Results are re-ordered to match input order.
        assert [desc for (desc, _value) in results] == ["a", "b", "c"]
        assert [value for (_desc, value) in results] == [1, 2, 3]

    def test_error_message_lists_the_duplicate(self):
        """The error message names the duplicate desc so the caller"""
        items = [
            ("alpha", lambda: 1, 1.0),
            ("beta", lambda: 2, 1.0),
            ("alpha", lambda: 3, 1.0),
        ]
        with pytest.raises(ValueError) as exc_info:
            _run_parallel_with_timeout(items)
        msg = str(exc_info.value)
        assert "alpha" in msg, f"error message should name the duplicate; got: {msg}"

    def test_triple_duplicate_raises(self):
        """Three items with the same desc also raise."""
        items = [
            ("same", lambda: 1, 1.0),
            ("same", lambda: 2, 1.0),
            ("same", lambda: 3, 1.0),
        ]
        with pytest.raises(ValueError, match="duplicate descriptions"):
            _run_parallel_with_timeout(items)

    def test_empty_items_does_not_check_uniqueness(self):
        """Empty items short-circuits before the uniqueness check."""
        assert _run_parallel_with_timeout([]) == []

    def test_single_item_passes(self):
        """A single item trivially has unique descs."""
        results = _run_parallel_with_timeout([("only", lambda: 42, 1.0)])
        assert results == [("only", 42)]

    def test_duplicate_desc_does_not_run_any_func(self):
        """The uniqueness check fires BEFORE any func is submitted to"""
        call_count = 0
        lock = threading.Lock()

        def _counting():
            nonlocal call_count
            with lock:
                call_count += 1
            return call_count

        items = [
            ("dup", _counting, 1.0),
            ("dup", _counting, 1.0),
        ]
        with pytest.raises(ValueError):
            _run_parallel_with_timeout(items)
        # The check is at entry; no func should have run.
        assert call_count == 0, f"uniqueness check should fire before any func runs; call_count={call_count}"

    def test_timeout_sentinel_propagates_in_parallel(self):
        """A func that times out returns ``TIMEOUT`` in its result tuple"""
        blocker = threading.Event()

        def _blocking():
            blocker.wait(timeout=5.0)

        try:
            items = [
                ("fast", lambda: "ok", 1.0),
                ("slow", _blocking, 0.05),
            ]
            results = _run_parallel_with_timeout(items)
            by_desc = dict(results)
            assert by_desc["fast"] == "ok"
            assert by_desc["slow"] is TIMEOUT, f"slow func should return TIMEOUT; got {by_desc['slow']!r}"
        finally:
            blocker.set()
            # Drain the leaked worker.
            join_leaked_workers(timeout=2.0)


# __all__ cleanup ──────────────────────────────────────────


class TestAllCleanup:
    """UE-11-F9: ``__all__`` no longer exports the alias names."""

    def test_all_does_not_contain_timeout_alias(self):
        """``_TIMEOUT`` is removed from ``__all__``."""
        assert "_TIMEOUT" not in _tu.__all__, f"_TIMEOUT should not be in __all__; got: {_tu.__all__}"

    def test_all_does_not_contain_grace_period_alias(self):
        """``_DE11_GRACE_PERIOD_SECONDS`` is removed from ``__all__``."""
        assert "_DE11_GRACE_PERIOD_SECONDS" not in _tu.__all__, (
            f"_DE11_GRACE_PERIOD_SECONDS should not be in __all__; got: {_tu.__all__}"
        )

    def test_all_contains_canonical_timeout(self):
        """``TIMEOUT`` remains in ``__all__``."""
        assert "TIMEOUT" in _tu.__all__

    def test_all_contains_canonical_watchdog_constant(self):
        """``SHUTDOWN_WATCHDOG_TIMEOUT_S`` remains in ``__all__``."""
        assert "SHUTDOWN_WATCHDOG_TIMEOUT_S" in _tu.__all__

    def test_all_contains_join_leaked_workers(self):
        """``join_leaked_workers`` is in ``__all__`` (new public API)."""
        assert "join_leaked_workers" in _tu.__all__

    def test_timeout_alias_still_module_level(self):
        """``_TIMEOUT`` is still accessible as a module attribute"""
        assert _tu._TIMEOUT is TIMEOUT, "_TIMEOUT must remain a module-level alias for TIMEOUT"
        assert _TIMEOUT is _tu.TIMEOUT

    def test_de11_grace_period_alias_still_module_level(self):
        """``_DE11_GRACE_PERIOD_SECONDS`` is still accessible as a"""
        assert _tu._DE11_GRACE_PERIOD_SECONDS == 1.0
        # And it must equal the canonical constant.
        assert _DE11_GRACE_PERIOD_SECONDS == SHUTDOWN_WATCHDOG_TIMEOUT_S

    def test_star_import_does_not_pull_timeout_alias(self):
        """``from _timeout_utils import *`` does NOT bind ``_TIMEOUT``"""
        # The contract: every name in __all__ is exported; names NOT
        for name in _tu.__all__:
            assert hasattr(_tu, name), f"__all__ entry {name!r} missing from module"
        # The aliases are accessible via getattr but NOT in __all__.
        assert hasattr(_tu, "_TIMEOUT"), "alias must still be getattr-able"
        assert hasattr(_tu, "_DE11_GRACE_PERIOD_SECONDS"), "alias must still be getattr-able"


class TestRunParallelSubmitShutdownRace:
    """``RuntimeError('cannot schedule new futures after interpreter"""

    def test_submit_runtime_error_recorded_as_item_failure(self, monkeypatch):
        """When ``pool.submit`` raises ``RuntimeError`` (interpreter"""
        import concurrent.futures

        real_submit = concurrent.futures.ThreadPoolExecutor.submit

        def _rejecting_submit(self, fn, *args, **kwargs):
            raise RuntimeError("cannot schedule new futures after interpreter shutdown")

        # Patch the class method directly: ``_run_parallel_with_timeout``
        monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "submit", _rejecting_submit)
        try:
            items = [
                ("a", lambda: 1, 1.0),
                ("b", lambda: 2, 1.0),
            ]
            results = _run_parallel_with_timeout(items)
        finally:
            monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "submit", real_submit)
        by_desc = dict(results)
        assert isinstance(by_desc["a"], RuntimeError), f"expected RuntimeError result; got {by_desc['a']!r}"
        assert isinstance(by_desc["b"], RuntimeError), f"expected RuntimeError result; got {by_desc['b']!r}"
        assert "interpreter shutdown" in str(by_desc["a"])

    def test_partial_submit_failure_records_only_failed_items(self, monkeypatch):
        """exception - order is preserved."""
        import concurrent.futures

        real_submit = concurrent.futures.ThreadPoolExecutor.submit
        calls = {"n": 0}

        def _flaky_submit(self, fn, *args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 2:
                raise RuntimeError("cannot schedule new futures after interpreter shutdown")
            return real_submit(self, fn, *args, **kwargs)

        monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "submit", _flaky_submit)
        try:
            items = [
                ("ok", lambda: "done", 1.0),
                ("rejected", lambda: "never", 1.0),
            ]
            results = _run_parallel_with_timeout(items)
        finally:
            monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, "submit", real_submit)
        by_desc = dict(results)
        assert by_desc["ok"] == "done"
        assert isinstance(by_desc["rejected"], RuntimeError)


class TestLeakedWorkerRegistryCap:
    """The ``_LEAKED_WORKERS`` registry is bounded."""

    def test_registry_evicts_oldest_at_cap(self, monkeypatch):
        """A full registry evicts its oldest entry to stay bounded."""
        monkeypatch.setattr(_tu, "_MAX_LEAKED_WORKERS", 3)
        release = threading.Event()
        blockers: list[threading.Thread] = []

        def _block() -> None:
            release.wait(timeout=5.0)

        try:
            # Pre-fill the registry with live "leaked" workers.
            for i in range(3):
                t = threading.Thread(target=_block, name=f"pre-leak-{i}", daemon=True)
                t.start()
                blockers.append(t)
                with _tu._LEAKED_WORKERS_LOCK:
                    _tu._LEAKED_WORKERS.append(t)
            result = _run_with_timeout("cap-test-blocked", _block, timeout=0.05)
            assert result is TIMEOUT
            with _tu._LEAKED_WORKERS_LOCK:
                names = [w.name for w in _tu._LEAKED_WORKERS]
                assert len(_tu._LEAKED_WORKERS) == 3, "registry must stay at the cap"
                assert "cleanup-cap-test-blocked" in names, "newest worker must be registered"
                assert "pre-leak-0" not in names, "oldest entry must be evicted first"
        finally:
            release.set()
            for t in blockers:
                t.join(timeout=1.0)

    def test_registry_prunes_dead_workers_on_append(self, monkeypatch):
        """Appending to a full registry of EXITED workers prunes them"""
        monkeypatch.setattr(_tu, "_MAX_LEAKED_WORKERS", 3)

        def _quick() -> None:
            time.sleep(0.01)

        # Simulate workers that leaked earlier but have since exited.
        for i in range(3):
            t = threading.Thread(target=_quick, name=f"dead-leak-{i}", daemon=True)
            t.start()
            t.join(timeout=1.0)
            assert not t.is_alive()
            with _tu._LEAKED_WORKERS_LOCK:
                _tu._LEAKED_WORKERS.append(t)
        release = threading.Event()
        try:
            result = _run_with_timeout("prune-test-blocked", lambda: release.wait(timeout=5.0), timeout=0.05)
            assert result is TIMEOUT
            with _tu._LEAKED_WORKERS_LOCK:
                assert [_w.name for _w in _tu._LEAKED_WORKERS] == ["cleanup-prune-test-blocked"], (
                    "dead workers must be pruned before the new append"
                )
        finally:
            release.set()

    def test_registry_grows_normally_under_cap(self):
        """Below the cap nothing is evicted, the single-leak contract"""
        release = threading.Event()
        try:
            result = _run_with_timeout("under-cap-blocked", lambda: release.wait(timeout=5.0), timeout=0.05)
            assert result is TIMEOUT
            with _tu._LEAKED_WORKERS_LOCK:
                assert len(_tu._LEAKED_WORKERS) == 1
        finally:
            release.set()
