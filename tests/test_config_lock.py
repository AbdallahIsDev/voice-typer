"""Regression test for CR-25: ``Config.save()`` must acquire the
mutation lock when one is registered via :meth:`set_mutation_lock`.

Context
-------
``VoiceTyperApp`` owns a ``self._config_mutation_lock = threading.RLock()``
that ``service.apply_config`` and ``onboarding_apply`` acquire for the
full read-modify-save sequence (so two simultaneous ``set_config`` IPC
calls don't interleave attribute writes and produce a torn
``config.json``).

The lock was added in RACE-011 but was NOT retrofitted to the 10+ other
``config.save()`` call sites (``settings_controller``,
``hotkey_dispatcher``, ``model_manager``, ``recorder._persist_mic``,
``startup_sequence``, etc.).  Without the lock, a mic-fallback save on
a background thread can interleave with an in-flight ``apply_config``
IPC call: the mic-fallback's ``asdict(self)`` reads every field, and if
``apply_config`` is mid-setattr when that snapshot is taken, the
snapshot is torn — half the fields from the IPC request, half from the
prior state.  The mic-fallback save then persists that torn snapshot to
disk, overwriting the user's intended change.

The fix (CR-25) moves the lock acquisition INSIDE ``Config.save()`` by
giving ``Config`` a class-level ``_mutation_lock`` reference (set via
:meth:`set_mutation_lock` after :meth:`Config.load`).  When set,
:meth:`save` wraps the actual save work (:meth:`_save_unlocked`) in the
lock so the lock is impossible to forget at any call site.  When not
set (e.g. tests that construct ``Config()`` directly without an app),
saves proceed without locking — preserving backward compat.

These tests pin the new contract:

* ``save()`` MUST acquire the lock if one is set.
* ``save()`` MUST still work without a lock (backward compat).
* The lock is an ``RLock``, so reentrant acquisition is allowed (a
  thread that already holds the lock can call ``save()`` without
  self-deadlock — required for the ``apply_config`` path which acquires
  the lock and then calls ``save()``).
* ``_mutation_lock`` MUST be a ``ClassVar`` so ``asdict(self)`` does
  not try to serialize it into ``config.json`` (an ``RLock`` is not
  JSON-serializable and would crash save()).
"""

from __future__ import annotations

import json
import threading
from unittest.mock import patch

import pytest
from voice_typer.server.config import Config


