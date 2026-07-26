"""Tests for voice_typer.crash_recovery — CrashRecovery add, save, clear, check."""

import json
from pathlib import Path

import pytest


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


class TestCrashRecoveryFlushTimeout:
    """RW-4: ``flush(timeout=...)`` must actually enforce the timeout.

    Previously ``flush()`` called ``Queue.join()``, which has no
    ``timeout`` parameter in the stdlib — the ``timeout`` argument was
    dead code.  If the worker stalled (disk full, NFS hang, fsync on a
    dying SSD, antivirus lock on Windows), ``flush()`` blocked forever,
    preventing clean shutdown.  The fix uses a sentinel + ``threading.Event``
    pattern so the timeout is actually enforced.
    """

    def test_flush_returns_false_when_worker_stalled(self, cr):
        """RW-4: ``flush(timeout=0.1)`` returns ``False`` when the worker
        can't drain the queue within the timeout.

        We patch ``_save_sync`` to sleep 0.5s per save and enqueue 5
        saves — the worker needs ~2.5s to drain, so a 0.1s timeout must
        fire and return ``False``.
        """
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
            # The worker thread must survive the timeout — flush() just
            # gives up waiting; it does NOT kill the worker.
            assert cr._save_thread is not None
            assert cr._save_thread.is_alive(), "worker thread must survive a flush timeout"

    def test_flush_returns_true_when_queue_drains_quickly(self, cr):
        """RW-4: ``flush(timeout=5.0)`` returns ``True`` when all saves
        complete within the timeout (the normal case)."""
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
        """RW-4: the flush sentinel must NOT be processed as a real save
        item — i.e., the worker must NOT call ``_save_sync()`` for it.

        We count ``_save_sync`` calls; after 2 ``add()`` calls + 1
        ``flush()``, exactly 2 saves should have been performed (one per
        add).  A 3rd call would mean the sentinel was incorrectly
        treated as a save.
        """
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
            # flush() returns True only after the sentinel is processed,
            # which happens AFTER both adds.  So save_calls is final.
            assert len(save_calls) == 2, (
                f"expected exactly 2 save calls (one per add), got "
                f"{len(save_calls)}; the flush sentinel must not be "
                f"processed as a save"
            )

    def test_flush_does_not_break_subsequent_saves(self, cr):
        """RW-4: after a timed-out flush(), the worker must still process
        new saves normally.  The sentinel left in the queue must not
        corrupt subsequent operations."""
        import time
        from unittest.mock import patch

        # First, trigger a timeout with a slow save.
        with patch.object(cr, "_save_sync", lambda: time.sleep(0.3)):
            cr._enqueue_save()
            assert cr.flush(timeout=0.05) is False

        # Now, with the original (fast) _save_sync restored, add more
        # entries and flush — the worker should still be functioning.
        cr.add("After timeout", pasted=False)
        result = cr.flush(timeout=5.0)
        assert result is True, "worker must still process saves normally after a flush timeout"
        assert cr._save_thread is not None
        assert cr._save_thread.is_alive()


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

        assert unpasted is not None, "Expected check_on_startup to surface the unpasted entry, got None"
        # The returned entry should include the original text.
        texts = [e.get("text", "") for e in unpasted] if isinstance(unpasted, list) else [unpasted.get("text", "")]
        assert any("Recover me" in t for t in texts), f"Expected recovery text in {texts}"

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
        assert result is None, f"Expected None (no unpasted entries), got {result!r}"


# ─── Task 4: Prewarm health check in diagnostics bundle ────────────────


