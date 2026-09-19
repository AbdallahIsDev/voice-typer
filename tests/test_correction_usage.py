"""Tests for per-correction usage tracking (``correction_usage.py``)."""

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
    """The debounced flush runs on the shared background sweeper"""

    @pytest.fixture(autouse=True)
    def _quiesce_shared_sweeper(self):
        """Stop the shared sweeper after each test."""
        yield
        with contextlib.suppress(Exception):
            correction_usage.stop_flush_sweeper()

    def test_record_dictation_does_not_block_on_slow_save(self, tracker, monkeypatch):
        """E6 pin: the caller returns promptly while the usage-file"""
        entered = threading.Event()
        release = threading.Event()
        real_save = tracker._store.save

        def slow_save(*args, **kwargs):
            entered.set()
            # releases it. The dictation caller must NOT wait for this.
            release.wait(timeout=10.0)
            return real_save(*args, **kwargs)

        monkeypatch.setattr(tracker._store, "save", slow_save)
        # Make the debounce window already elapsed, so the very next
        with tracker._lock:
            tracker._last_flush = 0.0

        try:
            start = time.perf_counter()
            tracker.record_dictation()
            elapsed = time.perf_counter() - start

            assert elapsed < 2.0, (
                f"record_dictation blocked for {elapsed:.2f}s on the usage-file "
                "save, the debounced flush must run on the sweeper thread, "
                "never on the dictation path"
            )
            # The save itself still runs (on the sweeper thread) without
            assert entered.wait(timeout=5.0), "sweeper never attempted the save"
        finally:
            release.set()

    def test_sweeper_persists_after_debounce_window(self, tmp_path, monkeypatch):
        """End-to-end: pending increments reach disk without any explicit"""
        monkeypatch.setattr(correction_usage, "FLUSH_INTERVAL_S", 0.2)
        t = CorrectionUsageTracker(tmp_path)

        # First dictation: the very first flush is immediately due
        t.record_dictation()
        t.get_snapshot()
        path = tmp_path / CORRECTION_USAGE_FILENAME
        today = _day(time.time())

        # Second dictation lands INSIDE the debounce window: the file
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
        """White-box: inside the window → report remaining time and do"""
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
        """get_snapshot keeps its forced flush: the IPC read path must"""
        t = CorrectionUsageTracker(tmp_path)
        t.record_corrections([("misspellings", "recieve", 1)])
        # No explicit flush(), only the snapshot read.
        snap = t.get_snapshot()
        assert snap["entries"]["misspellings"]["recieve"]["count"] == 1
        raw = json.loads((tmp_path / CORRECTION_USAGE_FILENAME).read_text(encoding="utf-8"))
        assert raw["entries"]["misspellings"]["recieve"]["count"] == 1
        assert raw["version"] == 1  # schema untouched (persistence model unchanged)

    def test_shared_sweeper_is_a_single_thread(self, tracker):
        """Recording from several trackers must not spawn one thread per"""
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
        """A dictation record landing DURING an in-flight (slow) flush"""
        entered = threading.Event()
        release = threading.Event()
        real_save = tracker._store.save

        def slow_save(*args, **kwargs):
            entered.set()
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

        start = time.perf_counter()
        tracker.record_dictation()
        elapsed = time.perf_counter() - start
        assert elapsed < 1.0, (
            f"record blocked for {elapsed:.2f}s on the in-flight flush save, "
            "the store write must run outside the instance lock"
        )

        release.set()
        assert flush_done.wait(timeout=5.0), "flush never finished after release"
        worker.join(timeout=5.0)

        # must NOT be lost: a follow-up flush persists it.
        snap = tracker.get_snapshot()
        assert snap["dictations_by_day"][_day(time.time())] == 2
        assert snap["version"] == 1  # schema untouched (C-PERSIST-2)

    def test_flush_failure_keeps_increments_pending(self, tracker, tmp_path, monkeypatch):
        """A failed save must not raise out of flush() and must keep the"""
        # Keep the sweeper out of this test, only the explicit flush
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
        """Snapshot order must equal save order: the payload snapshot is"""
        # Keep the sweeper out of this test, only the explicit flush
        monkeypatch.setattr(tracker, "_schedule_flush", lambda: None)

        slow_save_in_flight = threading.Event()
        release_slow_save = threading.Event()
        saves: list[dict] = []  # payloads in WRITE order
        real_save = tracker._store.save

        def gate_save(payload, **kwargs):
            if not slow_save_in_flight.is_set():
                # Save #1 (the first flusher): park mid-write while
                slow_save_in_flight.set()
                saves.append(payload)
                release_slow_save.wait(timeout=10.0)
                return real_save(payload, **kwargs)
            saves.append(payload)
            return real_save(payload, **kwargs)

        monkeypatch.setattr(tracker._store, "save", gate_save)

        # Probe flush's snapshot step (prune runs under the instance
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
        time.sleep(0.2)
        assert not second_snapshot_done.is_set(), (
            "a flusher queued behind an in-flight save snapshotted BEFORE that "
            "save landed, snapshot order no longer equals save order"
        )

        release_slow_save.set()
        assert flusher_a_done.wait(timeout=5.0), "flusher A never finished"
        assert flusher_b_done.wait(timeout=5.0), "flusher B never finished"
        worker_a.join(timeout=5.0)
        worker_b.join(timeout=5.0)
        assert second_snapshot_done.is_set(), "flusher B never snapshotted after the save landed"

        assert len(saves) == 2, f"expected exactly 2 saves, got {len(saves)}"
        assert saves[0]["dictations_by_day"][today] == 1, "first save must carry the older payload"
        assert saves[-1]["dictations_by_day"][today] == 2, "the NEWER payload must land LAST on disk"
        raw = json.loads((tmp_path / CORRECTION_USAGE_FILENAME).read_text(encoding="utf-8"))
        assert raw["dictations_by_day"][today] == 2, "the on-disk file must end up matching the newest payload"
        assert tracker._dirty is False


