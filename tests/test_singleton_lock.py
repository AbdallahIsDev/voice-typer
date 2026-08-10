"""XZ-9 / XV-103 regression tests: module-level singleton init race.

The two module-level caches in ``voice_typer.server.clipboard_target_safety`` —

* ``_WE_ELEVATED`` (cached "are we elevated?" bool)
* ``_UIA_SINGLETON`` (cached ``IUIAutomation`` COM proxy)

— are populated lazily on first call. Before XV-103 they had no lock
guarding the check-then-act pattern, so two threads calling
``_get_we_elevated`` / ``_get_uia_singleton`` concurrently on the cold
path could both observe ``None`` / ``False``, both run the Win32 /
comtypes init, and stomp each other's write to the module-level cache.

These tests pin the XV-103 fix:

1. The module exposes ``_WE_ELEVATED_LOCK`` and ``_UIA_SINGLETON_LOCK``
   as ``threading.Lock`` instances.
2. The fast path (cache hit) does NOT acquire the lock — verified by
   asserting the lock is un-acquired after a populated-cache call.
3. The cold path serializes concurrent callers — verified by spawning
   N threads that all hit the cold path simultaneously and asserting
   the underlying Win32 / comtypes init runs exactly once.

The tests mock ``ctypes.windll`` and ``comtypes`` so they run on Linux
without Windows or comtypes installed. They mirror the mocking strategy
of ``tests/test_clipboard_win32_coverage.py``.
"""

from __future__ import annotations

import contextlib  # noqa: E402
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server import clipboard_target_safety as safety_mod  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _set_byref_value(byref_obj: Any, value: int) -> None:
    """Mutate the c_ulong instance wrapped by ``ctypes.byref``.

    Mirrors the helper in ``test_clipboard_win32_coverage.py``: fakes the
    kernel writing an output DWORD into a by-ref buffer.
    """
    byref_obj._obj.value = value


@pytest.fixture
def reset_caches():
    """Reset both module-level caches before AND after each test.

    XV-103: the locks themselves are NOT reset (they're stateless) — only
    the cached values they protect. This mirrors what the existing
    ``_reset_we_elevated`` / ``_reset_uia_singleton`` fixtures in
    ``test_clipboard_win32_coverage.py`` do.
    """
    safety_mod._WE_ELEVATED = None
    safety_mod._UIA_SINGLETON = None
    safety_mod._UIA_MODULE = None
    safety_mod._UIA_SINGLETON_INIT_ATTEMPTED = False
    yield
    safety_mod._WE_ELEVATED = None
    safety_mod._UIA_SINGLETON = None
    safety_mod._UIA_MODULE = None
    safety_mod._UIA_SINGLETON_INIT_ATTEMPTED = False


@pytest.fixture
def fake_win32_elevated():
    """Mock ``ctypes.windll`` so ``_get_we_elevated`` runs its Win32 branch.

    Configures advapi32.OpenProcessToken to succeed and GetTokenInformation
    to report "elevated = 1". The ``ctypes.cast`` patch makes the DWORD
    dereference yield 1 (we ARE elevated).
    """
    mock_user32 = MagicMock()
    mock_kernel32 = MagicMock()
    mock_advapi32 = MagicMock()
    mock_windll = MagicMock()
    mock_windll.user32 = mock_user32
    mock_windll.kernel32 = mock_kernel32
    mock_windll.advapi32 = mock_advapi32

    mock_kernel32.GetCurrentProcess.return_value = 0xABCD
    mock_kernel32.CloseHandle.return_value = 1
    mock_advapi32.OpenProcessToken.return_value = 1  # success
    mock_advapi32.GetTokenInformation.return_value = 1  # success

    # Patch ctypes.cast to yield a fake pointer whose [0] is 1 (elevated).
    fake_ptr = MagicMock()
    fake_ptr.__getitem__.return_value = 1

    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        patch.object(safety_mod, "is_windows", return_value=True),
        patch("ctypes.windll", mock_windll, create=True),
        patch("ctypes.cast", return_value=fake_ptr),
    ):
        yield {
            "user32": mock_user32,
            "kernel32": mock_kernel32,
            "advapi32": mock_advapi32,
            "windll": mock_windll,
            "fake_ptr": fake_ptr,
        }


