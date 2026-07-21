"""R4-F18: ``_get_rate_limiter`` lazy-init race condition tests.

The CR-11 fix moved the IPC rate limiter from per-connection to
per-process: a single ``_RateLimiter`` instance is lazily created and
stored on the ``IPCServer`` instance via ``_get_rate_limiter(server)``.
All TCP reconnects and WS reconnects within the same server process
share the same sliding-window deque.

The pre-R4-F18 implementation had a TOCTOU (time-of-check /
time-of-use) race in the get-or-create block::

    def _get_rate_limiter(server):
        limiter = getattr(server, "_rate_limiter_instance", None)
        if not isinstance(limiter, _RateLimiter):     # ← check
            limiter = _RateLimiter()                   # ← create
            server._rate_limiter_instance = limiter    # ← use
        return limiter

Two threads simultaneously hitting the helper on a FRESH server
instance could both observe ``limiter is None`` (the check passes for
both), both construct a fresh ``_RateLimiter``, and one of the two
would be orphaned by the second ``setattr``. The orphaned limiter's
accepted timestamps would NOT count toward the canonical budget, so a
slow-drip attacker could effectively double the rate-limit budget for
the brief overlap window (or worse, N× with N racing threads).

The R4-F18 fix wraps the get-or-create sequence in a module-level
``threading.Lock`` (``_RATE_LIMITER_INIT_LOCK``), using the classic
double-checked locking pattern: the fast path reads the attribute
without acquiring the lock (the common case after the first dispatch
on each server), and the slow path acquires the lock and re-checks
before creating+storing.

These tests pin the fix. The pre-R4-F18 code would FAIL these tests
when the race window is widened by stubbing ``_RateLimiter.__init__``
with a small sleep — without the lock, two threads would each create
their own instance; with the lock, only one instance is created.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock

import pytest

# ── Helpers ─────────────────────────────────────────────────────────────


class _CountingInitRateLimiter:
    """Stand-in for ``_RateLimiter`` that counts how many times
    ``__init__`` runs and sleeps briefly inside the constructor to
    widen the race window.

    The real ``_RateLimiter.__init__`` is fast (a few microseconds),
    so a naive concurrent-init test would rarely trip the race even
    on the buggy pre-R4-F18 code. By sleeping ~5 ms inside the
    constructor, we guarantee that two threads simultaneously inside
    the get-or-create block overlap — the lock-free version would
    produce two instances, the locked version produces one.

    The class duck-types as a ``_RateLimiter``: the helper only
    checks ``isinstance(limiter, _RateLimiter)`` and stores the
    instance. We swap ``_RateLimiter`` (the class object the helper
    references) for this stand-in by monkeypatching the module-level
    name, so ``isinstance(limiter, _RateLimiter)`` returns ``True``
    for our stand-in instances.
    """

    init_count = 0
    init_lock = threading.Lock()
    init_delay_seconds = 0.005  # 5 ms — wide enough to make the race deterministic

    def __init__(self) -> None:
        # Sleep BEFORE incrementing the counter so two threads racing
        # through the constructor overlap inside __init__. The lock
        # is acquired only around the counter mutation (not the sleep)
        # so the threads genuinely overlap.
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


# ── Tests ───────────────────────────────────────────────────────────────


class TestR4F18ConcurrentInit:
    """R4-F18: concurrent first-call on a fresh server must produce
    exactly ONE ``_RateLimiter`` instance, not N."""

    def test_concurrent_first_call_returns_single_instance(self, monkeypatch):
        """Two threads simultaneously hitting ``_get_rate_limiter(server)``
        on a fresh server instance must both observe the SAME
        ``_RateLimiter`` instance, and the constructor must run
        exactly ONCE.

        Pre-R4-F18: this test would FAIL ~50% of the time (the race is
        probabilistic without the init lock) — both threads would
        observe ``limiter is None``, both would construct a fresh
        instance, and the second ``setattr`` would orphan the first.
        The constructor would run TWICE; the two threads would
        return DIFFERENT instances.

        Post-R4-F18: the module-level ``_RATE_LIMITER_INIT_LOCK``
        serializes the get-or-create block. The second thread to
        acquire the lock sees the instance the first thread stored and
        returns it directly. The constructor runs exactly ONCE.

        We widen the race window by stubbing ``_RateLimiter.__init__``
        with a 5 ms sleep (see ``_CountingInitRateLimiter`` above) so
        the test is deterministic, not probabilistic.
        """
        from voice_typer.server import ipc_server as ipc_server_mod

        # Swap the module-level _RateLimiter class for the counting
        # stand-in. ``isinstance(limiter, _RateLimiter)`` inside the
        # helper now checks against our stand-in (because the helper
        # looks up ``_RateLimiter`` from module globals at call time,
        # not at import time).
        _CountingInitRateLimiter.reset()
        monkeypatch.setattr(ipc_server_mod, "_RateLimiter", _CountingInitRateLimiter)

        class FakeServer:
            pass

        server = FakeServer()

        # Barrier ensures both threads start the call at the SAME
        # instant — without it, the OS scheduler might serialize the
        # two calls, masking the race.
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
        # Pre-R4-F18: this assertion would fail (count == 2) ~50% of
        # the time because both threads would construct their own
        # instance before either stored it.
        assert _CountingInitRateLimiter.init_count == 1, (
            f"_RateLimiter.__init__ must run exactly once under concurrent "
            f"first-call race; ran {_CountingInitRateLimiter.init_count} times. "
            f"R4-F18: _RATE_LIMITER_INIT_LOCK should serialize the get-or-create."
        )

        # Both threads must have received the SAME instance.
        assert len(results) == 2, "both workers must append their result"
        assert results[0] is results[1], (
            "both threads must observe the same _RateLimiter instance — "
            "the R4-F18 init lock guarantees the second thread sees the "
            "instance the first thread stored, not a fresh one"
        )

    def test_concurrent_first_call_many_threads_single_instance(self, monkeypatch):
        """Stress test: 16 threads simultaneously hitting
        ``_get_rate_limiter(server)`` on a fresh server instance must
        all observe the SAME ``_RateLimiter`` instance, and the
        constructor must run exactly ONCE.

        The wider the thread count, the more likely the pre-R4-F18
        code would produce N constructions (one per racing thread)
        — the test pins the post-R4-F18 invariant that the lock
        collapses all N racers to a single construction.
        """
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
                f"thread 0 — R4-F18 init lock guarantees single construction"
            )

    def test_concurrent_calls_on_different_servers_are_independent(self, monkeypatch):
        """Two threads each calling ``_get_rate_limiter`` on DIFFERENT
        fresh servers must produce TWO ``_RateLimiter`` instances (one
        per server).

        This pins that the module-level init lock does NOT serialize
        different servers' inits to a single instance — the lock only
        serializes the get-or-create ON THE SAME SERVER. Different
        servers have different ``_rate_limiter_instance`` attributes,
        so they get independent limiters.
        """
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
            "different servers must each construct their own _RateLimiter — "
            "the init lock does NOT collapse cross-server inits to a single "
            f"instance (ran {_CountingInitRateLimiter.init_count} times, expected 2)"
        )
        assert len(results) == 2
        assert results[0] is not results[1], "different servers must get different _RateLimiter instances"

    def test_init_lock_is_module_level_constant(self):
        """R4-F18: the init lock is exposed as a module-level constant
        ``_RATE_LIMITER_INIT_LOCK`` so tests and ADR docs can reference
        it by name (and so a future refactor can't accidentally move
        it back to a function-local without breaking the import).
        """
        from voice_typer.server.ipc_server import _RATE_LIMITER_INIT_LOCK

        assert isinstance(_RATE_LIMITER_INIT_LOCK, type(threading.Lock())), (
            "_RATE_LIMITER_INIT_LOCK must be a threading.Lock instance "
            "(or compatible reentrant lock) — R4-F18 pins the name + type"
        )

    def test_leaf_copy_also_has_init_lock(self):
        """R4-F18: the parallel leaf copy at
        ``voice_typer/server/ipc/rate_limiter.py`` must ALSO have the
        init lock, since CR-14 (delete ``ipc/`` package) was deferred.

        The leaf copy is currently a duplicate of the canonical
        implementation in ``ipc_server.py``; both must stay in sync.
        If a future refactor deletes the leaf copy (CR-14), this test
        should be removed alongside it.
        """
        # Import as a constant name (the symbol is module-level
        # ``_RATE_LIMITER_INIT_LOCK``; ruff N811 wants the alias to
        # match the constant naming convention).
        from voice_typer.server.ipc.rate_limiter import (
            _RATE_LIMITER_INIT_LOCK as _LEAF_LOCK,
        )

        assert isinstance(_LEAF_LOCK, type(threading.Lock())), (
            "_RATE_LIMITER_INIT_LOCK in ipc/rate_limiter.py must be a "
            "threading.Lock instance — kept in sync with ipc_server.py"
        )

    def test_magic_mock_server_concurrent_init(self, monkeypatch):
        """A MagicMock server (used by ~20 test files) must also be
        safe under concurrent first-call. The pre-R4-F18 code had a
        subtle interaction with MagicMock's auto-vivification: the
        first ``getattr(server, '_rate_limiter_instance', None)``
        returns a child MagicMock (auto-vivified), which fails the
        ``isinstance(limiter, _RateLimiter)`` check, triggering the
        create+store path. Two threads simultaneously doing this
        could both create+store, with the second ``setattr``
        overwriting the first.

        Post-R4-F18: the init lock serializes the create+store, so
        only one thread actually constructs a ``_RateLimiter``; the
        other sees the stored instance and returns it directly.
        """
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
