"""regression tests for ``Config.save()`` cross-process lock."""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    yield


class TestConfigSaveLock:
    """Config.save() must acquire config.json.lock."""

    def test_save_acquires_config_lock(self, tmp_path):
        """Config.save() must call ``_acquire_config_lock`` before"""
        from voice_typer.server import config as config_mod
        from voice_typer.server.config import Config

        c = Config(hotkey="<f5>")

        called = {"count": 0}

        from contextlib import contextmanager

        @contextmanager
        def _fake_lock(timeout=None):
            called["count"] += 1
            yield

        original = config_mod._acquire_config_lock
        config_mod._acquire_config_lock = _fake_lock
        try:
            result = c.save()
        finally:
            config_mod._acquire_config_lock = original

        assert result is True
        assert called["count"] == 1, (
            "CR-37 regression: Config.save() did not call "
            "_acquire_config_lock, without the lock, save() races "
            "with migrate_secrets_to_keyring and can overwrite the "
            "migration's keyring:// reference token with plaintext."
        )

    def test_save_blocks_when_lock_held(self, tmp_path):
        """When the lock is held by another caller, Config.save() must"""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        lock_file = tmp_path / "config.json.lock"
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        try:
            c = Config(hotkey="<f5>")
            result_holder: dict = {}

            def save_thread():
                result_holder["result"] = c.save()

            t = threading.Thread(target=save_thread, daemon=True)
            t.start()

            # Poll until save() has attempted and is blocked on the lock.
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if t.is_alive():
                    # Give it a moment to reach the lock, then confirm
                    if result_holder:
                        break
                    time.sleep(0.05)
                else:
                    break
            assert t.is_alive() and not result_holder, (
                "CR-37 regression: Config.save() did not block on the "
                "lock, it must serialize with migrate_secrets_to_keyring "
                "via config.json.lock to avoid overwriting the migration's "
                "keyring:// reference token with plaintext."
            )

            # Release the lock, save() should now proceed.
            with contextlib.suppress(OSError):
                lock_fd.close()
            t.join(timeout=5.0)

            assert not t.is_alive(), (
                "Config.save() thread did not complete within 5s of "
                "releasing the lock, the lock release did not unblock it."
            )
            assert result_holder.get("result") is True, (
                f"Config.save() returned {result_holder.get('result')} after the lock was released, expected True."
            )
        finally:
            with contextlib.suppress(OSError):
                lock_fd.close()

    def test_save_returns_false_on_lock_timeout(self, tmp_path, monkeypatch):
        """When the lock cannot be acquired within the timeout,"""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        # Use a short timeout for the test (default is 5s, too long
        monkeypatch.setattr("voice_typer.server.config._CONFIG_LOCK_TIMEOUT_SECONDS", 0.5)

        lock_file = tmp_path / "config.json.lock"
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        try:
            c = Config(hotkey="<f5>")
            start = time.monotonic()
            result = c.save()
            elapsed = time.monotonic() - start

            assert result is False, (
                f"CR-37 regression: Config.save() returned {result}, "
                "expected False when the lock cannot be acquired within "
                "the timeout. Without this guard, the IPC thread could "
                "hang indefinitely on a long-held lock."
            )
            # Elapsed should be at least the timeout (0.5s).
            assert elapsed >= 0.4, (
                f"Config.save() returned too quickly ({elapsed:.2f}s), "
                "expected to wait at least the timeout duration (0.5s) "
                "before failing with False."
            )
        finally:
            with contextlib.suppress(OSError):
                lock_fd.close()

    def test_save_serializes_with_migrate(self, tmp_path, monkeypatch):
        """Config.save() and migrate_secrets_to_keyring must serialize"""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "openai_api_key": "sk-test-secret-cr37",
                    "secrets_migrated": False,
                }
            )
        )

        # Mock keyring as available with an in-memory store.
        store: dict[tuple[str, str], str] = {}
        fake_keyring = MagicMock()
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

        errors: list = []

        def run_migrate():
            try:
                credential_store.migrate_secrets_to_keyring()
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        def run_save():
            try:
                c = Config.load()
                c.hotkey = "<f6>"
                ok = c.save()
                if not ok:
                    errors.append(RuntimeError("save() returned False"))
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        t1 = threading.Thread(target=run_migrate, daemon=True)
        t2 = threading.Thread(target=run_save, daemon=True)
        t1.start()
        t2.start()
        t1.join(timeout=15.0)
        t2.join(timeout=15.0)

        assert not errors, f"concurrent migrate + save operations failed: {errors}"

        # The plaintext key should NEVER be on disk after both complete.
        data = json.loads(config_file.read_text())
        assert data.get("openai_api_key") != "sk-test-secret-cr37", (
            "CR-37 regression: plaintext API key is on disk after "
            "concurrent migrate + save, the lock did not serialize "
            "them properly. The migration's keyring:// reference was "
            f"overwritten with plaintext. Data: {data}"
        )
