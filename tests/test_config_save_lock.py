"""CR-37: regression tests for ``Config.save()`` cross-process lock.

The previous ``Config.save()`` implementation went straight to
``_secure_atomic_write`` without acquiring ``config.json.lock``. The
``migrate_secrets_to_keyring`` function DOES acquire that lock for the
read-migrate-write sequence. The two operations could race:

  (a) ``Config.load()`` reads plaintext key into memory.
  (b) migration acquires lock, reads same plaintext, writes
      ``keyring://openai`` reference + ``secrets_migrated=True`` to disk.
  (c) ``Config.save()`` (no lock) writes the FULL in-memory Config —
      including the original plaintext ``openai_api_key`` and
      ``secrets_migrated=False`` — overwriting the migration's
      reference token. Plaintext key is back on disk.

The fix acquires ``config.json.lock`` inside ``Config.save()`` using
the same ``fcntl.flock`` (POSIX) / ``msvcrt.locking`` (Windows)
pattern as ``migrate_secrets_to_keyring``. Non-blocking acquire with
a short timeout (default 5 seconds) to avoid hanging the IPC thread.

Tests:

1. ``test_save_acquires_config_lock`` — Config.save() must call
   ``_acquire_config_lock`` before writing (verified via mock).

2. ``test_save_blocks_when_lock_held`` — when the lock is held by
   another caller (simulating a running migration), ``Config.save()``
   must block until the lock is released.

3. ``test_save_returns_false_on_lock_timeout`` — when the lock cannot
   be acquired within the timeout, ``Config.save()`` returns False
   (rather than hanging the IPC thread indefinitely).

4. ``test_save_serializes_with_migrate`` — running migrate and save
   concurrently, the on-disk key must never be the plaintext (the
   lock serializes them so save cannot overwrite the migration's
   keyring:// reference with plaintext).
"""

from __future__ import annotations

import contextlib
import json
import sys
import threading
import time
from unittest.mock import MagicMock

import pytest


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path, monkeypatch):
    """Point ``_config_dir`` at a tmp_path so each test gets a clean slate."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    yield


class TestConfigSaveLock:
    """CR-37: Config.save() must acquire config.json.lock."""

    def test_save_acquires_config_lock(self, tmp_path):
        """Config.save() must call ``_acquire_config_lock`` before
        writing to config.json. Verified by patching the lock helper
        with a mock and asserting it was called."""
        from voice_typer.server import config as config_mod
        from voice_typer.server.config import Config

        c = Config(hotkey="<f5>")

        # Patch _acquire_config_lock with a context manager that
        # tracks invocation but otherwise does nothing (no real lock).
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
            "_acquire_config_lock — without the lock, save() races "
            "with migrate_secrets_to_keyring and can overwrite the "
            "migration's keyring:// reference token with plaintext."
        )

    def test_save_blocks_when_lock_held(self, tmp_path):
        """When the lock is held by another caller, Config.save() must
        block until the lock is released (it must NOT proceed without
        the lock and overwrite a concurrent migration)."""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        # Acquire the lock from outside Config.save() — simulating a
        # running migrate_secrets_to_keyring holding the lock.
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
            # The save thread should still be alive (blocked on lock).
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                if t.is_alive():
                    # Give it a moment to reach the lock, then confirm
                    # it's still blocked by checking result_holder.
                    if result_holder:
                        break
                    time.sleep(0.05)
                else:
                    break
            # save() should still be blocked (thread alive, no result yet).
            assert t.is_alive() and not result_holder, (
                "CR-37 regression: Config.save() did not block on the "
                "lock — it must serialize with migrate_secrets_to_keyring "
                "via config.json.lock to avoid overwriting the migration's "
                "keyring:// reference token with plaintext."
            )

            # Release the lock — save() should now proceed.
            with contextlib.suppress(OSError):
                lock_fd.close()
            t.join(timeout=5.0)

            assert not t.is_alive(), (
                "Config.save() thread did not complete within 5s of "
                "releasing the lock — the lock release did not unblock it."
            )
            assert result_holder.get("result") is True, (
                f"Config.save() returned {result_holder.get('result')} after the lock was released — expected True."
            )
        finally:
            with contextlib.suppress(OSError):
                lock_fd.close()

    def test_save_returns_false_on_lock_timeout(self, tmp_path, monkeypatch):
        """When the lock cannot be acquired within the timeout,
        Config.save() returns False (rather than hanging the IPC
        thread indefinitely)."""
        from voice_typer.server import credential_store
        from voice_typer.server.config import Config

        # Use a short timeout for the test (default is 5s — too long
        # for a test).
        monkeypatch.setattr("voice_typer.server.config._CONFIG_LOCK_TIMEOUT_SECONDS", 0.5)

        lock_file = tmp_path / "config.json.lock"
        lock_fd = credential_store._acquire_migration_lock(lock_file)
        try:
            c = Config(hotkey="<f5>")
            start = time.monotonic()
            result = c.save()
            elapsed = time.monotonic() - start

            # save() returned False due to timeout.
            assert result is False, (
                f"CR-37 regression: Config.save() returned {result} — "
                "expected False when the lock cannot be acquired within "
                "the timeout. Without this guard, the IPC thread could "
                "hang indefinitely on a long-held lock."
            )
            # Elapsed should be at least the timeout (0.5s).
            assert elapsed >= 0.4, (
                f"Config.save() returned too quickly ({elapsed:.2f}s) — "
                "expected to wait at least the timeout duration (0.5s) "
                "before failing with False."
            )
        finally:
            with contextlib.suppress(OSError):
                lock_fd.close()

    def test_save_serializes_with_migrate(self, tmp_path, monkeypatch):
        """Config.save() and migrate_secrets_to_keyring must serialize
        via the lock — they cannot interleave their read-modify-write
        cycles.

        Without the lock, save() could overwrite the migration's
        keyring:// reference token with the in-memory plaintext key —
        silently reverting the migration. The plaintext key would
        persist on disk despite the migration appearing to complete.
        """
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
        # With the lock: save() either runs before migrate (writes
        # plaintext + secrets_migrated=False, then migrate replaces
        # with keyring:// + secrets_migrated=True) or after migrate
        # (Config.load() resolves keyring:// to real value, save()
        # routes through credential_store which re-stores it and writes
        # keyring:// reference).
        #
        # Without the lock: save() could run AFTER migrate's write but
        # with a stale in-memory Config (plaintext key), overwriting
        # the migration's keyring:// reference with plaintext.
        data = json.loads(config_file.read_text())
        assert data.get("openai_api_key") != "sk-test-secret-cr37", (
            "CR-37 regression: plaintext API key is on disk after "
            "concurrent migrate + save — the lock did not serialize "
            "them properly. The migration's keyring:// reference was "
            f"overwritten with plaintext. Data: {data}"
        )
