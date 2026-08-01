"""S5-CR-28: verify the ``_backup_before_migration`` extraction.

The implementation was extracted from ``Config._backup_before_migration``
(config.py — 2,698-LOC monolith) to a module-level function
``_backup_before_migration_impl`` in
``voice_typer.server.config_internals.migrations``. The classmethod on
``Config`` is now a thin delegating wrapper.

This test module pins three contracts of the extraction:

1. **Delegation**: ``Config._backup_before_migration(...)`` calls
   ``_backup_before_migration_impl(...)`` with the same arguments.
2. **Patch-path preservation**: monkeypatching
   ``config_mod._secure_read_text`` / ``config_mod._secure_atomic_write``
   / ``config_mod._prune_kept_backups`` still takes effect on the
   extracted impl (because it looks those up via the ``config`` module
   namespace — lazy import — rather than importing them directly from
   ``secure_file_io``).
3. **Behavior parity**: the impl produces the same on-disk artifact
   (a timestamped ``config.json.pre-migration-v*.bak`` file with the
   source's bytes) that the original classmethod did.
"""

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


@pytest.fixture
def isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``Config`` at an isolated tmp_path so tests don't touch the
    user's real ``~/.voice-typer`` directory.
    """
    config_dir = tmp_path / "voice-typer"
    config_dir.mkdir()
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: config_dir, raising=True)
    return config_dir


class TestBackupBeforeMigrationExtraction:
    """S5-CR-28: ``Config._backup_before_migration`` delegates to the
    extracted ``_backup_before_migration_impl`` in
    ``config_internals.migrations``.
    """

    def test_impl_is_callable_from_migrations_module(self, tmp_path: Path) -> None:
        """The impl function must be importable from
        ``config_internals.migrations`` and callable directly (without
        going through the ``Config`` classmethod).
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        # Calling the impl directly must produce a backup file — same
        # contract as the classmethod.
        _backup_before_migration_impl(config_file, 0)

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert len(backups) == 1, f"expected 1 pre-migration backup, found {len(backups)}: {[b.name for b in backups]}"
        # The backup must contain the source file's bytes.
        assert backups[0].read_text() == config_file.read_text()

    def test_classmethod_delegates_to_impl(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """``Config._backup_before_migration`` must call
        ``_backup_before_migration_impl`` with the same arguments (no
        inline implementation remaining in config.py).
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        call_log: list[tuple] = []

        def spy_impl(config_file_arg, loaded_version_arg):
            call_log.append((config_file_arg, loaded_version_arg))
            # Forward to the real impl so the side-effect (backup file)
            # still happens.
            return _backup_before_migration_impl(config_file_arg, loaded_version_arg)

        # Config.py imports ``_backup_before_migration_impl`` into its own
        # namespace at module-load time, so the classmethod resolves it via
        # ``config_mod._backup_before_migration_impl``. Patch on the config
        # module to intercept that lookup.
        import voice_typer.server.config as config_mod

        monkeypatch.setattr(config_mod, "_backup_before_migration_impl", spy_impl)

        Config._backup_before_migration(config_file, 0)

        assert len(call_log) == 1, f"expected exactly 1 delegation call, got {len(call_log)}"
        assert call_log[0][0] == config_file
        assert call_log[0][1] == 0

    def test_patch_path_bridge_preserved_for_secure_read(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Tests that monkeypatch ``config_mod._secure_read_text`` must
        keep taking effect on the extracted impl (the impl looks the
        helper up via the ``config`` module namespace, not via direct
        import from ``secure_file_io``).
        """
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
        """Same as above but for ``_secure_atomic_write`` — the impl
        must call it via ``config_mod._secure_atomic_write`` so test
        patches take effect.
        """
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
        """When ``loaded_version >= _CURRENT_SCHEMA_VERSION``, the impl
        must early-return without writing any backup (no migration to
        back up before).
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": _CURRENT_SCHEMA_VERSION}))

        _backup_before_migration_impl(config_file, _CURRENT_SCHEMA_VERSION)

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert backups == [], f"expected no backup when loaded_version >= current; found {[b.name for b in backups]}"

    def test_impl_skips_when_loaded_version_not_int(self, tmp_path: Path) -> None:
        """Non-int ``loaded_version`` (missing/corrupt schema_version)
        must early-return without writing a backup.
        """
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": "garbage"}))

        _backup_before_migration_impl(config_file, None)
        _backup_before_migration_impl(config_file, "garbage")

        backups = list(tmp_path.glob("config.json.pre-migration-v*.bak"))
        assert backups == [], f"expected no backup for non-int loaded_version; found {[b.name for b in backups]}"

    def test_impl_filename_embeds_timestamp_pid_microseconds(self, tmp_path: Path) -> None:
        """XZ-CFG-11 contract preserved by the extraction: the backup
        filename must embed a Unix timestamp + PID + microsecond
        fraction so two backup events never collide.
        """
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
        """XZ-CFG-11 retention contract preserved by the extraction:
        after the 4th pre-migration backup is created, the oldest is
        pruned so only 3 remain.
        """
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
