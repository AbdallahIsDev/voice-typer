"""overwrite-backup behavior."""

from __future__ import annotations

import json
import logging
import re
import time

from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config


def _warning_records(caplog):
    """Return WARNING+ records from the config logger."""
    return [r for r in caplog.records if r.name == "voice_typer.server.config" and r.levelno >= logging.WARNING]


class TestCorruptConfigBackup:
    """``Config.load()`` must move a corrupt config file aside"""

    def test_corrupt_config_moved_to_corrupt_backup(self, tmp_config_dir):
        """``.corrupt-<timestamp>`` before returning defaults."""
        config_file = tmp_config_dir / "config.json"
        corrupt_content = "{ this is not valid json"
        config_file.write_text(corrupt_content, encoding="utf-8")

        # Load, should return defaults (corrupt JSON) AND move the file.
        cfg = Config.load()

        # Defaults returned.
        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()

        # The original config.json must NO LONGER EXIST (was moved).
        assert not config_file.exists(), (
            "Config.load() did not move the corrupt config aside, "
            "config.json still exists at its original path.  CR-62 regression: "
            "the corrupt file must be atomically renamed to .corrupt-<timestamp>."
        )

        # A .corrupt-<timestamp> backup must exist with the original content.
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 1, f"Expected exactly one .corrupt-<timestamp> backup, got: {corrupt_backups}"
        assert corrupt_backups[0].read_text(encoding="utf-8") == corrupt_content, (
            "The .corrupt-<timestamp> backup must contain the original corrupt content "
            "(byte-for-byte) so the user can inspect/recover their settings manually."
        )

        match = re.match(
            r"^config\.json\.corrupt-(\d+)-(\d+)-(\d+)$",
            corrupt_backups[0].name,
        )
        assert match is not None, (
            f"Backup filename {corrupt_backups[0].name!r} must match the pattern "
            "'config.json.corrupt-<int-timestamp>-<pid>-<microseconds>'."
        )
        ts = int(match.group(1))
        # The timestamp must be recent (within the last 60 seconds —
        now = int(time.time())
        assert abs(now - ts) < 60, (
            f"Backup timestamp {ts} is not recent (now={now}), "
            f"the .corrupt-<timestamp> suffix must use int(time.time())."
        )
        import os as _os

        assert int(match.group(2)) == _os.getpid(), (
            f"Backup PID {match.group(2)} does not match current PID "
            f"{_os.getpid()}, the .corrupt-<timestamp>-<pid> suffix "
            "must use os.getpid() so same-second loads from different "
            "processes produce unique filenames."
        )
        # The microsecond fraction must be in [0, 1_000_000).
        us = int(match.group(3))
        assert 0 <= us < 1_000_000, (
            f"Backup microsecond fraction {us} out of range [0, 1_000_000), "
            "the suffix must be time.time_ns() % 1_000_000."
        )

    def test_corrupt_config_moved_atomically(self, tmp_config_dir):
        """The move must be atomic (``Path.replace``), no partial state."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        Config.load()

        # Post-atomic-rename postcondition: exactly one of the two exists.
        original_exists = config_file.exists()
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        backup_exists = len(corrupt_backups) == 1

        # The original MUST be gone (renamed away).
        assert not original_exists, (
            "Original config.json still exists after load(), the atomic rename must have moved it (Path.replace)."
        )
        # A backup MUST exist (the rename target).
        assert backup_exists, (
            "No .corrupt-<timestamp> backup exists after load(), the atomic rename must have created one."
        )

    def test_corrupt_config_move_logs_warning(self, tmp_config_dir, caplog):
        """A WARNING must be logged when the corrupt file is moved aside."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        recs = _warning_records(caplog)
        # At least one warning must mention the corrupt backup.
        backup_warnings = [r for r in recs if "moved corrupt config" in r.message or ".corrupt-" in r.message]
        assert backup_warnings, (
            f"No warning logged about the corrupt-config backup.  All records: {[r.message for r in recs]}"
        )

    def test_valid_config_not_moved(self, tmp_config_dir):
        """
        A valid config must NOT be moved aside (only corrupt ones are).
        Sanity check, the CR-62 fix must not aggressively move
        """
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f5>"}), encoding="utf-8")

        cfg = Config.load()

        # The original config.json must still exist (not moved).
        assert config_file.exists(), "Valid config.json was moved aside, only corrupt configs should be moved."
        # No .corrupt-<timestamp> backup should exist.
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Unexpected .corrupt-<timestamp> backups created for a valid config: {corrupt_backups}"
        )
        # The loaded config has the user's value.
        assert cfg.hotkey == "<f5>"

    def test_missing_config_not_moved(self, tmp_config_dir):
        """A missing config (first run) must not create a backup."""
        # No config.json created, first-run scenario.

        cfg = Config.load()

        # Defaults returned.
        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()
        # No .corrupt-<timestamp> backup created.
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Unexpected .corrupt-<timestamp> backups created for a missing config: {corrupt_backups}"
        )

    def test_corrupt_non_dict_json_moved(self, tmp_config_dir):
        """A valid-JSON-but-non-dict config (e.g. ``[]``, ``42``,"""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("42", encoding="utf-8")  # valid JSON, not a dict

        Config.load()

        # The original config.json must NO LONGER EXIST (was moved).
        assert not config_file.exists()
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 1
        assert corrupt_backups[0].read_text(encoding="utf-8") == "42"

    def test_multiple_corrupt_loads_create_unique_backups(self, tmp_config_dir):
        """Two corrupt-loads in the same second must NOT overwrite each other."""
        config_file = tmp_config_dir / "config.json"

        # First corrupt load.
        config_file.write_text("CORRUPT_1", encoding="utf-8")
        Config.load()

        # Second corrupt load (within the same second on fast machines).
        config_file.write_text("CORRUPT_2", encoding="utf-8")
        Config.load()

        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 2, (
            "regression: expected exactly 2 .corrupt-* backups "
            f"(one per corrupt load, made unique by the PID + microsecond "
            f"suffix), got {len(corrupt_backups)}: "
            f"{[p.name for p in corrupt_backups]}"
        )
        assert corrupt_backups[0].name != corrupt_backups[1].name, (
            "regression: two corrupt loads produced identical "
            f"backup filenames ({corrupt_backups[0].name!r}), the PID + "
            "microsecond suffix must disambiguate them."
        )
        contents = sorted(p.read_text(encoding="utf-8") for p in corrupt_backups)
        assert contents == ["CORRUPT_1", "CORRUPT_2"], (
            f"backup contents should be both CORRUPT_1 and CORRUPT_2 (no overwrite), got {contents!r}"
        )

    def test_corrupt_load_returns_defaults(self, tmp_config_dir):
        """Sanity: after moving the corrupt file aside, defaults are returned."""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("INVALID JSON {{{", encoding="utf-8")

        cfg = Config.load()

        from voice_typer.server.config import _default_hotkey_for_platform

        # Defaults.
        assert cfg.hotkey == _default_hotkey_for_platform()
        assert cfg.autostart is True
        assert cfg.paste_on_stop is True
        assert cfg.sample_rate == 16000

    def test_corrupt_move_failure_does_not_block_load(self, tmp_config_dir, monkeypatch):
        """If the move-aside fails (e.g. permission denied on the rename),"""
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("INVALID JSON {{{", encoding="utf-8")

        # Mock Path.replace at the CLASS level so the corrupt-config
        import pathlib

        def failing_replace(self, target):
            raise OSError("simulated permission denied on rename")

        monkeypatch.setattr(pathlib.Path, "replace", failing_replace)

        # Must NOT raise, the failure is caught and logged.
        cfg = Config.load()

        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()


