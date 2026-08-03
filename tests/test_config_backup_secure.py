"""FR-23 / FR-24 regression tests: config.json backups use the secure
read/write helpers (O_NOFOLLOW + atomic os.replace + fsync + 0o600),
not ``shutil.copy2`` / ``Path.read_bytes()`` / ``Path.write_bytes()``
(which follow symlinks and are non-atomic).

FR-23: ``Config._backup_before_downgrade`` (invoked when an older
build loads a newer-version config.json) previously used
``shutil.copy2`` — a local attacker who replaces config.json with a
symlink to ~/.bashrc between the user's downgrade-launch and the
copy2 call gets ~/.bashrc content copied into the .bak (info
disclosure via the .bak file).

FR-24: ``Config._save_unlocked`` previously read the existing
config.json via ``config_file.read_bytes()`` (follows symlinks) for
the backup block. A local attacker who replaces config.json with a
symlink to ~/.bashrc between saves causes Config.save() to copy
~/.bashrc content into config.json.bak (info disclosure).

Both fixes route the backup READ through ``_secure_read_text`` (POSIX
``O_NOFOLLOW`` + inode re-verify) and the backup WRITE through
``_secure_atomic_write`` (atomic ``os.replace`` + fsync + 0o600).

Platform note: validated ON LINUX (sandbox). The O_NOFOLLOW / inode
re-verify guards are POSIX-only; the Windows path in
``_secure_read_text`` uses reparse-point detection instead. Symlink
attack tests are skipped on Windows (no POSIX symlinks).
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import pytest
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config


@pytest.fixture
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    yield tmp_path


# _backup_before_downgrade uses secure helpers ──────────────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only symlink test")
class TestBackupBeforeDowngradeSecure:
    """FR-23: ``_backup_before_downgrade`` must use ``_secure_read_text``
    + ``_secure_atomic_write`` (not ``shutil.copy2``)."""

    def test_downgrade_backup_created_with_correct_content(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """A downgrade (loading a newer-version config.json) must
        create a ``config.json.v{N}*.bak`` with the EXACT on-disk bytes
        of the original (not the in-memory filtered view).

        XE-10-1: the filename now embeds a timestamp + PID + ns suffix
        (``config.json.v{N}-{ts}-{pid}-{ns}.bak``) so two backup events
        never collide — the test globs for any matching filename.
        """
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

        # backup filename now has a timestamp suffix — glob
        # for any matching filename.
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
        # (including the future field) — not the in-memory filtered
        # view that drops unknown keys.
        bak_data = json.loads(bak_path.read_text())
        assert bak_data["future_field_xyz"] == "future-value-that-should-be-preserved-in-bak", (
            "FR-23: downgrade backup lost the future field — the "
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
        """FR-23: if config.json is a symlink, ``_secure_read_text``
        must refuse to follow it (POSIX ``O_NOFOLLOW``). The backup
        must NOT be created with the symlink target's content — info
        disclosure prevention.

        Setup: config.json is a symlink to ~/.bashrc (simulated as a
        separate file in tmp_path). The downgrade backup must fail
        cleanly (no .bak file with .bashrc content) and the load must
        still proceed (best-effort).
        """
        # Set up: create a "secret" file that the attacker wants to
        # exfiltrate via the .bak.
        secret_file = _isolated_config_dir / "attacker_secret.txt"
        secret_content = "ATTACKER_SECRET_VALUE_should_not_appear_in_bak"
        secret_file.write_text(secret_content)

        # Replace config.json with a symlink to the secret file.
        config_file = _isolated_config_dir / "config.json"
        # First create a legit config so the load doesn't bail early
        # (before _backup_before_downgrade is called).
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": _CURRENT_SCHEMA_VERSION + 1,
                    "hotkey": "<caps_lock>",
                }
            )
        )

        # Now replace with a symlink to the secret file. The load()
        # code path that calls _backup_before_downgrade happens AFTER
        # _read_raw_json (which uses _secure_read_text and would
        # already refuse the symlink). To exercise _backup_before_downgrade
        # in isolation, we call it directly.
        config_file.unlink()
        config_file.symlink_to(secret_file)

        # Reset Config._load_warnings by calling _backup_before_downgrade
        # directly (bypasses the early _read_raw_json symlink refusal).
        data: dict = {}
        # Use a higher version to trigger the backup branch.
        Config._backup_before_downgrade(config_file, _CURRENT_SCHEMA_VERSION + 1, data)

        # The backup file MUST NOT have been created (or if it was,
        # it must NOT contain the secret content). _secure_read_text
        # raises OSError on POSIX when the path is a symlink, so the
        # except branch fires and no .bak is written.
        bak_path = _isolated_config_dir / f"config.json.v{_CURRENT_SCHEMA_VERSION + 1}.bak"
        if bak_path.exists():
            # If a .bak was created somehow, it must NOT contain the
            # secret content.
            bak_text = bak_path.read_text()
            assert secret_content not in bak_text, (
                "FR-23 regression: _backup_before_downgrade copied the "
                "symlink target's content into the .bak file — info "
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
        """FR-23: the backup write must be atomic — if the write is
        interrupted mid-flight, no partial .bak should be visible on
        disk. ``_secure_atomic_write`` writes to a temp file and
        ``os.replace``s it into place, so the .bak either appears in
        full or doesn't appear at all.

        We verify the atomicity contract by inspecting the .bak after
        a successful backup — it must be a complete, valid JSON file
        (no truncation, no partial writes).

        XE-10-1: the filename now embeds a timestamp + PID + ns suffix
        (``config.json.v{N}-{ts}-{pid}-{ns}.bak``) — the test globs for
        any matching filename.
        """
        config_file = _isolated_config_dir / "config.json"
        original_content = {
            "schema_version": _CURRENT_SCHEMA_VERSION + 2,
            "hotkey": "<caps_lock>",
            "language": "fr",
            "autostart": True,
        }
        config_file.write_text(json.dumps(original_content, indent=2))

        Config.load()

        # backup filename now has a timestamp suffix — glob
        # for any matching filename.
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
    """FR-24: ``Config._save_unlocked`` must read the existing
    config.json via ``_secure_read_text`` (O_NOFOLLOW), not
    ``config_file.read_bytes()`` (follows symlinks)."""

    def test_save_does_not_poison_bak_via_symlinked_source(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-24: if config.json is a symlink to ~/.bashrc (simulated
        as a separate file), ``Config.save()`` must NOT copy the
        symlink target's content into config.json.bak. The backup
        READ uses ``_secure_read_text`` (O_NOFOLLOW) which raises
        OSError on a symlink — the except branch fires and no .bak
        is written. The actual config.json write still proceeds (via
        ``_secure_atomic_write`` which uses os.replace and replaces
        the SYMLINK itself, not the target)."""
        # Start with a legit config.json so Config.load() succeeds.
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>", "openai_api_key": ""}))

        # Load the config.
        cfg = Config.load()

        # Now simulate the attacker replacing config.json with a
        # symlink to a secret file (between load and save).
        secret_file = _isolated_config_dir / "attacker_secret.txt"
        secret_content = "ATTACKER_SECRET_should_not_leak_into_bak"
        secret_file.write_text(secret_content)

        # IMPORTANT: we need _last_saved_bytes to NOT match the new
        # content so the backup block actually runs. Set it to None.
        object.__setattr__(cfg, "_last_saved_bytes", None)

        # Replace config.json with a symlink to the secret file.
        config_file.unlink()
        config_file.symlink_to(secret_file)

        # Trigger a save (which runs the backup block first).
        # The backup block reads config_file via _secure_read_text,
        # which raises OSError on the symlink. The except branch
        # fires and no .bak is written. The actual save proceeds.
        cfg.save()

        # The .bak must NOT exist (or if it does, must NOT contain
        # the secret content).
        bak_path = _isolated_config_dir / "config.json.bak"
        if bak_path.exists():
            bak_text = bak_path.read_text()
            assert secret_content not in bak_text, (
                "FR-24 regression: Config.save() copied the symlink "
                "target's content into config.json.bak — info "
                "disclosure via .bak. The backup READ must use "
                "_secure_read_text (O_NOFOLLOW) so symlinks are refused."
            )

    def test_save_backup_uses_secure_read(
        self,
        _isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """FR-24: verify the backup block reads via _secure_read_text
        (not Path.read_bytes). We patch _secure_read_text to spy on
        the call and ensure it's invoked during the backup block."""
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

        # Modify and save — should trigger backup block (since
        # _last_saved_bytes won't match).
        cfg.hotkey = "<f8>"
        object.__setattr__(cfg, "_last_saved_bytes", None)
        cfg.save()

        # The backup block must have called _secure_read_text with
        # config_file (NOT Path.read_bytes).
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
        """FR-24: a normal save (non-symlinked source) must still
        create config.json.bak with the previous content. This
        verifies the fix doesn't break the happy path.

        Note: ``Config.load()`` runs ``migrate_secrets_to_keyring``
        which itself writes a new config.json (with the diagnostic
        ``secrets_migrated`` flag + the merged defaults). So by the
        time we call ``cfg.save()``, the on-disk config is the
        post-migrate state — the .bak preserves THAT, not the
        original pre-load file. We capture the post-load on-disk
        content and assert the .bak matches it.
        """
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>", "language": "en"}))

        cfg = Config.load()

        # Capture the on-disk state AFTER load (migrate may have
        # rewritten it with the merged defaults + secrets_migrated
        # flag).
        pre_save_on_disk = config_file.read_text()

        # Modify and save — should create .bak with the pre-save
        # on-disk content.
        cfg.hotkey = "<f9>"
        object.__setattr__(cfg, "_last_saved_bytes", None)
        cfg.save()

        bak_path = _isolated_config_dir / "config.json.bak"
        assert bak_path.exists(), (
            "FR-24: Config.save() did not create config.json.bak — the backup block should fire when content changes."
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
        """FR-24: the .bak file must be chmod'd to 0o600 on POSIX
        (matching the config.json perms invariant). The fix routes
        the .bak write through _secure_atomic_write + explicit chmod,
        mirroring the production config.json perms contract."""
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


# pre-migration backup uses secure helpers + ────
#   unique filename + retention cap                                       ────


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only symlink test")
class TestPreMigrationBackupSecure:
    """``_backup_before_migration`` must use
    ``_secure_read_text`` + ``_secure_atomic_write`` (not
    ``shutil.copy2``).

    the filename must embed a timestamp + PID + microsecond
    fraction so a downgrade-then-upgrade cycle (or two app instances
    launched in parallel during a downgrade, or back-to-back calls in
    the same process) does not silently overwrite the first backup.
    The retained pre-migration backups are capped at 3 (oldest
    pruned) so the directory does not grow unbounded.
    """

    def test_pre_migration_backup_uses_secure_read(
        self,
        _isolated_config_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """``_backup_before_migration`` must read the source
        config.json via ``_secure_read_text`` (O_NOFOLLOW + inode
        re-verify), not ``shutil.copy2`` (which follows symlinks on
        both source AND destination)."""
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
        """the backup WRITE must go through
        ``_secure_atomic_write`` (atomic os.replace + fsync + 0o600),
        not ``shutil.copy2`` (non-atomic, no fsync, 0o644 window)."""
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
        """the backup filename must embed a Unix timestamp,
        PID, and microsecond fraction so two backup events never
        collide (downgrade-then-upgrade cycle, parallel app launches,
        or back-to-back calls in the same process).

        Pattern: ``config.json.pre-migration-v{N}-{ts}-{pid}-{us}.bak``
        """
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
                f"backup PID {pid} does not match current PID {_os.getpid()} — the PID suffix must use os.getpid()."
            )
            assert 0 <= us < 1_000_000, f"microsecond fraction {us} out of range [0, 1_000_000)."

    def test_pre_migration_backup_rejects_symlinked_source(
        self,
        _isolated_config_dir: Path,
    ) -> None:
        """if config.json is a symlink, the secure read must
        refuse it (POSIX O_NOFOLLOW). The backup must NOT contain the
        symlink target's content — info disclosure prevention."""
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
        """after the 4th pre-migration backup is created, the
        oldest must be pruned so only the 3 newest remain."""
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