class TestSweeperStopRestartRace:
    """The stop→restart race on the shared sweeper thread."""

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
            release.wait(timeout=10.0)
            return real_save(*args, **kwargs)

        monkeypatch.setattr(tracker._store, "save", slow_save)
        # Make the first record flush-due immediately so the sweeper is
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
            f"got {len(sweepers)}, the stale thread survived a timed-out stop"
        )
        # The survivor must be the REGISTERED replacement (not the stale
        replacement = correction_usage._sweeper_thread
        assert replacement is not None and replacement.is_alive()
        assert replacement is sweepers[0]

        # Nothing was lost across the race: the mid-race record is still
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
        assert snap["corrections_by_day"] == "corrupt-string"
        assert snap["dictations_by_day"] == "corrupt-string"


class TestEndToEndAppToIpc:
    """Real-workflow: dictation pass → app property → service snapshot."""

    def test_dictation_to_service_snapshot(self, tmp_path: Path, empty_bundled: Path):
        from types import SimpleNamespace

        from voice_typer.server.app import VoiceTyperApp
        from voice_typer.server.service.vocabulary import VocabularyMixin

        vm = VocabularyManager(config_dir=tmp_path, bundled_path=empty_bundled)
        vm.add_entry("misspellings", "recieve", "receive")

        # Build an app WITHOUT running ``VoiceTyperApp.__init__`` (it
        app = object.__new__(VoiceTyperApp)
        app._vocabulary_manager_backing = vm
        app.config = SimpleNamespace(config_dir=tmp_path)

        # The property must expose the LIVE manager's tracker, one
        assert app.correction_usage is vm.usage_tracker

        # Dictation path: corrections fire into the shared tracker…
        out = vm.apply_to_text("I recieve the file")
        assert out == "I receive the file"
        # …and the storage-step hook records the completed dictation
        app.correction_usage.record_dictation()

        # Service path: the exact call the ``get_correction_usage`` IPC
        svc = object.__new__(VocabularyMixin)
        svc._app = app
        snap = svc.get_correction_usage()

        assert snap["entries"]["misspellings"]["recieve"]["count"] == 1
        assert snap["corrections_by_day"][_day(time.time())] == 1
        assert snap["dictations_by_day"][_day(time.time())] == 1
