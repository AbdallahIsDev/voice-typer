"""SA-4 race-condition regression tests for clipboard restore / atexit."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from voice_typer.server import clipboard as clip_mod  # noqa: E402
from voice_typer.server.clipboard.manager import (  # noqa: E402
    _force_restore_pending_at_exit,
    _pending_restores,
    _pending_restores_lock,
)
from voice_typer.server.clipboard_snapshot import (  # noqa: E402
    ClipboardSnapshot,
    _restore_lock,
)

from tests.fixtures.clipboard_helpers import make_clipboard_manager, make_clipboard_snapshot  # noqa: E402


@pytest.fixture(autouse=True)
def _mock_display_env(monkeypatch):
    """Ensure DISPLAY is set and WAYLAND_DISPLAY is unset for clipboard tests."""
    monkeypatch.setenv("DISPLAY", ":99")
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    yield


@pytest.fixture(autouse=True)
def _isolate_pending_restores():
    """Ensure each test starts and ends with an empty _pending_restores list."""
    with _pending_restores_lock:
        _pending_restores.clear()
    yield
    with _pending_restores_lock:
        _pending_restores.clear()


class TestAtexitVsDaemonSameSnapshot:
    """At most ONE of {atexit, daemon} may call ``snapshot.restore()``."""

    def test_atexit_claims_first_then_daemon_short_circuits(self):
        """daemon claims its entry. The daemon must short-circuit (ValueError"""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = (cm, snap, "the dictation", 0.0)

        # Register the entry as paste() would.
        with _pending_restores_lock:
            _pending_restores.append(entry)

        restore_calls = []

        def _track_restore():
            restore_calls.append(threading.current_thread().name)
            return True

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="the dictation"),
            patch.object(snap, "restore", side_effect=_track_restore) as mock_restore,
            patch.object(clip_mod, "log"),
        ):
            _force_restore_pending_at_exit()
            assert mock_restore.call_count == 1

            # Daemon wakes up AFTER atexit has cleared the list. It must
            cm._delayed_restore(snap, "the dictation", 0.0, entry)

        # Total restore() calls: 1 (from atexit only). Daemon short-circuited.
        assert len(restore_calls) == 1, (
            f"Expected exactly 1 restore() call (atexit only); got {len(restore_calls)}: {restore_calls}"
        )

    def test_daemon_claims_first_then_atexit_skips(self):
        """``_pending_restores``) and restores BEFORE atexit fires. Atexit's"""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = (cm, snap, "the dictation", 0.0)

        # Register the entry as paste() would.
        with _pending_restores_lock:
            _pending_restores.append(entry)

        restore_calls = []

        def _track_restore():
            restore_calls.append(threading.current_thread().name)
            return True

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="the dictation"),
            patch.object(snap, "restore", side_effect=_track_restore) as mock_restore,
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()

            # Daemon runs to completion FIRST (claim + restore).
            cm._delayed_restore(snap, "the dictation", 0.0, entry)
            assert mock_restore.call_count == 1, "Daemon should have restored once after claiming its entry"

            # Atexit fires AFTER the daemon has finished. The list is
            _force_restore_pending_at_exit()

        # Total restore() calls: 1 (from daemon only). Atexit's snapshot
        assert len(restore_calls) == 1, (
            f"Expected exactly 1 restore() call (daemon only); got {len(restore_calls)}: {restore_calls}"
        )
        assert mock_restore.call_count == 1

    def test_concurrent_atexit_and_daemon_never_double_restore(self):
        """Race ordering (C): atexit and the daemon contend on the lock."""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = (cm, snap, "the dictation", 0.0)

        # Register the entry.
        with _pending_restores_lock:
            _pending_restores.append(entry)

        atexit_ready = threading.Event()
        daemon_ready = threading.Event()
        both_ready = threading.Event()
        restore_calls = []
        restore_calls_lock = threading.Lock()

        def _track_restore():
            with restore_calls_lock:
                restore_calls.append(threading.current_thread().name)
            return True

        def atexit_fn():
            atexit_ready.set()
            # Wait for the daemon to also be ready, then both proceed.
            daemon_ready.wait(timeout=2.0)
            both_ready.set()
            _force_restore_pending_at_exit()

        # Daemon thread, claims the entry under the lock, then calls
        def daemon_fn():
            daemon_ready.set()
            atexit_ready.wait(timeout=2.0)
            both_ready.wait(timeout=2.0)
            cm._delayed_restore(snap, "the dictation", 0.0, entry)

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="the dictation"),
            patch.object(snap, "restore", side_effect=_track_restore),
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "time") as mock_time,
        ):
            # Skip the daemon's sleep, we want it to reach the claim
            mock_time.sleep = MagicMock()

            daemon_thread = threading.Thread(target=daemon_fn, name="daemon-restore", daemon=True)
            daemon_thread.start()

            atexit_fn()
            daemon_thread.join(timeout=2.0)

        # Exactly ONE restore() call, either from atexit or from the daemon,
        assert len(restore_calls) == 1, (
            f"Expected exactly 1 restore() call (either atexit OR daemon); "
            f"got {len(restore_calls)}: {restore_calls}. "
            "This indicates a double-restore race, the DE-63 claim-step "
            "short-circuit is broken."
        )


