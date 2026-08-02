"""regression tests for the startup backup-file sweep.

Background
----------
``voice_typer/server/startup_sequence.py`` previously had NO sweep for
stale corrupt-quarantine / pre-migration backup files. The secure-file-IO
corruption path writes ``config.json.corrupt-<ts>`` / ``history.db.corrupt-*``
quarantine files; schema migrations write
``config.json.pre-migration-v*.bak`` / ``history.db.pre-migration-v*.bak`` /
``config.json.v*.bak`` / ``config.json.bak.failed-migration-*``;
the crash-recovery path writes ``voice-typer-recovery.json.corrupt.*``.
Without a sweep these accumulate indefinitely on disk (one per crash /
per migration attempt).

The fix mirrors the existing
``log._sweep_stale_log_rotations`` (``log/__init__.py:95-156``) and
``crash_handler._sweep_stale_diagnostics``
(``crash_handler/_diagnostics_archive.py:363-425``) patterns: a
best-effort, per-file-error-tolerant sweep called once per process
startup that unlinks any matched file older than 30 days (mtime).

These tests pin the behaviour in isolation — they call
``_sweep_stale_backup_files(tmp_path)`` directly so they don't depend
on the heavy ``app_for_startup`` fixture.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from voice_typer.server import startup_sequence as ss_mod

# ── Helpers ────────────────────────────────────────────────────────────

# 31 days in seconds — comfortably past the 30-day cutoff so the file
# is unconditionally "stale" regardless of test runner clock skew.
_STALE_AGE_SECONDS = 31 * 24 * 60 * 60
# 1 day in seconds — comfortably inside the 30-day cutoff so the file
# is unconditionally "fresh" (must NOT be deleted — forensic value).
_FRESH_AGE_SECONDS = 1 * 24 * 60 * 60


def _touch_with_age(path: Path, age_seconds: float) -> None:
    """Create ``path`` (empty file) and backdate its mtime by ``age_seconds``.

    Uses ``os.utime`` so the test does NOT have to actually sleep — the
    mtime is set deterministically to ``now - age_seconds`` regardless
    of how long the test takes.
    """
    path.write_text("stale-backup-content", encoding="utf-8")
    target_mtime = time.time() - age_seconds
    os.utime(path, (target_mtime, target_mtime))


# ── (a) old .bak file (mtime > 30 days) — deleted ─────────────────────


class TestSweepDeletesStaleBackups:
    """``_sweep_stale_backup_files`` deletes matched files older than 30 days."""

    @pytest.mark.parametrize(
        "filename",
        [
            "history.db.pre-migration-v1.bak",
            "history.db.pre-migration-v42.bak",
            "history.db.corrupt-1700000000",
            "config.json.corrupt-1700000000",
            "config.json.pre-migration-v1.bak",
            "config.json.pre-migration-v7.bak",
            "config.json.v2.bak",
            "config.json.bak.failed-migration-20240101",
            "voice-typer-recovery.json.corrupt.1700000000",
        ],
    )
    def test_old_backup_file_deleted(self, tmp_path: Path, filename: str) -> None:
        path = tmp_path / filename
        _touch_with_age(path, _STALE_AGE_SECONDS)
        assert path.exists()

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not path.exists(), f"expected {filename} to be purged (stale)"

    def test_all_patterns_swept_in_one_call(self, tmp_path: Path) -> None:
        """A single sweep call should purge every stale pattern at once."""
        stale_files = [
            "history.db.pre-migration-v1.bak",
            "history.db.corrupt-1700000000",
            "config.json.corrupt-1700000000",
            "config.json.pre-migration-v1.bak",
            "config.json.v3.bak",
            "config.json.bak.failed-migration-20240101",
            "voice-typer-recovery.json.corrupt.1700000000",
        ]
        for name in stale_files:
            _touch_with_age(tmp_path / name, _STALE_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        for name in stale_files:
            assert not (tmp_path / name).exists(), f"expected {name} to be purged (stale)"


# ── (b) recent .bak file (mtime < 30 days) — preserved ────────────────


class TestSweepPreservesFreshBackups:
    """``_sweep_stale_backup_files`` NEVER deletes files newer than 30 days
    (forensic value — see CONSTRAINT 4 / NEVER DOWNGRADE)."""

    @pytest.mark.parametrize(
        "filename",
        [
            "history.db.pre-migration-v1.bak",
            "history.db.corrupt-1700000000",
            "config.json.corrupt-1700000000",
            "config.json.pre-migration-v1.bak",
            "config.json.v2.bak",
            "config.json.bak.failed-migration-20240101",
            "voice-typer-recovery.json.corrupt.1700000000",
        ],
    )
    def test_recent_backup_file_preserved(self, tmp_path: Path, filename: str) -> None:
        path = tmp_path / filename
        _touch_with_age(path, _FRESH_AGE_SECONDS)
        assert path.exists()

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert path.exists(), f"expected {filename} to be PRESERVED (fresh — forensic value)"

    def test_mixed_dir_only_deletes_stale(self, tmp_path: Path) -> None:
        """In a dir with both stale and fresh files, only stale ones are purged."""
        stale = tmp_path / "config.json.corrupt-1700000000"
        fresh = tmp_path / "config.json.corrupt-1800000000"
        _touch_with_age(stale, _STALE_AGE_SECONDS)
        _touch_with_age(fresh, _FRESH_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not stale.exists(), "stale file must be purged"
        assert fresh.exists(), "fresh file must be preserved"


# ── (c) corrupt-* file deleted if old ─────────────────────────────────


class TestSweepCorruptFiles:
    """Explicit coverage for ``config.json.corrupt-*`` (the most common
    pattern, written by the secure-file-IO corruption path)."""

    def test_old_corrupt_config_deleted(self, tmp_path: Path) -> None:
        path = tmp_path / "config.json.corrupt-1700000000"
        _touch_with_age(path, _STALE_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not path.exists()

    def test_old_corrupt_history_db_deleted(self, tmp_path: Path) -> None:
        path = tmp_path / "history.db.corrupt-1700000000"
        _touch_with_age(path, _STALE_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not path.exists()

    def test_old_corrupt_recovery_json_deleted(self, tmp_path: Path) -> None:
        path = tmp_path / "voice-typer-recovery.json.corrupt.1700000000"
        _touch_with_age(path, _STALE_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert not path.exists()

    def test_unmatched_files_preserved(self, tmp_path: Path) -> None:
        """Files that don't match any glob pattern are NEVER touched —
        even if old. This guards against the sweep being widened
        accidentally (e.g. a glob typo that matches ``config.json``)."""
        unmatched_files = [
            "config.json",  # the live config — must NEVER be swept
            "history.db",  # the live history DB — must NEVER be swept
            "voice-typer-recovery.json",  # the live recovery file
            "config.json.bak",  # plain .bak (not in pattern list)
            "config.json.corrupt",  # no suffix after corrupt
            "random-file.txt",
        ]
        for name in unmatched_files:
            _touch_with_age(tmp_path / name, _STALE_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)

        for name in unmatched_files:
            assert (tmp_path / name).exists(), f"unmatched file {name} must NOT be swept"


# ── Per-file error tolerance ──────────────────────────────────────────


class TestSweepErrorTolerance:
    """A single unreadable / unstat-able file must NOT abort the sweep
    (CONSTRAINT: per-file error handling)."""

    def test_nonexistent_config_dir_is_noop(self, tmp_path: Path) -> None:
        """A missing / non-directory config_dir is silently skipped."""
        bogus = tmp_path / "does-not-exist"
        # Must not raise.
        ss_mod._sweep_stale_backup_files(bogus)

    def test_sweep_continues_after_one_bad_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """If ``unlink`` raises on one file, subsequent files are still swept."""
        bad = tmp_path / "config.json.corrupt-bad"
        good = tmp_path / "config.json.corrupt-good"
        _touch_with_age(bad, _STALE_AGE_SECONDS)
        _touch_with_age(good, _STALE_AGE_SECONDS)

        real_unlink = Path.unlink

        def flaky_unlink(self: Path, *args: object, **kwargs: object) -> None:
            if self.name == "config.json.corrupt-bad":
                raise OSError("simulated lock")
            real_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

        monkeypatch.setattr(Path, "unlink", flaky_unlink)

        # Must NOT raise even though one unlink failed.
        ss_mod._sweep_stale_backup_files(tmp_path)

        # The "good" file was still purged despite the earlier failure.
        assert not good.exists(), "sweep must continue past per-file errors"
        # The "bad" file is still on disk (the simulated OSError was raised).
        assert bad.exists()


# ── Boundary ──────────────────────────────────────────────────────────


class TestSweepBoundary:
    """Edge cases around the 30-day cutoff and idempotency."""

    def test_exactly_30_days_preserved(self, tmp_path: Path) -> None:
        """A file just under the 30-day cutoff is preserved (the check is
        strict ``>``, so a file at 29d23h is NOT yet stale).
        This pins the NEVER-DOWNGRADE boundary."""
        path = tmp_path / "config.json.corrupt-boundary"
        # 29d 23h — strictly under the 30-day cutoff (``>`` must NOT fire).
        just_under_30_days = ss_mod._BACKUP_RETENTION_MAX_AGE_SECONDS - 3600.0
        _touch_with_age(path, just_under_30_days)

        ss_mod._sweep_stale_backup_files(tmp_path)

        assert path.exists(), "file just under 30 days must be preserved (strict > comparison)"

    def test_sweep_is_idempotent(self, tmp_path: Path) -> None:
        """Calling the sweep twice is a no-op the second time (no files
        left to delete)."""
        path = tmp_path / "config.json.corrupt-1700000000"
        _touch_with_age(path, _STALE_AGE_SECONDS)

        ss_mod._sweep_stale_backup_files(tmp_path)
        assert not path.exists()

        # Second call must not raise and must not affect anything.
        ss_mod._sweep_stale_backup_files(tmp_path)

    def test_empty_dir_is_noop(self, tmp_path: Path) -> None:
        """An empty config_dir is a no-op (must not raise)."""
        ss_mod._sweep_stale_backup_files(tmp_path)
