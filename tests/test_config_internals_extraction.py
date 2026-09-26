"""the ``_backup_before_migration`` extraction."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from voice_typer.server.config import Config
from voice_typer.server.config_internals.migrations import (
    _CURRENT_SCHEMA_VERSION,
    _backup_before_migration_impl,
)

from tests.fixtures.config_helpers import patch_config_dir_refs  # noqa: E402


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """user's real ``~/.lausu`` directory."""
    config_dir = tmp_path / "lausu"
    config_dir.mkdir()
    patch_config_dir_refs(monkeypatch, config_dir)
    return config_dir


class TestBackupBeforeMigrationExtraction:
    """``config_internals.migrations``."""

    def test_impl_is_callable_from_migrations_module(self, tmp_path: Path) -> None:
        """``config_internals.migrations`` and callable directly (without"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        _backup_before_migration_impl(config_file, 0)

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert len(backups) == 1, f"expected 1 pre-migration backup, found {len(backups)}: {[b.name for b in backups]}"
        # The backup must contain the source file's bytes.
        assert backups[0].read_text() == config_file.read_text()

    def test_classmethod_delegates_to_impl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``Config._backup_before_migration`` must call"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        call_log: list[tuple] = []

        def spy_impl(config_file_arg, loaded_version_arg):
            call_log.append((config_file_arg, loaded_version_arg))
            # Forward to the real impl so the side-effect (backup file)
            return _backup_before_migration_impl(config_file_arg, loaded_version_arg)

        # Config.py imports ``_backup_before_migration_impl`` into its own
        import voice_typer.server.config as config_mod

        monkeypatch.setattr(config_mod, "_backup_before_migration_impl", spy_impl)

        Config._backup_before_migration(config_file, 0)

        assert len(call_log) == 1, f"expected exactly 1 delegation call, got {len(call_log)}"
        assert call_log[0][0] == config_file
        assert call_log[0][1] == 0

    def test_patch_path_bridge_preserved_for_secure_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tests that monkeypatch ``config_mod._secure_read_text`` must"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        import voice_typer.server.config as config_mod

        original_read = config_mod._secure_read_text
        read_calls: list[Path] = []

        def spy_read(path, *args, **kwargs):
            read_calls.append(Path(path))
            return original_read(path, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_read_text", spy_read)

        _backup_before_migration_impl(config_file, 0)

        assert any(p == config_file for p in read_calls), (
            "patch-path bridge broken: _backup_before_migration_impl did "
            "not call config_mod._secure_read_text (the test patch on "
            "config_mod._secure_read_text had no effect)."
        )

    def test_patch_path_bridge_preserved_for_secure_atomic_write(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Same as above but for ``_secure_atomic_write``, the impl"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        import voice_typer.server.config as config_mod

        original_write = config_mod._secure_atomic_write
        write_paths: list[str] = []

        def spy_write(path, content, *args, **kwargs):
            write_paths.append(str(path))
            return original_write(path, content, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_atomic_write", spy_write)

        _backup_before_migration_impl(config_file, 0)

        assert any("pre-migration-v" in p and p.endswith(".bak") for p in write_paths), (
            "patch-path bridge broken: _backup_before_migration_impl did "
            f"not call config_mod._secure_atomic_write with a .bak path. "
            f"Observed: {write_paths}"
        )

    def test_impl_skips_when_loaded_version_is_current(self, tmp_path: Path) -> None:
        """When ``loaded_version >= _CURRENT_SCHEMA_VERSION``, the impl"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": _CURRENT_SCHEMA_VERSION}))

        _backup_before_migration_impl(config_file, _CURRENT_SCHEMA_VERSION)

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert backups == [], f"expected no backup when loaded_version >= current; found {[b.name for b in backups]}"

    def test_impl_skips_when_loaded_version_not_int(self, tmp_path: Path) -> None:
        """Non-int ``loaded_version`` (missing/corrupt schema_version)"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": "garbage"}))

        _backup_before_migration_impl(config_file, None)
        _backup_before_migration_impl(config_file, "garbage")

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert backups == [], f"expected no backup for non-int loaded_version; found {[b.name for b in backups]}"

    def test_impl_filename_embeds_timestamp_pid_microseconds(self, tmp_path: Path) -> None:
        """contract preserved by the extraction: the backup"""
        import re
        import time

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        _backup_before_migration_impl(config_file, 0)

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert len(backups) == 1
        match = re.match(
            r"^config\.json\.pre-migration-v(\d+)-(\d+)-(\d+)-(\d+)\.bak$",
            backups[0].name,
        )
        assert match is not None, (
            f"filename {backups[0].name!r} does not match the expected "
            "config.json.pre-migration-v<N>-<ts>-<pid>-<us>.bak pattern"
        )
        schema_v, ts, pid, us = (int(g) for g in match.groups())
        assert schema_v == 0
        assert abs(int(time.time()) - ts) < 60
        assert pid == os.getpid()
        assert 0 <= us < 1_000_000

    def test_impl_retention_caps_at_three(self, tmp_path: Path) -> None:
        """retention contract preserved by the extraction:"""
        import time

        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        for i in range(4):
            # Bump mtime so the prune logic sees a strict ordering.
            future_ts = time.time() + i
            os.utime(config_file, (future_ts, future_ts))
            _backup_before_migration_impl(config_file, 0)
            time.sleep(0.01)

        backups = sorted(
            tmp_path.glob("config.json.pre-migration-v*.bak"),
            key=lambda p: p.stat().st_mtime,
        )
        assert len(backups) == 3, (
            f"expected 3 retained pre-migration backups (keep=3); found {len(backups)}: {[b.name for b in backups]}"
        )