class TestDiagnosticsPrewarmBundle:
    """Task 4: the diagnostic bundle must include a prewarm.json with
    the full prewarm status + sentinel/PID file contents.

    This gives support engineers full prewarm context in bug reports
    without asking the user to run `--status` manually.
    """

    def test_diagnostic_bundle_includes_prewarm_json(self, recovery_dir):
        """The bundle zip contains a prewarm.json entry."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
            assert "prewarm.json" in names, "Task 4: diagnostic bundle must include prewarm.json"

    def test_prewarm_json_contains_status_fields(self, recovery_dir):
        """prewarm.json contains all get_prewarm_status() fields."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = json.loads(zf.read("prewarm.json"))

        # All get_prewarm_status() fields must be present.
        for field in (
            "last_run",
            "elapsed_s",
            "cache_ratio",
            "cache_label",
            "cached_bytes",
            "total_bytes",
            "prewarm_running",
        ):
            assert field in prewarm_data, f"Task 4: prewarm.json must include '{field}'"

    def test_prewarm_json_contains_sentinel_and_pid_paths(self, recovery_dir):
        """prewarm.json includes sentinel_path and pid_file_path."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = json.loads(zf.read("prewarm.json"))

        assert "sentinel_path" in prewarm_data
        assert "pid_file_path" in prewarm_data
        # The paths must be non-empty strings.
        assert isinstance(prewarm_data["sentinel_path"], str)
        assert len(prewarm_data["sentinel_path"]) > 0
        assert isinstance(prewarm_data["pid_file_path"], str)
        assert len(prewarm_data["pid_file_path"]) > 0

    def test_prewarm_json_contains_sentinel_contents(self, recovery_dir):
        """prewarm.json includes sentinel_contents (raw file contents or None)."""
        import zipfile

        from voice_typer.server import prewarm
        from voice_typer.server.crash_recovery import CrashRecovery

        # Write a sentinel file so we can verify its contents are bundled.
        sentinel = prewarm._sentinel_path()
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("1720000000\n20.4\n2026-07-08T13:48:49")

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = json.loads(zf.read("prewarm.json"))

        assert prewarm_data["sentinel_contents"] == "1720000000\n20.4\n2026-07-08T13:48:49"

        # Cleanup.
        sentinel.unlink(missing_ok=True)

    def test_prewarm_json_includes_error_on_failure(self, recovery_dir, monkeypatch):
        """If the prewarm probe raises, prewarm.json includes {"error": ...}.

        Task 4: the prewarm probe must never abort the entire diagnostics
        export. If it fails, the error is included so support engineers
        know why prewarm data is missing.
        """
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        # Make get_prewarm_status raise.
        def raising_status():
            raise RuntimeError("sentinel corrupted")

        monkeypatch.setattr(
            "voice_typer.server.prewarm.get_prewarm_status",
            raising_status,
        )

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None, (
            "Task 4: diagnostics export must NOT fail when the prewarm "
            "probe raises — the error should be captured in prewarm.json"
        )

        with zipfile.ZipFile(bundle_path, "r") as zf:
            prewarm_data = json.loads(zf.read("prewarm.json"))

        assert "error" in prewarm_data, "Task 4: prewarm.json must include 'error' when the probe raises"
        assert "sentinel corrupted" in prewarm_data["error"]


# ── a-review Findings A1 + A3: shutdown() sync fallback + __del__ safety ──


class TestCrashRecoveryShutdownFallback:
    """a-review Finding A1: ``shutdown()``'s docstring claims post-shutdown
    calls to ``add()`` / ``mark_pasted()`` / etc. "will fall back to
    synchronous saves".  Previously this was false — ``_enqueue_save()``
    put on a queue whose worker had exited, silently losing the mutation.

    The fix makes ``_enqueue_save()`` call ``_save_sync()`` directly when
    ``self._stopped`` is True, serialized via ``_save_lock`` so concurrent
    callers don't trample each other.
    """

    def test_add_after_shutdown_persists_synchronously(self, recovery_dir):
        """Finding A1: ``add()`` post-shutdown must persist to disk.

        Steps (per directive):
        1. Construct CrashRecovery with a temp path.
        2. Call ``shutdown()``.
        3. Call ``add(...)`` post-shutdown.
        4. Verify the entry was actually persisted to disk by reading
           the file back.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        # Ensure the worker has fully exited before the post-shutdown
        # mutation (so the test is deterministic — the fallback path
        # must be exercised, not the worker drain path).
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive(), "worker thread should exit promptly after shutdown()"

        # Post-shutdown mutation — must be saved synchronously.
        cr.add("post-shutdown entry", pasted=False)

        # Read the recovery file back from disk and verify the entry
        # was persisted (not just held in-memory).
        recovery_file = recovery_dir / "voice-typer-recovery.json"
        assert recovery_file.exists(), "Recovery file must exist on disk after post-shutdown add()"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        texts = [e.get("text", "") for e in data.get("entries", [])]
        assert "post-shutdown entry" in texts, (
            f"Post-shutdown add() must persist via synchronous fallback; got entries: {texts}"
        )

    def test_mark_latest_pasted_after_shutdown_persists(self, recovery_dir):
        """Finding A1: ``mark_latest_pasted()`` post-shutdown must also
        persist (the documented contract covers all mutating methods,
        not just ``add()``)."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("pre-shutdown entry", pasted=False)
        cr.flush(timeout=2.0)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        # Pre-shutdown entry exists; mark it pasted post-shutdown.
        # mark_latest_pasted() returns None (per its signature), so we
        # just call it and verify the on-disk state below.
        cr.mark_latest_pasted()

        recovery_file = recovery_dir / "voice-typer-recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        assert entries, "expected the pre-shutdown entry on disk"
        assert entries[-1].get("pasted") is True, (
            "mark_latest_pasted() post-shutdown must persist the pasted=True flag to disk via the sync fallback"
        )

    def test_clear_after_shutdown_persists_empty_state(self, recovery_dir):
        """Finding A1: ``clear()`` post-shutdown must persist the empty
        state (otherwise a re-opened session would resurrect stale
        entries)."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.add("will be cleared", pasted=False)
        cr.flush(timeout=2.0)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)

        cr.clear()

        recovery_file = recovery_dir / "voice-typer-recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        assert data.get("entries") == [], (
            "clear() post-shutdown must persist the empty state via the "
            "sync fallback; got: " + repr(data.get("entries"))
        )

    def test_concurrent_post_shutdown_adds_are_safe(self, recovery_dir):
        """Finding A1: ``_save_lock`` must serialize concurrent
        post-shutdown sync saves so they don't trample each other on
        the file write.  Without the lock, two threads calling
        ``add()`` post-shutdown could race on ``_secure_atomic_write``
        and corrupt the recovery file or lose entries."""
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
        # by _save_lock — no torn writes, no corruption).
        recovery_file = recovery_dir / "voice-typer-recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        on_disk_entries = data.get("entries", [])
        assert len(on_disk_entries) == 10, (
            f"expected 10 entries on disk, got {len(on_disk_entries)}; "
            f"_save_lock may have failed to serialize concurrent saves"
        )