class TestConcurrentRestoreSerialization:
    """``_restore_lock`` so that two concurrent restores (e.g. atexit"""

    def test_two_concurrent_restores_do_not_overlap(self):
        """second thread's restore must not start until the first thread's"""
        snap_a = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain", b"content A")],
            captured_at=time.monotonic(),
        )
        snap_b = ClipboardSnapshot(
            platform="linux-x11",
            items=[("text/plain", b"content B")],
            captured_at=time.monotonic(),
        )

        # Track which thread is inside the critical section.
        own_threads = {"restore-A", "restore-B"}
        in_critical = threading.Event()
        in_critical_owner: list[str] = []
        in_critical_lock = threading.Lock()
        overlap_detected = threading.Event()

        # Start gate, both threads wait here until both are ready.
        start_gate_a = threading.Event()
        start_gate_b = threading.Event()
        both_ready = threading.Event()

        # Override _restore_x11 to detect overlap. The real
        def _instrumented_restore_x11(self):
            tid = threading.current_thread().name
            with in_critical_lock:
                if in_critical.is_set():
                    # Another thread is already inside the critical
                    overlap_detected.set()
                in_critical.set()
                if tid in own_threads:
                    in_critical_owner.append(tid)
            try:
                # Simulate the platform clipboard call taking some time
                time.sleep(0.03)
                return True
            finally:
                with in_critical_lock:
                    in_critical.clear()

        def thread_a_fn():
            start_gate_a.set()
            both_ready.wait(timeout=2.0)
            snap_a.restore()

        def thread_b_fn():
            start_gate_b.set()
            both_ready.wait(timeout=2.0)
            snap_b.restore()

        with patch.object(ClipboardSnapshot, "_restore_x11", _instrumented_restore_x11):
            t_a = threading.Thread(target=thread_a_fn, name="restore-A", daemon=True)
            t_b = threading.Thread(target=thread_b_fn, name="restore-B", daemon=True)
            t_a.start()
            t_b.start()

            # Wait for both to signal ready.
            assert start_gate_a.wait(timeout=2.0), "Thread A never signaled ready"
            assert start_gate_b.wait(timeout=2.0), "Thread B never signaled ready"
            # Release both at the same time.
            both_ready.set()

            t_a.join(timeout=2.0)
            t_b.join(timeout=2.0)

        assert not t_a.is_alive(), "Thread A did not finish"
        assert not t_b.is_alive(), "Thread B did not finish"
        assert not overlap_detected.is_set(), (
            "Concurrent restore() calls overlapped, _restore_lock failed to "
            "serialize them. Two threads were inside the platform clipboard "
            "critical section at the same time, which races on Win32 "
            "OpenClipboard / macOS NSPasteboard / Linux xclip selection."
        )
        # Both restores ran (serialized, not overlapping).
        assert len(in_critical_owner) == 2, f"Expected 2 restore() calls; got {len(in_critical_owner)}"

    def test_three_concurrent_restores_serialized_via_lock(self):
        """Stress test: 3 threads call ``snapshot.restore()`` concurrently."""
        snaps = [
            ClipboardSnapshot(
                platform="linux-x11",
                items=[("text/plain", f"content {i}".encode())],
                captured_at=time.monotonic(),
            )
            for i in range(3)
        ]

        in_critical = threading.Event()
        overlap_detected = threading.Event()
        in_critical_lock = threading.Lock()
        completed: list[str] = []
        completed_lock = threading.Lock()

        start_gates = [threading.Event() for _ in range(3)]
        all_ready = threading.Event()

        def _instrumented_restore_x11(self):
            with in_critical_lock:
                if in_critical.is_set():
                    overlap_detected.set()
                in_critical.set()
            try:
                time.sleep(0.02)
                return True
            finally:
                with in_critical_lock:
                    in_critical.clear()

        def thread_fn(idx: int):
            start_gates[idx].set()
            all_ready.wait(timeout=2.0)
            snaps[idx].restore()
            with completed_lock:
                completed.append(f"thread-{idx}")

        with patch.object(ClipboardSnapshot, "_restore_x11", _instrumented_restore_x11):
            threads = [
                threading.Thread(target=thread_fn, args=(i,), name=f"restore-{i}", daemon=True) for i in range(3)
            ]
            for t in threads:
                t.start()

            for i, g in enumerate(start_gates):
                assert g.wait(timeout=2.0), f"Thread {i} never signaled ready"
            all_ready.set()

            for t in threads:
                t.join(timeout=2.0)

        assert all(not t.is_alive() for t in threads), "A thread did not finish"
        assert not overlap_detected.is_set(), (
            "Concurrent restore() calls overlapped, _restore_lock failed to serialize 3 concurrent restores."
        )
        assert len(completed) == 3

    def test_restore_lock_is_module_level_not_per_instance(self):
        """The ``_restore_lock`` must be module-level (not per-instance)"""
        snap_a = ClipboardSnapshot(platform="linux-x11", items=[], captured_at=0.0)
        snap_b = ClipboardSnapshot(platform="linux-x11", items=[], captured_at=0.0)

        # The _restore_lock is module-level, both snapshots are
        from voice_typer.server import clipboard_snapshot as snap_mod

        assert snap_mod._restore_lock is _restore_lock, (
            "_restore_lock must be the module-level lock object, a "
            "per-instance lock would not serialize restores across "
            "different snapshots."
        )
        # The lock must be a Lock (or RLock), not None or some other type.
        assert hasattr(snap_mod._restore_lock, "acquire"), (
            "_restore_lock must be a threading.Lock or RLock, missing acquire method"
        )
        # Smoke-test the lock: acquire and release.
        acquired = snap_mod._restore_lock.acquire(blocking=False)
        assert acquired, "_restore_lock should be acquirable when uncontended"
        try:
            # Second acquire (non-blocking) must fail, the lock is held.
            second = snap_mod._restore_lock.acquire(blocking=False)
            assert not second, (
                "_restore_lock must be a non-reentrant Lock (or RLock), "
                "second non-blocking acquire should fail while held"
            )
        finally:
            snap_mod._restore_lock.release()

        # Sanity: snap_a and snap_b don't carry their own locks.
        assert not hasattr(snap_a, "_restore_lock"), (
            "ClipboardSnapshot should NOT have a per-instance _restore_lock, "
            "the lock must be module-level to serialize across instances."
        )
        assert not hasattr(snap_b, "_restore_lock"), (
            "ClipboardSnapshot should NOT have a per-instance _restore_lock, "
            "the lock must be module-level to serialize across instances."
        )


