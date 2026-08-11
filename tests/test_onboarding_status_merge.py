"""Tests for the merged ``.onboarding_status.json`` document.

``voice_typer/server/onboarding_status.py`` consolidated the three
legacy onboarding marker files (``.onboarding_complete``,
``.onboarding_started``, ``.onboarding_fail_count``) into ONE JSON
document. These tests pin the merge schema, the one-time migration
(read legacy → write status → delete legacy), and the
read/write/reset contracts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from voice_typer.server import onboarding_status as os_mod

# ── read_status ────────────────────────────────────────────────────────


class TestReadStatus:
    def test_defaults_when_nothing_exists(self, tmp_path: Path) -> None:
        """No status file and no legacy markers → defaults, and the
        status file is NOT created (a fresh install has no state)."""
        data = os_mod.read_status(tmp_path)
        assert data == {
            "version": 1,
            "started": False,
            "completed": False,
            "fail_count": 0,
            "last_fail_ts": 0.0,
        }
        assert not (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).exists()

    def test_reads_written_document(self, tmp_path: Path) -> None:
        os_mod.write_status(
            tmp_path, started=True, completed=True, fail_count=3, last_fail_ts=1.5
        )
        data = os_mod.read_status(tmp_path)
        assert data["started"] is True
        assert data["completed"] is True
        assert data["fail_count"] == 3
        assert data["last_fail_ts"] == 1.5

    def test_corrupt_document_falls_back_to_defaults(self, tmp_path: Path) -> None:
        (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).write_text(
            "not json {{{", encoding="utf-8"
        )
        data = os_mod.read_status(tmp_path)
        assert data["completed"] is False
        assert data["fail_count"] == 0

    def test_partial_document_merges_defaults(self, tmp_path: Path) -> None:
        (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).write_text(
            json.dumps({"fail_count": 7}), encoding="utf-8"
        )
        data = os_mod.read_status(tmp_path)
        assert data["fail_count"] == 7
        assert data["started"] is False
        assert data["completed"] is False

    def test_non_dict_root_falls_back_to_defaults(self, tmp_path: Path) -> None:
        (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).write_text(
            json.dumps([1, 2, 3]), encoding="utf-8"
        )
        data = os_mod.read_status(tmp_path)
        assert data["completed"] is False
        assert data["fail_count"] == 0

    def test_pre_rename_count_key_is_migrated_to_fail_count(self, tmp_path: Path) -> None:
        """Documents written before the ``count`` → ``fail_count`` rename
        keep the old key; reads must map it onto the canonical
        ``fail_count`` and a read-modify-write must drop the legacy key.
        """
        (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).write_text(
            json.dumps(
                {
                    "version": 1,
                    "started": True,
                    "completed": True,
                    "count": 29,
                    "last_fail_ts": 123.0,
                }
            ),
            encoding="utf-8",
        )
        data = os_mod.read_status(tmp_path)
        assert data["fail_count"] == 29
        assert "count" not in data
        # After a write, the file carries only the canonical key.
        os_mod.write_status(tmp_path, started=False)
        raw = json.loads(
            (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).read_text(encoding="utf-8")
        )
        assert raw["fail_count"] == 29
        assert "count" not in raw


# ── migration ──────────────────────────────────────────────────────────


class TestLegacyMigration:
    def _seed_legacy(self, tmp_path: Path) -> None:
        (tmp_path / ".onboarding_complete").write_text(
            json.dumps({"completed": True, "version": 1}), encoding="utf-8"
        )
        (tmp_path / ".onboarding_started").write_text(
            json.dumps({"started": True, "version": 1}), encoding="utf-8"
        )
        (tmp_path / ".onboarding_fail_count").write_text(
            json.dumps({"count": 29, "last_fail_ts": 1786059143.8}), encoding="utf-8"
        )

    def test_read_migrates_and_merges_legacy_markers(self, tmp_path: Path) -> None:
        self._seed_legacy(tmp_path)
        data = os_mod.read_status(tmp_path)
        # All three markers merged into one document.
        assert data["completed"] is True
        assert data["started"] is True
        assert data["fail_count"] == 29
        assert data["last_fail_ts"] == 1786059143.8
        # The merged file was written and the legacy markers deleted.
        assert (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).exists()
        assert not (tmp_path / ".onboarding_complete").exists()
        assert not (tmp_path / ".onboarding_started").exists()
        assert not (tmp_path / ".onboarding_fail_count").exists()

    def test_migration_is_idempotent(self, tmp_path: Path) -> None:
        self._seed_legacy(tmp_path)
        os_mod.read_status(tmp_path)
        # Second read: the status file exists → no re-migration, no raise.
        data = os_mod.read_status(tmp_path)
        assert data["completed"] is True
        assert not (tmp_path / ".onboarding_complete").exists()

    def test_write_triggers_migration_first(self, tmp_path: Path) -> None:
        # A write on a legacy-only dir migrates FIRST, then applies the
        # update — so the completed flag from the legacy marker survives.
        self._seed_legacy(tmp_path)
        os_mod.write_status(tmp_path, started=False)
        data = json.loads(
            (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).read_text(encoding="utf-8")
        )
        assert data["completed"] is True  # preserved from legacy
        assert data["started"] is False  # overwritten by the update
        assert data["fail_count"] == 29  # preserved from legacy
        assert not (tmp_path / ".onboarding_complete").exists()

    def test_partial_legacy_markers(self, tmp_path: Path) -> None:
        # Only the fail counter exists (e.g. an install that never
        # started the wizard).
        (tmp_path / ".onboarding_fail_count").write_text(
            json.dumps({"count": 2, "last_fail_ts": 1.0}), encoding="utf-8"
        )
        data = os_mod.read_status(tmp_path)
        assert data["fail_count"] == 2
        assert data["started"] is False
        assert data["completed"] is False

    def test_corrupt_legacy_marker_is_skipped(self, tmp_path: Path) -> None:
        (tmp_path / ".onboarding_complete").write_text("not json", encoding="utf-8")
        (tmp_path / ".onboarding_fail_count").write_text(
            json.dumps({"count": 4, "last_fail_ts": 2.0}), encoding="utf-8"
        )
        data = os_mod.read_status(tmp_path)
        assert data["completed"] is False  # corrupt marker skipped
        assert data["fail_count"] == 4  # valid marker still merged
        assert (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).exists()


# ── write_status / reset_status ────────────────────────────────────────


class TestWriteAndReset:
    def test_write_preserves_unknown_fields(self, tmp_path: Path) -> None:
        # Forward-compat: fields written by a newer app version must
        # survive a read-modify-write by this version.
        (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).write_text(
            json.dumps(
                {
                    "version": 1,
                    "started": False,
                    "completed": False,
                    "fail_count": 0,
                    "last_fail_ts": 0.0,
                    "future_field": 42,
                }
            ),
            encoding="utf-8",
        )
        os_mod.write_status(tmp_path, started=True)
        data = json.loads(
            (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).read_text(encoding="utf-8")
        )
        assert data["started"] is True
        assert data["future_field"] == 42

    def test_reset_deletes_document_and_legacy(self, tmp_path: Path) -> None:
        TestLegacyMigration._seed_legacy(self, tmp_path)
        os_mod.write_status(tmp_path, completed=True)
        assert (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).exists()
        os_mod.reset_status(tmp_path)
        assert not (tmp_path / os_mod.ONBOARDING_STATUS_FILENAME).exists()
        assert not (tmp_path / ".onboarding_complete").exists()
        assert not (tmp_path / ".onboarding_started").exists()
        assert not (tmp_path / ".onboarding_fail_count").exists()

    def test_reset_missing_document_is_noop(self, tmp_path: Path) -> None:
        os_mod.reset_status(tmp_path)  # must not raise

    def test_write_raises_on_disk_failure(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """write_status propagates write errors (the mark_complete
        re-raise contract)."""
        import voice_typer.server.secure_file_io as sio

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise OSError("disk full")

        monkeypatch.setattr(sio, "_secure_atomic_write", _boom)
        with pytest.raises(OSError, match="disk full"):
            os_mod.write_status(tmp_path, completed=True)
