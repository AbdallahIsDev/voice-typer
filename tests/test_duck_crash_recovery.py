"""WM-FIX-P4 regression tests for ``duck_crash_recovery.py``.

Covers:

* **** (Medium): ``_mark_consumed`` previously wrote
  ``consumed=True`` BEFORE the caller restored the volume. A
  cross-process crash between the flip and the restore left the
  volume stuck at the ducked level with no way to detect the
  incomplete restore on next launch. The fix introduces a separate
  ``duck_crash_recovery.restoring`` sentinel file written by
  ``load_stale`` before the restore; the on-disk ``consumed=True``
  flag is now flipped only inside ``clear()`` AFTER the restore
  succeeded. The four next-launch cases (no/consumed=False,
  no/consumed=True, sentinel/consumed=False, sentinel/consumed=True)
  are each pinned by a dedicated test.

* **** (Low): the ``_cache_dirty: bool`` field was
  write-only (zero read sites) — pinned by a test that asserts the
  class no longer has the attribute.

These tests use the same ``recovery_dir`` / ``crash_recovery`` fixture
pattern as ``tests/test_volume_ducker.py`` so they integrate cleanly
with the existing test suite (same ``tmp_path`` isolation, same
teardown ``clear()``).
"""

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


# ═══════════════════════════════════════════════════════════════════════════
# _cache_dirty field is gone
# ═══════════════════════════════════════════════════════════════════════════


class TestCacheDirtyFieldRemoved:
    """: ``_cache_dirty`` was write-only (zero read sites).
    Pinned by a test that asserts the field is gone from the class."""

    def test_no_cache_dirty_attribute(self, crash_recovery: DuckCrashRecovery) -> None:
        assert not hasattr(crash_recovery, "_cache_dirty"), (
            ": _cache_dirty field should be removed — it was write-only (zero read sites) and served no purpose."
        )

    def test_no_cache_dirty_in_source(self) -> None:
        """The string ``_cache_dirty`` must not appear anywhere in the
        module's source (catches reintroduction via comments or
        docstrings too)."""
        import voice_typer.server.duck_crash_recovery as mod

        with open(mod.__file__, encoding="utf-8") as f:
            source = f.read()
            assert "_cache_dirty" not in source, ": _cache_dirty must not appear in duck_crash_recovery.py source"


# ═══════════════════════════════════════════════════════════════════════════
# separate restoring sentinel
# ═══════════════════════════════════════════════════════════════════════════


class TestRestoringSentinelPath:
    """: ``DuckCrashRecovery`` exposes a separate
    ``_restoring_sentinel_path`` for the in-progress-restore signal."""

    def test_sentinel_path_is_distinct_from_main(self, crash_recovery: DuckCrashRecovery) -> None:
        assert crash_recovery._restoring_sentinel_path != crash_recovery.path
        assert crash_recovery._restoring_sentinel_path.name == _RESTORING_SENTINEL_FILENAME
        assert crash_recovery._restoring_sentinel_path.parent == crash_recovery.path.parent


# ═══════════════════════════════════════════════════════════════════════════
# Case 1: consumed=False, no sentinel → write sentinel, return state
# ═══════════════════════════════════════════════════════════════════════════


