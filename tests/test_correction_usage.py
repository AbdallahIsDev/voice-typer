"""Tests for per-correction usage tracking (``correction_usage.py``).

Covers:

- ``CorrectionUsageTracker`` unit behaviour: record_corrections /
  record_dictation counting, last-trigger timestamps, persistence +
  reload, pruning (dead entries + old days).
- Integration with ``VocabularyManager.apply_to_text``: phrase and
  word-level corrections both report hits during real dictation, while
  ``track_usage=False`` (the "Test corrections" preview path) records
  nothing.
"""

from __future__ import annotations

import contextlib
import json
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest
from voice_typer.server import correction_usage
from voice_typer.server.correction_usage import (
    CORRECTION_USAGE_FILENAME,
    CorrectionUsageTracker,
)
from voice_typer.server.vocabulary import VocabularyManager


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


@pytest.fixture
def tracker(tmp_path: Path) -> CorrectionUsageTracker:
    return CorrectionUsageTracker(tmp_path)


class TestCorrectionUsageTracker:
    def test_record_corrections_counts_and_last_ts(self, tracker: CorrectionUsageTracker):
        now = time.time()
        tracker.record_corrections([("misspellings", "recieve", 2)], ts=now)
        tracker.record_corrections([("misspellings", "recieve", 1)], ts=now + 100)
        snap = tracker.get_snapshot()
        entry = snap["entries"]["misspellings"]["recieve"]
        assert entry["count"] == 3
        assert entry["last_ts"] == now + 100
        # per-day total aggregates both calls.
        assert snap["corrections_by_day"][_day(now)] == 3

    def test_phrase_and_word_hits_are_distinct_keys(self, tracker: CorrectionUsageTracker):
        tracker.record_corrections(
            [("phrase_corrections", "to 2", 1), ("misspellings", "teh", 3)],
            ts=time.time(),
        )
        snap = tracker.get_snapshot()
        assert snap["entries"]["phrase_corrections"]["to 2"]["count"] == 1
        assert snap["entries"]["misspellings"]["teh"]["count"] == 3

    def test_record_dictation_increments(self, tracker: CorrectionUsageTracker):
        now = time.time()
        tracker.record_dictation(ts=now)
        tracker.record_dictation(ts=now)
        snap = tracker.get_snapshot()
        assert snap["dictations_by_day"][_day(now)] == 2

    def test_persists_and_reloads(self, tmp_path: Path):
        now = time.time()
        t1 = CorrectionUsageTracker(tmp_path)
        t1.record_corrections([("misspellings", "recieve", 4)], ts=now)
        t1.record_dictation(ts=now)
        t1.flush()

        t2 = CorrectionUsageTracker(tmp_path)  # fresh instance reads the file
        snap = t2.get_snapshot()
        assert snap["entries"]["misspellings"]["recieve"]["count"] == 4
        assert snap["dictations_by_day"][_day(now)] == 1
        # schema version pinned on disk.
        assert snap["version"] == 1

    def test_prune_entries_drops_deleted_corrections(self, tmp_path: Path):
        t = CorrectionUsageTracker(tmp_path)
        t.record_corrections(
            [
                ("misspellings", "recieve", 2),
                ("phrase_corrections", "to 2", 1),
            ],
            ts=time.time(),
        )
        # Full merged vocabulary payload AFTER deleting "recieve".
        t.prune_entries(
            {
                "misspellings": {},
                "phrase_corrections": [["to 2", "to"]],
                "extra_word_patterns": [],
                "technical_terms": {},
                "names": {},
                "products": {},
            }
        )
        snap = t.get_snapshot()
        assert "recieve" not in snap["entries"].get("misspellings", {})
        assert "to 2" in snap["entries"]["phrase_corrections"]

    def test_prune_days_drops_old_totals(self, tmp_path: Path):
        t = CorrectionUsageTracker(tmp_path)
        t.record_corrections([("misspellings", "recieve", 1)], ts=1_700_000_000.0)  # 2023-11-14
        t.record_corrections([("misspellings", "recieve", 1)], ts=1_900_000_000.0)  # 2030-03-17
        # Force an old key older than KEEP_DAYS by writing one directly
        # (the debounced flush prunes on the next flush).
        with t._lock:
            t._data["corrections_by_day"]["2000-01-01"] = 99
            t._data["dictations_by_day"]["2000-01-01"] = 99
        t.flush()
        snap = t.get_snapshot()
        assert "2000-01-01" not in snap["corrections_by_day"]
        assert "2000-01-01" not in snap["dictations_by_day"]
        # Recent totals survive.
        assert snap["corrections_by_day"][_day(1_900_000_000.0)] == 1

    def test_snapshot_is_a_deep_copy(self, tracker: CorrectionUsageTracker):
        tracker.record_corrections([("misspellings", "recieve", 1)], ts=time.time())
        snap = tracker.get_snapshot()
        snap["entries"]["misspellings"]["recieve"]["count"] = 999
        assert tracker.get_snapshot()["entries"]["misspellings"]["recieve"]["count"] == 1

    def test_usage_file_written_with_schema(self, tmp_path: Path):
        t = CorrectionUsageTracker(tmp_path)
        t.record_corrections([("misspellings", "recieve", 1)], ts=time.time())
        t.flush()
        raw = json.loads((tmp_path / CORRECTION_USAGE_FILENAME).read_text(encoding="utf-8"))
        assert raw["version"] == 1
        assert raw["entries"]["misspellings"]["recieve"]["count"] == 1


