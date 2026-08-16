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

import json
import time
from datetime import datetime
from pathlib import Path

import pytest
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
