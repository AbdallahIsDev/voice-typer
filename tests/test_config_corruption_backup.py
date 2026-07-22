"""Regression tests for CR-38 + CR-62: corrupt-config backup and
overwrite-backup behavior.

Context
-------
Two related data-integrity findings from the comprehensive review:

CR-38 (Medium) — Config schema downgrade-safety: forward-compat path
silently drops unknown fields on next save.  When a newer-version
config.json (with fields the current build doesn't know about) is
loaded by an older build, ``Config.load()`` filters out the unknown
keys via ``k in cls.__dataclass_fields__``.  The next ``save()``
persists the filtered dict — the unknown fields are LOST forever.
Fix: before overwriting, copy the existing file to ``config.json.bak``
(single-slot rotation) so the user has a forensic recovery path.

CR-62 (Medium) — ``config.json`` corruption silently overwrites user
settings on next save (no backup).  When ``Config.load()`` encounters
a corrupt ``config.json`` (JSONDecodeError, TypeError, ValueError,
OSError), it falls back to defaults.  The next ``save()`` then writes
the defaults to disk — overwriting the corrupt file and any
recoverable data in it.  Fix: in the ``except`` fallback path, move
the corrupt file aside to ``config.json.corrupt-<timestamp>`` (atomic
rename) so the user can inspect/recover their settings manually.

Both fixes are best-effort: a backup failure does NOT block the
save/load — we'd rather have a working (if defaults-only) config than
no config.  The tests below pin both contracts.
"""

from __future__ import annotations

import json
import logging
import re
import time

from voice_typer.server.config import Config

# ── Helpers ────────────────────────────────────────────────────────────────