class TestCrashRecoveryDelAfterShutdown:
    """a-review Finding A3: ``__del__`` previously only saved if
    ``_save_thread.is_alive() and not _save_queue.empty()`` — which
    skipped the save entirely after ``shutdown()`` killed the worker,
    dropping any post-shutdown mutations on GC.

    The fix drops the ``is_alive()`` guard and saves whenever
    ``_entries`` is non-empty.  ``shutdown()`` also now does a final
    ``_save_sync()`` after joining the worker as cheap insurance.
    """

    def test_post_shutdown_entry_survives_del(self, recovery_dir):
        """Finding A3: post-shutdown mutations must survive ``__del__``.

        Steps (per directive):
        1. Construct CrashRecovery.
        2. Call ``shutdown()``.
        3. Call ``add()`` post-shutdown.
        4. Explicitly ``del`` the object.
        5. Re-instantiate a new CrashRecovery from the same path.
        6. Verify the post-shutdown entry survived.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive()

        # Post-shutdown mutation — relies on the A1 sync fallback to
        # be persisted at all.  The A3 fix ensures __del__ doesn't
        # *drop* it again even if a future regression breaks the
        # fallback path.
        cr.add("survives-del", pasted=False)

        # Force GC of the instance — __del__ must not lose the entry.
        del cr

        # New session re-opens the same file.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        texts = [e.get("text", "") for e in cr2.get_all()]
        assert "survives-del" in texts, f"Post-shutdown entry must survive __del__; got texts: {texts}"
        cr2.shutdown()

    def test_del_saves_unpersisted_post_shutdown_mutations(self, recovery_dir):
        """Finding A3 regression guard: ``__del__`` must save any
        post-shutdown mutations that bypassed ``_enqueue_save()``.

        Scenario: after ``shutdown()``, directly mutate ``_entries``
        (simulating an internal caller that skips the public API).
        With the OLD ``is_alive()`` guard, ``__del__`` would skip the
        save because the worker is dead.  With the A3 fix
        (``if self._entries:``), ``__del__`` saves regardless of
        worker state.

        Note: we can't rely on ``del cr`` to trigger ``__del__``
        while the worker is alive (the worker holds ``self`` via its
        bound-method target).  But after ``shutdown()`` + ``join()``,
        the worker has exited and ``del cr`` does fire ``__del__`` —
        which is exactly the post-shutdown scenario A3 targets.
        """
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
            assert not cr._save_thread.is_alive()

        # Mutate _entries directly, bypassing add()/_enqueue_save()
        # so the only path to disk is __del__'s save.
        with cr._lock:
            cr._entries.append(
                {
                    "text": "del-only-mutation",
                    "timestamp": "2026-07-15T00:00:00",
                    "pasted": False,
                }
            )

        # Sanity: the entry is in memory but NOT on disk yet.
        recovery_file = recovery_dir / "voice-typer-recovery.json"
        if recovery_file.exists():
            pre = json.loads(recovery_file.read_text(encoding="utf-8"))
            assert all(e.get("text") != "del-only-mutation" for e in pre.get("entries", [])), (
                "test setup error: entry should not be on disk before __del__"
            )

        # Force GC of the instance — worker is dead, so __del__ fires.
        del cr

        # Re-instantiate and verify the bypassed mutation survived.
        cr2 = CrashRecovery(config_dir=recovery_dir)
        texts = [e.get("text", "") for e in cr2.get_all()]
        assert "del-only-mutation" in texts, (
            f"__del__ must save post-shutdown _entries mutations that bypassed _enqueue_save(); got texts: {texts}"
        )
        cr2.shutdown()

    def test_shutdown_does_final_sync_save(self, recovery_dir):
        """Finding A3: ``shutdown()`` itself must do a final
        ``_save_sync()`` after joining the worker, so any in-flight
        mutation (raced just before shutdown) is persisted even if the
        worker didn't drain it.  We verify by checking the on-disk
        state matches ``_entries`` immediately after ``shutdown()``
        returns — no flush() or __del__ needed."""
        from voice_typer.server.crash_recovery import CrashRecovery

        cr = CrashRecovery(config_dir=recovery_dir)
        # Enqueue several saves rapidly so the worker may not have
        # drained them all before shutdown() joins with timeout=1.0.
        for i in range(15):
            cr.add(f"entry-{i}", pasted=False)
        cr.shutdown()

        # On-disk state must reflect the latest _entries (capped at 10).
        recovery_file = recovery_dir / "voice-typer-recovery.json"
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


# ============================================================================
# GT-A1-5: corrupt recovery file is quarantined (not silently dropped)
# ============================================================================


class TestCrashRecoveryQuarantineCorrupt:
    """GT-A1-5: when ``_load`` encounters a corrupt recovery file
    (unparseable JSON, truncated by a mid-write crash, wrong shape),
    it renames the file to ``<path>.corrupt.<timestamp>`` before
    resetting ``_entries = []``.
    """

    def test_corrupt_json_file_is_quarantined(self, recovery_dir):
        """GT-A1-5: a file with broken JSON is renamed to
        ``<name>.corrupt.<ts>`` and ``_entries`` is reset to ``[]``."""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "voice-typer-recovery.json"
        path.write_text('{"entries": [NOT VALID JSON', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0, "GT-A1-5: corrupt file must yield _entries=[]"
        assert not path.exists(), "GT-A1-5: original corrupt file must be moved out of the way"
        quarantined = list(recovery_dir.glob("voice-typer-recovery.json.corrupt.*"))
        assert len(quarantined) == 1, f"GT-A1-5: expected exactly one quarantine file; got {quarantined}"
        assert "NOT VALID JSON" in quarantined[0].read_text(encoding="utf-8")

    def test_corrupt_shape_file_is_quarantined(self, recovery_dir):
        """GT-A1-5: a file with valid JSON but the wrong shape (no
        ``entries`` key, not a list) is also quarantined."""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "voice-typer-recovery.json"
        path.write_text('{"unexpected_key": 42}', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
        assert not path.exists(), "GT-A1-5: wrong-shape file must be quarantined"
        quarantined = list(recovery_dir.glob("voice-typer-recovery.json.corrupt.*"))
        assert len(quarantined) == 1

    def test_quarantine_allows_next_save_to_start_fresh(self, recovery_dir):
        """GT-A1-5: after a corrupt file is quarantined, the next
        ``add()`` writes a fresh file at the original path."""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "voice-typer-recovery.json"
        path.write_text('{"entries": [BROKEN', encoding="utf-8")
        cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
        cr.add("fresh after corruption", pasted=False)
        cr.flush(timeout=2.0)
        assert path.exists(), "GT-A1-5: fresh file must exist at original path"
        data = json.loads(path.read_text(encoding="utf-8"))
        texts = [e.get("text", "") for e in data.get("entries", [])]
        assert "fresh after corruption" in texts
        quarantined = list(recovery_dir.glob("voice-typer-recovery.json.corrupt.*"))
        assert len(quarantined) == 1
        cr.shutdown()

    def test_quarantine_corrupt_is_best_effort(self, recovery_dir):
        """GT-A1-5: if the rename fails, ``_quarantine_corrupt`` must
        not raise — callers rely on a clean reset to ``_entries = []``."""
        from voice_typer.server.crash_recovery import CrashRecovery

        path = recovery_dir / "voice-typer-recovery.json"
        path.write_text('{"entries": [BROKEN', encoding="utf-8")

        import unittest.mock as _mock

        original_rename = Path.rename

        def boom(self, target):
            if self == path:
                raise OSError("simulated cross-device rename failure")
            return original_rename(self, target)

        with _mock.patch.object(Path, "rename", boom):
            cr = CrashRecovery(config_dir=recovery_dir)
        assert cr.count == 0
        cr.shutdown()


# ============================================================================
# GT-B2-13: log file added to diagnostic zip is redacted line-by-line
# ============================================================================


class TestDiagnosticBundleLogRedaction:
    """GT-B2-13: the voice-typer.log file is run through
    ``redact_secret(redact_pii(line))`` line-by-line before being added
    to the diagnostic bundle zip.
    """

    def test_log_in_zip_is_redacted_for_pii(self, recovery_dir):
        """GT-B2-13: a log line containing an email address has the
        email replaced with ``[EMAIL]`` in the bundled zip."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        log_path = recovery_dir / "voice-typer.log"
        log_path.write_text(
            "2026-07-24 INFO something happened for user@example.com\n",
            encoding="utf-8",
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None
        with zipfile.ZipFile(bundle_path, "r") as zf:
            bundled_log = zf.read("voice-typer.log").decode("utf-8")
        assert "user@example.com" not in bundled_log, f"GT-B2-13: PII (email) must be redacted; got:\n{bundled_log}"
        assert "[EMAIL]" in bundled_log, f"GT-B2-13: redacted log must contain [EMAIL] token; got:\n{bundled_log}"
        cr.shutdown()

    def test_log_in_zip_is_redacted_for_secrets(self, recovery_dir):
        """GT-B2-13: a log line containing a Bearer token has the
        token redacted in the bundled zip."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        log_path = recovery_dir / "voice-typer.log"
        log_path.write_text(
            "2026-07-24 DEBUG http call Authorization: Bearer eyJhbGciOiJIUzI1NiJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0.SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV_adQssw5c\n",
            encoding="utf-8",
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None
        with zipfile.ZipFile(bundle_path, "r") as zf:
            bundled_log = zf.read("voice-typer.log").decode("utf-8")
        assert "eyJhbGciOiJIUzI1NiJ9" not in bundled_log, (
            f"GT-B2-13: Bearer token must be redacted; got:\n{bundled_log}"
        )
        cr.shutdown()

    def test_log_in_zip_preserves_non_pii_content(self, recovery_dir):
        """GT-B2-13: non-PII / non-secret log content is preserved verbatim."""
        import zipfile

        from voice_typer.server.crash_recovery import CrashRecovery

        log_path = recovery_dir / "voice-typer.log"
        log_path.write_text(
            "2026-07-24 INFO model loaded successfully\n2026-07-24 INFO audio device opened\n",
            encoding="utf-8",
        )
        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        with zipfile.ZipFile(bundle_path, "r") as zf:
            bundled_log = zf.read("voice-typer.log").decode("utf-8")
        assert "model loaded successfully" in bundled_log
        assert "audio device opened" in bundled_log
        cr.shutdown()

    def test_log_redaction_failure_skips_log(self, recovery_dir, monkeypatch):
        """GT-B2-13: if redaction fails, the log is SKIPPED entirely
        rather than shipped raw — defense in depth."""
        import zipfile

        from voice_typer.server import security
        from voice_typer.server.crash_recovery import CrashRecovery

        log_path = recovery_dir / "voice-typer.log"
        log_path.write_text(
            "2026-07-24 INFO user@example.com leaked\n",
            encoding="utf-8",
        )

        def raising_redact_pii(text):
            raise RuntimeError("redaction unavailable")

        monkeypatch.setattr(security, "redact_pii", raising_redact_pii)

        cr = CrashRecovery(config_dir=recovery_dir)
        bundle_path = cr.create_diagnostic_bundle()
        assert bundle_path is not None, "GT-B2-13: bundle creation must not fail when redaction raises"
        with zipfile.ZipFile(bundle_path, "r") as zf:
            names = zf.namelist()
        assert "voice-typer.log" not in names, f"GT-B2-13: log must be skipped when redaction fails; got names: {names}"
        cr.shutdown()
