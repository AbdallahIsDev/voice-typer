"""FR-23 / FR-24 regression tests: config.json backups use the secure"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config


@pytest.fixture
def _isolated_config_dir(tmp_config_dir: Path) -> Path:
    yield tmp_config_dir


# _backup_before_downgrade uses secure helpers ──────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only symlink test")
class TestBackupBeforeDowngradeSecure:
    """FR-23: ``_backup_before_downgrade`` must use ``_secure_read_text``"""

    def test_downgrade_backup_created_with_correct_content(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """A downgrade (loading a newer-version config.json) must"""
        config_file = _isolated_config_dir / "config.json"
        original_content = {
            "schema_version": _CURRENT_SCHEMA_VERSION + 5,  # newer than supported
            "hotkey": "<caps_lock>",
            # A "future" field the current build doesn't know about.
            "future_field_xyz": "future-value-that-should-be-preserved-in-bak",
        }
        config_file.write_text(json.dumps(original_content, indent=2))
        os.chmod(config_file, 0o600)

        cfg = Config.load()

        bak_candidates = sorted(_isolated_config_dir.glob(f"config.json.v{_CURRENT_SCHEMA_VERSION + 5}-*.bak"))
        assert bak_candidates, (
            f"FR-23: expected a downgrade backup matching "
            f"config.json.v{_CURRENT_SCHEMA_VERSION + 5}-*.bak in "
            f"{_isolated_config_dir}, but found none. "
            "_backup_before_downgrade should create it via "
            "_secure_atomic_write."
        )
        bak_path = bak_candidates[0]

        # The backup must contain the ORIGINAL on-disk content
        bak_data = json.loads(bak_path.read_text())
        assert bak_data["future_field_xyz"] == "future-value-that-should-be-preserved-in-bak", (
            "FR-23: downgrade backup lost the future field, the "
            "backup should preserve the EXACT on-disk bytes (the "
            "user needs this to recover after re-upgrading)."
        )
        assert bak_data["schema_version"] == _CURRENT_SCHEMA_VERSION + 5

        # The in-memory Config must have the future field filtered out.
        assert not hasattr(cfg, "future_field_xyz")

    def test_downgrade_backup_rejects_symlinked_source(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-23: if config.json is a symlink, ``_secure_read_text``"""
        secret_file = _isolated_config_dir / "attacker_secret.txt"
        secret_content = "ATTACKER_SECRET_VALUE_should_not_appear_in_bak"
        secret_file.write_text(secret_content)

        # Replace config.json with a symlink to the secret file.
        config_file = _isolated_config_dir / "config.json"
        # First create a legit config so the load doesn't bail early
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": _CURRENT_SCHEMA_VERSION + 1,
                    "hotkey": "<caps_lock>",
                }
            )
        )

        # Now replace with a symlink to the secret file. The load()
        config_file.unlink()
        config_file.symlink_to(secret_file)

        # Reset Config._load_warnings by calling _backup_before_downgrade
        data: dict = {}
        # Use a higher version to trigger the backup branch.
        Config._backup_before_downgrade(config_file, _CURRENT_SCHEMA_VERSION + 1, data)

        # The backup file MUST NOT have been created (or if it was,
        # it must NOT contain the secret content). _secure_read_text
        bak_path = _isolated_config_dir / f"config.json.v{_CURRENT_SCHEMA_VERSION + 1}.bak"
        if bak_path.exists():
            # If a .bak was created somehow, it must NOT contain the
            bak_text = bak_path.read_text()
            assert secret_content not in bak_text, (
                "FR-23 regression: _backup_before_downgrade copied the "
                "symlink target's content into the .bak file, info "
                "disclosure via .bak. The fix must use _secure_read_text "
                "(O_NOFOLLOW) so symlinks are refused."
            )

        # The load_warnings list should mention the backup failure.
        warnings_text = json.dumps(data.get("_load_warnings", []))
        assert "backup" in warnings_text.lower(), (
            f"FR-23: expected a backup-failed warning in _load_warnings, got {warnings_text}"
        )

    def test_downgrade_backup_is_atomic(
        self,
        _isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR-23: the backup write must be atomic, if the write is"""
        config_file = _isolated_config_dir / "config.json"
        original_content = {
            "schema_version": _CURRENT_SCHEMA_VERSION + 2,
            "hotkey": "<caps_lock>",
            "language": "fr",
            "autostart": True,
        }
        config_file.write_text(json.dumps(original_content, indent=2))

        Config.load()

        bak_candidates = sorted(_isolated_config_dir.glob(f"config.json.v{_CURRENT_SCHEMA_VERSION + 2}-*.bak"))
        assert bak_candidates, (
            f"FR-23: expected a downgrade backup matching "
            f"config.json.v{_CURRENT_SCHEMA_VERSION + 2}-*.bak in "
            f"{_isolated_config_dir}, but found none."
        )
        bak_path = bak_candidates[0]

        # The .bak must be valid JSON (no partial write).
        bak_text = bak_path.read_text()
        bak_data = json.loads(bak_text)  # raises JSONDecodeError if partial
        assert bak_data == original_content


# _save_unlocked backup READ uses _secure_read_text ─────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only symlink test")
class TestSaveUnlockedBackupSecure:
    """FR-24: ``Config._save_unlocked`` must read the existing"""

    def test_save_does_not_poison_bak_via_symlinked_source(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-24: if config.json is a symlink to ~/.bashrc (simulated"""
        # Start with a legit config.json so Config.load() succeeds.
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>", "openai_api_key": ""}))

        # Load the config.
        cfg = Config.load()

        secret_file = _isolated_config_dir / "attacker_secret.txt"
        secret_content = "ATTACKER_SECRET_should_not_leak_into_bak"
        secret_file.write_text(secret_content)

        # IMPORTANT: we need _last_saved_bytes to NOT match the new
        object.__setattr__(cfg, "_last_saved_bytes", None)

        # Replace config.json with a symlink to the secret file.
        config_file.unlink()
        config_file.symlink_to(secret_file)

        # Trigger a save (which runs the backup block first).
        cfg.save()

        # The .bak must NOT exist (or if it does, must NOT contain
        bak_path = _isolated_config_dir / "config.json.bak"
        if bak_path.exists():
            bak_text = bak_path.read_text()
            assert secret_content not in bak_text, (
                "FR-24 regression: Config.save() copied the symlink "
                "target's content into config.json.bak, info "
                "disclosure via .bak. The backup READ must use "
                "_secure_read_text (O_NOFOLLOW) so symlinks are refused."
            )

    def test_save_backup_uses_secure_read(
        self,
        _isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR-24: verify the backup block reads via _secure_read_text"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()

        # Spy on _secure_read_text.
        import voice_typer.server.config as config_mod

        calls: list = []
        original_fn = config_mod._secure_read_text

        def spy_read(path, *args, **kwargs):
            calls.append(str(path))
            return original_fn(path, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_read_text", spy_read)

        # Modify and save, should trigger backup block (since
        cfg.hotkey = "<f8>"
        object.__setattr__(cfg, "_last_saved_bytes", None)
        cfg.save()

        config_file_str = str(config_file)
        assert any(config_file_str in c for c in calls), (
            f"FR-24: Config.save() did not call _secure_read_text on "
            f"config.json during the backup block. Calls observed: "
            f"{calls}. The backup READ must use _secure_read_text "
            f"(not Path.read_bytes) to prevent symlink-following."
        )

    def test_save_backup_block_creates_bak_with_correct_content(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-24: a normal save (non-symlinked source) must still"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>", "language": "en"}))

        cfg = Config.load()

        # Capture the on-disk state AFTER load (migrate may have
        pre_save_on_disk = config_file.read_text()

        # Modify and save, should create .bak with the pre-save
        cfg.hotkey = "<f9>"
        object.__setattr__(cfg, "_last_saved_bytes", None)
        cfg.save()

        bak_path = _isolated_config_dir / "config.json.bak"
        assert bak_path.exists(), (
            "FR-24: Config.save() did not create config.json.bak, the backup block should fire when content changes."
        )
        bak_text = bak_path.read_text()
        assert bak_text == pre_save_on_disk, (
            "FR-24: .bak content mismatch. Expected the pre-save "
            "on-disk content (captured after load).\n"
            f"  Expected: {pre_save_on_disk!r}\n"
            f"  Got:      {bak_text!r}"
        )

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only 0o600 check")
    def test_save_bak_has_0600_perms(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-24: the .bak file must be chmod'd to 0o600 on POSIX"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))
        os.chmod(config_file, 0o600)

        cfg = Config.load()
        cfg.hotkey = "<f10>"
        object.__setattr__(cfg, "_last_saved_bytes", None)
        cfg.save()

        bak_path = _isolated_config_dir / "config.json.bak"
        assert bak_path.exists()
        mode = 0o777 & os.stat(bak_path).st_mode
        assert mode == 0o600, f"FR-24: config.json.bak should be 0o600 on POSIX, got 0o{mode:o}"


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only symlink test")
class TestPreMigrationBackupSecure:
    """``_backup_before_migration`` must use"""

    def test_pre_migration_backup_uses_secure_read(
        self,
        _isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_backup_before_migration`` must read the source"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<caps_lock>"}))

        import voice_typer.server.config as config_mod

        calls: list[str] = []
        original_fn = config_mod._secure_read_text

        def spy_read(path, *args, **kwargs):
            calls.append(str(path))
            return original_fn(path, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_read_text", spy_read)

        Config.load()

        config_file_str = str(config_file)
        assert any(config_file_str in c for c in calls), (
            "_backup_before_migration did not call "
            "_secure_read_text on config.json. The backup READ must "
            "use _secure_read_text (not shutil.copy2) to prevent "
            "symlink-following on the source path."
        )

    def test_pre_migration_backup_uses_secure_atomic_write(
        self,
        _isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """the backup WRITE must go through"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<caps_lock>"}))

        import voice_typer.server.config as config_mod

        write_paths: list[str] = []
        original_fn = config_mod._secure_atomic_write

        def spy_write(path, content, *args, **kwargs):
            write_paths.append(str(path))
            return original_fn(path, content, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_atomic_write", spy_write)

        Config.load()

        assert any("pre-migration-v" in p and p.endswith(".bak") for p in write_paths), (
            "_backup_before_migration did not call "
            "_secure_atomic_write with a pre-migration .bak path. "
            f"Observed write paths: {write_paths}"
        )

    def test_pre_migration_backup_filename_has_timestamp_pid_microseconds(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """the backup filename must embed a Unix timestamp,"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<caps_lock>"}))

        Config.load()

        pre_mig_backups = list(_isolated_config_dir.glob("config.json.pre-migration-v*.bak"))
        assert len(pre_mig_backups) >= 1, (
            "expected at least one pre-migration backup, "
            f"found {len(pre_mig_backups)}: {[p.name for p in pre_mig_backups]}"
        )
        import os as _os
        import re as _re

        for bak in pre_mig_backups:
            match = _re.match(
                r"^config\.json\.pre-migration-v(\d+)-(\d+)-(\d+)-(\d+)\.bak$",
                bak.name,
            )
            assert match is not None, (
                f"backup filename {bak.name!r} must match 'config.json.pre-migration-v<N>-<ts>-<pid>-<us>.bak'."
            )
            ts = int(match.group(2))
            pid = int(match.group(3))
            us = int(match.group(4))
            now = int(time.time())
            assert abs(now - ts) < 60, f"backup timestamp {ts} not recent (now={now})."
            assert pid == _os.getpid(), (
                f"backup PID {pid} does not match current PID {_os.getpid()}, the PID suffix must use os.getpid()."
            )
            assert 0 <= us < 1_000_000, f"microsecond fraction {us} out of range [0, 1_000_000)."

    def test_pre_migration_backup_rejects_symlinked_source(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """if config.json is a symlink, the secure read must"""
        secret_file = _isolated_config_dir / "attacker_secret.txt"
        secret_content = "ATTACKER_SECRET_should_not_appear_in_pre_mig_bak"
        secret_file.write_text(secret_content)

        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<caps_lock>"}))

        config_file.unlink()
        config_file.symlink_to(secret_file)

        Config._backup_before_migration(config_file, 0)

        pre_mig_backups = list(_isolated_config_dir.glob("config.json.pre-migration-v*.bak"))
        for bak in pre_mig_backups:
            bak_text = bak.read_text()
            assert secret_content not in bak_text, (
                "regression: _backup_before_migration copied "
                "the symlink target's content into the pre-migration .bak "
                "— info disclosure via .bak. The fix must use "
                "_secure_read_text (O_NOFOLLOW) so symlinks are refused."
            )

    def test_pre_migration_backup_retention_caps_at_three(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """after the 4th pre-migration backup is created, the"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<caps_lock>"}))

        import os as _os
        import time as _time

        for i in range(4):
            future_ts = _time.time() + i
            _os.utime(config_file, (future_ts, future_ts))
            Config._backup_before_migration(config_file, 0)
            _time.sleep(0.01)

        pre_mig_backups = list(_isolated_config_dir.glob("config.json.pre-migration-v*.bak"))
        assert len(pre_mig_backups) == 3, (
            "expected exactly 3 retained pre-migration backups "
            f"after 4 backup events, got {len(pre_mig_backups)}: "
            f"{[p.name for p in pre_mig_backups]}"
        )
