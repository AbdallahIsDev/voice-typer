"""XZ-9 / XV-103 regression tests: module-level singleton init race."""

from __future__ import annotations

import contextlib  # noqa: E402
import sys
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import (
    clipboard as clip_mod,  # noqa: E402
    clipboard_target_safety as safety_mod,  # noqa: E402
)


def _set_byref_value(byref_obj: Any, value: int) -> None:
    """Mutate the c_ulong instance wrapped by ``ctypes.byref``."""
    byref_obj._obj.value = value


@pytest.fixture
def reset_caches():
    """Reset both module-level caches before AND after each test."""
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
    """Mock ``ctypes.windll`` so ``_get_we_elevated`` runs its Win32 branch."""
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
    """Mock ``comtypes`` / ``comtypes.client`` so ``_get_uia_singleton``"""
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


class TestLocksExist:
    """XV-103 contract: the safety module exposes two module-level locks."""

    def test_we_elevated_lock_is_threading_lock(self):
        """``_WE_ELEVATED_LOCK`` is a ``threading.Lock`` (or RLock)."""
        lock = safety_mod._WE_ELEVATED_LOCK
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


class TestGetWeElevatedLockSemantics:
    """XV-103: ``_get_we_elevated`` uses double-checked locking."""

    def test_fast_path_does_not_block_on_lock(self, fake_win32_elevated, reset_caches):
        """
        When the cache is already populated, the lock is NOT held.
        This pins the "double-checked" half of the pattern: the fast path
        """
        # Prime the cache.
        first = safety_mod._get_we_elevated()
        assert first is True

        # If the fast path erroneously acquired the lock, we'd block here
        lock = safety_mod._WE_ELEVATED_LOCK
        acquired = lock.acquire(blocking=False)
        assert acquired, "Lock should be free if fast path doesn't acquire it"
        try:
            # Fast path: cache is populated, this should return immediately
            result = safety_mod._get_we_elevated()
            assert result is True
        finally:
            lock.release()

    def test_cold_path_acquires_lock(self, fake_win32_elevated, reset_caches):
        """When the cache is empty, the lock IS acquired."""
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
                "empty, cold path must acquire the lock before init."
            )
            # Release the lock, worker should now complete.
            lock.release()
            t.join(timeout=2.0)
            assert not t.is_alive(), "Worker should have completed after lock release"
            assert result_holder["value"] is True
        finally:
            # Defensive: ensure we don't leave the lock held if the assert
            with contextlib.suppress(RuntimeError):
                lock.release()

    def test_concurrent_cold_path_calls_init_once(self, fake_win32_elevated, reset_caches):
        """N threads hitting the cold path → OpenProcessToken called once."""
        n_threads = 16
        barrier = threading.Barrier(n_threads)
        results: list[bool] = []
        results_lock = threading.Lock()

        # Widen the race window: the mock OpenProcessToken sleeps briefly
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

        assert advapi32.OpenProcessToken.call_count == 1, (
            f"OpenProcessToken should be called once (lock serializes init); got {advapi32.OpenProcessToken.call_count}"
        )


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
                "been attempted, cold path must acquire the lock."
            )
            lock.release()
            t.join(timeout=2.0)
            assert not t.is_alive(), "Worker should have completed after lock release"
            assert result_holder["value"] is fake_comtypes_uia["uia"]
        finally:
            with contextlib.suppress(RuntimeError):
                lock.release()

    def test_concurrent_cold_path_calls_init_once(self, fake_comtypes_uia, reset_caches):
        """N threads hitting the cold path → CoCreateInstance called once."""
        n_threads = 16
        barrier = threading.Barrier(n_threads)
        results: list[Any] = []
        results_lock = threading.Lock()

        # Widen the race window so all threads observe the un-attempted
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

        assert comtypes.CoCreateInstance.call_count == 1, (
            f"CoCreateInstance should be called once (lock serializes init); got {comtypes.CoCreateInstance.call_count}"
        )
        # And GetModule (the heavier call) too.
        assert comtypes.client.GetModule.call_count == 1, (
            f"comtypes.client.GetModule should be called once; got {comtypes.client.GetModule.call_count}"
        )

    def test_init_failure_does_not_deadlock(self, fake_win32_elevated, reset_caches):
        """If comtypes raises during init, the lock is released."""
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


class TestLockReentrancy:
    """XV-103: the locks are non-reentrant, so the cold path must NOT"""

    def test_we_elevated_cold_path_does_not_reenter(self, fake_win32_elevated, reset_caches):
        """Cold path must not call ``_get_we_elevated`` recursively."""
        # Spy on _get_we_elevated: wrap the real function and count
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
        assert state["max_depth"] == 1, f"_get_we_elevated must not re-enter itself; max depth = {state['max_depth']}"