def _patch_config_dir(tmp_path, monkeypatch):
    """Point ``config._config_dir`` at ``tmp_path`` for the duration of the test."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)


def _warning_records(caplog):
    """Return WARNING+ records from the config logger."""
    return [r for r in caplog.records if r.name == "voice_typer.server.config" and r.levelno >= logging.WARNING]


# ── CR-62: corrupt config moved aside on load ─────────────────────────────


class TestCorruptConfigBackup:
    """CR-62: ``Config.load()`` must move a corrupt config file aside
    before falling back to defaults, so the user can recover their
    settings manually from the ``.corrupt-<timestamp>`` backup."""

    def test_corrupt_config_moved_to_corrupt_backup(self, tmp_path, monkeypatch):
        """When config.json is corrupt JSON, load() must move it to
        ``.corrupt-<timestamp>`` before returning defaults.

        This is the core CR-62 regression test.  Before the fix,
        ``Config.load()`` would catch the JSONDecodeError, log a
        warning, return defaults — and leave the corrupt file in
        place.  The next ``save()`` would then overwrite the corrupt
        file with the defaults, destroying any chance of forensic
        recovery.  After the fix, the corrupt file is atomically
        renamed to ``.corrupt-<timestamp>`` so the user's settings
        are preserved on disk for manual inspection.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        corrupt_content = "{ this is not valid json"
        config_file.write_text(corrupt_content, encoding="utf-8")

        # Load — should return defaults (corrupt JSON) AND move the file.
        cfg = Config.load()

        # Defaults returned.
        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()

        # The original config.json must NO LONGER EXIST (was moved).
        assert not config_file.exists(), (
            "Config.load() did not move the corrupt config aside — "
            "config.json still exists at its original path.  CR-62 regression: "
            "the corrupt file must be atomically renamed to .corrupt-<timestamp>."
        )

        # A .corrupt-<timestamp> backup must exist with the original content.
        corrupt_backups = list(tmp_path.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 1, f"Expected exactly one .corrupt-<timestamp> backup, got: {corrupt_backups}"
        assert corrupt_backups[0].read_text(encoding="utf-8") == corrupt_content, (
            "The .corrupt-<timestamp> backup must contain the original corrupt content "
            "(byte-for-byte) so the user can inspect/recover their settings manually."
        )

        # The timestamp suffix must be a valid integer (Unix epoch seconds).
        match = re.match(r"^config\.json\.corrupt-(\d+)$", corrupt_backups[0].name)
        assert match is not None, (
            f"Backup filename {corrupt_backups[0].name!r} must match the pattern 'config.json.corrupt-<int-timestamp>'."
        )
        ts = int(match.group(1))
        # The timestamp must be recent (within the last 60 seconds —
        # generous bound to account for slow CI runners).
        now = int(time.time())
        assert abs(now - ts) < 60, (
            f"Backup timestamp {ts} is not recent (now={now}) — "
            f"the .corrupt-<timestamp> suffix must use int(time.time())."
        )

    def test_corrupt_config_moved_atomically(self, tmp_path, monkeypatch):
        """The move must be atomic (``Path.replace``) — no partial state.

        We can't easily test atomicity directly, but we CAN test that
        after the load, EITHER the original file exists OR the
        .corrupt-<timestamp> backup exists (never both, never neither).
        This is the postcondition of an atomic rename.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        Config.load()

        # Post-atomic-rename postcondition: exactly one of the two exists.
        original_exists = config_file.exists()
        corrupt_backups = list(tmp_path.glob("config.json.corrupt-*"))
        backup_exists = len(corrupt_backups) == 1

        # The original MUST be gone (renamed away).
        assert not original_exists, (
            "Original config.json still exists after load() — the atomic rename must have moved it (Path.replace)."
        )
        # A backup MUST exist (the rename target).
        assert backup_exists, (
            "No .corrupt-<timestamp> backup exists after load() — the atomic rename must have created one."
        )

    def test_corrupt_config_move_logs_warning(self, tmp_path, monkeypatch, caplog):
        """A WARNING must be logged when the corrupt file is moved aside.

        The user needs to know their config was corrupt AND that a
        backup was created — otherwise they might not realize they
        can recover their settings from the ``.corrupt-<timestamp>``
        file.  The warning must mention the backup path so the user
        knows where to look.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        recs = _warning_records(caplog)
        # At least one warning must mention the corrupt backup.
        backup_warnings = [r for r in recs if "moved corrupt config" in r.message or ".corrupt-" in r.message]
        assert backup_warnings, (
            f"No warning logged about the corrupt-config backup.  All records: {[r.message for r in recs]}"
        )

    def test_valid_config_not_moved(self, tmp_path, monkeypatch):
        """A valid config must NOT be moved aside (only corrupt ones are).

        Sanity check — the CR-62 fix must not aggressively move
        configs that load successfully.  Otherwise every load would
        create a .corrupt-<timestamp> backup, filling the user's
        config dir with stale copies.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f5>"}), encoding="utf-8")

        cfg = Config.load()

        # The original config.json must still exist (not moved).
        assert config_file.exists(), "Valid config.json was moved aside — only corrupt configs should be moved."
        # No .corrupt-<timestamp> backup should exist.
        corrupt_backups = list(tmp_path.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Unexpected .corrupt-<timestamp> backups created for a valid config: {corrupt_backups}"
        )
        # The loaded config has the user's value.
        assert cfg.hotkey == "<f5>"

    def test_missing_config_not_moved(self, tmp_path, monkeypatch):
        """A missing config (first run) must not create a backup.

        There's nothing to move — the file doesn't exist.  The
        ``config_file.exists()`` guard in the load() except block
        prevents trying to rename a non-existent file.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        # No config.json created — first-run scenario.

        cfg = Config.load()

        # Defaults returned.
        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()
        # No .corrupt-<timestamp> backup created.
        corrupt_backups = list(tmp_path.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Unexpected .corrupt-<timestamp> backups created for a missing config: {corrupt_backups}"
        )

    def test_corrupt_non_dict_json_moved(self, tmp_path, monkeypatch):
        """A valid-JSON-but-non-dict config (e.g. ``[]``, ``42``,
        ``"string"``) is also corrupt and must be moved aside.

        ``Config.load()`` explicitly checks ``isinstance(parsed, dict)``
        and raises ``TypeError`` if not.  The except block must catch
        this and move the file aside — same as for JSONDecodeError.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        config_file.write_text("42", encoding="utf-8")  # valid JSON, not a dict

        Config.load()

        # The original config.json must NO LONGER EXIST (was moved).
        assert not config_file.exists()
        corrupt_backups = list(tmp_path.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 1
        assert corrupt_backups[0].read_text(encoding="utf-8") == "42"

    def test_multiple_corrupt_loads_create_unique_backups(self, tmp_path, monkeypatch):
        """Two corrupt-loads in the same second must not overwrite each other.

        The ``.corrupt-<timestamp>`` suffix uses 1-second resolution,
        so two loads in the same second would produce the same backup
        filename.  ``Path.replace`` would atomically overwrite the
        first backup with the second — losing the first corrupt
        content.

        This test documents that behavior: the SECOND load's backup
        wins.  If we wanted to preserve both, we'd need a higher-
        resolution suffix (microseconds) or a counter.  For now, the
        1-second resolution is good enough for forensic purposes —
        rapid double-corruption is rare in practice (the user has to
        manually corrupt the file twice in the same second).
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"

        # First corrupt load.
        config_file.write_text("CORRUPT_1", encoding="utf-8")
        Config.load()

        # Second corrupt load (within the same second on fast machines).
        config_file.write_text("CORRUPT_2", encoding="utf-8")
        Config.load()

        # We expect either 1 or 2 backups:
        # - 1 backup: both loads happened in the same second, second wins.
        # - 2 backups: loads happened in different seconds.
        corrupt_backups = list(tmp_path.glob("config.json.corrupt-*"))
        assert 1 <= len(corrupt_backups) <= 2, (
            f"Expected 1 or 2 .corrupt-<timestamp> backups, got: {len(corrupt_backups)}"
        )

        # If there's only one backup, it must contain CORRUPT_2 (the
        # second load's content, since Path.replace atomically
        # overwrites the first).
        if len(corrupt_backups) == 1:
            content = corrupt_backups[0].read_text(encoding="utf-8")
            assert content in ("CORRUPT_1", "CORRUPT_2"), f"Unexpected backup content: {content!r}"

    def test_corrupt_load_returns_defaults(self, tmp_path, monkeypatch):
        """Sanity: after moving the corrupt file aside, defaults are returned.

        The move-aside is best-effort and must not affect the
        return-Defaults contract.  Even if the move fails, defaults
        are returned (the user still gets a working app).
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        config_file.write_text("INVALID JSON {{{", encoding="utf-8")

        cfg = Config.load()

        from voice_typer.server.config import _default_hotkey_for_platform

        # Defaults.
        assert cfg.hotkey == _default_hotkey_for_platform()
        assert cfg.autostart is True
        assert cfg.paste_on_stop is True
        assert cfg.sample_rate == 16000

    def test_corrupt_move_failure_does_not_block_load(self, tmp_path, monkeypatch):
        """If the move-aside fails (e.g. permission denied on the rename),
        load() must still return defaults (not raise).

        The move is best-effort — a failure is logged at DEBUG level
        and the load proceeds.  The user gets a working (defaults-
        only) app even if the backup couldn't be created.

        G4-H-10 note: we monkeypatch ``pathlib.Path.replace`` at the
        CLASS level (not the instance level) because PosixPath uses
        ``__slots__`` which makes instance attribute assignment raise
        ``AttributeError: 'PosixPath' object attribute 'replace' is
        read-only``.  The class-level patch affects all Path instances
        for the duration of the test, which is fine here because the
        only ``Path.replace`` call during ``Config.load()`` is the
        corrupt-config move-aside (no save happens during load).
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        config_file.write_text("INVALID JSON {{{", encoding="utf-8")

        # Mock Path.replace at the CLASS level so the corrupt-config
        # move-aside raises OSError (simulating permission denied).
        import pathlib

        original_replace = pathlib.Path.replace

        def failing_replace(self, target):
            raise OSError("simulated permission denied on rename")

        monkeypatch.setattr(pathlib.Path, "replace", failing_replace)

        # Must NOT raise — the failure is caught and logged.
        cfg = Config.load()

        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()


# ── CR-38: backup before overwrite on save ────────────────────────────────


class TestSaveBackupBeforeOverwrite:
    """CR-38 + CR-62: ``Config.save()`` must create a ``.bak`` backup
    before overwriting an existing ``config.json`` that differs from
    the new content.  Single-slot rotation (no .bak.1, .bak.2)."""

    def test_save_creates_bak_when_overwriting_different_content(self, tmp_path, monkeypatch):
        """``Config.save()`` must create a ``config.json.bak`` when
        overwriting significantly different content.

        CR-38 regression: a downgrade save (older build saving over a
        newer-build config) silently drops unknown fields.  Without a
        backup, those fields are lost forever.  With a backup, the
        user can manually merge them back.

        We test this by:
          1. Writing a config with hotkey=<f3> to disk (the "old" config).
          2. Loading it into a Config instance.
          3. Changing hotkey to <f9>.
          4. Saving — this should create config.json.bak with the
             <f3> content (the previous version).
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"

        # Step 1: write the "old" config to disk.
        old_cfg = Config(hotkey="<f3>")
        old_cfg.save()
        old_content = config_file.read_text(encoding="utf-8")
        assert json.loads(old_content)["hotkey"] == "<f3>"

        # No .bak should exist yet (nothing was overwritten).
        bak = tmp_path / "config.json.bak"
        assert not bak.exists()

        # Step 2-3: change hotkey and save again.
        old_cfg.hotkey = "<f9>"
        old_cfg.save()

        # Step 4: the .bak must now exist with the OLD content (<f3>).
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

    def test_save_skips_bak_when_content_unchanged(self, tmp_path, monkeypatch):
        """If the new content is identical to the existing file, no
        ``.bak`` is created.

        Saving the same config twice in a row is a no-op for the
        ``.bak`` logic — there's nothing to back up (the new content
        equals the existing content).  This avoids creating stale
        ``.bak`` files on idempotent re-saves.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        bak = tmp_path / "config.json.bak"

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

    def test_save_bak_is_single_slot_rotation(self, tmp_path, monkeypatch):
        """The ``.bak`` is a single-slot rotation — no .bak.1, .bak.2.

        A third save overwrites the previous .bak (single-slot).  This
        keeps the user's config dir clean (one .bak file at most) at
        the cost of preserving only the immediately-previous version.
        For the CR-38 use case (downgrade save), the immediately-
        previous version is exactly what the user wants to recover.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        bak = tmp_path / "config.json.bak"

        # Save v1.
        cfg = Config(hotkey="<f3>")
        cfg.save()

        # Save v2 — .bak should contain v1.
        cfg.hotkey = "<f9>"
        cfg.save()
        assert bak.exists()
        assert json.loads(bak.read_text())["hotkey"] == "<f3>"

        # Save v3 — .bak should now contain v2 (single-slot rotation).
        cfg.hotkey = "<f12>"
        cfg.save()
        assert bak.exists()
        assert json.loads(bak.read_text())["hotkey"] == "<f9>"
        # And config.json has v3.
        assert json.loads(config_file.read_text())["hotkey"] == "<f12>"

        # No .bak.1 or .bak.2 should exist (no multi-slot rotation).
        assert not (tmp_path / "config.json.bak.1").exists()
        assert not (tmp_path / "config.json.bak.2").exists()

    def test_save_bak_failure_does_not_block_save(self, tmp_path, monkeypatch):
        """If the backup fails (e.g. permission denied on the .bak write),
        ``Config.save()`` must still proceed with the overwrite.

        The backup is best-effort — a failure is logged at DEBUG level
        and the save proceeds.  The user still gets the new config;
        they just don't get a .bak to recover from.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"

        # Write an initial config.
        cfg = Config(hotkey="<f3>")
        cfg.save()
        assert config_file.exists()

        # Mock write_text on the config_file's parent (or on Path
        # objects) to raise OSError when writing the .bak.  We need a
        # targeted mock that only affects the .bak write, not the
        # _secure_atomic_write call.

        # Approach: mock config_file.read_text to return a non-matching
        # content (so the .bak branch is entered), then mock
        # backup.write_text to raise.  But we don't control the
        # ``backup`` Path object directly.

        # Simpler approach: monkeypatch Path.write_bytes globally to
        # raise ONLY when the path ends with .bak.  This way the
        # _secure_atomic_write call (which writes to a unique .tmp
        # file via mkstemp, not .bak) still works.  G4-H-09 uses
        # write_bytes (not write_text) so the backup is byte-for-byte.
        original_write_bytes = type(config_file).write_bytes

        def selective_write_bytes(self, data):
            if str(self).endswith(".bak"):
                raise OSError("simulated permission denied on .bak write")
            return original_write_bytes(self, data)

        monkeypatch.setattr("pathlib.Path.write_bytes", selective_write_bytes)

        # Change hotkey and save — .bak write will fail but save must proceed.
        cfg.hotkey = "<f9>"

        # Must NOT raise — the .bak failure is caught and logged.
        result = cfg.save()

        # The save must succeed (the _secure_atomic_write call worked).
        assert result is True, (
            "Config.save() returned False when the .bak write failed — "
            ".bak failure must not block the save (best-effort backup)."
        )
        # config.json must have the NEW content (<f9>).
        new_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert new_data["hotkey"] == "<f9>"
        # And no .bak should exist (the write failed).
        assert not (tmp_path / "config.json.bak").exists()

    def test_save_no_bak_on_first_save(self, tmp_path, monkeypatch):
        """The first save (no existing config.json) must NOT create a .bak.

        There's nothing to back up — the file doesn't exist yet.
        The ``config_file.exists()`` guard in save() prevents trying
        to back up a non-existent file.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        bak = tmp_path / "config.json.bak"

        cfg = Config(hotkey="<f5>")
        cfg.save()

        assert config_file.exists()
        assert not bak.exists(), (
            "Config.save() created a .bak on the first save (no existing "
            "config to back up).  The .bak branch must only run when "
            "config.json already exists."
        )

    def test_save_bak_preserves_byte_for_byte_content(self, tmp_path, monkeypatch):
        """The .bak must contain the EXACT bytes of the previous config.json.

        Not a re-serialization, not a normalized form — the raw bytes
        as they were on disk.  This is important because the user may
        have manually edited config.json with custom formatting
        (e.g. trailing newlines, specific indentation) that we want
        to preserve in the backup.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        bak = tmp_path / "config.json.bak"

        # Write a config with unusual formatting (no indent, trailing newline).
        # G4-M-15 / G4-H-09 note: set schema_version=_CURRENT_SCHEMA_VERSION
        # and secrets_migrated=True so that Config.load() does NOT trigger
        # any migration (which would eager-save a normalized version) and
        # does NOT trigger credential_store.migrate_secrets_to_keyring
        # (which would also overwrite config.json with a normalized version).
        # Without these, the on-disk bytes would be normalized before the
        # test's cfg.save() runs, and the .bak would contain the normalized
        # bytes rather than the original custom_content.
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

    def test_save_bak_round_trip_recovery(self, tmp_path, monkeypatch):
        """End-to-end: a user can recover from a bad save by renaming
        ``config.json.bak`` back to ``config.json``.

        This is the user-facing recovery flow that CR-38 enables:
          1. User has a working config.json.
          2. A downgrade save (older build overwrites newer config)
             drops some fields.
          3. User notices the missing fields.
          4. User renames config.json.bak → config.json to restore.

        We test this by simulating the full flow and verifying the
        recovered config has the original (pre-downgrade) values.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        config_file = tmp_path / "config.json"
        bak = tmp_path / "config.json.bak"

        # Step 1: write a "newer build" config with extra fields the
        # "older build" doesn't know about.  We can't actually add
        # unknown fields to the Config dataclass (it would reject
        # them), so we simulate by writing raw JSON with extra keys.
        # G4-H-09 / G4-H-10 note: set secrets_migrated=True so that
        # Config.load() does NOT trigger credential_store.migrate_secrets_to_keyring
        # (which would overwrite config.json with a normalized version before
        # the test's cfg.save() runs, causing the .bak to contain the normalized
        # bytes rather than the original newer_content).
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

        # Step 2: load with the current (older) build — the future_field
        # is silently dropped (filtered by ``k in cls.__dataclass_fields__``).
        cfg = Config.load()
        # The dropped field is gone.
        assert not hasattr(cfg, "future_field")

        # Step 3: save — overwrites config.json with the older build's
        # view of the config (no future_field).  The .bak must contain
        # the ORIGINAL newer_content (with future_field).
        cfg.save()
        assert bak.exists()
        assert bak.read_text(encoding="utf-8") == newer_content, (
            "The .bak must contain the original (pre-downgrade) config "
            "so the user can recover the dropped future_field."
        )

        # Step 4: simulate user recovery — rename .bak → config.json.
        config_file.unlink()
        bak.rename(config_file)

        # Verify the recovered config has the future_field back.
        recovered = json.loads(config_file.read_text(encoding="utf-8"))
        assert recovered["future_field"] == "value the older build doesn't know about"
        assert recovered["hotkey"] == "<f3>"
