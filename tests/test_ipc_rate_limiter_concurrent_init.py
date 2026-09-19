"""``_get_rate_limiter`` lazy-init race condition tests."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest


class _CountingInitRateLimiter:
    """Stand-in for ``_RateLimiter`` that counts how many times"""

    init_count = 0
    init_lock = threading.Lock()
    init_delay_seconds = 0.005  # 5 ms, wide enough to make the race deterministic

    def __init__(self) -> None:
        # Sleep BEFORE incrementing the counter so two threads racing
        time.sleep(self.init_delay_seconds)
        with self.init_lock:
            self.__class__.init_count += 1

    # Duck-type the bits of _RateLimiter the tests touch.
    def allow(self, *, now=None):  # noqa: ARG002
        return True

    @property
    def rejected_count(self) -> int:
        return 0

    def reject(self) -> None:
        return None

    @classmethod
    def reset(cls) -> None:
        cls.init_count = 0


class TestConcurrentInit:
    """R4-F18: concurrent first-call on a fresh server must produce"""

    def test_concurrent_first_call_returns_single_instance(self, monkeypatch):
        """Two threads simultaneously hitting ``_get_rate_limiter(server)``"""
        from voice_typer.server import ipc_server as ipc_server_mod

        # Swap the module-level _RateLimiter class for the counting
        _CountingInitRateLimiter.reset()
        monkeypatch.setattr(ipc_server_mod, "_RateLimiter", _CountingInitRateLimiter)

        class FakeServer:
            pass

        server = FakeServer()

        # Barrier ensures both threads start the call at the SAME
        barrier = threading.Barrier(2)
        results: list[object] = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()  # block until both threads are ready
            rl = ipc_server_mod._get_rate_limiter(server)
            with results_lock:
                results.append(rl)

        t1 = threading.Thread(target=worker, name="R4F18-worker-1")
        t2 = threading.Thread(target=worker, name="R4F18-worker-2")
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert not t1.is_alive(), "worker 1 must terminate within 5s"
        assert not t2.is_alive(), "worker 2 must terminate within 5s"

        # The constructor must have run EXACTLY ONCE.
        assert _CountingInitRateLimiter.init_count == 1, (
            f"_RateLimiter.__init__ must run exactly once under concurrent "
            f"first-call race; ran {_CountingInitRateLimiter.init_count} times. "
            f"R4-F18: _RATE_LIMITER_INIT_LOCK should serialize the get-or-create."
        )

        # Both threads must have received the SAME instance.
        assert len(results) == 2, "both workers must append their result"
        assert results[0] is results[1], (
            "both threads must observe the same _RateLimiter instance, "
            "the R4-F18 init lock guarantees the second thread sees the "
            "instance the first thread stored, not a fresh one"
        )

    def test_concurrent_first_call_many_threads_single_instance(self, monkeypatch):
        """Stress test: 16 threads simultaneously hitting"""
        from voice_typer.server import ipc_server as ipc_server_mod

        _CountingInitRateLimiter.reset()
        monkeypatch.setattr(ipc_server_mod, "_RateLimiter", _CountingInitRateLimiter)

        class FakeServer:
            pass

        server = FakeServer()
        n_threads = 16
        barrier = threading.Barrier(n_threads)
        results: list[object] = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            rl = ipc_server_mod._get_rate_limiter(server)
            with results_lock:
                results.append(rl)

        threads = [threading.Thread(target=worker, name=f"R4F18-stress-{i}") for i in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10.0)

        for i, t in enumerate(threads):
            assert not t.is_alive(), f"thread {i} must terminate within 10s"

        assert _CountingInitRateLimiter.init_count == 1, (
            f"_RateLimiter.__init__ must run exactly once under {n_threads}-way "
            f"concurrent first-call; ran {_CountingInitRateLimiter.init_count} times."
        )
        assert len(results) == n_threads
        first = results[0]
        for i, r in enumerate(results):
            assert r is first, (
                f"thread {i} must observe the same _RateLimiter instance as "
                f"thread 0, R4-F18 init lock guarantees single construction"
            )

    def test_concurrent_calls_on_different_servers_are_independent(self, monkeypatch):
        """Two threads each calling ``_get_rate_limiter`` on DIFFERENT"""
        from voice_typer.server import ipc_server as ipc_server_mod

        _CountingInitRateLimiter.reset()
        monkeypatch.setattr(ipc_server_mod, "_RateLimiter", _CountingInitRateLimiter)

        class FakeServer:
            pass

        server_a = FakeServer()
        server_b = FakeServer()
        barrier = threading.Barrier(2)
        results: list[object] = []
        results_lock = threading.Lock()

        def worker(server):
            barrier.wait()
            rl = ipc_server_mod._get_rate_limiter(server)
            with results_lock:
                results.append(rl)

        t1 = threading.Thread(target=worker, args=(server_a,), name="R4F18-serverA")
        t2 = threading.Thread(target=worker, args=(server_b,), name="R4F18-serverB")
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert _CountingInitRateLimiter.init_count == 2, (
            "different servers must each construct their own _RateLimiter, "
            "the init lock does NOT collapse cross-server inits to a single "
            f"instance (ran {_CountingInitRateLimiter.init_count} times, expected 2)"
        )
        assert len(results) == 2
        assert results[0] is not results[1], "different servers must get different _RateLimiter instances"

    def test_init_lock_is_module_level_constant(self):
        """R4-F18: the init lock is exposed as a module-level constant"""
        from voice_typer.server.ipc_server import _RATE_LIMITER_INIT_LOCK

        assert isinstance(_RATE_LIMITER_INIT_LOCK, type(threading.Lock())), (
            "_RATE_LIMITER_INIT_LOCK must be a threading.Lock instance "
            "(or compatible reentrant lock), R4-F18 pins the name + type"
        )

    def test_leaf_copy_also_has_init_lock(self):
        """R4-F18: the parallel leaf copy at"""
        # Import as a constant name (the symbol is module-level
        from voice_typer.server.ipc.rate_limiter import (
            _RATE_LIMITER_INIT_LOCK as _LEAF_LOCK,
        )

        assert isinstance(_LEAF_LOCK, type(threading.Lock())), (
            "_RATE_LIMITER_INIT_LOCK in ipc/rate_limiter.py must be a "
            "threading.Lock instance, kept in sync with ipc_server.py"
        )

    def test_magic_mock_server_concurrent_init(self, monkeypatch):
        """A MagicMock server (used by ~20 test files) must also be"""
        from voice_typer.server import ipc_server as ipc_server_mod

        _CountingInitRateLimiter.reset()
        monkeypatch.setattr(ipc_server_mod, "_RateLimiter", _CountingInitRateLimiter)

        server = MagicMock()
        barrier = threading.Barrier(2)
        results: list[object] = []
        results_lock = threading.Lock()

        def worker():
            barrier.wait()
            rl = ipc_server_mod._get_rate_limiter(server)
            with results_lock:
                results.append(rl)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert _CountingInitRateLimiter.init_count == 1, (
            "MagicMock server: exactly one _RateLimiter must be constructed "
            f"under concurrent first-call (got {_CountingInitRateLimiter.init_count})"
        )
        assert results[0] is results[1], (
            "both threads must observe the same _RateLimiter instance on the MagicMock server"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
