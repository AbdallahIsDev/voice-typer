"""FR-8: regression tests for the secure pre-migration backup in
``history_db._backup_before_migration``.

The previous implementation used ``shutil.copy2`` which:
  - follows symlinks on BOTH source and destination (a symlink-planting
    attacker could redirect the backup to an arbitrary file or read an
    arbitrary file's content into the backup location), and
  - is non-atomic (a crash mid-copy leaves a partial .bak), and
  - has no ``fsync`` of the destination.

The fix introduces ``_secure_copy_db_file`` which uses
``os.open(..., O_NOFOLLOW)`` + ``shutil.copyfileobj`` + ``fsync`` on
POSIX, and reparse-point rejection + binary copy + ``fsync`` on Windows.

These tests pin the new secure behaviour on Linux (the sandbox
platform). The Windows ``O_NOFOLLOW``-not-supported branch is covered
by the shared helper's structure; the platform-qualified note in
``SUMMARY.md`` (P4-A4) records that the Windows branch was not
exercised on a real Windows host in this sandbox run.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
from pathlib import Path

import pytest


@pytest.fixture
def db(tmp_path):
    """Create a HistoryDB with a temp path."""
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_history.db")
    yield db_instance
    db_instance.close()


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


# ── _secure_copy_db_file unit tests ────────────────────────────────────────


class TestSecureCopyDbFile:
    """Direct unit tests for the ``_secure_copy_db_file`` helper."""

    def test_copies_bytesfaithfully_and_fsycs(self, tmp_path):
        """A normal file copy: bytes are preserved; mode is 0o600 on POSIX."""
        from voice_typer.server.history_db import _secure_copy_db_file

        src = tmp_path / "src.bin"
        dst = tmp_path / "dst.bin"
        payload = b"hello\x00\x01\x02world" * 1024  # ~11 KB
        src.write_bytes(payload)

        _secure_copy_db_file(src, dst)

        assert dst.read_bytes() == payload
        if _is_linux():
            mode = stat.S_IMODE(os.lstat(dst).st_mode)
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_refuses_to_follow_source_symlink_posix(self, tmp_path):
        """On POSIX, a symlink source raises OSError (ELOOP) at open time."""
        if not _is_linux():
            pytest.skip("O_NOFOLLOW enforcement is POSIX-only")
        from voice_typer.server.history_db import _secure_copy_db_file

        target = tmp_path / "target.bin"
        target.write_bytes(b"secret")
        link = tmp_path / "link.bin"
        link.symlink_to(target)

        with pytest.raises(OSError):
            _secure_copy_db_file(link, tmp_path / "out.bin")

    def test_refuses_to_write_through_destination_symlink_posix(self, tmp_path):
        """On POSIX, a symlink destination raises OSError (ELOOP) at open time."""
        if not _is_linux():
            pytest.skip("O_NOFOLLOW enforcement is POSIX-only")
        from voice_typer.server.history_db import _secure_copy_db_file

        src = tmp_path / "src.bin"
        src.write_bytes(b"payload")
        # Plant a symlink where the backup would land — the secure copy
        # must refuse to write through it to the symlink target.
        target = tmp_path / "exfil-target.bin"
        link = tmp_path / "out.bin"
        link.symlink_to(target)

        with pytest.raises(OSError):
            _secure_copy_db_file(src, link)
        # The attacker's target must NOT have been written.
        assert not target.exists() or target.read_bytes() == b""


# ─_backup_before_migration integration tests ─────────────────────────────


class TestBackupBeforeMigrationSecure:
    """FR-8: ``_backup_before_migration`` uses the secure copy helper."""

    def test_backup_uses_secure_copy_helper(self, db, tmp_path, monkeypatch):
        """The ``_backup_before_migration`` method must call the secure
        ``_secure_copy_db_file`` helper instead of ``shutil.copy2``."""
        from voice_typer.server import history_db

        calls: list[tuple[Path, Path]] = []

        def spy(src, dst):
            calls.append((Path(src), Path(dst)))
            # Re-create the destination file so the caller's
            # `log.info` does not see a missing file.
            Path(dst).write_bytes(Path(src).read_bytes())

        monkeypatch.setattr(history_db, "_secure_copy_db_file", spy)

        db._backup_before_migration(current_version=2)

        # The main DB file must have been copied. Sidecars may or may
        # not exist (WAL files are created lazily); the helper is only
        # called for files that exist.
        assert calls, "expected _secure_copy_db_file to be called at least once"
        # The first call must be the main DB file → main .bak file.
        src0, dst0 = calls[0]
        assert src0 == db.db_path
        assert dst0.name == "test_history.db.pre-migration-v2.bak"

    def test_backup_does_not_use_shutil_copy2(self, db, monkeypatch):
        """``shutil.copy2`` must NOT be called by ``_backup_before_migration``."""
        import shutil

        copy2_calls: list[tuple] = []
        original_copy2 = shutil.copy2

        def spy_copy2(*args, **kwargs):
            copy2_calls.append(args)
            return original_copy2(*args, **kwargs)

        monkeypatch.setattr(shutil, "copy2", spy_copy2)

        db._backup_before_migration(current_version=2)

        assert not copy2_calls, (
            "FR-8 violation: _backup_before_migration called shutil.copy2 "
            f"({copy2_calls}); should use _secure_copy_db_file instead"
        )

    def test_backup_handles_missing_source_gracefully(self, db, tmp_path):
        """If the source DB file is missing, the backup is skipped (no crash)."""
        # The DB file exists (HistoryDB.__init__ created it). Remove
        # it to simulate the missing-file case.
        db.db_path.unlink()
        # Should not raise.
        db._backup_before_migration(current_version=2)

    def test_backup_creates_file_with_0o600_perms_on_posix(self, db, tmp_path):
        """The backup file must have 0o600 perms on POSIX."""
        if not _is_linux():
            pytest.skip("POSIX-only perm assertion")
        db._backup_before_migration(current_version=2)
        bak = db.db_path.with_name("test_history.db.pre-migration-v2.bak")
        assert bak.exists()
        mode = stat.S_IMODE(os.lstat(bak).st_mode)
        assert mode == 0o600, f"expected 0o600, got {oct(mode)}"

    def test_backup_continues_on_helper_failure(self, db, monkeypatch, caplog):
        """Best-effort contract: a helper OSError is logged but does NOT
        propagate (the migration must still proceed)."""
        from voice_typer.server import history_db

        def boom(src, dst):
            raise OSError("disk full")

        monkeypatch.setattr(history_db, "_secure_copy_db_file", boom)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.history_db"):
            # Must NOT raise.
            db._backup_before_migration(current_version=2)

        assert any(
            "Pre-migration backup FAILED" in r.getMessage() for r in caplog.records
        ), "expected a WARNING log about the failed backup"