class TestDebouncedFlushSweeper:
    """The debounced flush runs on the shared background sweeper
    thread — NOT on the dictation path.

    ``record_corrections`` / ``record_dictation`` are called between
    "transcription done" and "text pasted"; a full json.dumps + atomic
    write + .bak rotation there is a periodic latency spike right
    before the paste. These tests pin the moved contract: recording
    returns promptly even when the save is slow, the save still happens
    (off-thread) once the debounce window elapses, and the forced
    ``flush()`` on ``get_snapshot`` is preserved so IPC reads stay
    consistent.
    """

    @pytest.fixture(autouse=True)
    def _quiesce_shared_sweeper(self):
        """Stop the shared sweeper after each test.

        The sweeper is process-wide; a slow-disk simulation (or a
        shortened debounce window) must never leak into later tests.
        """
        yield
        with contextlib.suppress(Exception):
            correction_usage.stop_flush_sweeper()

    def test_record_dictation_does_not_block_on_slow_save(self, tracker, monkeypatch):
        """E6 pin: the caller returns promptly while the usage-file
        save is still in flight on the sweeper thread."""
        entered = threading.Event()
        release = threading.Event()
        real_save = tracker._store.save

        def slow_save(*args, **kwargs):
            entered.set()
            # Simulate a slow disk: the save blocks until the test
            # releases it. The dictation caller must NOT wait for this.
            release.wait(timeout=10.0)
            return real_save(*args, **kwargs)

        monkeypatch.setattr(tracker._store, "save", slow_save)
        # Make the debounce window already elapsed, so the very next
        # record is flush-due (the exact condition that used to trigger
        # the inline flush).
        with tracker._lock:
            tracker._last_flush = 0.0

        try:
            start = time.perf_counter()
            tracker.record_dictation()
            elapsed = time.perf_counter() - start

            assert elapsed < 2.0, (
                f"record_dictation blocked for {elapsed:.2f}s on the usage-file "
                "save — the debounced flush must run on the sweeper thread, "
                "never on the dictation path"
            )
            # The save itself still runs (on the sweeper thread) without
            # the caller having waited for it.
            assert entered.wait(timeout=5.0), "sweeper never attempted the save"
        finally:
            release.set()

    def test_sweeper_persists_after_debounce_window(self, tmp_path, monkeypatch):
        """End-to-end: pending increments reach disk without any explicit
        flush(), once the debounce window has elapsed."""
        monkeypatch.setattr(correction_usage, "FLUSH_INTERVAL_S", 0.2)
        t = CorrectionUsageTracker(tmp_path)

        # First dictation: the very first flush is immediately due
        # ("never flushed yet"), then get_snapshot re-flushes forcibly —
        # the file now exists with count 1 and the debounce window is
        # freshly reset.
        t.record_dictation()
        t.get_snapshot()
        path = tmp_path / CORRECTION_USAGE_FILENAME
        today = _day(time.time())

        # Second dictation lands INSIDE the debounce window: the file
        # may not reflect it yet, but the sweeper must persist it once
        # the window elapses — with no explicit flush() call.
        t.record_dictation()
        deadline = time.monotonic() + 5.0
        raw: dict = {}
        while time.monotonic() < deadline:
            if path.exists():
                raw = json.loads(path.read_text(encoding="utf-8"))
                if raw.get("dictations_by_day", {}).get(today) == 2:
                    break
            time.sleep(0.05)
        assert raw.get("dictations_by_day", {}).get(today) == 2, (
            "sweeper did not persist the pending increment after the debounce window"
        )

    def test_flush_if_due_honors_debounce_window(self, tracker):
        """White-box: inside the window → report remaining time and do
        NOT write; after the window → flush."""
        now = time.time()
        with tracker._lock:
            tracker._dirty = True
            tracker._last_flush = now

        remaining = tracker._flush_if_due(now)
        assert remaining is not None, "a just-flushed tracker must not be flush-due"
        assert 0 < remaining <= correction_usage.FLUSH_INTERVAL_S
        assert tracker._dirty is True, "nothing must be written inside the window"

        with tracker._lock:
            tracker._last_flush = now - correction_usage.FLUSH_INTERVAL_S - 1
        assert tracker._flush_if_due(now + correction_usage.FLUSH_INTERVAL_S + 1) is None
        assert tracker._dirty is False, "the due flush must have written the file"

    def test_snapshot_forced_flush_still_writes_file(self, tmp_path):
        """get_snapshot keeps its forced flush: the IPC read path must
        persist pending increments so the served snapshot and the file
        agree."""
        t = CorrectionUsageTracker(tmp_path)
        t.record_corrections([("misspellings", "recieve", 1)])
        # No explicit flush() — only the snapshot read.
        snap = t.get_snapshot()
        assert snap["entries"]["misspellings"]["recieve"]["count"] == 1
        raw = json.loads((tmp_path / CORRECTION_USAGE_FILENAME).read_text(encoding="utf-8"))
        assert raw["entries"]["misspellings"]["recieve"]["count"] == 1
        assert raw["version"] == 1  # schema untouched (persistence model unchanged)

    def test_shared_sweeper_is_a_single_thread(self, tracker):
        """Recording from several trackers must not spawn one thread per
        tracker — the sweeper is a process-wide singleton."""
        before = threading.active_count()
        tracker.record_dictation()
        tracker.record_corrections([("misspellings", "recieve", 1)])
        for _ in range(5):
            tracker.record_dictation()
        # Allow the (idempotent) ensure call to settle, then compare.
        time.sleep(0.1)
        assert threading.active_count() <= before + 1, (
            "each record call must reuse the shared sweeper thread, not spawn a new one"
        )

    def test_flush_write_outside_lock_does_not_block_recorders(self, tracker, monkeypatch):
        """A dictation record landing DURING an in-flight (slow) flush
        save must not wait for the save: flush snapshots the payload
        under the instance lock and writes OUTSIDE it."""
        entered = threading.Event()
        release = threading.Event()
        real_save = tracker._store.save

        def slow_save(*args, **kwargs):
            entered.set()
            # Simulate a slow disk: the write blocks until the test
            # releases it. A recorder landing mid-save must NOT wait.
            release.wait(timeout=10.0)
            return real_save(*args, **kwargs)

        monkeypatch.setattr(tracker._store, "save", slow_save)

        tracker.record_dictation()
        flush_done = threading.Event()

        def _flush_in_background():
            tracker.flush()
            flush_done.set()

        worker = threading.Thread(target=_flush_in_background, daemon=True)
        worker.start()
        assert entered.wait(timeout=5.0), "flush never started the save"

        # The write is in flight and the instance lock is RELEASED: a
        # record landing right now must return promptly instead of
        # stalling for the whole save duration.
        start = time.perf_counter()
        tracker.record_dictation()
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, (
            f"record blocked for {elapsed:.2f}s on the in-flight flush save — "
            "the store write must run outside the instance lock"
        )

        release.set()
        assert flush_done.wait(timeout=5.0), "flush never finished after release"
        worker.join(timeout=5.0)

        # The mid-save record was only re-armed for the next flush — it
        # must NOT be lost: a follow-up flush persists it.
        snap = tracker.get_snapshot()
        assert snap["dictations_by_day"][_day(time.time())] == 2
        assert snap["version"] == 1  # schema untouched (C-PERSIST-2)

    def test_flush_failure_keeps_increments_pending(self, tracker, tmp_path, monkeypatch):
        """A failed save must not raise out of flush() and must keep the
        increments pending so the next flush retries them."""
        # Keep the sweeper out of this test — only the explicit flush
        # calls below may attempt saves, so the failure/retry sequence
        # is deterministic.
        monkeypatch.setattr(tracker, "_schedule_flush", lambda: None)
        attempts = {"n": 0}
        real_save = tracker._store.save

        def flaky_save(*args, **kwargs):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise OSError("simulated transient save failure")
            return real_save(*args, **kwargs)

        monkeypatch.setattr(tracker._store, "save", flaky_save)

        tracker.record_dictation()
        tracker.flush()  # must NOT raise despite the failing store write
        assert tracker._dirty is True, "a failed save must keep the increments pending for retry"

        tracker.flush()  # retry succeeds
        assert tracker._dirty is False
        assert attempts["n"] == 2, "the retried flush must attempt the save again"
        raw = json.loads((tmp_path / CORRECTION_USAGE_FILENAME).read_text(encoding="utf-8"))
        assert raw["dictations_by_day"][_day(time.time())] == 1

    def test_flush_ordering_newer_payload_lands_last(self, tracker, tmp_path, monkeypatch):
        """Snapshot order must equal save order: the payload snapshot is
        taken while the save lock is held, so a flusher queued behind an
        in-flight save cannot snapshot until that save has landed — an
        interleaved snapshot/save sequence can therefore never write an
        OLDER payload over a NEWER one on disk."""
        # Keep the sweeper out of this test — only the explicit flush
        # threads below may attempt saves, so the interleaving is
        # deterministic.
        monkeypatch.setattr(tracker, "_schedule_flush", lambda: None)

        slow_save_in_flight = threading.Event()
        release_slow_save = threading.Event()
        saves: list[dict] = []  # payloads in WRITE order
        real_save = tracker._store.save

        def gate_save(payload, **kwargs):
            if not slow_save_in_flight.is_set():
                # Save #1 (the first flusher): park mid-write while
                # holding the save lock, like a slow disk would.
                slow_save_in_flight.set()
                saves.append(payload)
                release_slow_save.wait(timeout=10.0)
                return real_save(payload, **kwargs)
            saves.append(payload)
            return real_save(payload, **kwargs)

        monkeypatch.setattr(tracker._store, "save", gate_save)

        # Probe flush's snapshot step (prune runs under the instance
        # lock right before the payload deepcopy) to observe WHEN each
        # flusher snapshots.
        first_snapshot_done = threading.Event()
        second_snapshot_done = threading.Event()
        real_prune = tracker._prune_days

        def prune_spy():
            if first_snapshot_done.is_set():
                second_snapshot_done.set()
            else:
                first_snapshot_done.set()
            return real_prune()

        monkeypatch.setattr(tracker, "_prune_days", prune_spy)

        today = _day(time.time())
        tracker.record_dictation()  # pending increment #1 (older payload)

        flusher_a_done = threading.Event()

        def _flush_a():
            tracker.flush()
            flusher_a_done.set()

        worker_a = threading.Thread(target=_flush_a, daemon=True)
        worker_a.start()
        assert slow_save_in_flight.wait(timeout=5.0), "flusher A never started its save"
        # Flusher A is parked mid-save while HOLDING the save lock; its
        # snapshot already happened (prune fired before the save).
        assert first_snapshot_done.is_set(), "flusher A must snapshot before its save"

        # A NEWER increment lands while save #1 is in flight.
        tracker.record_dictation()  # pending increment #2 (newer payload)

        flusher_b_started = threading.Event()
        flusher_b_done = threading.Event()

        def _flush_b():
            flusher_b_started.set()
            tracker.flush()
            flusher_b_done.set()

        worker_b = threading.Thread(target=_flush_b, daemon=True)
        worker_b.start()
        assert flusher_b_started.wait(timeout=5.0), "flusher B never ran"
        # While save #1 is parked (save lock held), flusher B must NOT be
        # able to reach its snapshot: with snapshot-then-write as two
        # separate critical sections, B could snapshot here and complete
        # its (newer) write while A's older snapshot was still queued —
        # letting the older payload land last. The held save lock forbids
        # exactly that.
        time.sleep(0.2)
        assert not second_snapshot_done.is_set(), (
            "a flusher queued behind an in-flight save snapshotted BEFORE that "
            "save landed — snapshot order no longer equals save order"
        )

        release_slow_save.set()
        assert flusher_a_done.wait(timeout=5.0), "flusher A never finished"
        assert flusher_b_done.wait(timeout=5.0), "flusher B never finished"
        worker_a.join(timeout=5.0)
        worker_b.join(timeout=5.0)
        assert second_snapshot_done.is_set(), "flusher B never snapshotted after the save landed"

        # Two saves, in snapshot order: the OLDER payload first, the
        # NEWER payload LAST (an older-payload-last order would leave
        # the file stale even though the newer flush ran).
        assert len(saves) == 2, f"expected exactly 2 saves, got {len(saves)}"
        assert saves[0]["dictations_by_day"][today] == 1, "first save must carry the older payload"
        assert saves[-1]["dictations_by_day"][today] == 2, "the NEWER payload must land LAST on disk"
        raw = json.loads((tmp_path / CORRECTION_USAGE_FILENAME).read_text(encoding="utf-8"))
        assert raw["dictations_by_day"][today] == 2, "the on-disk file must end up matching the newest payload"
        assert tracker._dirty is False


