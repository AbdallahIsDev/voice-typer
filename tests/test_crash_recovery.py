"""Tests for voice_typer.crash_recovery: CrashRecovery add, save, clear, check."""

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


@pytest.fixture
def recovery_dir(tmp_config_dir):
    """Point config to a temp directory (via the canonical tmp_config_dir fixture)."""
    return tmp_config_dir


@pytest.fixture
def cr(recovery_dir):
    """Create a CrashRecovery instance with temp dir."""
    from voice_typer.server.crash_recovery import CrashRecovery

    return CrashRecovery(config_dir=recovery_dir)


class TestCrashRecoveryAdd:
    def test_add_entry(self, cr):
        cr.add("Hello world", pasted=False)
        assert cr.count == 1

    def test_add_multiple_entries(self, cr):
        cr.add("First", pasted=True)
        cr.add("Second", pasted=False)
        assert cr.count == 2

    def test_max_10_entries(self, cr):
        for i in range(15):
            cr.add(f"Entry {i}", pasted=False)
        assert cr.count == 10


class TestCrashRecoveryMarkPasted:
    def test_mark_latest_pasted(self, cr):
        cr.add("Hello", pasted=False)
        cr.mark_latest_pasted()
        entries = cr.get_all()
        assert entries[-1]["pasted"] is True

    def test_mark_pasted_by_index(self, cr):
        cr.add("First", pasted=False)
        cr.add("Second", pasted=False)
        assert cr.mark_pasted(0) is True
        entries = cr.get_all()
        assert entries[0]["pasted"] is True

    def test_mark_pasted_invalid_index(self, cr):
        assert cr.mark_pasted(99) is False


class TestCrashRecoveryUnpasted:
    def test_get_unpasted(self, cr):
        cr.add("Pasted", pasted=True)
        cr.add("Unpasted", pasted=False)
        unpasted = cr.get_unpasted()
        assert len(unpasted) == 1
        assert "Unpasted" in unpasted[0]["text"]


class TestCrashRecoveryCheckOnStartup:
    def test_check_returns_unpasted(self, cr):
        cr.add("Lost text", pasted=False)
        result = cr.check_on_startup()
        assert result is not None
        assert len(result) == 1

    def test_check_returns_none_when_all_pasted(self, cr):
        cr.add("Saved", pasted=True)
        result = cr.check_on_startup()
        assert result is None


class TestCrashRecoveryClear:
    def test_clear(self, cr):
        cr.add("Entry 1")
        cr.add("Entry 2")
        cr.clear()
        assert cr.count == 0


