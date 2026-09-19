"""Regression tests for Group-4 (Session DE) findings in ``config.py``."""

from __future__ import annotations

import json
import logging
import sys
from contextlib import contextmanager
from unittest.mock import MagicMock

import pytest
from voice_typer.server import config as config_mod
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield


class TestMigratorFailureDoesNotBumpSchemaVersion:
    """DE-3: schema_version must NOT be bumped to _CURRENT_SCHEMA_VERSION"""

    def test_migrator_failure_leaves_schema_version_at_loaded_version(self, tmp_path, monkeypatch):
        """Simulate a migrator that raises and verify schema_version is"""
        config_file = tmp_path / "config.json"
        # Write an old-schema config so a migration is required.
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>", "model_size": "small.en"}))

        # Patch _MIGRATIONS so the v2 migrator raises.  v3 must NOT
        original_migrations = dict(config_mod._MIGRATIONS)
        call_count = {"v2": 0, "v3": 0}

        def _failing_v2(data):
            call_count["v2"] += 1
            raise RuntimeError("simulated migrator failure")

        def _v3_should_not_run(data):
            call_count["v3"] += 1
            return data

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)
        monkeypatch.setitem(config_mod._MIGRATIONS, 3, _v3_should_not_run)
        try:
            loaded = Config.load()
        finally:
            config_mod._MIGRATIONS.clear()
            config_mod._MIGRATIONS.update(original_migrations)

        # loaded_version (0).  schema_version must NOT be 3.
        assert loaded.schema_version == 0, (
            f"DE-3 regression: schema_version was bumped to {loaded.schema_version} "
            f"even though the v2 migrator raised, the config is now bricked in a "
            f"half-migrated state and the next launch will skip the failed migrator."
        )
        # v3 must NOT have run (the fix breaks on first failure).
        assert call_count["v2"] == 1, "v2 migrator should have run exactly once"
        assert call_count["v3"] == 0, (
            "v3 migrator must NOT run after v2 raised, later migrators expect "
            "v2-format data and would compound the corruption."
        )

    def test_successful_migration_bumps_schema_version(self, tmp_path, monkeypatch):
        """Sanity: when all migrators succeed, schema_version IS bumped"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>", "model_size": "small.en"}))

        loaded = Config.load()
        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION, (
            "Happy-path regression: schema_version should be bumped to "
            f"{_CURRENT_SCHEMA_VERSION} when all migrators succeed."
        )

    def test_migrator_failure_records_load_warning(self, tmp_path, monkeypatch, caplog):
        """DE-3 (companion): a failed migrator must surface a visible"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"schema_version": 0}))

        original_migrations = dict(config_mod._MIGRATIONS)

        def _failing_v2(data):
            raise RuntimeError("simulated migrator failure")

        monkeypatch.setitem(config_mod._MIGRATIONS, 2, _failing_v2)
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
                Config.load()
        finally:
            config_mod._MIGRATIONS.clear()
            config_mod._MIGRATIONS.update(original_migrations)

        migrator_errors = [
            r for r in caplog.records if "migrator v2 raised" in r.message and "RuntimeError" in r.message
        ]
        assert len(migrator_errors) >= 1, (
            f"DE-3: migrator failure not surfaced in logs: {[r.message for r in caplog.records]}"
        )


class TestLockCreationFailureIsFailClosed:
    """DE-25: when the lock file cannot be created, ``Config.save()``"""

    def test_save_returns_false_when_lock_creation_fails(self, tmp_path, monkeypatch):
        """Simulate lock file creation failure (os.open raises) and"""
        c = Config(hotkey="<f5>")

        # Patch _acquire_config_lock to raise OSError (simulating lock
        @contextmanager
        def _failing_lock(timeout=None):
            raise OSError(13, "Permission denied, cannot create lock file")
            yield  # pragma: no cover, unreachable

        monkeypatch.setattr(config_mod, "_acquire_config_lock", _failing_lock)

        result = c.save()
        assert result is False, (
            "DE-25 regression: save() returned True when the lock file could not be "
            "created, the fail-open path silently disabled mutual exclusion, allowing "
            "two processes to clobber each other's writes."
        )

    def test_lock_creation_failure_logs_at_warning(self, tmp_path, monkeypatch, caplog):
        """DE-25 (companion): the lock creation failure must be logged"""
        c = Config(hotkey="<f5>")

        @contextmanager
        def _failing_lock(timeout=None):
            raise OSError(13, "Permission denied")
            yield  # pragma: no cover

        monkeypatch.setattr(config_mod, "_acquire_config_lock", _failing_lock)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            c.save()

        # The fail-closed OSError propagates to save(), which logs at
        assert any("lock" in r.message.lower() or "save" in r.message.lower() for r in caplog.records), (
            f"DE-25: lock creation failure not surfaced in logs: {[r.message for r in caplog.records]}"
        )