@pytest.fixture
def fake_comtypes_uia():
    """Mock ``comtypes`` / ``comtypes.client`` so ``_get_uia_singleton``
    runs its COM branch.

    ``CoCreateInstance`` returns a sentinel object so the test can assert
    it was called exactly once across concurrent threads.
    """
    fake_uia_mod = MagicMock(name="UIA_module")
    fake_uia = MagicMock(name="uia_instance")
    fake_comtypes = MagicMock(name="comtypes")
    fake_comtypes_client = MagicMock(name="comtypes.client")
    fake_comtypes.client = fake_comtypes_client
    fake_comtypes_client.GetModule.return_value = fake_uia_mod
    fake_comtypes.CoCreateInstance.return_value = fake_uia

    with (
        patch.object(clip_mod, "is_windows", return_value=True),
        patch.object(safety_mod, "is_windows", return_value=True),
        patch.dict(
            sys.modules,
            {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
        ),
    ):
        yield {
            "comtypes": fake_comtypes,
            "comtypes_client": fake_comtypes_client,
            "uia_mod": fake_uia_mod,
            "uia": fake_uia,
        }


# ===========================================================================
# Lock existence
# ===========================================================================


class TestLocksExist:
    """XV-103 contract: the safety module exposes two module-level locks."""

    def test_we_elevated_lock_is_threading_lock(self):
        """``_WE_ELEVATED_LOCK`` is a ``threading.Lock`` (or RLock)."""
        lock = safety_mod._WE_ELEVATED_LOCK
        # threading.Lock returns a _thread.lock object; RLock returns a
        # threading._RLock. Both expose ``acquire``/``release`` and support
        # the context-manager protocol. Use ``hasattr`` rather than
        # isinstance to stay lock-type-agnostic.
        assert hasattr(lock, "acquire")
        assert hasattr(lock, "release")
        # Must be usable as a context manager (the impl uses ``with lock:``).
        with lock:
            pass

    def test_uia_singleton_lock_is_threading_lock(self):
        """``_UIA_SINGLETON_LOCK`` is a ``threading.Lock`` (or RLock)."""
        lock = safety_mod._UIA_SINGLETON_LOCK
        assert hasattr(lock, "acquire")
        assert hasattr(lock, "release")
        with lock:
            pass


# ===========================================================================
# _get_we_elevated: lock semantics + concurrent init
# ===========================================================================


class TestGetWeElevatedLockSemantics:
    """XV-103: ``_get_we_elevated`` uses double-checked locking."""

    def test_fast_path_does_not_block_on_lock(self, fake_win32_elevated, reset_caches):
        """When the cache is already populated, the lock is NOT held.

        This pins the "double-checked" half of the pattern: the fast path
        must check the cache BEFORE acquiring the lock so concurrent
        readers don't serialize on the lock once init is done.
        """
        # Prime the cache.
        first = safety_mod._get_we_elevated()
        assert first is True

        # If the fast path erroneously acquired the lock, we'd block here
        # forever (we hold the lock on the main thread). Acquire it
        # ourselves first to prove the fast path doesn't need it.
        lock = safety_mod._WE_ELEVATED_LOCK
        acquired = lock.acquire(blocking=False)
        assert acquired, "Lock should be free if fast path doesn't acquire it"
        try:
            # Fast path: cache is populated, this should return immediately
            # WITHOUT trying to acquire the lock we already hold.
            result = safety_mod._get_we_elevated()
            assert result is True
        finally:
            lock.release()

    def test_cold_path_acquires_lock(self, fake_win32_elevated, reset_caches):
        """When the cache is empty, the lock IS acquired.

        We pre-acquire the lock on the main thread and verify that a
        background thread calling ``_get_we_elevated`` blocks until we
        release it. (This is the "cold path locks" half of the pattern.)
        """
        lock = safety_mod._WE_ELEVATED_LOCK
        lock.acquire()
        try:
            result_holder: dict[str, Any] = {}

            def _call():
                result_holder["value"] = safety_mod._get_we_elevated()

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            # Give the worker time to (try to) enter the cold path.
            t.join(timeout=0.2)
            assert t.is_alive(), (
                "Worker should be blocked on the lock while the cache is "
                "empty — cold path must acquire the lock before init."
            )
            # Release the lock — worker should now complete.
            lock.release()
            t.join(timeout=2.0)
            assert not t.is_alive(), "Worker should have completed after lock release"
            assert result_holder["value"] is True
        finally:
            # Defensive: ensure we don't leave the lock held if the assert
            # above fired before release(). Lock.release on an un-held lock
            # raises RuntimeError, so swallow it.
            with contextlib.suppress(RuntimeError):
                lock.release()

    def test_concurrent_cold_path_calls_init_once(self, fake_win32_elevated, reset_caches):
        """N threads hitting the cold path → OpenProcessToken called once.

        This is the XV-103 regression: before the lock, N threads could
        all observe ``_WE_ELEVATED is None`` and all run the Win32 token
        query, leaking handles and stomping the cache. With the lock,
        only the first thread runs init; the rest take the fast path
        once the first releases the lock.

        Determinism note: a plain ``MagicMock`` returns instantly, so on
        CPython the GIL can let one thread complete the whole init
        before another observes the cache is still ``None``. We add a
        small ``time.sleep`` to the OpenProcessToken side_effect so the
        init holds the GIL long enough for all racing threads to observe
        the empty cache — making the test reliably fail without the lock.
        """
        n_threads = 16
        barrier = threading.Barrier(n_threads)
        results: list[bool] = []
        results_lock = threading.Lock()

        # Widen the race window: the mock OpenProcessToken sleeps briefly
        # (releasing the GIL) so other threads get a chance to observe
        # the still-empty cache and enter the cold path too. With the
        # lock, only one thread enters the cold path; the others
        # block on the lock and then take the fast path.
        advapi32 = fake_win32_elevated["advapi32"]

        def _slow_open_process_token(*args, **kwargs):
            time.sleep(0.02)
            return 1

        advapi32.OpenProcessToken.side_effect = _slow_open_process_token

        def _call():
            # Synchronize start so all threads race into the cold path.
            barrier.wait()
            r = safety_mod._get_we_elevated()
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=_call, daemon=True) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "Worker thread should have completed"

        # All callers must observe the same cached value (elevated=True).
        assert len(results) == n_threads
        assert all(r is True for r in results), f"All threads should see elevated=True; got {results}"

        # contract: the Win32 OpenProcessToken call must happen
        # EXACTLY once across all N threads. Before the lock, this would
        # be N calls.
        assert advapi32.OpenProcessToken.call_count == 1, (
            f"OpenProcessToken should be called once (lock serializes init); got {advapi32.OpenProcessToken.call_count}"
        )