class TestSweeperStopRestartRace:
    """The stop→restart race on the shared sweeper thread.

    ``stop_flush_sweeper`` can time out its join while the sweeper is
    still inside one slow save pass; a record landing between that stop
    and the next restart used to restart the sweeper WITHOUT invalidating
    the stale thread, leaving TWO permanent sweeper loops. The sweeper is
    now bound to a generation counter: stop and restart both bump it, and
    a stale thread exits at its next loop check instead of resurrecting.
    """

    @pytest.fixture(autouse=True)
    def _quiesce_shared_sweeper(self):
        yield
        with contextlib.suppress(Exception):
            correction_usage.stop_flush_sweeper()

    def test_restart_after_timed_out_stop_leaves_exactly_one_sweeper(self, tmp_path, monkeypatch):
        tracker = CorrectionUsageTracker(tmp_path)
        entered = threading.Event()
        release = threading.Event()
        real_save = tracker._store.save

        def slow_save(*args, **kwargs):
            entered.set()
            # Hold the save open so the stop's join times out while the
            # stale thread is still mid-pass.
            release.wait(timeout=10.0)
            return real_save(*args, **kwargs)

        monkeypatch.setattr(tracker._store, "save", slow_save)
        # Make the first record flush-due immediately so the sweeper is
        # inside the slow save when the stop lands.
        with tracker._lock:
            tracker._last_flush = 0.0
        tracker.record_dictation()
        assert entered.wait(timeout=5.0), "sweeper never entered the slow save"

        # Stop with a join budget the blocked thread cannot meet.
        correction_usage.stop_flush_sweeper(timeout=0.1)
        assert correction_usage._sweeper_thread is None

        # A record lands between the stop and the next restart…
        tracker.record_dictation()
        # …starting a replacement. Release the slow save so the stale
        # thread can finish its in-flight pass; it must then EXIT (its
        # generation is stale) instead of looping forever beside the
        # replacement.
        release.set()
        deadline = time.monotonic() + 5.0
        sweepers: list[threading.Thread] = []
        while time.monotonic() < deadline:
            sweepers = [t for t in threading.enumerate() if t.name == "correction-usage-flusher" and t.is_alive()]
            if len(sweepers) == 1:
                break
            time.sleep(0.02)

        assert len(sweepers) == 1, (
            f"expected exactly ONE live sweeper after the stop→restart race; "
            f"got {len(sweepers)} — the stale thread survived a timed-out stop"
        )
        # The survivor must be the REGISTERED replacement (not the stale
        # thread with the replacement somehow dead).
        replacement = correction_usage._sweeper_thread
        assert replacement is not None and replacement.is_alive()
        assert replacement is sweepers[0]

        # Nothing was lost across the race: the mid-race record is still
        # persisted (forced flush on read).
        snap = tracker.get_snapshot()
        assert snap["dictations_by_day"][_day(time.time())] == 2


