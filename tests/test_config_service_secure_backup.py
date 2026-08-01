"""Regression test: ``ConfigMutationMixin.reset_config_to_defaults`` must
NOT use ``shutil.copy2`` to back up the current config.

The previous implementation called ``shutil.copy2(config_file, backup_path)``
which is:

  * non-atomic (file-by-file copy — an interrupted copy leaves a partial
    ``config.json.bak`` that gives a false sense of recovery),
  * symlink-following on BOTH source and destination (a local attacker
    who replaces ``config.json`` with a symlink to ``~/.bashrc`` between
    the user's "Reset to defaults" click and the ``copy2`` call gets
    ``~/.bashrc`` content copied into the .bak — info disclosure via
    the .bak file), and
  * has no ``fsync`` (the .bak may not be durable across power loss).

This is the same vulnerability class as the one already fixed in
``config.py:_backup_before_migration`` (which now uses
``_secure_read_text`` + ``_secure_atomic_write``).  This test pins the
fix: ``shutil.copy2`` must NOT be called by ``reset_config_to_defaults``,
the backup file must still be created, and its bytes must match the
original config.json byte-for-byte.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_service(tmp_path: Path):
    """Build a real VoiceTyperService backed by a tmp config dir."""
    from voice_typer.server import config as cfg_mod
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    app = MagicMock()
    # Real Config dataclass so setattr actually persists values (a
    # MagicMock would silently accept any setattr, hiding regressions).
    cfg = Config()
    app.config = cfg
    # Real lock — MagicMock would silently accept the `with` statement
    # but not actually serialize, hiding concurrency bugs.
    app._config_mutation_lock = threading.Lock()
    app.tray.notify = MagicMock()
    svc = VoiceTyperService(app)

    mp = pytest.MonkeyPatch()
    mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
    return svc, mp, cfg


def test_reset_config_to_defaults_does_not_use_shutil_copy2(tmp_path: Path) -> None:
    """``reset_config_to_defaults`` must NOT call ``shutil.copy2``.

    Spies on ``shutil.copy2`` (replaces it with a wrapper that records
    the call but still calls the real function so the backup still
    succeeds if the implementation regresses).  Asserts the spy was
    never invoked.
    """
    svc, mp, _ = _build_service(tmp_path)
    try:
        # Seed a real config.json with user settings.
        original_config = {
            "hotkey": "<f5>",
            "model_size": "medium.en",
            "language": "fr",
            "auto_punctuation": True,
        }
        (tmp_path / "config.json").write_text(json.dumps(original_config))

        # Spy on shutil.copy2 — record calls but still call through so
        # a regressing implementation doesn't fail for the wrong reason.
        import shutil

        copy2_calls: list[tuple] = []
        real_copy2 = shutil.copy2

        def spying_copy2(*args, **kwargs):
            copy2_calls.append((args, kwargs))
            return real_copy2(*args, **kwargs)

        mp.setattr(shutil, "copy2", spying_copy2)

        result = svc.reset_config_to_defaults()

        assert result["success"] is True, f"reset_config_to_defaults must succeed — got: {result}"
        assert copy2_calls == [], (
            "reset_config_to_defaults must NOT call shutil.copy2 — "
            "it is non-atomic, symlink-following, and lacks fsync. "
            "Use _secure_read_text + _secure_atomic_write instead. "
            f"Got {len(copy2_calls)} call(s): {copy2_calls}"
        )
    finally:
        mp.undo()


def test_reset_config_to_defaults_backup_matches_original_bytes(tmp_path: Path) -> None:
    """The backup file must contain the exact bytes of the original config.

    ``_secure_read_text`` + ``_secure_atomic_write`` round-trips the
    file content as a UTF-8 string.  This test pins that the backup
    file's bytes match the original config.json bytes byte-for-byte
    (so a forensic recovery via ``cp config.json.bak config.json``
    restores the exact pre-reset state).
    """
    svc, mp, _ = _build_service(tmp_path)
    try:
        original_config = {
            "hotkey": "<f5>",
            "model_size": "medium.en",
            "language": "fr",
            "auto_punctuation": True,
            "openai_api_key": "sk-test-123",
        }
        # Use ensure_ascii=False + indent=2 so non-ASCII round-trips
        # through _secure_read_text + _secure_atomic_write cleanly.
        original_bytes = json.dumps(original_config, indent=2, ensure_ascii=False).encode("utf-8")
        (tmp_path / "config.json").write_bytes(original_bytes)

        result = svc.reset_config_to_defaults()

        assert result["success"] is True
        backup_path = Path(result["backup_path"])
        assert backup_path.exists(), "config.json.bak must be created"
        backup_bytes = backup_path.read_bytes()
        assert backup_bytes == original_bytes, (
            "config.json.bak must contain the EXACT bytes of the original "
            f"config.json (forensic recovery contract). Got {len(backup_bytes)} "
            f"bytes, expected {len(original_bytes)} bytes."
        )
        # Round-trip parse check — backup must be valid JSON.
        parsed = json.loads(backup_bytes)
        assert parsed["hotkey"] == "<f5>"
        assert parsed["model_size"] == "medium.en"
        assert parsed["language"] == "fr"
        assert parsed["auto_punctuation"] is True
        assert parsed["openai_api_key"] == "sk-test-123"
    finally:
        mp.undo()


def test_reset_config_to_defaults_uses_secure_helpers(tmp_path: Path) -> None:
    """The backup path must route through the shared secure helpers.

    Asserts that ``_secure_read_text`` and ``_secure_atomic_write``
    (from ``voice_typer.server.secure_file_io``) are both invoked at
    least once during ``reset_config_to_defaults``.  This pins the
    architectural choice — future refactors that swap in a different
    helper will trip this guard and force the author to re-evaluate
    the security properties.
    """
    svc, mp, _ = _build_service(tmp_path)
    try:
        (tmp_path / "config.json").write_text(json.dumps({"hotkey": "<f5>", "language": "fr"}))

        # Spy on both secure helpers — count calls but call through.
        import voice_typer.server.secure_file_io as sio_mod

        read_calls: list[tuple] = []
        write_calls: list[tuple] = []
        real_read = sio_mod._secure_read_text
        real_write = sio_mod._secure_atomic_write

        def spying_read(*args, **kwargs):
            read_calls.append((args, kwargs))
            return real_read(*args, **kwargs)

        def spying_write(*args, **kwargs):
            write_calls.append((args, kwargs))
            return real_write(*args, **kwargs)

        mp.setattr(sio_mod, "_secure_read_text", spying_read)
        mp.setattr(sio_mod, "_secure_atomic_write", spying_write)

        result = svc.reset_config_to_defaults()

        assert result["success"] is True
        assert len(read_calls) >= 1, (
            "reset_config_to_defaults must call _secure_read_text to read "
            "the current config.json (O_NOFOLLOW + inode re-verification)."
        )
        assert len(write_calls) >= 1, (
            "reset_config_to_defaults must call _secure_atomic_write to "
            "persist the backup (atomic os.replace + fsync + 0o600)."
        )
        # The write call's first positional arg should be the backup path.
        first_write_args = write_calls[0][0]
        backup_path_arg = str(first_write_args[0])
        assert backup_path_arg.endswith("config.json.bak"), (
            f"_secure_atomic_write must target config.json.bak — got: {backup_path_arg}"
        )
    finally:
        mp.undo()


def test_reset_config_to_defaults_backup_survives_symlink_config(tmp_path: Path) -> None:
    """Defense-in-depth: if config.json is a symlink, the secure read
    must NOT follow it (POSIX ``O_NOFOLLOW``).

    On POSIX, ``_secure_read_text`` raises ``OSError`` (``ELOOP``)
    when the path is a symlink.  The reset must surface this as a
    backup failure (returning ``{"success": False, ...}``) rather than
    silently following the symlink into an arbitrary file.  On Windows
    the behavior is similar (reparse-point check).

    This test creates a config.json that is a symlink to a sibling
    file containing sensitive content, then verifies the reset does
    NOT copy that sensitive content into the .bak.
    """
    import os

    if os.name == "nt":
        pytest.skip("symlink-POSIX-O_NOFOLLOW test is POSIX-only")

    svc, mp, _ = _build_service(tmp_path)
    try:
        # Plant a "sensitive" file outside config.json.
        secret_file = tmp_path / "secret.txt"
        secret_content = "THIS MUST NOT END UP IN config.json.bak"
        secret_file.write_text(secret_content)

        # Plant a symlink config.json → secret.txt.
        config_link = tmp_path / "config.json"
        os.symlink(secret_file, config_link)
        assert config_link.is_symlink()

        result = svc.reset_config_to_defaults()

        # The backup must NOT contain the secret content.  Either the
        # backup failed (returned success=False, no .bak created) OR
        # the .bak was created but does NOT contain the secret string.
        if result["success"]:
            # If somehow a backup was created, it must not contain
            # the secret file's content.
            backup_path = Path(result.get("backup_path", ""))
            if backup_path.exists():
                backup_text = backup_path.read_text()
                assert secret_content not in backup_text, (
                    "config.json.bak must NOT contain content from the "
                    "symlink target — _secure_read_text must refuse to "
                    "follow symlinks (POSIX O_NOFOLLOW)."
                )
        else:
            # This is the expected path on POSIX: _secure_read_text
            # raises OSError(ELOOP), the except clause returns
            # success=False.
            assert "back up" in result.get("message", "").lower() or "backup" in result.get("message", "").lower(), (
                "Backup failure message should mention the backup operation."
            )
    finally:
        mp.undo()