class TestCrashRecoveryPersistence:
    def test_persists_to_disk(self, recovery_dir):
        from voice_typer.server.crash_recovery import CrashRecovery

        cr1 = CrashRecovery(config_dir=recovery_dir)
        cr1.add("Persistent entry", pasted=False)
        # RELIABILITY-005: writes are async; explicit flush before
        cr1.flush()
        del cr1

        cr2 = CrashRecovery(config_dir=recovery_dir)
        assert cr2.count == 1
        assert cr2.get_all()[0]["text"] == "Persistent entry"

    def test_empty_recovery_file(self, recovery_dir):
        from voice_typer.server.crash_recovery import CrashRecovery

        # Write an empty recovery file
        path = recovery_dir / "recovery.json"
        path.write_text('{"entries": []}', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0


class TestCrashRecoveryAsyncWrites:
    """RELIABILITY-005: writes happen on a background thread, so"""

    def test_add_returns_immediately(self, cr):
        """returns.  We can't easily measure wall-clock in a unit test,"""
        cr.add("Fast entry", pasted=False)
        # The in-memory state should be updated synchronously
        assert cr.count == 1
        assert cr.get_all()[0]["text"] == "Fast entry"

    def test_flush_waits_for_pending_saves(self, cr, recovery_dir):
        """flush() blocks until all queued saves are done."""
        for i in range(20):
            cr.add(f"Entry {i}", pasted=False)
        assert cr.flush(timeout=5.0) is True
        # After flush, the file should reflect the latest state
        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        # MAX_RECOVERY_ENTRIES is 10, so only the last 10 should be on disk
        assert len(data["entries"]) == 10
        assert data["entries"][-1]["text"] == "Entry 19"

    def test_concurrent_adds_are_safe(self, cr):
        """Multiple threads calling add() concurrently should not"""
        import threading

        def writer(n: int) -> None:
            for i in range(50):
                cr.add(f"thread-{n}-item-{i}", pasted=False)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 4 threads * 50 items = 200, capped at MAX_RECOVERY_ENTRIES (10)
        assert cr.count == 10
        cr.flush(timeout=5.0)

    def test_shutdown_stops_worker(self, cr):
        """shutdown() signals the worker to exit."""
        cr.add("Before shutdown", pasted=False)
        cr.shutdown()
        # Worker thread should exit within a reasonable time
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive()

    def test_enqueue_save_drops_oldest_when_full(self, cr):
        """When the save queue is full, the oldest pending save is"""
        # Fill the queue past capacity without giving the worker time
        from unittest.mock import patch

        with patch.object(cr, "_save_sync", lambda: None):
            # _SAVE_QUEUE_MAXSIZE is 32; push 100 saves
            for _ in range(100):
                cr._enqueue_save()
        # No exception should have been raised; the queue should be
        assert cr._save_queue.qsize() <= 32 + 1  # +1 for race tolerance


class TestCrashRecoveryFlushTimeout:
    """``flush(timeout=...)`` must actually enforce the timeout."""

    def test_flush_returns_false_when_worker_stalled(self, cr):
        """``flush(timeout=0.1)`` returns ``False`` when the worker"""
        import time
        from unittest.mock import patch

        # Each save takes 0.5s; 5 saves need ~2.5s to drain.
        with patch.object(cr, "_save_sync", lambda: time.sleep(0.5)):
            for _ in range(5):
                cr._enqueue_save()
            result = cr.flush(timeout=0.1)
            assert result is False, (
                "flush(timeout=0.1) must return False when the worker is stalled and cannot drain the queue in time"
            )
            # The worker thread must survive the timeout, flush() just
            assert cr._save_thread is not None
            assert cr._save_thread.is_alive(), "worker thread must survive a flush timeout"

    def test_flush_returns_true_when_queue_drains_quickly(self, cr):
        """``flush(timeout=5.0)`` returns ``True`` when all saves"""
        # Enqueue several saves via the public add() API.
        for i in range(10):
            cr.add(f"Entry {i}", pasted=False)
        # 5s is plenty for 10 fast saves.
        result = cr.flush(timeout=5.0)
        assert result is True, "flush(timeout=5.0) must return True when the queue drains quickly"
        # Worker thread should still be alive and ready for more work.
        assert cr._save_thread is not None
        assert cr._save_thread.is_alive()

    def test_flush_sentinel_not_processed_as_save(self, cr, recovery_dir):
        """the flush sentinel must NOT be processed as a real save"""
        from unittest.mock import patch

        save_calls = []
        original_save = cr._save_sync

        def counting_save():
            save_calls.append(1)
            original_save()

        with patch.object(cr, "_save_sync", counting_save):
            cr.add("Entry 1", pasted=False)
            cr.add("Entry 2", pasted=False)
            assert cr.flush(timeout=5.0) is True
            assert len(save_calls) == 2, (
                f"expected exactly 2 save calls (one per add), got "
                f"{len(save_calls)}; the flush sentinel must not be "
                f"processed as a save"
            )

    def test_flush_does_not_break_subsequent_saves(self, cr):
        """after a timed-out flush(), the worker must still process"""
        import time
        from unittest.mock import patch

        # First, trigger a timeout with a slow save.
        with patch.object(cr, "_save_sync", lambda: time.sleep(0.3)):
            cr._enqueue_save()
            assert cr.flush(timeout=0.05) is False

        # Now, with the original (fast) _save_sync restored, add more
        cr.add("After timeout", pasted=False)
        result = cr.flush(timeout=5.0)
        assert result is True, "worker must still process saves normally after a flush timeout"
        assert cr._save_thread is not None
        assert cr._save_thread.is_alive()


class TestCrashRecoveryIntegration:
    """TEST-036: full crash-recovery loop, simulate a crash mid-dictation"""

    def test_recovery_after_simulated_crash(self, recovery_dir):
        """End-to-end:"""
        from voice_typer.server.crash_recovery import CrashRecovery

        # Session A: dictation completes but paste fails (simulated by
        cr_a = CrashRecovery(config_dir=recovery_dir)
        cr_a.add("Recover me, clipboard was unavailable", pasted=False)
        cr_a.flush(timeout=2.0)
        # Simulate crash: do NOT call shutdown(); just drop the ref.
        del cr_a

        # Session B: new process boots.
        cr_b = CrashRecovery(config_dir=recovery_dir)
        unpasted = cr_b.check_on_startup()

        assert unpasted is not None, "Expected check_on_startup to surface the unpasted entry, got None"
        # The returned entry should include the original text.
        texts = [e.get("text", "") for e in unpasted] if isinstance(unpasted, list) else [unpasted.get("text", "")]
        assert any("Recover me" in t for t in texts), f"Expected recovery text in {texts}"

    def test_mark_pasted_clears_from_unpasted_set(self, recovery_dir):
        """entry from the unpasted set so check_on_startup doesn't re-surface"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("Will be pasted", pasted=False)
        cr.flush(timeout=2.0)

        # Simulate successful paste.
        cr.mark_latest_pasted()
        cr.flush(timeout=2.0)
        del cr

        # New session: nothing should be unpasted.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        result = cr2.check_on_startup()
        # (fix): tighten the weak `result is None or []` assertion.
        assert result is None, f"Expected None (no unpasted entries), got {result!r}"


class TestCrashRecoveryShutdownFallback:
    """a-review Finding A1: ``shutdown()``'s docstring claims post-shutdown"""

    def test_add_after_shutdown_persists_synchronously(self, recovery_dir):
        """Finding A1: ``add()`` post-shutdown must persist to disk."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        # Ensure the worker has fully exited before the post-shutdown
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive(), "worker thread should exit promptly after shutdown()"

        # Post-shutdown mutation, must be saved synchronously.
        cr.add("post-shutdown entry", pasted=False)

        # Read the recovery file back from disk and verify the entry
        recovery_file = recovery_dir / "recovery.json"
        assert recovery_file.exists(), "Recovery file must exist on disk after post-shutdown add()"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        texts = [e.get("text", "") for e in data.get("entries", [])]
        assert "post-shutdown entry" in texts, (
            f"Post-shutdown add() must persist via synchronous fallback; got entries: {texts}"
        )

    def test_mark_latest_pasted_after_shutdown_persists(self, recovery_dir):
        """Finding A1: ``mark_latest_pasted()`` post-shutdown must also"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("pre-shutdown entry", pasted=False)
        cr.flush(timeout=2.0)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        # Pre-shutdown entry exists; mark it pasted post-shutdown.
        cr.mark_latest_pasted()

        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        assert entries, "expected the pre-shutdown entry on disk"
        assert entries[-1].get("pasted") is True, (
            "mark_latest_pasted() post-shutdown must persist the pasted=True flag to disk via the sync fallback"
        )

    def test_mark_pasted_after_shutdown_persists(self, recovery_dir):
        """XE-16-1: ``mark_pasted(index)`` post-shutdown must also"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("first entry", pasted=False)
        cr.add("second entry", pasted=False)
        cr.flush(timeout=2.0)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        # Post-shutdown: mark the FIRST entry as pasted. Pre-fix this
        ok = cr.mark_pasted(0)
        assert ok is True, "mark_pasted(0) must return True for an in-range index"

        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        assert entries, "expected the pre-shutdown entries on disk"
        assert entries[0].get("pasted") is True, (
            "mark_pasted(0) post-shutdown must persist the pasted=True "
            "flag to disk via the sync fallback (XE-16-1 regression)"
        )
        # The second entry must remain unpasted (no over-broad mutation).
        assert entries[1].get("pasted") is False, "mark_pasted(0) must not affect other entries' pasted flag"

    def test_clear_after_shutdown_persists_empty_state(self, recovery_dir):
        """Finding A1: ``clear()`` post-shutdown must persist the empty"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("will be cleared", pasted=False)
        cr.flush(timeout=2.0)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        cr.clear()

        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        assert data.get("entries") == [], (
            "clear() post-shutdown must persist the empty state via the "
            "sync fallback; got: " + repr(data.get("entries"))
        )

    def test_concurrent_post_shutdown_adds_are_safe(self, recovery_dir):
        """Finding A1: ``_save_lock`` must serialize concurrent"""
        import threading

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        # 4 threads × 5 adds = 20 entries, capped at MAX_RECOVERY_ENTRIES (10).
        def writer(n: int) -> None:
            for i in range(5):
                cr.add(f"thread-{n}-item-{i}", pasted=False)

        threads = [threading.Thread(target=writer, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # In-memory state: capped at 10.
        assert cr.count == 10

        # On-disk state must match (final sync save wins, serialized
        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        on_disk_entries = data.get("entries", [])
        assert len(on_disk_entries) == 10, (
            f"expected 10 entries on disk, got {len(on_disk_entries)}; "
            f"_save_lock may have failed to serialize concurrent saves"
        )


class TestCrashRecoveryDelAfterShutdown:
    """skipped the save entirely after ``shutdown()`` killed the worker,"""

    def test_post_shutdown_entry_survives_del(self, recovery_dir):
        """Finding A3: post-shutdown mutations must survive ``__del__``."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive()

        cr.add("survives-del", pasted=False)

        # Force GC of the instance, __del__ must not lose the entry.
        del cr

        # New session re-opens the same file.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        texts = [e.get("text", "") for e in cr2.get_all()]
        assert "survives-del" in texts, f"Post-shutdown entry must survive __del__; got texts: {texts}"
        cr2.shutdown()

    def test_del_saves_unpersisted_post_shutdown_mutations(self, recovery_dir):
        """Finding A3 regression guard: ``__del__`` must save any"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive()

        # Mutate _entries directly, bypassing add()/_enqueue_save()
        with cr._lock:
            cr._entries.append(
                {
                    "text": "del-only-mutation",
                    "timestamp": "2026-07-15T00:00:00",
                    "pasted": False,
                }
            )

        # Sanity: the entry is in memory but NOT on disk yet.
        recovery_file = recovery_dir / "recovery.json"
        if recovery_file.exists():
            pre = json.loads(recovery_file.read_text(encoding="utf-8"))
            assert all(e.get("text") != "del-only-mutation" for e in pre.get("entries", [])), (
                "test setup error: entry should not be on disk before __del__"
            )

        # Force GC of the instance, worker is dead, so __del__ fires.
        del cr

        # Re-instantiate and verify the bypassed mutation survived.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        texts = [e.get("text", "") for e in cr2.get_all()]
        assert "del-only-mutation" in texts, (
            f"__del__ must save post-shutdown _entries mutations that bypassed _enqueue_save(); got texts: {texts}"
        )
        cr2.shutdown()

    def test_shutdown_does_final_sync_save(self, recovery_dir):
        """Finding A3: ``shutdown()`` itself must do a final"""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # Enqueue several saves rapidly so the worker may not have
        for i in range(15):
            cr.add(f"entry-{i}", pasted=False)
        cr.shutdown()

        # On-disk state must reflect the latest _entries (capped at 10).
        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        on_disk_texts = [e.get("text", "") for e in data.get("entries", [])]
        in_memory_texts = [e.get("text", "") for e in cr.get_all()]
        assert on_disk_texts == in_memory_texts, (
            f"shutdown()'s final _save_sync() must persist the latest "
            f"_entries state. On disk: {on_disk_texts}; in memory: "
            f"{in_memory_texts}"
        )
        # Specifically, the last entry should be entry-14.
        assert on_disk_texts[-1] == "entry-14", (
            f"expected entry-14 to be the last on-disk entry, got {on_disk_texts[-1]}"
        )


class TestCrashRecoveryQuarantineCorrupt:
    """GT-A1-5: when ``_load`` encounters a corrupt recovery file"""

    def test_corrupt_json_file_is_quarantined(self, recovery_dir):
        """GT-A1-5: a file with broken JSON is renamed to"""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "recovery.json"
        path.write_text('{"entries": [NOT VALID JSON', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0, "GT-A1-5: corrupt file must yield _entries=[]"
        assert not path.exists(), "GT-A1-5: original corrupt file must be moved out of the way"
        quarantined = list(recovery_dir.glob("recovery.json.corrupt.*"))
        assert len(quarantined) == 1, f"GT-A1-5: expected exactly one quarantine file; got {quarantined}"
        assert "NOT VALID JSON" in quarantined[0].read_text(encoding="utf-8")

    def test_corrupt_shape_file_is_quarantined(self, recovery_dir):
        """GT-A1-5: a file with valid JSON but the wrong shape (no"""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "recovery.json"
        path.write_text('{"unexpected_key": 42}', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
        assert not path.exists(), "GT-A1-5: wrong-shape file must be quarantined"
        quarantined = list(recovery_dir.glob("recovery.json.corrupt.*"))
        assert len(quarantined) == 1

    def test_quarantine_allows_next_save_to_start_fresh(self, recovery_dir):
        """GT-A1-5: after a corrupt file is quarantined, the next"""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "recovery.json"
        path.write_text('{"entries": [BROKEN', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
        cr.add("fresh after corruption", pasted=False)
        cr.flush(timeout=2.0)
        assert path.exists(), "GT-A1-5: fresh file must exist at original path"
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [e.get("text", "") for e in data.get("entries", [])]
        assert "fresh after corruption" in texts
        quarantined = list(recovery_dir.glob("recovery.json.corrupt.*"))
        assert len(quarantined) == 1
        cr.shutdown()

    def test_quarantine_corrupt_is_best_effort(self, recovery_dir):
        """GT-A1-5: if the move fails, ``_quarantine_corrupt`` must"""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "recovery.json"
        path.write_text('{"entries": [BROKEN', encoding="utf-8")

        import os as _os
        import unittest.mock as _mock

        original_replace = _os.replace

        def boom(src, dst, *args, **kwargs):
            if str(src) == str(path):
                raise OSError("simulated cross-device replace failure")
            return original_replace(src, dst, *args, **kwargs)

        with _mock.patch.object(_os, "replace", boom):
            cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
        cr.shutdown()


class TestQuarantineHardenedAtomicMove:
    """``_quarantine_corrupt`` must use the hardened move:"""

    def test_quarantine_filename_includes_pid_and_ns(self, recovery_dir):
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "recovery.json"
        path.write_text('{"entries": [BROKEN', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        cr._quarantine_corrupt()

        quarantined = list(recovery_dir.glob("recovery.json.corrupt.*"))
        assert len(quarantined) == 1
        name = quarantined[0].name
        assert name.startswith("recovery.json.corrupt."), (
            f"the dot-separated base must be preserved (startup sweep glob contract): {name}"
        )
        suffix = name.split(".corrupt.", 1)[1]
        import os as _os
        import re as _re

        assert _re.fullmatch(r"\d{8}_\d{6}-\d+-\d+", suffix), (
            f"quarantine suffix must be <YYYYMMDD_HHMMSS>-<pid>-<ns>, got: {suffix}"
        )
        assert f"-{_os.getpid()}-" in suffix, f"the embedded pid must match this process: {suffix}"
        cr.shutdown()

    def test_concurrent_same_second_quarantines_produce_distinct_files(self, tmp_path):
        """Two quarantine calls racing within the same second on files"""
        import threading

        from voice_typer.server.crash_recovery import CrashRecovery

        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()

        errors: list[Exception] = []
        start = threading.Barrier(2)

        def quarantine_in(dir_path: Path, marker: str):
            try:
                start.wait()  # maximise the same-second collision window
                path = dir_path / "recovery.json"
                path.write_text(marker, encoding="utf-8")
                cr = CrashRecovery(config_dir=dir_path)
                cr._quarantine_corrupt()
                cr.shutdown()
            except Exception as exc:  # noqa: BLE001, thread-pool error collection
                errors.append(exc)

        threads = [
            threading.Thread(target=quarantine_in, args=(dir_a, "corrupt-from-A")),
            threading.Thread(target=quarantine_in, args=(dir_b, "corrupt-from-B")),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"threads raised: {errors}"

        names_a = [p.name for p in dir_a.glob("recovery.json.corrupt.*")]
        names_b = [p.name for p in dir_b.glob("recovery.json.corrupt.*")]
        assert len(names_a) == 1 and len(names_b) == 1, (
            f"expected one quarantine file per dir, got {names_a} / {names_b}"
        )
        assert names_a[0] != names_b[0], (
            "two same-second quarantines produced the SAME filename, "
            "the pid+ns uniqueness suffix is missing and one forensic "
            "copy can silently overwrite the other"
        )
        assert (dir_a / names_a[0]).read_text(encoding="utf-8") == "corrupt-from-A"
        assert (dir_b / names_b[0]).read_text(encoding="utf-8") == "corrupt-from-B"

    def test_quarantine_overwrites_preexisting_destination(self, recovery_dir, monkeypatch):
        """A pre-existing file at the exact computed quarantine"""
        import itertools
        import os as _os
        import types as _types
        from datetime import datetime as _real_datetime

        import voice_typer.server.crash_recovery._io as _io
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "recovery.json"
        path.write_text("new corrupt content", encoding="utf-8")

        class _FixedDatetime(_real_datetime):
            @classmethod
            def now(cls, tz=None):
                return cls(2026, 1, 2, 3, 4, 5)

        monkeypatch.setattr(_io, "datetime", _FixedDatetime)
        monkeypatch.setattr(_io, "time", _types.SimpleNamespace(time_ns=lambda: 777777))
        monkeypatch.setattr(_io, "_QUARANTINE_SUFFIX_SEQ", itertools.count())
        monkeypatch.setattr(_os, "getpid", lambda: 99999)

        dst = recovery_dir / "recovery.json.corrupt.20260102_030405-99999-777777"
        dst.write_text("previous quarantine content", encoding="utf-8")

        cr = CrashRecovery(config_dir=recovery_dir)
        # Must NOT raise, the pre-existing destination is replaced.
        cr._quarantine_corrupt()

        assert dst.read_text(encoding="utf-8") == "new corrupt content", (
            "the pre-existing destination must be overwritten with the new corrupt "
            "content (os.replace semantics; Path.rename would fail on Windows)"
        )
        assert not path.exists(), "the source corrupt file must be moved away"
        cr.shutdown()

    def test_quarantine_uses_os_replace_not_path_rename(self):
        """Structural pin: the quarantine move must go through"""
        import inspect

        import voice_typer.server.crash_recovery._io as _io

        source = inspect.getsource(_io._RecoveryIO._quarantine_corrupt)
        assert "os.replace(" in source, (
            "_quarantine_corrupt must move the corrupt file via os.replace "
            "(atomic, overwrites existing destination on POSIX and Windows)"
        )
        assert ".rename(" not in source, (
            "_quarantine_corrupt must not use Path.rename/os.rename, it raises "
            "OSError (winerror 183) on Windows when the destination exists"
        )


class TestDiagnosticBundleSurfaceRemoved:
    """The server-side diagnostic-bundle pipeline is deliberately gone."""

    def test_crash_recovery_has_no_bundle_delegate(self):
        from voice_typer.server.crash_recovery import CrashRecovery

        assert not hasattr(CrashRecovery, "create_diagnostic_bundle")

    def test_service_and_protocol_have_no_export_diagnostics(self):
        from unittest.mock import MagicMock

        from voice_typer.server.providers import ServiceProtocol
        from voice_typer.server.service import VoiceTyperService

        svc = VoiceTyperService(MagicMock())
        assert not hasattr(svc, "export_diagnostics")
        # The protocol and the implementation stay in lockstep.
        assert not hasattr(ServiceProtocol, "export_diagnostics")
        assert isinstance(svc, ServiceProtocol)

    def test_metadata_snapshot_still_available(self):
        """The live metadata-only accessor survives the pipeline removal."""
        from voice_typer.server.crash_recovery import CrashRecovery

        assert callable(CrashRecovery.entries_metadata_snapshot)