@pytest.fixture
def empty_bundled(tmp_path: Path) -> Path:
    p = tmp_path / "empty-corrections.json"
    p.write_text("{}", encoding="utf-8")
    return p


class TestApplyToTextTracking:
    def test_dictation_pass_records_hits(self, tmp_path: Path, empty_bundled: Path):
        vm = VocabularyManager(config_dir=tmp_path, bundled_path=empty_bundled)
        vm.add_entry("misspellings", "recieve", "receive")
        vm.add_phrase("phrase_corrections", "to 2", "to")

        out = vm.apply_to_text("I recieve it to 2 times")
        assert out == "I receive it to times"

        snap = vm.usage_tracker.get_snapshot()
        assert snap["entries"]["misspellings"]["recieve"]["count"] == 1
        assert snap["entries"]["phrase_corrections"]["to 2"]["count"] == 1
        # per-day total = 2 firings, dictation NOT counted here (that's
        # the storage-step hook).
        assert snap["corrections_by_day"] != {}

    def test_multiple_firings_in_one_pass_count_each(self, tmp_path: Path, empty_bundled: Path):
        vm = VocabularyManager(config_dir=tmp_path, bundled_path=empty_bundled)
        vm.add_entry("misspellings", "teh", "the")
        vm.apply_to_text("teh teh teh")
        snap = vm.usage_tracker.get_snapshot()
        assert snap["entries"]["misspellings"]["teh"]["count"] == 3

    def test_preview_pass_does_not_record(self, tmp_path: Path, empty_bundled: Path):
        vm = VocabularyManager(config_dir=tmp_path, bundled_path=empty_bundled)
        vm.add_entry("misspellings", "recieve", "receive")
        out = vm.apply_to_text("recieve", track_usage=False)
        assert out == "receive"
        snap = vm.usage_tracker.get_snapshot()
        assert snap["entries"] == {}
        assert snap["corrections_by_day"] == {}

    def test_injected_tracker_is_shared(self, tmp_path: Path, empty_bundled: Path):
        tracker = CorrectionUsageTracker(tmp_path)
        vm = VocabularyManager(
            config_dir=tmp_path,
            bundled_path=empty_bundled,
            usage_tracker=tracker,
        )
        vm.add_entry("misspellings", "recieve", "receive")
        vm.apply_to_text("recieve")
        # The injected instance is the one that recorded.
        assert tracker.get_snapshot()["entries"]["misspellings"]["recieve"]["count"] == 1

    def test_corrupt_file_does_not_kill_recording(self, tmp_path: Path):
        # A hand-edited / partially-written file with a non-dict bucket
        # must not silently disable usage tracking for the session (the
        # caller catches exceptions, so an AttributeError here would be
        # invisible). The good bucket still records.
        (tmp_path / CORRECTION_USAGE_FILENAME).write_text(
            json.dumps(
                {
                    "version": 1,
                    "entries": {"misspellings": "corrupt-string"},
                    "corrections_by_day": "corrupt-string",
                    "dictations_by_day": "corrupt-string",
                }
            ),
            encoding="utf-8",
        )
        t = CorrectionUsageTracker(tmp_path)
        # Must not raise; the corrupt buckets are skipped.
        t.record_corrections([("misspellings", "recieve", 1)])
        t.record_dictation()
        snap = t.get_snapshot()
        assert snap["entries"]["misspellings"] == "corrupt-string"
        # corrections_by_day / dictations_by_day remain untouched (the
        # corrupt buckets were skipped, not overwritten).
        assert snap["corrections_by_day"] == "corrupt-string"
        assert snap["dictations_by_day"] == "corrupt-string"