class TestAtexitIteratesAllPending:
    """The atexit handler restores ALL pending entries that the daemon"""

    def test_atexit_restores_multiple_pending_entries(self):
        """If multiple paste cycles are in-flight when atexit fires, ALL"""
        cm = make_clipboard_manager()
        snap_a = make_clipboard_snapshot()
        snap_b = make_clipboard_snapshot()
        snap_c = make_clipboard_snapshot()
        entries = [
            (cm, snap_a, "dictation A", 0.0),
            (cm, snap_b, "dictation B", 0.0),
            (cm, snap_c, "dictation C", 0.0),
        ]

        with _pending_restores_lock:
            _pending_restores.extend(entries)

        restored: list[str] = []

        def _track_restore_a():
            restored.append("A")
            return True

        def _track_restore_b():
            restored.append("B")
            return True

        def _track_restore_c():
            restored.append("C")
            return True

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value=None),
            patch.object(snap_a, "restore", side_effect=_track_restore_a),
            patch.object(snap_b, "restore", side_effect=_track_restore_b),
            patch.object(snap_c, "restore", side_effect=_track_restore_c),
            patch.object(clip_mod, "log"),
        ):
            _force_restore_pending_at_exit()

        # All three snapshots were restored exactly once.
        assert sorted(restored) == ["A", "B", "C"], f"Expected all 3 snapshots restored; got {restored}"
        # The list is cleared after atexit runs.
        with _pending_restores_lock:
            assert _pending_restores == []

    def test_atexit_clears_list_even_if_restore_raises(self):
        """exception is logged but the loop continues. The list is cleared"""
        cm = make_clipboard_manager()
        snap_a = make_clipboard_snapshot()
        snap_b = make_clipboard_snapshot()
        entries = [
            (cm, snap_a, "dictation A", 0.0),
            (cm, snap_b, "dictation B", 0.0),
        ]

        with _pending_restores_lock:
            _pending_restores.extend(entries)

        restored: list[str] = []

        def _track_restore_b():
            restored.append("B")
            return True

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value=None),
            patch.object(snap_a, "restore", side_effect=RuntimeError("restore A blew up")),
            patch.object(snap_b, "restore", side_effect=_track_restore_b),
            patch.object(clip_mod, "log"),
        ):
            # Must NOT raise, atexit swallows per-entry exceptions.
            _force_restore_pending_at_exit()

        assert restored == ["B"], f"Expected snap_b restored after snap_a raised; got {restored}"
        # The list is cleared.
        with _pending_restores_lock:
            assert _pending_restores == []

    def test_atexit_with_empty_list_is_noop(self):
        """If no pending restores when atexit fires, it must not raise and"""
        with (
            patch.object(clip_mod, "log"),
            patch.object(ClipboardSnapshot, "restore") as mock_restore,
        ):
            _force_restore_pending_at_exit()

        mock_restore.assert_not_called()
        with _pending_restores_lock:
            assert _pending_restores == []