class TestConfigMutationLock:
    """CR-25: ``Config.save()`` must acquire ``_mutation_lock`` when set."""

    def test_default_mutation_lock_is_none(self):
        """A freshly-constructed ``Config()`` has ``_mutation_lock = None``.

        This is the backward-compat path — tests that construct
        ``Config()`` directly (without an app) must still work, and the
        save must proceed without trying to acquire a lock that doesn't
        exist.
        """
        cfg = Config()
        assert cfg._mutation_lock is None

    def test_set_mutation_lock_stores_reference(self):
        """``set_mutation_lock`` stores the lock reference on the instance.

        The reference is stored as an instance attribute (shadowing the
        ``ClassVar`` default of ``None``) so each ``Config`` instance
        can have its own lock — multiple ``VoiceTyperApp`` instances in
        the same process (rare but possible in tests) don't share a
        single global lock.
        """
        cfg = Config()
        lock = threading.RLock()
        cfg.set_mutation_lock(lock)
        assert cfg._mutation_lock is lock

    def test_save_acquires_lock_when_set(self, tmp_path, monkeypatch):
        """``Config.save()`` MUST acquire the mutation lock if one is set.

        This is the core CR-25 regression test.  We spy on
        ``_save_unlocked`` and assert that the lock IS held when the
        spy runs — if ``save()`` forgot to acquire the lock, the spy
        would see the lock as not-held and the assertion would fail.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        # Spy on _save_unlocked to capture whether the lock is held
        # when it runs.  We use a non-blocking acquire to TEST whether
        # the lock is held by the current thread (RLock supports
        # reentrant acquisition, so acquire(blocking=False) on a thread
        # that already holds the lock returns True and increments the
        # ownership count — we then release to balance).
        acquired_state: list[bool] = []
        original_unlocked = cfg._save_unlocked

        def spy():
            # If the lock is held by the current thread, acquire(False)
            # returns True (reentrant).  If NOT held, returns False.
            # Either way we release to keep the count balanced (if we
            # acquired, release; if we didn't, release is a no-op via
            # the if-check).
            got = lock.acquire(blocking=False)
            acquired_state.append(got)
            if got:
                lock.release()
            return original_unlocked()

        cfg._save_unlocked = spy  # type: ignore[method-assign]

        cfg.save()

        assert any(acquired_state), (
            "Config.save() did not acquire the mutation lock — "
            "the spy saw the lock as not-held.  CR-25 regression: "
            "save() must wrap _save_unlocked() in 'with self._mutation_lock:' "
            "when _mutation_lock is set."
        )

    def test_save_works_without_lock(self, tmp_path, monkeypatch):
        """``Config.save()`` MUST work without a lock set (backward compat).

        Tests that construct ``Config()`` directly (without an app)
        must still be able to save — the save path must not raise
        ``AttributeError`` or similar when ``_mutation_lock is None``.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        cfg = Config()
        assert cfg._mutation_lock is None

        # Should not raise.  Returns True on success.
        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            mock_write.return_value = None
            assert cfg.save() is True

    def test_save_reentrant_lock_does_not_deadlock(self, tmp_path, monkeypatch):
        """``save()`` must not self-deadlock when called by a thread
        that already holds the lock.

        This is the ``apply_config`` / ``onboarding_apply`` code path:
        those handlers acquire ``app._config_mutation_lock`` for the
        full setattr + side-effects + save sequence.  If ``save()``
        tried to acquire the lock again with a non-reentrant ``Lock``,
        the thread would block forever waiting for itself to release
        the lock.  Using ``RLock`` (reentrant) lets the same thread
        acquire the lock multiple times without deadlock.

        We pin this by acquiring the lock in the test thread, then
        calling ``save()``.  If ``save()`` used a non-reentrant lock,
        this test would hang (and pytest's --timeout would kill it).
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        lock = threading.RLock()
        cfg = Config()
        cfg.set_mutation_lock(lock)

        # Pre-acquire the lock in the current thread (simulating
        # apply_config having acquired it before calling save()).
        with lock:
            # save() must not block here — RLock allows reentrant
            # acquisition by the same thread.
            with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
                mock_write.return_value = None
                assert cfg.save() is True

    def test_concurrent_saves_are_serialized(self, tmp_path, monkeypatch):
        """Two concurrent ``save()`` calls must not interleave their
        ``asdict(self)`` + ``_secure_atomic_write`` sequences.

        CR-25 regression: without the lock, two threads could
        simultaneously call ``save()``, and the ``asdict(self)`` of
        one could race with attribute writes from the other — producing
        a torn snapshot that gets persisted to disk.  With the lock,
        the two saves are serialized: the second save's ``asdict``
        sees the state left by the first save's writes (or the state
        before, but not a torn mix).

        We test this by making ``_save_unlocked`` block on a barrier
        inside the lock — if the lock is NOT held, both threads would
        reach the barrier simultaneously and the barrier would
        unblock; if the lock IS held, only one thread enters
        ``_save_unlocked`` at a time and the barrier never reaches 2
        (the second thread is blocked on the lock).
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

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
                # If both threads reach here simultaneously, the
                # barrier unblocks (returns without raising).  If only
                # one thread is here (because the other is blocked on
                # the mutation lock), the barrier times out and raises
                # BrokenBarrierError — which is the EXPECTED case (the
                # lock is serializing the saves).
                try:
                    barrier.wait(timeout=0.2)
                except threading.BrokenBarrierError:
                    # Expected: the second thread is blocked on the
                    # mutation lock, so only one thread reached the
                    # barrier.  This proves the lock is being held.
                    pass
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
            assert not t.is_alive(), "save() thread did not finish — possible deadlock"

        # Both saves must succeed.
        assert results == [True, True]
        # max_concurrent must be 1 — the lock serialized the saves so
        # only one thread was inside _save_unlocked at a time.
        assert max_concurrent == 1, (
            f"Config.save() did not serialize concurrent calls — "
            f"max_concurrent={max_concurrent} (expected 1).  "
            f"CR-25 regression: the mutation lock must ensure only "
            f"one thread is inside _save_unlocked() at a time."
        )

    def test_mutation_lock_is_classvar_not_dataclass_field(self, tmp_path, monkeypatch):
        """``_mutation_lock`` MUST be a ``ClassVar``, not a regular dataclass field.

        If it were a regular dataclass field, ``asdict(self)`` (used by
        ``_save_unlocked``) would include it in the dict passed to
        ``json.dumps`` — and ``json.dumps`` would raise ``TypeError``
        because ``threading.RLock`` is not JSON-serializable.  This
        would crash every save after ``set_mutation_lock`` was called.

        ``ClassVar`` annotations are excluded from ``asdict()`` output
        (the dataclass decorator marks them with ``_FIELD_CLASSVAR`` so
        ``asdict()`` knows to skip them).  We test the contract end-to-end
        by saving a Config with a registered lock and asserting the
        resulting ``config.json`` does NOT contain a ``_mutation_lock`` key.
        """
        import dataclasses

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        # _mutation_lock MUST be marked as _FIELD_CLASSVAR (i.e. a
        # ClassVar annotation).  If it's marked _FIELD (regular field),
        # asdict() would include it and json.dumps would crash.
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
            "Config.save() leaked the _mutation_lock RLock into config.json — "
            "ClassVar annotation must exclude it from asdict() output."
        )

    def test_save_unlocked_bypasses_lock(self, tmp_path, monkeypatch):
        """``_save_unlocked`` is the lock-free entry point for tests.

        Direct callers of ``_save_unlocked`` (rare — only tests that
        want to bypass the lock to inspect mid-save state) must NOT
        acquire the lock.  This lets tests that already hold the lock
        call ``_save_unlocked`` directly without self-deadlock, and
        lets tests that don't want any locking bypass it entirely.

        We can't subclass ``threading.RLock`` (it's a C-level factory
        function, not a Python class), so we use a sentinel object
        that records every method call.  If ``_save_unlocked`` touches
        the lock at all, the sentinel records it and the assertion
        fails.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        cfg = Config()

        # Set a sentinel object as the lock; _save_unlocked must NOT
        # touch it (i.e. must not call acquire/release/__enter__/__exit__).
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
            f"_save_unlocked must be the lock-free entry point — only save() "
            f"should acquire the lock."
        )

    def test_save_strict_also_acquires_lock(self, tmp_path, monkeypatch):
        """``save_strict`` MUST also acquire the lock (it delegates to save).

        ``save_strict`` is the raising variant of ``save`` — it wraps
        ``save()`` and raises ``RuntimeError`` if ``save()`` returned
        ``False``.  Since it delegates to ``save()``, the lock
        acquisition happens automatically.  This test pins that
        contract so a future refactor that makes ``save_strict`` call
        ``_save_unlocked`` directly doesn't silently bypass the lock.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

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

        # save_strict returns None on success (raises RuntimeError on failure).
        cfg.save_strict()

        assert any(acquired_state), (
            "Config.save_strict() did not acquire the mutation lock — "
            "save_strict() must delegate to save() (which acquires the lock) "
            "rather than calling _save_unlocked() directly."
        )

    def test_save_strict_raises_on_failure_even_with_lock(self, tmp_path, monkeypatch):
        """``save_strict`` must still raise on failure when the lock is set.

        The lock acquisition must not swallow the failure path — if
        ``_save_unlocked`` returns ``False`` (e.g. disk full), the
        lock is released and ``save_strict`` raises ``RuntimeError``.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

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

    def test_lock_can_be_cleared(self, tmp_path, monkeypatch):
        """``set_mutation_lock(None)`` should disable locking.

        This isn't currently a documented use case, but it's a natural
        extension: if a caller wants to disable locking after enabling
        it (e.g. a test that wants to bypass the lock after setup),
        setting ``_mutation_lock = None`` should restore the
        no-locking behavior.  We pin this so future refactors don't
        break the symmetric set/clear contract.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)

        cfg = Config()
        cfg.set_mutation_lock(threading.RLock())
        assert cfg._mutation_lock is not None

        # Clear the lock.
        cfg._mutation_lock = None
        assert cfg._mutation_lock is None

        # save() must now proceed without locking.
        with patch("voice_typer.server.config._secure_atomic_write") as mock_write:
            mock_write.return_value = None
            assert cfg.save() is True
