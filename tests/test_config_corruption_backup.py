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

from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config

# ── Helpers ────────────────────────────────────────────────────────────────


def _warning_records(caplog):
    """Return WARNING+ records from the config logger."""
    return [r for r in caplog.records if r.name == "voice_typer.server.config" and r.levelno >= logging.WARNING]


# corrupt config moved aside on load ─────────────────────────────


class TestCorruptConfigBackup:
    """CR-62: ``Config.load()`` must move a corrupt config file aside
    before falling back to defaults, so the user can recover their
    settings manually from the ``.corrupt-<timestamp>`` backup."""

    def test_corrupt_config_moved_to_corrupt_backup(self, tmp_config_dir):
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
        config_file = tmp_config_dir / "config.json"
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
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 1, f"Expected exactly one .corrupt-<timestamp> backup, got: {corrupt_backups}"
        assert corrupt_backups[0].read_text(encoding="utf-8") == corrupt_content, (
            "The .corrupt-<timestamp> backup must contain the original corrupt content "
            "(byte-for-byte) so the user can inspect/recover their settings manually."
        )

        # the filename pattern is now
        # ``config.json.corrupt-<int-timestamp>-<pid>-<microseconds>``
        # (PID + microsecond suffix added to disambiguate same-second
        # loads from different processes and back-to-back loads within
        # the same process). The first group must be a recent Unix
        # timestamp (epoch seconds); the second group is the PID; the
        # third group is ``time.time_ns() % 1_000_000`` (microsecond
        # fraction, 0..999999).
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
        # generous bound to account for slow CI runners).
        now = int(time.time())
        assert abs(now - ts) < 60, (
            f"Backup timestamp {ts} is not recent (now={now}) — "
            f"the .corrupt-<timestamp> suffix must use int(time.time())."
        )
        # the PID group must match os.getpid() (the test
        # runs in-process so the PID is the current process's PID).
        import os as _os

        assert int(match.group(2)) == _os.getpid(), (
            f"Backup PID {match.group(2)} does not match current PID "
            f"{_os.getpid()} — the .corrupt-<timestamp>-<pid> suffix "
            "must use os.getpid() so same-second loads from different "
            "processes produce unique filenames."
        )
        # The microsecond fraction must be in [0, 1_000_000).
        us = int(match.group(3))
        assert 0 <= us < 1_000_000, (
            f"Backup microsecond fraction {us} out of range [0, 1_000_000) — "
            "the suffix must be time.time_ns() % 1_000_000."
        )

    def test_corrupt_config_moved_atomically(self, tmp_config_dir):
        """The move must be atomic (``Path.replace``) — no partial state.

        We can't easily test atomicity directly, but we CAN test that
        after the load, EITHER the original file exists OR the
        .corrupt-<timestamp> backup exists (never both, never neither).
        This is the postcondition of an atomic rename.
        """
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("NOT VALID JSON {{{", encoding="utf-8")

        Config.load()

        # Post-atomic-rename postcondition: exactly one of the two exists.
        original_exists = config_file.exists()
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        backup_exists = len(corrupt_backups) == 1

        # The original MUST be gone (renamed away).
        assert not original_exists, (
            "Original config.json still exists after load() — the atomic rename must have moved it (Path.replace)."
        )
        # A backup MUST exist (the rename target).
        assert backup_exists, (
            "No .corrupt-<timestamp> backup exists after load() — the atomic rename must have created one."
        )

    def test_corrupt_config_move_logs_warning(self, tmp_config_dir, caplog):
        """A WARNING must be logged when the corrupt file is moved aside.

        The user needs to know their config was corrupt AND that a
        backup was created — otherwise they might not realize they
        can recover their settings from the ``.corrupt-<timestamp>``
        file.  The warning must mention the backup path so the user
        knows where to look.
        """
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
        """A valid config must NOT be moved aside (only corrupt ones are).

        Sanity check — the CR-62 fix must not aggressively move
        configs that load successfully.  Otherwise every load would
        create a .corrupt-<timestamp> backup, filling the user's
        config dir with stale copies.
        """
        config_file = tmp_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f5>"}), encoding="utf-8")

        cfg = Config.load()

        # The original config.json must still exist (not moved).
        assert config_file.exists(), "Valid config.json was moved aside — only corrupt configs should be moved."
        # No .corrupt-<timestamp> backup should exist.
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert corrupt_backups == [], (
            f"Unexpected .corrupt-<timestamp> backups created for a valid config: {corrupt_backups}"
        )
        # The loaded config has the user's value.
        assert cfg.hotkey == "<f5>"

    def test_missing_config_not_moved(self, tmp_config_dir):
        """A missing config (first run) must not create a backup.

        There's nothing to move — the file doesn't exist.  The
        ``config_file.exists()`` guard in the load() except block
        prevents trying to rename a non-existent file.
        """
        # No config.json created — first-run scenario.

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
        """A valid-JSON-but-non-dict config (e.g. ``[]``, ``42``,
        ``"string"``) is also corrupt and must be moved aside.

        ``Config.load()`` explicitly checks ``isinstance(parsed, dict)``
        and raises ``TypeError`` if not.  The except block must catch
        this and move the file aside — same as for JSONDecodeError.
        """
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("42", encoding="utf-8")  # valid JSON, not a dict

        Config.load()

        # The original config.json must NO LONGER EXIST (was moved).
        assert not config_file.exists()
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 1
        assert corrupt_backups[0].read_text(encoding="utf-8") == "42"

    def test_multiple_corrupt_loads_create_unique_backups(self, tmp_config_dir):
        """Two corrupt-loads in the same second must NOT overwrite each other.

        the previous ``.corrupt-<timestamp>`` suffix used
        1-second resolution, so two loads in the same second produced
        the same backup filename and ``Path.replace`` atomically
        overwrote the first backup with the second — losing the first
        corrupt content's forensic recovery point. The fix appends a
        PID + microsecond-fraction suffix so back-to-back loads in the
        same process always produce unique filenames.
        """
        config_file = tmp_config_dir / "config.json"

        # First corrupt load.
        config_file.write_text("CORRUPT_1", encoding="utf-8")
        Config.load()

        # Second corrupt load (within the same second on fast machines).
        config_file.write_text("CORRUPT_2", encoding="utf-8")
        Config.load()

        # two corrupt loads MUST produce two distinct
        # backups (PID + microsecond suffix disambiguates them even
        # within the same second from the same process).
        corrupt_backups = list(tmp_config_dir.glob("config.json.corrupt-*"))
        assert len(corrupt_backups) == 2, (
            "regression: expected exactly 2 .corrupt-* backups "
            f"(one per corrupt load, made unique by the PID + microsecond "
            f"suffix), got {len(corrupt_backups)}: "
            f"{[p.name for p in corrupt_backups]}"
        )
        assert corrupt_backups[0].name != corrupt_backups[1].name, (
            "regression: two corrupt loads produced identical "
            f"backup filenames ({corrupt_backups[0].name!r}) — the PID + "
            "microsecond suffix must disambiguate them."
        )
        contents = sorted(p.read_text(encoding="utf-8") for p in corrupt_backups)
        assert contents == ["CORRUPT_1", "CORRUPT_2"], (
            f"backup contents should be both CORRUPT_1 and CORRUPT_2 (no overwrite), got {contents!r}"
        )

    def test_corrupt_load_returns_defaults(self, tmp_config_dir):
        """Sanity: after moving the corrupt file aside, defaults are returned.

        The move-aside is best-effort and must not affect the
        return-Defaults contract.  Even if the move fails, defaults
        are returned (the user still gets a working app).
        """
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
        config_file = tmp_config_dir / "config.json"
        config_file.write_text("INVALID JSON {{{", encoding="utf-8")

        # Mock Path.replace at the CLASS level so the corrupt-config
        # move-aside raises OSError (simulating permission denied).
        import pathlib

        def failing_replace(self, target):
            raise OSError("simulated permission denied on rename")

        monkeypatch.setattr(pathlib.Path, "replace", failing_replace)

        # Must NOT raise — the failure is caught and logged.
        cfg = Config.load()

        from voice_typer.server.config import _default_hotkey_for_platform

        assert cfg.hotkey == _default_hotkey_for_platform()


