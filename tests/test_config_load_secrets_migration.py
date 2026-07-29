"""FR-1 regression test: Config.load() must not clobber the XZ-SEC-04
keyring-migration deferral state.

The XZ-SEC-04 fix in ``credential_store.migrate_secrets_to_keyring``
defers setting ``secrets_migrated = True`` on disk when keyring is
unavailable AND real plaintext was skipped. The next ``Config.load()``
must observe the deferred state (secrets_migrated absent / False) so
the next ``Config.save()`` does NOT persist ``secrets_migrated = True``
to disk — otherwise the deferred migration never re-runs and plaintext
API keys stay in config.json forever (defeating RW-01 encryption-at-
rest).

Pre-fix, ``Config.load()`` unconditionally set
``data["secrets_migrated"] = True`` in the in-memory dict after the
migrate call, clobbering the deferral. This file pins the post-fix
contract.

Platform note: validated ON LINUX (sandbox). Windows/macOS host
validation pending — the keyring-unavailable mock simulates the
headless-Linux case (no gnome-keyring-daemon / D-Bus); the same
deferral logic applies on macOS / Windows when their native backends
are unavailable, but those paths are not exercised here.
"""

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
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate.

    Also resets the keyring availability cache so each test re-probes.
    """
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    credential_store._reset_keyring_cache()
    yield tmp_path
    credential_store._reset_keyring_cache()


@pytest.fixture
def mock_keyring_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock keyring as unavailable (fail backend / D-Bus missing).

    This simulates the common headless-Linux-without-gnome-keyring case
    where the XZ-SEC-04 deferral is supposed to kick in.
    """
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
    """Mock keyring as available with an in-memory store.

    Used to simulate the user installing gnome-keyring-daemon after a
    period of running headless — the next Config.load() should observe
    the deferred state and re-run the migration.
    """
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