class TestAtexitAndDaemonDifferentEntries:
    """The most insidious race: atexit is restoring entry B (on the main"""

    def test_atexit_and_daemon_different_snapshots_serialized(self):
        """Atexit restores entry B while a daemon restores entry A."""
        cm = make_clipboard_manager()
        snap_a = make_clipboard_snapshot()  # daemon restores this
        snap_b = make_clipboard_snapshot()  # atexit restores this

        entry_a = (cm, snap_a, "dictation A", 0.0)
        entry_b = (cm, snap_b, "dictation B", 0.0)

        # Register both entries.
        with _pending_restores_lock:
            _pending_restores.append(entry_a)
            _pending_restores.append(entry_b)

        # Synchronization: both threads wait at the start gate, then
        daemon_ready = threading.Event()
        atexit_ready = threading.Event()
        both_ready = threading.Event()

        # Overlap detection.
        in_critical = threading.Event()
        in_critical_lock = threading.Lock()
        overlap_detected = threading.Event()

        def _instrumented_restore_x11(self):
            with in_critical_lock:
                if in_critical.is_set():
                    overlap_detected.set()
                in_critical.set()
            try:
                time.sleep(0.03)
                return True
            finally:
                with in_critical_lock:
                    in_critical.clear()

        def daemon_fn():
            daemon_ready.set()
            atexit_ready.wait(timeout=2.0)
            both_ready.wait(timeout=2.0)
            # Daemon claims its entry then restores.
            cm._delayed_restore(snap_a, "dictation A", 0.0, entry_a)

        def atexit_fn():
            atexit_ready.set()
            daemon_ready.wait(timeout=2.0)
            both_ready.set()
            _force_restore_pending_at_exit()

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="dictation B"),
            patch.object(ClipboardSnapshot, "_restore_x11", _instrumented_restore_x11),
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()  # skip daemon's delay sleep

            daemon_thread = threading.Thread(target=daemon_fn, name="daemon-A", daemon=True)
            daemon_thread.start()

            atexit_fn()
            daemon_thread.join(timeout=2.0)

        assert not daemon_thread.is_alive(), "Daemon thread did not finish"
        assert not overlap_detected.is_set(), (
            "Atexit's restore(snap_b) and the daemon's restore(snap_a) overlapped, "
            "_restore_lock failed to serialize two DIFFERENT snapshots' restores. "
            "This is the SA-4 / S1- race: atexit on main thread + daemon on "
            "worker thread both inside the platform clipboard critical section."
        )