class TestSaveBackupBeforeOverwrite:
    """+ CR-62: ``Config.save()`` must create a ``.bak`` backup"""

    def test_save_creates_bak_when_overwriting_different_content(self, tmp_config_dir):
        """``Config.save()`` must create a ``config.json.bak`` when"""
        config_file = tmp_config_dir / "config.json"

        # Step 1: write the "old" config to disk.
        old_cfg = Config(hotkey="<f3>")
        old_cfg.save()
        old_content = config_file.read_text(encoding="utf-8")
        assert json.loads(old_content)["hotkey"] == "<f3>"

        # No .bak should exist yet (nothing was overwritten).
        bak = tmp_config_dir / "config.json.bak"
        assert not bak.exists()

        # Step 2-3: change hotkey and save again.
        old_cfg.hotkey = "<f9>"
        old_cfg.save()

        assert bak.exists(), (
            "Config.save() did not create a config.json.bak when overwriting "
            "different content.  CR-38 regression: the previous version must "
            "be preserved for forensic recovery."
        )
        bak_data = json.loads(bak.read_text(encoding="utf-8"))
        assert bak_data["hotkey"] == "<f3>", (
            f"config.json.bak must contain the PREVIOUS content (hotkey=<f3>), got hotkey={bak_data['hotkey']!r}."
        )

        # And config.json must have the NEW content (<f9>).
        new_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert new_data["hotkey"] == "<f9>"

    def test_save_skips_bak_when_content_unchanged(self, tmp_config_dir):
        """If the new content is identical to the existing file, no"""
        tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        cfg = Config(hotkey="<f5>")
        cfg.save()

        # Save again with the same content.
        cfg.save()

        # No .bak should exist (content unchanged).
        assert not bak.exists(), (
            "Config.save() created a .bak even though the new content is "
            "identical to the existing file.  Idempotent re-saves must not "
            "create stale .bak files."
        )

    def test_save_bak_is_single_slot_rotation(self, tmp_config_dir):
        """The ``.bak`` is a single-slot rotation, no .bak.1, .bak.2."""
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        # Save v1.
        cfg = Config(hotkey="<f3>")
        cfg.save()

        # Save v2, .bak should contain v1.
        cfg.hotkey = "<f9>"
        cfg.save()
        assert bak.exists()
        assert json.loads(bak.read_text())["hotkey"] == "<f3>"

        # Save v3, .bak should now contain v2 (single-slot rotation).
        cfg.hotkey = "<f12>"
        cfg.save()
        assert bak.exists()
        assert json.loads(bak.read_text())["hotkey"] == "<f9>"
        # And config.json has v3.
        assert json.loads(config_file.read_text())["hotkey"] == "<f12>"

        # No .bak.1 or .bak.2 should exist (no multi-slot rotation).
        assert not (tmp_config_dir / "config.json.bak.1").exists()
        assert not (tmp_config_dir / "config.json.bak.2").exists()

    def test_save_bak_failure_does_not_block_save(self, tmp_config_dir, monkeypatch):
        """If the backup fails (e.g. permission denied on the .bak write),"""
        config_file = tmp_config_dir / "config.json"

        # Write an initial config.
        cfg = Config(hotkey="<f3>")
        cfg.save()
        assert config_file.exists()

        import voice_typer.server.config as config_mod

        original_secure_write = config_mod._secure_atomic_write

        def selective_secure_write(path, content, *args, **kwargs):
            if str(path).endswith(".bak"):
                raise OSError("simulated permission denied on .bak write")
            return original_secure_write(path, content, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_atomic_write", selective_secure_write)

        # Change hotkey and save, .bak write will fail but save must proceed.
        cfg.hotkey = "<f9>"

        # Must NOT raise, the .bak failure is caught and logged.
        result = cfg.save()

        assert result is True, (
            "Config.save() returned False when the .bak write failed, "
            ".bak failure must not block the save (best-effort backup)."
        )
        new_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert new_data["hotkey"] == "<f9>"
        # And no .bak should exist (the write failed).
        assert not (tmp_config_dir / "config.json.bak").exists()

    def test_save_no_bak_on_first_save(self, tmp_config_dir):
        """The first save (no existing config.json) must NOT create a .bak."""
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        cfg = Config(hotkey="<f5>")
        cfg.save()

        assert config_file.exists()
        assert not bak.exists(), (
            "Config.save() created a .bak on the first save (no existing "
            "config to back up).  The .bak branch must only run when "
            "config.json already exists."
        )

    def test_save_bak_preserves_byte_for_byte_content(self, tmp_config_dir):
        """The .bak must contain the EXACT bytes of the previous config.json."""
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        # Write a config with unusual formatting (no indent, trailing newline).
        from voice_typer.server.config import _CURRENT_SCHEMA_VERSION

        custom_content = (
            json.dumps(
                {"hotkey": "<f3>", "schema_version": _CURRENT_SCHEMA_VERSION, "secrets_migrated": True},
                separators=(",", ":"),
            )
            + "\n"
        )
        config_file.write_text(custom_content, encoding="utf-8")

        # Load it and change a field, then save.
        cfg = Config.load()
        assert cfg.hotkey == "<f3>"
        cfg.hotkey = "<f9>"
        cfg.save()

        # The .bak must contain the EXACT custom_content (byte-for-byte).
        assert bak.exists()
        bak_bytes = bak.read_text(encoding="utf-8")
        assert bak_bytes == custom_content, (
            f"config.json.bak must contain the exact previous bytes (byte-for-byte), got: {bak_bytes!r}"
        )

    def test_save_bak_round_trip_recovery(self, tmp_config_dir):
        """End-to-end: a user can recover from a bad save by renaming"""
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        newer_content = json.dumps(
            {
                "hotkey": "<f3>",
                "autostart": False,
                "schema_version": 99,  # future version
                "secrets_migrated": True,  # prevent credential_store overwrite
                "future_field": "value the older build doesn't know about",
            }
        )
        config_file.write_text(newer_content, encoding="utf-8")

        # Step 2: load with the current (older) build, the future_field
        cfg = Config.load()
        # The dropped field is gone.
        assert not hasattr(cfg, "future_field")

        # Step 3: save, overwrites config.json with the older build's
        cfg.save()
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == newer_content, (
            "The .bak must contain the original (pre-downgrade) config "
            "so the user can recover the dropped future_field."
        )

        # Step 4: simulate user recovery, rename .bak → config.json.
        config_file.unlink()
        bak.rename(config_file)

        # Verify the recovered config has the future_field back.
        recovered = json.loads(config_file.read_text(encoding="utf-8"))
        assert recovered["future_field"] == "value the older build doesn't know about"
        assert recovered["hotkey"] == "<f3>"


class TestDeprecatedKeysSilentlyScrubbed:
    """``Config.load()`` must accept a stale ``config.json``"""

    DEPRECATED_KEYS = [
        "silence_rms_threshold",
        "silence_peak_threshold",
        "normalize_audio",
        "normalize_target_peak",
        "volume_duck_per_session",
        "volume_duck_smart",
        "noise_filter_gate_threshold",
    ]

    def test_stale_v2_config_with_deprecated_keys_loads_cleanly(self, tmp_config_dir):
        """A schema-v2 config with all 7 deprecated keys must load"""
        config_file = tmp_config_dir / "config.json"

        stale_config = {
            "schema_version": 2,
            "hotkey": "<f9>",
            "autostart": False,
            "secrets_migrated": True,
        }
        for key in self.DEPRECATED_KEYS:
            if "threshold" in key or "peak" in key:
                stale_config[key] = 0.5
            else:
                stale_config[key] = True
        config_file.write_text(json.dumps(stale_config), encoding="utf-8")

        cfg = Config.load()

        assert cfg.hotkey == "<f9>"
        assert cfg.autostart is False
        assert cfg.schema_version == _CURRENT_SCHEMA_VERSION
        for key in self.DEPRECATED_KEYS:
            assert not hasattr(cfg, key), f"Deprecated field {key!r} must not be on the Config instance"
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Stale v2 config should NOT trigger corrupt-config backup; found: {corrupt_backups}"
        )

    def test_stale_v3_config_with_deprecated_keys_handled_gracefully(self, tmp_config_dir):
        """A schema-v3 config that *still* carries deprecated keys"""
        config_file = tmp_config_dir / "config.json"

        stale_config = {
            "schema_version": 3,
            "hotkey": "<f9>",
            "silence_rms_threshold": 0.5,
            "secrets_migrated": True,
        }
        config_file.write_text(json.dumps(stale_config), encoding="utf-8")

        cfg = Config.load()

        # Non-deprecated values survived.
        assert cfg.hotkey == "<f9>"
        # The deprecated field is NOT on the instance.
        assert not hasattr(cfg, "silence_rms_threshold")
        # No corrupt-config backup was created.
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Stale v3 config should NOT trigger corrupt-config backup; found: {corrupt_backups}"
        )