# backup before overwrite on save ────────────────────────────────


class TestSaveBackupBeforeOverwrite:
    """CR-38 + CR-62: ``Config.save()`` must create a ``.bak`` backup
    before overwriting an existing ``config.json`` that differs from
    the new content.  Single-slot rotation (no .bak.1, .bak.2)."""

    def test_save_creates_bak_when_overwriting_different_content(self, tmp_config_dir):
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

    def test_save_skips_bak_when_content_unchanged(self, tmp_config_dir):
        """If the new content is identical to the existing file, no
        ``.bak`` is created.

        Saving the same config twice in a row is a no-op for the
        ``.bak`` logic — there's nothing to back up (the new content
        equals the existing content).  This avoids creating stale
        ``.bak`` files on idempotent re-saves.
        """
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
        """The ``.bak`` is a single-slot rotation — no .bak.1, .bak.2.

        A third save overwrites the previous .bak (single-slot).  This
        keeps the user's config dir clean (one .bak file at most) at
        the cost of preserving only the immediately-previous version.
        For the CR-38 use case (downgrade save), the immediately-
        previous version is exactly what the user wants to recover.
        """
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

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
        assert not (tmp_config_dir / "config.json.bak.1").exists()
        assert not (tmp_config_dir / "config.json.bak.2").exists()

    def test_save_bak_failure_does_not_block_save(self, tmp_config_dir, monkeypatch):
        """If the backup fails (e.g. permission denied on the .bak write),
        ``Config.save()`` must still proceed with the overwrite.

        The backup is best-effort — a failure is logged at DEBUG level
        and the save proceeds.  The user still gets the new config;
        they just don't get a .bak to recover from.
        """
        config_file = tmp_config_dir / "config.json"

        # Write an initial config.
        cfg = Config(hotkey="<f3>")
        cfg.save()
        assert config_file.exists()

        # the .bak write is routed through
        # ``_secure_atomic_write`` (atomic os.replace + fsync + 0o600),
        # NOT through ``Path.write_bytes``. We patch
        # ``voice_typer.server.config._secure_atomic_write`` directly
        # so the .bak WRITE raises OSError while the actual config.json
        # WRITE (which uses the same function) still proceeds.
        import voice_typer.server.config as config_mod

        original_secure_write = config_mod._secure_atomic_write

        def selective_secure_write(path, content, *args, **kwargs):
            if str(path).endswith(".bak"):
                raise OSError("simulated permission denied on .bak write")
            return original_secure_write(path, content, *args, **kwargs)

        monkeypatch.setattr(config_mod, "_secure_atomic_write", selective_secure_write)

        # Change hotkey and save — .bak write will fail but save must proceed.
        cfg.hotkey = "<f9>"

        # Must NOT raise — the .bak failure is caught and logged.
        result = cfg.save()

        # The save must succeed (the _secure_atomic_write call for the
        # actual config.json write worked — only the .bak write raised).
        assert result is True, (
            "Config.save() returned False when the .bak write failed — "
            ".bak failure must not block the save (best-effort backup)."
        )
        # config.json must have the NEW content (<f9>).
        new_data = json.loads(config_file.read_text(encoding="utf-8"))
        assert new_data["hotkey"] == "<f9>"
        # And no .bak should exist (the write failed).
        assert not (tmp_config_dir / "config.json.bak").exists()

    def test_save_no_bak_on_first_save(self, tmp_config_dir):
        """The first save (no existing config.json) must NOT create a .bak.

        There's nothing to back up — the file doesn't exist yet.
        The ``config_file.exists()`` guard in save() prevents trying
        to back up a non-existent file.
        """
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
        """The .bak must contain the EXACT bytes of the previous config.json.

        Not a re-serialization, not a normalized form — the raw bytes
        as they were on disk.  This is important because the user may
        have manually edited config.json with custom formatting
        (e.g. trailing newlines, specific indentation) that we want
        to preserve in the backup.
        """
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        # Write a config with unusual formatting (no indent, trailing newline).
        # note: set schema_version=_CURRENT_SCHEMA_VERSION
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

    def test_save_bak_round_trip_recovery(self, tmp_config_dir):
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
        config_file = tmp_config_dir / "config.json"
        bak = tmp_config_dir / "config.json.bak"

        # Step 1: write a "newer build" config with extra fields the
        # "older build" doesn't know about.  We can't actually add
        # unknown fields to the Config dataclass (it would reject
        # them), so we simulate by writing raw JSON with extra keys.
        # note: set secrets_migrated=True so that
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


# ──────────────────────────────────────────────────────────────────────────
# stale config.json with deprecated keys still loads (silent scrub)
# ──────────────────────────────────────────────────────────────────────────


class TestDeprecatedKeysSilentlyScrubbed:
    """GT-58: ``Config.load()`` must accept a stale ``config.json``
    written by an older app version that still carries the 7 deprecated
    fields which have been removed from the ``Config`` dataclass.

    The unknown-key filter in ``Config.load()`` silently drops these keys
    before ``cls(**data)`` constructs the Config instance. The v3 schema
    migration's scrub list is a defense-in-depth backstop. Either way,
    the load MUST NOT raise.
    """

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
        """A schema-v2 config with all 7 deprecated keys must load
        without falling into the corrupt-config fallback path. The
        non-deprecated settings (hotkey, autostart) must survive, and
        no ``config.json.corrupt-*`` backup must be created."""
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
        """A schema-v3 config that *still* carries deprecated keys
        is handled gracefully by the unknown-key filter — the keys are
        silently dropped (with a WARNING log) and the remaining fields
        load normally. No corrupt-config fallback path is triggered."""
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