class TestFR1SecretsMigrationDeferralPreserved:
    """FR-1: ``Config.load()`` must NOT clobber the XZ-SEC-04 deferral."""

    def test_deferral_preserved_when_keyring_unavailable(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        """When keyring is unavailable and real plaintext is skipped,
        ``migrate_secrets_to_keyring`` defers (does NOT set
        ``secrets_migrated=True`` on disk). The subsequent
        ``Config.load()`` must observe the deferred state — the
        constructed ``Config`` instance's ``secrets_migrated`` field
        must be ``False`` (NOT ``True``)."""
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
        # FR-1: the in-memory flag must reflect the on-disk deferred
        # state (False), NOT the pre-fix unconditional True.
        assert cfg.secrets_migrated is False, (
            "FR-1 regression: Config.load() clobbered the XZ-SEC-04 "
            "deferral state. After migrate deferred (keyring unavailable "
            "+ real plaintext skipped), Config.secrets_migrated must be "
            "False so the next save() does NOT persist True to disk "
            "(which would prevent the migration from re-running when "
            "keyring becomes available)."
        )
        # Plaintext value should still be in the in-memory Config (so
        # cloud_engines / llm_polish can use it at runtime).
        assert cfg.openai_api_key == "sk-keep-me-plaintext"

    def test_save_after_deferral_does_not_persist_secrets_migrated_true(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-1 end-to-end: after a deferred migration, calling
        ``Config.save()`` (e.g. from a trivial set_config call) must
        NOT persist ``secrets_migrated = True`` to disk. Otherwise the
        next launch sees True and skips migration entirely."""
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

        # Simulate a trivial set_config call (e.g. user toggles an
        # unrelated setting) that triggers a save. Pre-fix, this would
        # persist secrets_migrated=True via asdict(self).
        cfg.hotkey = "<f5>"
        cfg.save()

        # Re-read the on-disk config and verify the flag is still
        # NOT set (or False).
        on_disk = json.loads(config_file.read_text())
        assert on_disk.get("secrets_migrated", False) is False, (
            "FR-1 regression: Config.save() persisted "
            "secrets_migrated=True to disk after a deferred migration. "
            "The next launch will see True and skip migration entirely — "
            "plaintext API keys stay in config.json forever."
        )
        # NOTE: the XZ-SEC-04 diagnostic flag
        # (``secrets_migrated_keyring_was_unavailable``) is NOT a
        # declared Config dataclass field, so ``Config.save()`` (which
        # writes ``asdict(self)``) drops it. That's acceptable — the
        # flag's purpose was to record the deferral state at migrate
        # time; once ``Config.save()`` writes ``secrets_migrated=False``
        # (the FR-1 fix), the False value itself communicates "migration
        # has not completed" and the next load re-runs migrate.

    def test_migration_reruns_after_keyring_becomes_available(
        self,
        mock_keyring_unavailable: None,
        _isolated_config_dir: Path,
    ) -> None:
        """FR-1 full end-to-end: simulate the user installing
        gnome-keyring-daemon after running headless. The next
        ``Config.load()`` must re-run the migration, move the plaintext
        to keyring, and replace the plaintext with a reference token
        in config.json.

        Phase 1 (headless): ``mock_keyring_unavailable`` fixture makes
        ``is_keyring_available()`` return False. Migration defers.

        Phase 2 (keyring installed): we manually override
        ``_probe_keyring`` inside the test body to return True (since
        we can't apply both fixtures at once — pytest would let the
        later fixture's monkeypatch win). Migration re-runs.
        """
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

        # Phase 1: headless (keyring unavailable via fixture) — migration
        # defers.
        cfg1 = Config.load()
        assert cfg1.secrets_migrated is False, (
            "FR-1 phase 1: Config.secrets_migrated should be False "
            "(deferred) but is True — the XZ-SEC-04 deferral was "
            "clobbered by Config.load()."
        )
        assert cfg1.openai_api_key == "sk-migrate-me-after-keyring-installed"
        # Simulate a trivial save (unrelated setting change).
        cfg1.hotkey = "<f6>"
        cfg1.save()

        # Phase 2: user installs gnome-keyring-daemon, restarts the
        # app. Re-probe keyring — now it's available.
        credential_store._reset_keyring_cache()
        # Override the unavailable fixture's monkeypatch for Phase 2.
        # We can't easily flip the monkeypatch mid-test, so we patch
        # ``_probe_keyring`` directly and restore it in the ``finally``
        # block below.
        original_probe = credential_store._probe_keyring
        credential_store._probe_keyring = lambda: (True, "FakeKeyring", None)
        try:
            cfg2 = Config.load()
        finally:
            credential_store._probe_keyring = original_probe

        # FR-1: migration re-ran, plaintext moved to keyring, reference
        # token in config.json.
        assert cfg2.secrets_migrated is True, (
            "FR-1 phase 2: after keyring became available, the "
            "deferred migration did NOT re-run. Config.secrets_migrated "
            "should be True (migration succeeded) but is False — the "
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
        # migration).
        assert "secrets_migrated_keyring_was_unavailable" not in on_disk


class TestFR1NonDeferredMigrationStillSetsFlag:
    """FR-1: when migration actually succeeds (or there's nothing to
    migrate), the in-memory flag must still be ``True`` (preserving the
    existing behavior). The fix only changes the deferred path."""

    def test_flag_true_when_migration_succeeds(
        self,
        mock_keyring_available: dict,
        _isolated_config_dir: Path,
    ) -> None:
        """When keyring is available and a plaintext secret is migrated,
        the on-disk flag is set to True by migrate; Config.load() must
        observe True (NOT clobber it with False)."""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"openai_api_key": "sk-migrate-me"}))

        cfg = Config.load()
        assert cfg.secrets_migrated is True
        # The in-memory Config carries the real value (resolved from
        # keyring via the reference token).
        assert cfg.openai_api_key == "sk-migrate-me"

    def test_flag_true_when_no_secrets_to_migrate(
        self,
        mock_keyring_available: dict,
        _isolated_config_dir: Path,
    ) -> None:
        """When there are no plaintext secrets (all empty), migrate
        sets the on-disk flag to True (nothing to retry); Config.load()
        must observe True."""
        config_file = _isolated_config_dir / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<caps_lock>"}))

        cfg = Config.load()
        assert cfg.secrets_migrated is True


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only 0o600 check")
class TestFR1FilePermissionsPreserved:
    """FR-1: the file permission invariant (0o600 on POSIX) is
    preserved across the deferred-then-resumed migration flow."""

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
