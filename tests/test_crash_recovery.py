"""Tests for voice_typer.crash_recovery — CrashRecovery add, save, clear, check."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def recovery_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


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
        # collection ensures the data is on disk.
        cr1.flush()
        del cr1

        cr2 = CrashRecovery(config_dir=recovery_dir)
        assert cr2.count == 1
        assert cr2.get_all()[0]["text"] == "Persistent entry"

    def test_empty_recovery_file(self, recovery_dir):
        from voice_typer.server.crash_recovery import CrashRecovery
        # Write an empty recovery file
        path = recovery_dir / "voice-typer-recovery.json"
        path.write_text('{"entries": []}', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0


# ── RELIABILITY-005: async write path ────────────────────────────────────


class TestCrashRecoveryAsyncWrites:
    """RELIABILITY-005: writes happen on a background thread, so
    rapid mutations don't block the caller and the latest state is
    always the one persisted."""

    def test_add_returns_immediately(self, cr):
        """add() must not block on disk I/O — it enqueues a save and
        returns.  We can't easily measure wall-clock in a unit test,
        but we can verify the entry is visible in-memory immediately
        (before the worker has necessarily written it)."""
        import time
        cr.add("Fast entry", pasted=False)
        # The in-memory state should be updated synchronously
        assert cr.count == 1
        assert cr.get_all()[0]["text"] == "Fast entry"

    def test_flush_waits_for_pending_saves(self, cr, recovery_dir):
        """flush() blocks until all queued saves are done."""
        for i in range(20):
            cr.add(f"Entry {i}", pasted=False)
        # flush should complete within a reasonable timeout
        assert cr.flush(timeout=5.0) is True
        # After flush, the file should reflect the latest state
        import json
        recovery_file = recovery_dir / "voice-typer-recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        # MAX_RECOVERY_ENTRIES is 10, so only the last 10 should be on disk
        assert len(data["entries"]) == 10
        assert data["entries"][-1]["text"] == "Entry 19"

    def test_concurrent_adds_are_safe(self, cr):
        """Multiple threads calling add() concurrently should not
        corrupt the in-memory state."""
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
        """When the save queue is full, the oldest pending save is
        dropped (not the latest state).  This is the documented
        RELIABILITY-005 behavior — we'd rather lose an intermediate
        snapshot than block the transcription thread."""
        # Fill the queue past capacity without giving the worker time
        # to drain it.  We mock _save_sync to be a no-op so nothing
        # actually gets written, then verify no exception is raised.
        from unittest.mock import patch
        with patch.object(cr, "_save_sync", lambda: None):
            # _SAVE_QUEUE_MAXSIZE is 32; push 100 saves
            for _ in range(100):
                cr._enqueue_save()
        # No exception should have been raised; the queue should be
        # at or below its max size.
        assert cr._save_queue.qsize() <= 32 + 1  # +1 for race tolerance


# ── TEST-036: integration test for crash-recovery loop ────────────────


class TestCrashRecoveryIntegration:
    """TEST-036: full crash-recovery loop — simulate a crash mid-dictation
    and verify the next session's check_on_startup surfaces the unpasted
    entry. Previously only unit-level add/get/persist tests existed."""

    def test_recovery_after_simulated_crash(self, recovery_dir):
        """End-to-end:
        1. Session A: add an unpasted transcription.
        2. Flush + simulate crash (don't call shutdown; just drop the ref).
        3. Session B: new CrashRecovery instance reads the same file.
        4. check_on_startup() must return the unpasted entry.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        # Session A: dictation completes but paste fails (simulated by
        # pasted=False — this is exactly what ERR-004 now does when the
        # clipboard is unavailable).
        cr_a = CrashRecovery(config_dir=recovery_dir)
        cr_a.add("Recover me — clipboard was unavailable", pasted=False)
        cr_a.flush(timeout=2.0)
        # Simulate crash: do NOT call shutdown(); just drop the ref.
        # The next process reopens the same file from disk.
        del cr_a

        # Session B: new process boots.
        cr_b = CrashRecovery(config_dir=recovery_dir)
        unpasted = cr_b.check_on_startup()

        assert unpasted is not None, (
            "Expected check_on_startup to surface the unpasted entry, got None"
        )
        # The returned entry should include the original text.
        texts = [e.get("text", "") for e in unpasted] if isinstance(unpasted, list) else [unpasted.get("text", "")]
        assert any("Recover me" in t for t in texts), (
            f"Expected recovery text in {texts}"
        )

    def test_mark_pasted_clears_from_unpasted_set(self, recovery_dir):
        """After a successful paste, mark_latest_pasted must remove the
        entry from the unpasted set so check_on_startup doesn't re-surface
        it on the next boot."""
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
        # TEST-009 (fix): tighten the weak `result is None or []` assertion.
        # check_on_startup() is documented to return None when there are no
        # unpasted entries (crash_recovery.py:248-252). The `or []` branch
        # was defensive against an implementation drift that never happened;
        # asserting exactly `is None` catches any future regression where
        # the function starts returning [] for "nothing" instead of None.
        assert result is None, (
            f"Expected None (no unpasted entries), got {result!r}"
        )