class TestCase1NormalFirstLaunch:
    """Case 1: main file ``consumed=False``, no sentinel.

    ``load_stale`` writes the sentinel BEFORE returning the state. The
    on-disk ``consumed`` flag is NOT flipped to True here (the flip
    happens later in ``clear`` after the restore succeeds)."""

    def test_load_stale_writes_sentinel_and_returns_state(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        # Pre-condition: no sentinel yet.
        assert not crash_recovery._restoring_sentinel_path.exists()

        state = crash_recovery.load_stale()

        assert state is not None
        assert state.linear == 0.7
        # sentinel was written.
        assert crash_recovery._restoring_sentinel_path.exists(), (
            " Case 1: load_stale must write the restoring sentinel "
            "BEFORE returning the state so a crash between load_stale and "
            "the caller's clear() is detectable on next launch."
        )

    def test_load_stale_does_not_flip_consumed_to_true(self, crash_recovery: DuckCrashRecovery) -> None:
        """: ``load_stale`` must NOT write ``consumed=True`` — that
        flip is now deferred to ``clear()`` so a crash between load_stale
        and restore doesn't leave the volume stuck."""
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        crash_recovery.load_stale()
        # Read the on-disk file — consumed must still be False.
        raw = crash_recovery.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert data["consumed"] is False, (
            ": load_stale must NOT flip consumed=True. The flip is "
            "deferred to clear() so a crash between load_stale and restore "
            "leaves consumed=False + sentinel exists (Case 3 next launch — "
            "re-attempt restore) instead of consumed=True + sentinel exists "
            "(Case 4 — return None, volume stuck at ducked level)."
        )

    def test_load_stale_is_idempotent_within_process(self, crash_recovery: DuckCrashRecovery) -> None:
        """Two successive ``load_stale`` calls in the same process
        return the same state (preserves the existing test contract
        pinned by ``test_duck_persists_state_for_crash_recovery``)."""
        crash_recovery.save(VolumeState(linear=0.6, muted=False))
        first = crash_recovery.load_stale()
        second = crash_recovery.load_stale()
        assert first is not None
        assert second is not None
        assert first.linear == second.linear == 0.6


# ═══════════════════════════════════════════════════════════════════════════
# Case 3: consumed=False, sentinel EXISTS → re-attempt restore
# ═══════════════════════════════════════════════════════════════════════════


class TestCase3ReattemptRestore:
    """Case 3: main file ``consumed=False``, sentinel EXISTS.

       The previous launch wrote the sentinel but crashed BEFORE the
       caller could restore the volume. ``load_stale`` re-attempts the
    restore (returns the state again). This is the fix: the
       previous code flipped ``consumed=True`` here, which on crash left
       the volume stuck at the ducked level with no way to detect the
       incomplete restore."""

    def test_load_stale_returns_state_when_sentinel_exists_and_consumed_false(
        self, crash_recovery: DuckCrashRecovery
    ) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        # Simulate a previous launch that wrote the sentinel but
        # crashed before the caller could restore the volume.
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery._restoring_sentinel_path.exists()

        state = crash_recovery.load_stale()

        assert state is not None, (
            " Case 3: load_stale must RE-ATTEMPT restore when the "
            "sentinel exists and consumed=False — the previous launch "
            "crashed mid-restore and the user's volume is still stuck at "
            "the ducked level. Returning None here would leave the user "
            "with no automatic recovery path."
        )
        assert state.linear == 0.7

    def test_sentinel_persists_after_reattempt(self, crash_recovery: DuckCrashRecovery) -> None:
        """After a Case 3 re-attempt, the sentinel must still exist so
        a SECOND crash (between this load_stale and clear) is also
        detectable."""
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        crash_recovery._write_restoring_sentinel()
        crash_recovery.load_stale()
        assert crash_recovery._restoring_sentinel_path.exists()


# ═══════════════════════════════════════════════════════════════════════════
# Case 4: consumed=True, sentinel EXISTS → clean up sentinel, return None
# ═══════════════════════════════════════════════════════════════════════════


class TestCase4CleanupSentinel:
    """Case 4: main file ``consumed=True``, sentinel EXISTS.

    The previous launch's ``clear`` called ``_mark_consumed`` (flipping
    ``consumed=True``) but crashed before deleting the sentinel. The
    restore already succeeded; ``load_stale`` cleans up the sentinel
    and returns None (the user's volume is correct)."""

    def test_load_stale_cleans_up_sentinel_when_consumed_true(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        # Simulate the previous clear() crashing after _mark_consumed but
        # before deleting the sentinel.
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
            "and sentinel exists — the previous restore already succeeded."
        )
        assert not crash_recovery._restoring_sentinel_path.exists(), (
            " Case 4: load_stale must clean up the sentinel when "
            "consumed=True (it's a stale leftover from a crashed clear())."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Case 2: consumed=True, no sentinel → return None
# ═══════════════════════════════════════════════════════════════════════════


class TestCase2PriorRestoreSucceeded:
    """Case 2: main file ``consumed=True``, no sentinel.

       The previous launch's ``clear`` completed fully. Return None
    (existing behaviour, preserved by )."""

    def test_load_stale_returns_none_when_consumed_true_no_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.7, muted=False))
        # Manually flip consumed=True (simulating a fully-completed prior
        # clear that left the file in place as a forensic artefact).
        raw = crash_recovery.path.read_text(encoding="utf-8")
        data = json.loads(raw)
        data["consumed"] = True
        from voice_typer.server.config import _secure_atomic_write

        _secure_atomic_write(crash_recovery.path, json.dumps(data), durability=False)

        state = crash_recovery.load_stale()

        assert state is None


