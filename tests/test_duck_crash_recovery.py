"""WM-FIX-P4 regression tests for ``duck_crash_recovery.py``."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

import pytest
from voice_typer.server.duck_crash_recovery import (
    _RESTORING_SENTINEL_FILENAME,
    DuckCrashRecovery,
)
from voice_typer.server.volume_backend_base import VolumeState


@pytest.fixture
def recovery_dir(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def crash_recovery(recovery_dir: Path) -> DuckCrashRecovery:
    cr = DuckCrashRecovery(config_dir=recovery_dir)
    yield cr
    # Idempotent teardown.
    with contextlib.suppress(Exception):
        cr.clear()


class TestCacheDirtyFieldRemoved:
    """: ``_cache_dirty`` was write-only (zero read sites)."""

    def test_no_cache_dirty_attribute(self, crash_recovery: DuckCrashRecovery) -> None:
        assert not hasattr(crash_recovery, "_cache_dirty"), (
            ": _cache_dirty field should be removed, it was write-only (zero read sites) and served no purpose."
        )

    def test_no_cache_dirty_in_source(self) -> None:
        """docstrings too)."""
        import voice_typer.server.duck_crash_recovery as mod

        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
            assert "_cache_dirty" not in source, ": _cache_dirty must not appear in duck_crash_recovery.py source"


class TestRestoringSentinelPath:
    """: ``DuckCrashRecovery`` exposes a separate"""

    def test_sentinel_path_is_distinct_from_main(self, crash_recovery: DuckCrashRecovery) -> None:
        assert crash_recovery._restoring_sentinel_path != crash_recovery.path
        assert crash_recovery._restoring_sentinel_path.name == _RESTORING_SENTINEL_FILENAME
        assert crash_recovery._restoring_sentinel_path.parent == crash_recovery.path.parent


class TestCase1NormalFirstLaunch:
    """Case 1: main file ``consumed=False``, no sentinel."""

    def test_load_stale_writes_sentinel_and_returns_state(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        # Pre-condition: no sentinel yet.
        assert not crash_recovery._restoring_sentinel_path.exists()

        state = crash_recovery.load_stale()

        assert state is not None
        assert state.linear == 0.7
        assert crash_recovery._restoring_sentinel_path.exists(), (
            " Case 1: load_stale must write the restoring sentinel "
            "BEFORE returning the state so a crash between load_stale and "
            "the caller's clear() is detectable on next launch."
        )

    def test_load_stale_does_not_flip_consumed_to_true(self, crash_recovery: DuckCrashRecovery) -> None:
        """flip is now deferred to ``clear()`` so a crash between load_stale"""
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        crash_recovery.load_stale()
        # Read the on-disk file, consumed must still be False.
        raw = crash_recovery.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["consumed"] is False, (
            ": load_stale must NOT flip consumed=True. The flip is "
            "deferred to clear() so a crash between load_stale and restore "
            "leaves consumed=False + sentinel exists (Case 3 next launch, "
            "re-attempt restore) instead of consumed=True + sentinel exists "
            "(Case 4, return None, volume stuck at ducked level)."
        )

    def test_load_stale_is_idempotent_within_process(self, crash_recovery: DuckCrashRecovery) -> None:
        """Two successive ``load_stale`` calls in the same process"""
        crash_recovery.save(VolumeState(linear=0.6, muted=False))
        first = crash_recovery.load_stale()
        second = crash_recovery.load_stale()
        assert first is not None
        assert second is not None
        assert first.linear == second.linear == 0.6


class TestCase3ReattemptRestore:
    """Case 3: main file ``consumed=False``, sentinel EXISTS."""

    def test_load_stale_returns_state_when_sentinel_exists_and_consumed_false(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery._restoring_sentinel_path.exists()

        state = crash_recovery.load_stale()

        assert state is not None, (
            " Case 3: load_stale must RE-ATTEMPT restore when the "
            "sentinel exists and consumed=False, the previous launch "
            "crashed mid-restore and the user's volume is still stuck at "
            "the ducked level. Returning None here would leave the user "
            "with no automatic recovery path."
        )
        assert state.linear == 0.7

    def test_sentinel_persists_after_reattempt(self, crash_recovery: DuckCrashRecovery) -> None:
        """After a Case 3 re-attempt, the sentinel must still exist so"""
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        crash_recovery._write_restoring_sentinel()
        crash_recovery.load_stale()
        assert crash_recovery._restoring_sentinel_path.exists()


class TestCase4CleanupSentinel:
    """Case 4: main file ``consumed=True``, sentinel EXISTS."""

    def test_load_stale_cleans_up_sentinel_when_consumed_true(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        crash_recovery._write_restoring_sentinel()
        # Manually flip consumed=True (simulating the _mark_consumed call).
        raw = crash_recovery.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        data["consumed"] = True
        from voice_typer.server.config import _secure_atomic_write

        _secure_atomic_write(crash_recovery.path, json.dumps(data), durability=False)
        assert crash_recovery._restoring_sentinel_path.exists()

        state = crash_recovery.load_stale()

        assert state is None, (
            " Case 4: load_stale must return None when consumed=True "
            "and sentinel exists, the previous restore already succeeded."
        )
        assert not crash_recovery._restoring_sentinel_path.exists(), (
            " Case 4: load_stale must clean up the sentinel when "
            "consumed=True (it's a stale leftover from a crashed clear())."
        )


class TestCase2PriorRestoreSucceeded:
    """Case 2: main file ``consumed=True``, no sentinel."""

    def test_load_stale_returns_none_when_consumed_true_no_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        # Manually flip consumed=True (simulating a fully-completed prior
        raw = crash_recovery.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        data["consumed"] = True
        from voice_typer.server.config import _secure_atomic_write

        _secure_atomic_write(crash_recovery.path, json.dumps(data), durability=False)

        state = crash_recovery.load_stale()

        assert state is None


class TestClearFlipsConsumedAndDeletesBoth:
    """: ``clear`` now performs three steps in order"""

    def test_clear_deletes_sentinel_and_main_file(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery.path.exists()
        assert crash_recovery._restoring_sentinel_path.exists()

        crash_recovery.clear()

        assert not crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()

    def test_clear_after_load_stale_cleans_up_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        """The end-to-end flow: save → load_stale (writes sentinel) →"""
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        state = crash_recovery.load_stale()
        assert state is not None
        assert crash_recovery._restoring_sentinel_path.exists()

        crash_recovery.clear()

        assert not crash_recovery._restoring_sentinel_path.exists()
        assert not crash_recovery.path.exists()
        assert crash_recovery.load_stale() is None

    def test_clear_with_only_main_file_no_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        """``clear`` works when only the main file exists (no sentinel"""
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        assert crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()

        # Should not raise.
        crash_recovery.clear()

        assert not crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()


class TestOrphanedSentinelCleanup:
    """: if the main file doesn't exist but the sentinel does"""

    def test_orphaned_sentinel_is_cleaned_up(self, crash_recovery: DuckCrashRecovery) -> None:
        # Write the sentinel without a main file.
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery._restoring_sentinel_path.exists()
        assert not crash_recovery.path.exists()

        state = crash_recovery.load_stale()

        assert state is None
        assert not crash_recovery._restoring_sentinel_path.exists(), (
            ": an orphaned sentinel (no main file) must be cleaned up by load_stale so it doesn't linger forever."
        )


class TestSaveClearsSentinel:
    """: a fresh ``save`` starts a new duck cycle, any leftover"""

    def test_save_removes_existing_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        # Simulate a leftover sentinel from a previous crashed restore.
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery._restoring_sentinel_path.exists()

        crash_recovery.save(VolumeState(linear=0.5, muted=False))

        assert not crash_recovery._restoring_sentinel_path.exists(), (
            ": save must remove any leftover sentinel, a fresh duck cycle starts a new restore lifecycle."
        )


class TestCrashMidRestoreReattemptsOnNextLaunch:
    """end-to-end: a crash between ``load_stale`` and the caller's"""

    def test_crash_after_load_stale_reattempts_on_next_launch(self, recovery_dir: Path) -> None:
        # Launch 1: save + load_stale (writes sentinel), then "crash"
        cr1 = DuckCrashRecovery(config_dir=recovery_dir)
        cr1.save(VolumeState(linear=0.7, muted=False))
        state = cr1.load_stale()
        assert state is not None
        assert state.linear == 0.7
        # Simulate crash: don't call clear. The sentinel is in place,
        assert cr1._restoring_sentinel_path.exists()
        raw = cr1.path.read_text(encoding="utf-8")
        assert json.loads(raw)["consumed"] is False

        # Launch 2: a fresh DuckCrashRecovery instance (simulating a new
        cr2 = DuckCrashRecovery(config_dir=recovery_dir)
        state2 = cr2.load_stale()

        assert state2 is not None, (
            "a crash between load_stale and clear must NOT prevent "
            "the next launch from re-attempting the restore. Pre- "
            "the on-disk consumed=True flag caused load_stale to return "
            "None, leaving the user's volume stuck at the ducked level."
        )
        assert state2.linear == 0.7

    def test_successful_restore_then_clear_returns_none_on_next_launch(self, recovery_dir: Path) -> None:
        """End-to-end happy path: launch 1 saves + restores + clears;"""
        cr1 = DuckCrashRecovery(config_dir=recovery_dir)
        cr1.save(VolumeState(linear=0.7, muted=False))
        state = cr1.load_stale()
        assert state is not None
        # Restore succeeded, clear() runs the full three-step cleanup.
        cr1.clear()
        assert not cr1.path.exists()
        assert not cr1._restoring_sentinel_path.exists()

        # Launch 2: nothing to restore.
        cr2 = DuckCrashRecovery(config_dir=recovery_dir)
        assert cr2.load_stale() is None


class TestCorruptFileHandlingPreserved:
    """``clear`` both the main file and the sentinel."""

    def test_corrupt_main_file_returns_none_and_clears(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.path.parent.mkdir(parents=True, exist_ok=True)
        crash_recovery.path.write_text("{invalid json")
        # Also leave a sentinel to verify clear() removes both.
        crash_recovery._write_restoring_sentinel()

        state = crash_recovery.load_stale()

        assert state is None
        assert not crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()
