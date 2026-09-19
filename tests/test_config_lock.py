"""mutation lock when one is registered via :meth:`set_mutation_lock`."""

from __future__ import annotations

import contextlib
import json
import threading
from unittest.mock import patch

import pytest
from voice_typer.server.config import Config


class TestConfigMutationLock:
    """``Config.save()`` must acquire ``_mutation_lock`` when set."""

    def test_default_mutation_lock_is_none(self):
        """A freshly-constructed ``Config()`` has ``_mutation_lock = None``."""
        cfg = Config()
        assert cfg._mutation_lock is None

    def test_set_mutation_lock_stores_reference(self):
        """``set_mutation_lock`` stores the lock reference on the instance."""
        cfg = Config()
        lock = threading.RLock()
        cfg.set_mutation_lock(lock)
        assert cfg._mutation_lock is lock

    def test_save_acquires_lock_when_set(self, tmp_path, tmp_config_dir):
        """``Config.save()`` MUST acquire the mutation lock if one is set."""
        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        # Spy on _save_unlocked to capture whether the lock is held
        acquired_state: list[bool] = []
        original_unlocked = cfg._save_unlocked

        def spy():
            # If the lock is held by the current thread, acquire(False)
            got = lock.acquire(blocking=False)
            acquired_state.append(got)
            if got:
                lock.release()
            return original_unlocked()

        cfg._save_unlocked = spy  # type: ignore[method-assign]

        cfg.save()

        assert any(acquired_state), (
            "Config.save() did not acquire the mutation lock, "
            "the spy saw the lock as not-held.   regression: "
            "save() must wrap _save_unlocked() in 'with self._mutation_lock:' "
            "when _mutation_lock is set."
        )

    def test_save_works_without_lock(self, tmp_path, tmp_config_dir):
        """``Config.save()`` MUST work without a lock set (backward compat)."""
        cfg = Config()
        assert cfg._mutation_lock is None

        # Should not raise.  Returns True on success.
        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            mock_write.return_value = None
            assert cfg.save() is True

    def test_save_reentrant_lock_does_not_deadlock(self, tmp_path, tmp_config_dir):
        """``save()`` must not self-deadlock when called by a thread"""
        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        # Pre-acquire the lock in the current thread (simulating
        with lock, patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            mock_write.return_value = None
            assert cfg.save() is True

    def test_concurrent_saves_are_serialized(self, tmp_path, tmp_config_dir):
        """Two concurrent ``save()`` calls must not interleave their"""
        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        # Track concurrent entries into _save_unlocked.
        concurrent = 0
        max_concurrent = 0
        state_lock = threading.Lock()
        barrier = threading.Barrier(2, timeout=1.0)

        original_unlocked = cfg._save_unlocked

        def tracking_unlocked():
            nonlocal concurrent, max_concurrent
            with state_lock:
                concurrent += 1
                max_concurrent = max(max_concurrent, concurrent)
            try:
                with contextlib.suppress(threading.BrokenBarrierError):
                    barrier.wait(timeout=0.2)
                return original_unlocked()
            finally:
                with state_lock:
                    concurrent -= 1

        cfg._save_unlocked = tracking_unlocked  # type: ignore[method-assign]

        results: list[bool] = []
        threads: list[threading.Thread] = []

        def worker():
            results.append(cfg.save())

        for _ in range(2):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=5.0)
            assert not t.is_alive(), "save() thread did not finish, possible deadlock"

        # Both saves must succeed.
        assert results == [True, True]
        assert max_concurrent == 1, (
            f"Config.save() did not serialize concurrent calls, "
            f"max_concurrent={max_concurrent} (expected 1).  "
            f" regression: the mutation lock must ensure only "
            f"one thread is inside _save_unlocked() at a time."
        )

    def test_mutation_lock_is_classvar_not_dataclass_field(self, tmp_path, tmp_config_dir):
        """``_mutation_lock`` MUST be a ``ClassVar``, not a regular dataclass field."""
        import dataclasses

        # ClassVar annotation).  If it's marked _FIELD (regular field),
        if "_mutation_lock" in Config.__dataclass_fields__:
            field_obj = Config.__dataclass_fields__["_mutation_lock"]
            assert field_obj._field_type == dataclasses._FIELD_CLASSVAR, (
                f"_mutation_lock must be annotated ClassVar (got _field_type="
                f"{field_obj._field_type!r}).  Without ClassVar, asdict(self) "
                f"would try to serialize the RLock into config.json (would "
                f"crash json.dumps)."
            )

        # Saving after set_mutation_lock must NOT raise TypeError.
        cfg = Config()
        cfg.set_mutation_lock(threading.RLock())
        assert cfg.save() is True

        # The saved config.json must NOT contain a "_mutation_lock" key.
        config_file = tmp_path / "config.json"
        data = json.loads(config_file.read_text())
        assert "_mutation_lock" not in data, (
            "Config.save() leaked the _mutation_lock RLock into config.json, "
            "ClassVar annotation must exclude it from asdict() output."
        )

    def test_save_unlocked_bypasses_lock(self, tmp_path, tmp_config_dir):
        """``_save_unlocked`` is the lock-free entry point for tests."""
        cfg = Config()

        # Set a sentinel object as the lock; _save_unlocked must NOT
        lock_touched: list[str] = []

        class SentinelLock:
            def acquire(self, *args, **kwargs):
                lock_touched.append("acquire")
                return True

            def release(self):
                lock_touched.append("release")

            def __enter__(self):
                lock_touched.append("__enter__")
                return self

            def __exit__(self, *args):
                lock_touched.append("__exit__")
                return False

        cfg.set_mutation_lock(SentinelLock())  # type: ignore[arg-type]

        cfg._save_unlocked()

        assert lock_touched == [], (
            f"_save_unlocked() touched the mutation lock: {lock_touched}. "
            f"_save_unlocked must be the lock-free entry point, only save() "
            f"should acquire the lock."
        )

    def test_save_strict_also_acquires_lock(self, tmp_path, tmp_config_dir):
        """``save_strict`` MUST also acquire the lock (it delegates to save)."""
        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        acquired_state: list[bool] = []
        original_unlocked = cfg._save_unlocked

        def spy():
            got = lock.acquire(blocking=False)
            acquired_state.append(got)
            if got:
                lock.release()
            return original_unlocked()

        cfg._save_unlocked = spy  # type: ignore[method-assign]

        cfg.save_strict()

        assert any(acquired_state), (
            "Config.save_strict() did not acquire the mutation lock, "
            "save_strict() must delegate to save() (which acquires the lock) "
            "rather than calling _save_unlocked() directly."
        )

    def test_save_strict_raises_on_failure_even_with_lock(self, tmp_path, tmp_config_dir):
        """
        ``save_strict`` must still raise on failure when the lock is set.
        The lock acquisition must not swallow the failure path, if
        """
        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        # Mock _secure_atomic_write to raise OSError (simulating disk full).
        with (
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("disk full"),
            ),
            pytest.raises(RuntimeError, match="failed to persist config"),
        ):
            cfg.save_strict()

    def test_lock_can_be_cleared(self, tmp_path, tmp_config_dir):
        """``set_mutation_lock(None)`` should disable locking."""
        cfg = Config()
        cfg.set_mutation_lock(threading.RLock())
        assert cfg._mutation_lock is not None

        # Clear the lock.
        cfg._mutation_lock = None
        assert cfg._mutation_lock is None

        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            mock_write.return_value = None
            assert cfg.save() is True