class TestEndToEndAppToIpc:
    """Real-workflow: dictation pass → app property → service snapshot.

    Exercises the FULL wiring the unit tests deliberately bypass: the
    app's ``correction_usage`` property must expose the LIVE
    vocabulary manager's tracker (one shared writer), the storage-step
    ``record_dictation`` hook must land in the same instance, and the
    service's ``get_correction_usage`` must serve the merged snapshot
    back out — i.e. exactly what the renderer's Vocabulary page and
    Analytics card read.
    """

    def test_dictation_to_service_snapshot(self, tmp_path: Path, empty_bundled: Path):
        from types import SimpleNamespace

        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.service.vocabulary import VocabularyMixin

        vm = VocabularyManager(config_dir=tmp_path, bundled_path=empty_bundled)
        vm.add_entry("misspellings", "recieve", "receive")

        # Build an app WITHOUT running ``VoiceTyperApp.__init__`` (it
        # boots the whole backend); exercise the real ``correction_usage``
        # property against the real ``_vocabulary_manager`` property.
        app = object.__new__(VoiceTyperApp)
        app._vocabulary_manager_backing = vm
        app.config = SimpleNamespace(config_dir=tmp_path)

        # The property must expose the LIVE manager's tracker — one
        # shared writer for dictation hits + the IPC read path.
        assert app.correction_usage is vm.usage_tracker

        # Dictation path: corrections fire into the shared tracker…
        out = vm.apply_to_text("I recieve the file")
        assert out == "I receive the file"
        # …and the storage-step hook records the completed dictation
        # through the SAME app property (as storage_step.py does).
        app.correction_usage.record_dictation()

        # Service path: the exact call the ``get_correction_usage`` IPC
        # handler makes.
        svc = object.__new__(VocabularyMixin)
        svc._app = app
        snap = svc.get_correction_usage()

        assert snap["entries"]["misspellings"]["recieve"]["count"] == 1
        assert snap["corrections_by_day"][_day(time.time())] == 1
        assert snap["dictations_by_day"][_day(time.time())] == 1