# ===========================================================================
# _get_uia_singleton: lock semantics + concurrent init
# ===========================================================================


class TestGetUiaSingletonLockSemantics:
    """XV-103: ``_get_uia_singleton`` uses double-checked locking."""

    def test_fast_path_does_not_block_on_lock(self, fake_comtypes_uia, reset_caches):
        """When init was already attempted, the lock is NOT held."""
        # Prime the cache.
        first = safety_mod._get_uia_singleton()
        assert first is fake_comtypes_uia["uia"]

        lock = safety_mod._UIA_SINGLETON_LOCK
        acquired = lock.acquire(blocking=False)
        assert acquired, "Lock should be free if fast path doesn't acquire it"
        try:
            result = safety_mod._get_uia_singleton()
            assert result is fake_comtypes_uia["uia"]
        finally:
            lock.release()

    def test_cold_path_acquires_lock(self, fake_comtypes_uia, reset_caches):
        """When init hasn't been attempted, the lock IS acquired."""
        lock = safety_mod._UIA_SINGLETON_LOCK
        lock.acquire()
        try:
            result_holder: dict[str, Any] = {}

            def _call():
                result_holder["value"] = safety_mod._get_uia_singleton()

            t = threading.Thread(target=_call, daemon=True)
            t.start()
            t.join(timeout=0.2)
            assert t.is_alive(), (
                "Worker should be blocked on the lock while init hasn't "
                "been attempted — cold path must acquire the lock."
            )
            lock.release()
            t.join(timeout=2.0)
            assert not t.is_alive(), "Worker should have completed after lock release"
            assert result_holder["value"] is fake_comtypes_uia["uia"]
        finally:
            with contextlib.suppress(RuntimeError):
                lock.release()

    def test_concurrent_cold_path_calls_init_once(self, fake_comtypes_uia, reset_caches):
        """N threads hitting the cold path → CoCreateInstance called once.

        This is the XV-103 regression: before the lock, N threads could
        all observe ``_UIA_SINGLETON_INIT_ATTEMPTED is False`` and all
        run ``comtypes.client.GetModule`` + ``CoCreateInstance``, leaking
        COM proxies. With the lock, only the first thread runs init.

        Determinism note: a plain ``MagicMock`` returns instantly, so on
        CPython the GIL can let one thread complete the whole init
        before another observes the flag is still ``False``. We add a
        small ``time.sleep`` to the CoCreateInstance side_effect so the
        init holds the GIL long enough for all racing threads to observe
        the un-attempted init — making the test reliably fail without
        the lock.
        """
        n_threads = 16
        barrier = threading.Barrier(n_threads)
        results: list[Any] = []
        results_lock = threading.Lock()

        # Widen the race window so all threads observe the un-attempted
        # init before any one of them sets the flag.
        comtypes = fake_comtypes_uia["comtypes"]
        expected_uia = fake_comtypes_uia["uia"]

        def _slow_co_create_instance(*args, **kwargs):
            time.sleep(0.02)
            return expected_uia

        comtypes.CoCreateInstance.side_effect = _slow_co_create_instance

        def _call():
            barrier.wait()
            r = safety_mod._get_uia_singleton()
            with results_lock:
                results.append(r)

        threads = [threading.Thread(target=_call, daemon=True) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "Worker thread should have completed"

        # All callers must observe the same cached UIA proxy.
        assert len(results) == n_threads
        assert all(r is expected_uia for r in results), "All threads should see the same cached UIA singleton"

        # contract: CoCreateInstance must be called EXACTLY once.
        assert comtypes.CoCreateInstance.call_count == 1, (
            f"CoCreateInstance should be called once (lock serializes init); got {comtypes.CoCreateInstance.call_count}"
        )
        # And GetModule (the heavier call) too.
        assert comtypes.client.GetModule.call_count == 1, (
            f"comtypes.client.GetModule should be called once; got {comtypes.client.GetModule.call_count}"
        )

    def test_init_failure_does_not_deadlock(self, fake_win32_elevated, reset_caches):
        """If comtypes raises during init, the lock is released.

        Regression for the XV-103 fix: the ``with _UIA_SINGLETON_LOCK:``
        must be a context manager so an exception in the cold path
        releases the lock. We force ``comtypes.client.GetModule`` to
        raise and verify (a) the function returns None and (b) the lock
        is releasable afterwards.
        """
        fake_comtypes = MagicMock(name="comtypes")
        fake_comtypes_client = MagicMock(name="comtypes.client")
        fake_comtypes.client = fake_comtypes_client
        fake_comtypes_client.GetModule.side_effect = OSError("comtypes broken")

        with (
            patch.object(clip_mod, "is_windows", return_value=True),
            patch.object(safety_mod, "is_windows", return_value=True),
            patch.dict(
                sys.modules,
                {"comtypes": fake_comtypes, "comtypes.client": fake_comtypes_client},
            ),
            patch.object(clip_mod, "log"),
        ):
            result = safety_mod._get_uia_singleton()

        assert result is None
        # Lock must be free after the exception path.
        lock = safety_mod._UIA_SINGLETON_LOCK
        assert lock.acquire(blocking=False), "Lock must be released after init exception (XV-103 context-manager fix)"
        lock.release()


# ===========================================================================
# Cross-cutting: lock is non-reentrant safe (no double-acquire from same
# thread in the cold path).
# ===========================================================================


class TestLockReentrancy:
    """XV-103: the locks are non-reentrant, so the cold path must NOT
    call any function that itself re-enters the same lock.

    We can't easily assert "no re-entrancy" structurally, but we CAN
    assert that the cold path's init code doesn't call back into the
    same getter (which would deadlock with a non-reentrant Lock).
    """

    def test_we_elevated_cold_path_does_not_reenter(self, fake_win32_elevated, reset_caches):
        """Cold path must not call ``_get_we_elevated`` recursively."""
        # Spy on _get_we_elevated: wrap the real function and count
        # re-entrant calls while the original is executing.
        original = safety_mod._get_we_elevated
        state: dict[str, Any] = {"depth": 0, "max_depth": 0}

        def _spy():
            state["depth"] += 1
            state["max_depth"] = max(state["max_depth"], state["depth"])
            try:
                return original()
            finally:
                state["depth"] -= 1

        with patch.object(safety_mod, "_get_we_elevated", side_effect=_spy):
            result = safety_mod._get_we_elevated()

        assert result is True
        # max_depth == 1 means the function was called once and did NOT
        # re-enter itself (which would deadlock with a non-reentrant Lock).
        assert state["max_depth"] == 1, f"_get_we_elevated must not re-enter itself; max depth = {state['max_depth']}"
