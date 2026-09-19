"""stale ``.tmp`` file sweep with age-gated unlink."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from voice_typer.server import startup_sequence as ss_mod

# 6 minutes in seconds, comfortably past the 5-minute cutoff so the file
_STALE_TMP_AGE_SECONDS = 6 * 60
# is unconditionally "fresh" (must NOT be deleted, might belong to a
_FRESH_TMP_AGE_SECONDS = 1 * 60


def _touch_with_age(path: Path, age_seconds: float) -> None:
    """Create ``path`` (empty file) and backdate its mtime by ``age_seconds``."""
    path.write_text("tmp-content", encoding="utf-8")
    target_mtime = time.time() - age_seconds
    os.utime(path, (target_mtime, target_mtime))


class TestSweepDeletesStaleTmpFiles:
    """``_sweep_stale_backup_files`` deletes ``*.tmp`` files older than 5 min."""

    @pytest.mark.parametrize(
        "filename",
        [
            # _secure_atomic_write pattern: target.name + ".<rand>.tmp"
            "config.json.abc123.tmp",
            "history.db.xyz789.tmp",
            "recovery.json.deadbeef.tmp",
            "gdpr-export-20240101-120000.zip.tmp",
            # generic
            "any.tmp",
        ],
    )
    def test_stale_tmp_file_deleted(self, tmp_path: Path, filename: str) -> None:
        path = tmp_path / filename
        _touch_with_age(path, _STALE_TMP_AGE_SECONDS)
        assert path.exists()

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not path.exists(), f"expected {filename} to be purged (stale .tmp)"


class TestSweepPreservesFreshTmpFiles:
    """A recent ``.tmp`` file is PRESERVED, it might belong to a concurrent"""

    @pytest.mark.parametrize(
        "filename",
        [
            "config.json.abc123.tmp",
            "history.db.xyz789.tmp",
            "gdpr-export-20240101-120000.zip.tmp",
        ],
    )
    def test_fresh_tmp_file_preserved(self, tmp_path: Path, filename: str) -> None:
        path = tmp_path / filename
        _touch_with_age(path, _FRESH_TMP_AGE_SECONDS)
        assert path.exists()

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert path.exists(), f"expected {filename} to be PRESERVED (fresh .tmp, concurrent process might be mid-write)"


class TestSweepMixedTmpDir:
    """In a dir with both stale and fresh ``.tmp`` files, only stale ones purged."""

    def test_mixed_dir_only_deletes_stale_tmp(self, tmp_path: Path) -> None:
        stale = tmp_path / "config.json.old.tmp"
        fresh = tmp_path / "config.json.new.tmp"
        _touch_with_age(stale, _STALE_TMP_AGE_SECONDS)
        _touch_with_age(fresh, _FRESH_TMP_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not stale.exists(), "stale .tmp must be purged"
        assert fresh.exists(), "fresh .tmp must be preserved"

    def test_mixed_with_backup_files(self, tmp_path: Path) -> None:
        """The ``.tmp`` sweep runs alongside the existing 30-day backup sweep."""
        stale_bak = tmp_path / "config.json.corrupt-1700000000"
        stale_tmp = tmp_path / "config.json.abc.tmp"
        fresh_bak = tmp_path / "config.json.corrupt-1800000000"
        fresh_tmp = tmp_path / "config.json.def.tmp"
        # 31-day age, past the 30-day backup cutoff.
        _touch_with_age(stale_bak, 31 * 24 * 60 * 60)
        _touch_with_age(fresh_bak, 1 * 24 * 60 * 60)
        # 6-min age, past the 5-min tmp cutoff.
        _touch_with_age(stale_tmp, _STALE_TMP_AGE_SECONDS)
        _touch_with_age(fresh_tmp, _FRESH_TMP_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not stale_bak.exists(), "stale .bak must be purged"
        assert not stale_tmp.exists(), "stale .tmp must be purged"
        assert fresh_bak.exists(), "fresh .bak must be preserved"
        assert fresh_tmp.exists(), "fresh .tmp must be preserved"


class TestSweepTmpSubdirs:
    """The ``.tmp`` sweep walks ``_TMP_SWEEP_SUBDIRS`` (e.g."""

    def test_stale_tmp_in_subdir_deleted(self, tmp_path: Path) -> None:
        subdir = tmp_path / "crash_diagnostics"
        subdir.mkdir()
        stale = subdir / "crash_diagnostics.12345.txt.tmp"
        _touch_with_age(stale, _STALE_TMP_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not stale.exists(), "stale .tmp in subdir must be purged"

    def test_fresh_tmp_in_subdir_preserved(self, tmp_path: Path) -> None:
        subdir = tmp_path / "crash_diagnostics"
        subdir.mkdir()
        fresh = subdir / "crash_diagnostics.12345.txt.tmp"
        _touch_with_age(fresh, _FRESH_TMP_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert fresh.exists(), "fresh .tmp in subdir must be preserved"

    def test_missing_subdir_is_noop(self, tmp_path: Path) -> None:
        """A missing subdir is silently skipped (no error, no creation)."""
        # No crash_diagnostics/ created, sweep must not raise.
        ss_mod._sweep_stale_backup_files(tmp_path)
        assert not (tmp_path / "crash_diagnostics").exists()


class TestSweepTmpBoundary:
    """Edge cases around the 5-minute cutoff (strict ``>`` comparison)."""

    def test_just_under_5_min_preserved(self, tmp_path: Path) -> None:
        """A ``.tmp`` file at 4m59s is preserved (the check is strict ``>``)."""
        path = tmp_path / "config.json.boundary.tmp"
        just_under = ss_mod._TMP_RETENTION_MAX_AGE_SECONDS - 10.0
        _touch_with_age(path, just_under)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert path.exists(), "file just under 5 min must be preserved (strict > comparison)"

    def test_just_over_5_min_purged(self, tmp_path: Path) -> None:
        """A ``.tmp`` file at 5m10s is purged."""
        path = tmp_path / "config.json.boundary.tmp"
        just_over = ss_mod._TMP_RETENTION_MAX_AGE_SECONDS + 10.0
        _touch_with_age(path, just_over)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not path.exists(), "file just over 5 min must be purged"


class TestTmpSweepConstants:
    """Pin the constants so a future change is intentional."""

    def test_tmp_retention_is_5_minutes(self) -> None:
        """5 min = 300 s, long enough for concurrent mid-write, short enough"""
        assert ss_mod._TMP_RETENTION_MAX_AGE_SECONDS == 300.0

    def test_tmp_retention_much_shorter_than_backup_retention(self) -> None:
        """30-day backup retention: ``.tmp`` files are mid-write intermediates"""
        assert ss_mod._TMP_RETENTION_MAX_AGE_SECONDS < ss_mod._BACKUP_RETENTION_MAX_AGE_SECONDS / 100

    def test_crash_diagnostics_in_subdirs(self) -> None:
        """``crash_diagnostics/`` receives atomic writes from the"""
        assert "crash_diagnostics" in ss_mod._TMP_SWEEP_SUBDIRS


class TestTmpSweepErrorTolerance:
    """A single unreadable / unstat-able ``.tmp`` file must NOT abort the sweep."""

    def test_sweep_continues_after_one_bad_tmp_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        bad = tmp_path / "config.json.bad.tmp"
        good = tmp_path / "config.json.good.tmp"
        _touch_with_age(bad, _STALE_TMP_AGE_SECONDS)
        _touch_with_age(good, _STALE_TMP_AGE_SECONDS)

        real_unlink = Path.unlink

        def flaky_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self.name == "config.json.bad.tmp":
                raise OSError("simulated lock")
            real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        # Must NOT raise even though one unlink failed.
        ss_mod._sweep_stale_backup_files(tmp_path)

        # The "good" file was still purged despite the earlier failure.
        assert not good.exists(), "sweep must continue past per-file errors"
        # The "bad" file is still on disk (the simulated OSError was raised).
        assert bad.exists()
