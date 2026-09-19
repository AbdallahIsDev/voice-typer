"""FR-1 regression test: Config.load() must not clobber the keyring-migration deferral state."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import credential_store
from voice_typer.server.config import Config


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir: Path) -> Path:
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    credential_store._reset_keyring_cache()
    yield tmp_config_dir
    credential_store._reset_keyring_cache()


@pytest.fixture
def mock_keyring_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock keyring as unavailable (fail backend / D-Bus missing)."""
    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = MagicMock(name="fail")
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (False, "fail", "keyring unavailable (headless mock)"),
    )


@pytest.fixture
def mock_keyring_available(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Mock keyring as available with an in-memory store."""
    store: dict[tuple[str, str], str] = {}

    fake_keyring = MagicMock()
    fake_keyring.get_keyring.return_value = MagicMock(name="FakeKeyring")
    fake_keyring.set_password.side_effect = lambda s, u, v: store.__setitem__((s, u), v)
    fake_keyring.get_password.side_effect = lambda s, u: store.get((s, u))
    fake_keyring.delete_password.side_effect = lambda s, u: store.pop((s, u), None)
    monkeypatch.setitem(sys.modules, "keyring", fake_keyring)
    fail_module = MagicMock()
    fail_module.Keyring = type("FailKeyring", (), {})
    monkeypatch.setitem(sys.modules, "keyring.backends.fail", fail_module)

    credential_store._reset_keyring_cache()
    monkeypatch.setattr(
        credential_store,
        "_probe_keyring",
        lambda: (True, "FakeKeyring", None),
    )
    return store


class TestSecretsMigrationDeferralPreserved:
    """FR-1: ``Config.load()`` must NOT clobber the deferral."""

    def test_deferral_preserved_when_keyring_unavailable(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        """When keyring is unavailable and real plaintext is skipped,"""
        del mock_keyring_unavailable  # fixture sets up the mock

        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-keep-me-plaintext",
                    "hotkey": "<caps_lock>",
                }
            )
        )

        cfg = Config.load()
        assert cfg.secrets_migrated is False, (
            "FR-1 regression: Config.load() clobbered the "
            "deferral state. After migrate deferred (keyring unavailable "
            "+ real plaintext skipped), Config.secrets_migrated must be "
            "False so the next save() does NOT persist True to disk "
            "(which would prevent the migration from re-running when "
            "keyring becomes available)."
        )
        assert cfg.openai_api_key == "sk-keep-me-plaintext"

    def test_save_after_deferral_does_not_persist_secrets_migrated_true(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-1 end-to-end: after a deferred migration, calling"""
        del mock_keyring_unavailable

        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-keep-me-plaintext",
                    "hotkey": "<caps_lock>",
                }
            )
        )

        cfg = Config.load()
        assert cfg.secrets_migrated is False

        cfg.hotkey = "<f5>"
        cfg.save()

        # Re-read the on-disk config and verify the flag is still
        on_disk = json.loads(config_file.read_text())
        assert on_disk.get("secrets_migrated", False) is False, (
            "FR-1 regression: Config.save() persisted "
            "secrets_migrated=True to disk after a deferred migration. "
            "The next launch will see True and skip migration entirely, "
            "plaintext API keys stay in config.json forever."
        )
        # NOTE: the  diagnostic flag

    def test_migration_reruns_after_keyring_becomes_available(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-1 full end-to-end: simulate the user installing"""
        del mock_keyring_unavailable

        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-migrate-me-after-keyring-installed",
                    "hotkey": "<caps_lock>",
                }
            )
        )

        cfg1 = Config.load()
        assert cfg1.secrets_migrated is False, (
            "FR-1 phase 1: Config.secrets_migrated should be False "
            "(deferred) but is True, the deferral was "
            "clobbered by Config.load()."
        )
        assert cfg1.openai_api_key == "sk-migrate-me-after-keyring-installed"
        # Simulate a trivial save (unrelated setting change).
        cfg1.hotkey = "<f6>"
        cfg1.save()

        credential_store._reset_keyring_cache()
        # Override the unavailable fixture's monkeypatch for Phase 2.
        original_probe = credential_store._probe_keyring
        credential_store._probe_keyring = lambda: (True, "FakeKeyring", None)
        try:
            cfg2 = Config.load()
        finally:
            credential_store._probe_keyring = original_probe

        assert cfg2.secrets_migrated is True, (
            "FR-1 phase 2: after keyring became available, the "
            "deferred migration did NOT re-run. Config.secrets_migrated "
            "should be True (migration succeeded) but is False, the "
            "plaintext API key is still in config.json."
        )
        # In-memory Config still has the real value (loaded from keyring).
        assert cfg2.openai_api_key == "sk-migrate-me-after-keyring-installed"
        # On-disk config has the reference token (not plaintext).
        on_disk = json.loads(config_file.read_text())
        assert on_disk["openai_api_key"] == "keyring://openai", (
            f"FR-1 phase 2: expected reference token 'keyring://openai' "
            f"in config.json, got {on_disk.get('openai_api_key')!r}"
        )
        # Diagnostic flag cleared (migrate pops it on successful
        assert "secrets_migrated_keyring_was_unavailable" not in on_disk


class TestNonDeferredMigrationStillSetsFlag:
    """existing behavior). The fix only changes the deferred path."""

    def test_flag_true_when_migration_succeeds(
        self,
        mock_keyring_available: dict,
        _isolated_config_dir: Path,
    ) -> None:
        """When keyring is available and a plaintext secret is migrated,"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-migrate-me"}))

        cfg = Config.load()
        assert cfg.secrets_migrated is True
        assert cfg.openai_api_key == "sk-migrate-me"

    def test_flag_true_when_no_secrets_to_migrate(
        self,
        mock_keyring_available: dict,
        _isolated_config_dir: Path,
    ) -> None:
        """When there are no plaintext secrets (all empty), migrate"""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()
        assert cfg.secrets_migrated is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only 0o600 check")
class TestFilePermissionsPreserved:
    """FR-1: the file permission invariant (0o600 on POSIX) is"""

    def test_config_json_stays_0600_across_deferral_flow(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        del mock_keyring_unavailable
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-keep-me"}))
        os.chmod(config_file, 0o600)

        cfg = Config.load()
        cfg.hotkey = "<f7>"  # trivial change
        cfg.save()

        mode = 0o777 & os.stat(config_file).st_mode
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"