class TestLoadAcquiresConfigLock:
    """DE-26: ``Config.load()`` must acquire the cross-process lock"""

    def test_load_calls_acquire_config_lock(self, tmp_path, monkeypatch):
        """Config.load() must call ``_acquire_config_lock`` before"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f5>"}))

        called = {"count": 0}

        @contextmanager
        def _tracking_lock(timeout=None):
            called["count"] += 1
            yield

        monkeypatch.setattr(config_mod, "_acquire_config_lock", _tracking_lock)

        loaded = Config.load()
        assert loaded.hotkey == "<f5>"
        assert called["count"] >= 1, (
            "DE-26 regression: Config.load() did not call _acquire_config_lock, "
            "without the lock, a concurrent save() in another process can write "
            "mid-read, causing a torn read of a half-written config.json."
        )

    def test_load_still_works_when_lock_is_noop(self, tmp_path, monkeypatch):
        """lock is a no-op (proves the lock acquisition doesn't break"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f9>", "autostart": False}))

        @contextmanager
        def _noop_lock(timeout=None):
            yield

        monkeypatch.setattr(config_mod, "_acquire_config_lock", _noop_lock)

        loaded = Config.load()
        assert loaded.hotkey == "<f9>"
        assert loaded.autostart is False


class TestPreMigrationBackupFailureLoggedAtWarning:
    """DE-27: the pre-migration backup failure was logged at DEBUG,"""

    def test_backup_failure_logged_at_warning(self, tmp_path, monkeypatch, caplog):
        """Simulate ``_secure_atomic_write`` failure on the pre-migration"""
        config_file = tmp_path / "config.json"
        # Old schema so the pre-migration backup path runs.
        config_file.write_text(json.dumps({"schema_version": 0, "hotkey": "<f5>"}))

        import voice_typer.server.config as _config_mod

        original_secure_write = _config_mod._secure_atomic_write

        def _failing_secure_write(path, content, *args, **kwargs):
            if "pre-migration-v" in str(path) and str(path).endswith(".bak"):
                raise OSError(28, "No space left on device (simulated)")
            return original_secure_write(path, content, *args, **kwargs)

        monkeypatch.setattr(_config_mod, "_secure_atomic_write", _failing_secure_write)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        backup_warnings = [
            r for r in caplog.records if "back up config.json" in r.message and "before migration" in r.message
        ]
        assert len(backup_warnings) >= 1, (
            "DE-27 regression: pre-migration backup failure was not logged at WARNING, "
            f"records: {[r.message for r in caplog.records]}"
        )
        assert backup_warnings[0].levelno >= logging.WARNING, (
            f"DE-27: backup failure logged at level {backup_warnings[0].levelno} "
            f"({backup_warnings[0].levelname}), expected WARNING or higher."
        )


class TestCredentialStoreExceptionsAreSanitised:
    """DE-28: credential_store exceptions were logged verbatim (``%s``"""

    def test_save_path_does_not_log_secret_from_credential_store_exception(self, tmp_path, monkeypatch, caplog):
        """Simulate credential_store raising an exception whose message"""
        c = Config(hotkey="<f5>")
        c.openai_api_key = "sk-secret-do-not-log-12345"

        fake_cs = MagicMock()
        fake_cs.KEYRING_REF_PREFIX = "keyring://"
        fake_cs.PROVIDER_TO_CONFIG_FIELD = {"openai": "openai_api_key"}
        secret_msg = "backend error echoing secret: sk-secret-do-not-log-12345"

        def _raise_with_secret():
            raise RuntimeError(secret_msg)

        fake_cs.is_keyring_available = _raise_with_secret

        # Inject the fake module, _save_locked does
        import voice_typer.server as server_pkg

        monkeypatch.setattr(server_pkg, "credential_store", fake_cs, raising=False)
        # Also patch the sys.modules entry so the ``from ... import``
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", fake_cs)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = c.save()

        assert result is True, (
            "DE-28: credential_store failure should NOT abort save(), the config "
            "must still be written with the current api_key values."
        )

        for rec in caplog.records:
            assert "sk-secret-do-not-log-12345" not in rec.message, (
                f"DE-28 regression: secret value leaked into log record: {rec.message!r}"
            )
        # And the exception TYPE should be logged (proves we didn't
        assert any("RuntimeError" in r.message for r in caplog.records), (
            "DE-28: exception type should still be logged so the operator can "
            f"diagnose the failure.  Records: {[r.message for r in caplog.records]}"
        )

    def test_load_path_does_not_log_secret_from_credential_store_exception(self, tmp_path, monkeypatch, caplog):
        """Simulate credential_store raising during load() and verify"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "schema_version": _CURRENT_SCHEMA_VERSION,
                    "openai_api_key": "sk-secret-load-67890",
                    "secrets_migrated": False,
                }
            )
        )

        fake_cs = MagicMock()
        fake_cs.KEYRING_REF_PREFIX = "keyring://"
        fake_cs.PROVIDER_TO_CONFIG_FIELD = {"openai": "openai_api_key"}
        secret_msg = "load_secret failed echoing: sk-secret-load-67890"
        fake_cs.migrate_secrets_to_keyring.side_effect = RuntimeError(secret_msg)

        import voice_typer.server as server_pkg

        monkeypatch.setattr(server_pkg, "credential_store", fake_cs, raising=False)
        monkeypatch.setitem(sys.modules, "voice_typer.server.credential_store", fake_cs)

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        for rec in caplog.records:
            assert "sk-secret-load-67890" not in rec.message, (
                f"DE-28 regression (load path): secret value leaked into log: {rec.message!r}"
            )


class TestCustomThemeValidatedOnLoad:
    """DE-29: ``custom_theme`` (a dict field) was validated by the IPC"""

    def test_malformed_custom_theme_resets_to_none(self, tmp_path, monkeypatch):
        """A custom_theme with the wrong shape (e.g. ``light`` is not"""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "custom_theme": {
                        "light": "not-a-dict",  # should be a dict of CSS vars
                        "dark": {},
                    }
                }
            )
        )

        c = Config.load()
        assert c.custom_theme is None, (
            f"DE-29 regression: malformed custom_theme was NOT reset to None on load, got: {c.custom_theme!r}"
        )

    def test_non_dict_custom_theme_resets_to_none(self, tmp_path, monkeypatch):
        """A custom_theme that is not a dict (e.g. a list) is reset to None."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"custom_theme": ["not", "a", "dict"]}))

        c = Config.load()
        assert c.custom_theme is None

    def test_valid_custom_theme_is_preserved(self, tmp_path, monkeypatch):
        """Sanity: a well-formed custom_theme is preserved on load."""
        valid_theme = {
            "light": {
                "--background": "#ffffff",
                "--foreground": "#000000",
                "--primary": "#0066cc",
                "--bg-subtle": "#f0f0f0",
                "--border": "#cccccc",
                "--text-muted": "#666666",
            },
            "dark": {
                "--background": "#000000",
                "--foreground": "#ffffff",
                "--primary": "#3399ff",
                "--bg-subtle": "#1a1a1a",
                "--border": "#333333",
                "--text-muted": "#999999",
            },
        }
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"custom_theme": valid_theme}))

        c = Config.load()
        assert c.custom_theme == valid_theme, (
            "DE-29 over-correction: a VALID custom_theme must be preserved on load, "
            f"not reset.  Got: {c.custom_theme!r}"
        )

    def test_none_custom_theme_is_preserved(self, tmp_path, monkeypatch):
        """Sanity: a None custom_theme (the default) is preserved."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"custom_theme": None}))

        c = Config.load()
        assert c.custom_theme is None

    def test_malformed_custom_theme_records_load_warning(self, tmp_path, monkeypatch):
        """warning to ``last_load_warnings`` so the user knows the field"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"custom_theme": {"light": "not-a-dict", "dark": {}}}))

        c = Config.load()
        warnings = getattr(c, "last_load_warnings", []) or []
        assert any("custom_theme" in w and "validation" in w.lower() for w in warnings), (
            f"DE-29: custom_theme validation failure not surfaced in last_load_warnings: {warnings}"
        )