# ═══════════════════════════════════════════════════════════════════════════
# clear now flips consumed=True AND deletes both files
# ═══════════════════════════════════════════════════════════════════════════


class TestClearFlipsConsumedAndDeletesBoth:
    """: ``clear`` now performs three steps in order
    1. ``_mark_consumed(data)`` — flip ``consumed=True`` on the main file.
    2. Delete the restoring sentinel.
    3. Delete the main file.

    The three-step ordering makes the failure modes recoverable (see
    ``clear``'s docstring for the per-step crash analysis).
    """

    def test_clear_deletes_sentinel_and_main_file(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery.path.exists()
        assert crash_recovery._restoring_sentinel_path.exists()

        crash_recovery.clear()

        assert not crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()

    def test_clear_after_load_stale_cleans_up_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        """The end-to-end flow: save → load_stale (writes sentinel) →
        clear (deletes sentinel + main file). The next load_stale
        returns None."""
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        state = crash_recovery.load_stale()
        assert state is not None
        assert crash_recovery._restoring_sentinel_path.exists()

        crash_recovery.clear()

        assert not crash_recovery._restoring_sentinel_path.exists()
        assert not crash_recovery.path.exists()
        assert crash_recovery.load_stale() is None

    def test_clear_with_only_main_file_no_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        """``clear`` works when only the main file exists (no sentinel
        written yet — e.g. called directly after ``save`` without an
        intervening ``load_stale``)."""
        crash_recovery.save(VolumeState(linear=0.5, muted=False))
        assert crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()

        # Should not raise.
        crash_recovery.clear()

        assert not crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()


# ═══════════════════════════════════════════════════════════════════════════
# orphaned sentinel (no main file) is cleaned up
# ═══════════════════════════════════════════════════════════════════════════


class TestOrphanedSentinelCleanup:
    """: if the main file doesn't exist but the sentinel does
    (e.g. ``clear`` deleted the main file but crashed before deleting
    the sentinel), ``load_stale`` cleans up the orphaned sentinel."""

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


# ═══════════════════════════════════════════════════════════════════════════
# save clears any leftover sentinel (fresh duck cycle)
# ═══════════════════════════════════════════════════════════════════════════


class TestSaveClearsSentinel:
    """: a fresh ``save`` starts a new duck cycle — any leftover
    sentinel from a previous (crashed) restore attempt is stale and
    must be removed so the next ``load_stale`` doesn't mistake it for
    an in-flight restore."""

    def test_save_removes_existing_sentinel(self, crash_recovery: DuckCrashRecovery) -> None:
        # Simulate a leftover sentinel from a previous crashed restore.
        crash_recovery._write_restoring_sentinel()
        assert crash_recovery._restoring_sentinel_path.exists()

        crash_recovery.save(VolumeState(linear=0.5, muted=False))

        assert not crash_recovery._restoring_sentinel_path.exists(), (
            ": save must remove any leftover sentinel — a fresh duck cycle starts a new restore lifecycle."
        )


# ═══════════════════════════════════════════════════════════════════════════
# end-to-end "crash mid-restore" simulation
# ═══════════════════════════════════════════════════════════════════════════


class TestCrashMidRestoreReattemptsOnNextLaunch:
    """end-to-end: a crash between ``load_stale`` and the caller's
       ``clear`` (i.e. mid-restore) must cause the next launch's
       ``load_stale`` to RE-ATTEMPT the restore. This is the core fix.

    Pre-: ``load_stale`` flipped ``consumed=True`` BEFORE returning,
       so a crash between load_stale and restore left the on-disk file with
       ``consumed=True``. The next launch's ``load_stale`` returned None
       (consumed=True), so the user's volume was stuck at the ducked level
       with no automatic recovery path.
    """

    def test_crash_after_load_stale_reattempts_on_next_launch(self, recovery_dir: Path) -> None:
        # Launch 1: save + load_stale (writes sentinel), then "crash"
        # before clear can run.
        cr1 = DuckCrashRecovery(config_dir=recovery_dir)
        cr1.save(VolumeState(linear=0.7, muted=False))
        state = cr1.load_stale()
        assert state is not None
        assert state.linear == 0.7
        # Simulate crash: don't call clear. The sentinel is in place,
        # and the main file still has consumed=False.
        assert cr1._restoring_sentinel_path.exists()
        raw = cr1.path.read_text(encoding="utf-8")
        assert json.loads(raw)["consumed"] is False

        # Launch 2: a fresh DuckCrashRecovery instance (simulating a new
        # process) reads the on-disk state.
        cr2 = DuckCrashRecovery(config_dir=recovery_dir)
        state2 = cr2.load_stale()

        # load_stale MUST re-attempt the restore (Case 3) — the
        # previous launch crashed mid-restore, so the volume is still
        # ducked and the user needs the automatic recovery.
        assert state2 is not None, (
            "a crash between load_stale and clear must NOT prevent "
            "the next launch from re-attempting the restore. Pre- "
            "the on-disk consumed=True flag caused load_stale to return "
            "None, leaving the user's volume stuck at the ducked level."
        )
        assert state2.linear == 0.7

    def test_successful_restore_then_clear_returns_none_on_next_launch(self, recovery_dir: Path) -> None:
        """End-to-end happy path: launch 1 saves + restores + clears;
        launch 2's load_stale returns None (no leftover state)."""
        cr1 = DuckCrashRecovery(config_dir=recovery_dir)
        cr1.save(VolumeState(linear=0.7, muted=False))
        state = cr1.load_stale()
        assert state is not None
        # Restore succeeded — clear() runs the full three-step cleanup.
        cr1.clear()
        assert not cr1.path.exists()
        assert not cr1._restoring_sentinel_path.exists()

        # Launch 2: nothing to restore.
        cr2 = DuckCrashRecovery(config_dir=recovery_dir)
        assert cr2.load_stale() is None


# ═══════════════════════════════════════════════════════════════════════════
# corrupt main file is still handled (preserved from )
# ═══════════════════════════════════════════════════════════════════════════


class TestCorruptFileHandlingPreserved:
    """must preserve the existing corrupt-file handling: a
    non-parseable main file causes ``load_stale`` to return None and
    ``clear`` both the main file and the sentinel."""

    def test_corrupt_main_file_returns_none_and_clears(self, crash_recovery: DuckCrashRecovery) -> None:
        crash_recovery.path.parent.mkdir(parents=True, exist_ok=True)
        crash_recovery.path.write_text("{invalid json")
        # Also leave a sentinel to verify clear() removes both.
        crash_recovery._write_restoring_sentinel()

        state = crash_recovery.load_stale()

        assert state is None
        assert not crash_recovery.path.exists()
        assert not crash_recovery._restoring_sentinel_path.exists()