class TestRestoreLockNoDeadlock:
    """Verify ``_restore_lock`` (in clipboard_snapshot.py) does not"""

    def test_delayed_restore_does_not_hold_pending_restores_lock_during_restore(self):
        """``_delayed_restore`` releases ``_pending_restores_lock`` BEFORE"""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = (cm, snap, "dictation", 0.0)

        with _pending_restores_lock:
            _pending_restores.append(entry)

        inside_restore = threading.Event()
        proceed_restore = threading.Event()
        pending_lock_acquired_during_restore = threading.Event()

        def _blocking_restore_x11(self):
            inside_restore.set()
            proceed_restore.wait(timeout=2.0)
            return True

        def try_acquire_pending_lock():
            # Wait until the daemon is inside snapshot.restore().
            inside_restore.wait(timeout=2.0)
            # Try to acquire _pending_restores_lock, should succeed
            acquired = _pending_restores_lock.acquire(blocking=False)
            if acquired:
                pending_lock_acquired_during_restore.set()
                _pending_restores_lock.release()

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="dictation"),
            patch.object(ClipboardSnapshot, "_restore_x11", _blocking_restore_x11),
            patch.object(clip_mod, "log"),
            patch.object(clip_mod, "time") as mock_time,
        ):
            mock_time.sleep = MagicMock()

            checker_thread = threading.Thread(target=try_acquire_pending_lock, name="lock-checker", daemon=True)
            checker_thread.start()

            # Run _delayed_restore on this thread (simulating the daemon).
            cm._delayed_restore(snap, "dictation", 0.0, entry)
            proceed_restore.set()

            checker_thread.join(timeout=2.0)

        assert pending_lock_acquired_during_restore.is_set(), (
            "_pending_restores_lock was held by _delayed_restore WHILE "
            "snapshot.restore() was running, this means the daemon holds "
            "_pending_restores_lock during the restore call, which would "
            "deadlock with _restore_lock if a future change acquires "
            "_restore_lock while holding _pending_restores_lock."
        )

    def test_atexit_does_not_hold_pending_restores_lock_during_restore(self):
        """``_force_restore_pending_at_exit`` releases"""
        cm = make_clipboard_manager()
        snap = make_clipboard_snapshot()
        entry = (cm, snap, "dictation", 0.0)

        with _pending_restores_lock:
            _pending_restores.append(entry)

        inside_restore = threading.Event()
        proceed_restore = threading.Event()
        pending_lock_acquired_during_restore = threading.Event()

        def _blocking_restore_x11(self):
            inside_restore.set()
            proceed_restore.wait(timeout=2.0)
            return True

        def try_acquire_pending_lock():
            inside_restore.wait(timeout=2.0)
            acquired = _pending_restores_lock.acquire(blocking=False)
            if acquired:
                pending_lock_acquired_during_restore.set()
                _pending_restores_lock.release()

        with (
            patch.object(clip_mod, "_paste_from_clipboard", return_value="dictation"),
            patch.object(ClipboardSnapshot, "_restore_x11", _blocking_restore_x11),
            patch.object(clip_mod, "log"),
        ):
            checker_thread = threading.Thread(target=try_acquire_pending_lock, name="lock-checker", daemon=True)
            checker_thread.start()

            _force_restore_pending_at_exit()
            proceed_restore.set()

            checker_thread.join(timeout=2.0)

        assert pending_lock_acquired_during_restore.is_set(), (
            "_pending_restores_lock was held by _force_restore_pending_at_exit "
            "WHILE snapshot.restore() was running, this would deadlock with "
            "_restore_lock under any future lock-ordering change."
        )
