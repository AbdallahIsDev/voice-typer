"""AB-44 regression: ``crash_recovery._save_sync`` uses ``durability=False``
for the per-dictation save path (and skips the redundant mkdir).

Pre-AB-44, ``_save_sync``:
- Called ``self._path.parent.mkdir(parents=True, exist_ok=True)`` on
  EVERY save (the ``_dir_ensured`` flag gated only the chmod).
- Called ``_secure_atomic_write`` with the default ``durability=True``
  (two fsyncs per save: file data + parent dir).

Under rapid dictation (5+ saves/sec when streaming is on), this was
5+ extra mkdir syscalls + 10+ fsync syscalls per second.

Post-AB-44:
- ``_dir_ensured`` now ALSO gates the mkdir (skip on subsequent saves).
- ``_save_sync`` accepts a ``durability`` kwarg (default ``False`` for
  the per-dictation path).
- ``_atexit_flush_all`` and ``__del__`` pass ``durability=True`` for
  the final shutdown save (one-time cost, durability guarantee
  matters there).
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from voice_typer.server import crash_recovery
from voice_typer.server.crash_recovery import CrashRecovery


@pytest.fixture
def recovery_dir(tmp_path: Path) -> Path:
    """Return a clean config_dir for a CrashRecovery instance."""
    d = tmp_path / "recovery"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def recovery(recovery_dir: Path) -> CrashRecovery:
    """Return a CrashRecovery instance whose save thread is stopped so
    tests can call ``_save_sync`` synchronously without races."""
    cr = CrashRecovery(config_dir=recovery_dir)
    cr.shutdown()
    if cr._save_thread is not None:
        cr._save_thread.join(timeout=2.0)
    # Reset the dir_ensured flag so each test starts fresh.
    cr._dir_ensured = False
    return cr


# ─── durability=False for per-dictation saves ─────────────────────────


class TestSaveSyncDurabilityFalse:
    """``_save_sync()`` (default call) passes ``durability=False`` to
    ``_secure_atomic_write``."""

    def test_save_sync_default_durability_false(self, recovery, recovery_dir):
        """Calling ``_save_sync()`` with no args uses durability=False."""
        recovery.add("hello", pasted=False)
        # Reset to force a save on the next _save_sync call.
        with mock.patch.object(
            crash_recovery, "_secure_atomic_write", wraps=crash_recovery._secure_atomic_write
        ) as mock_atomic:
            recovery._save_sync()
        mock_atomic.assert_called_once()
        assert mock_atomic.call_args.kwargs.get("durability") is False, (
            "AB-44: per-dictation _save_sync MUST pass durability=False "
            "(default).  Got: " + repr(mock_atomic.call_args.kwargs)
        )

    def test_save_sync_explicit_durability_true(self, recovery, recovery_dir):
        """``_save_sync(durability=True)`` is allowed (used by the atexit
        and __del__ final save paths)."""
        recovery.add("hello", pasted=False)
        with mock.patch.object(
            crash_recovery, "_secure_atomic_write", wraps=crash_recovery._secure_atomic_write
        ) as mock_atomic:
            recovery._save_sync(durability=True)
        mock_atomic.assert_called_once()
        assert mock_atomic.call_args.kwargs.get("durability") is True

    def test_save_sync_explicit_durability_false(self, recovery, recovery_dir):
        """``_save_sync(durability=False)`` is the explicit form of the
        default — used by the post-shutdown sync fallback in
        ``_enqueue_save``."""
        recovery.add("hello", pasted=False)
        with mock.patch.object(
            crash_recovery, "_secure_atomic_write", wraps=crash_recovery._secure_atomic_write
        ) as mock_atomic:
            recovery._save_sync(durability=False)
        mock_atomic.assert_called_once()
        assert mock_atomic.call_args.kwargs.get("durability") is False


# ─── mkdir gated by _dir_ensured ──────────────────────────────────────


class TestMkdirGatedByFlag:
    """The ``mkdir`` syscall is gated by ``_dir_ensured`` (same as the
    chmod).  The first save does mkdir+chmod+write; subsequent saves
    only do the atomic write."""

    def test_mkdir_called_on_first_save(self, recovery, recovery_dir):
        """First save: ``mkdir`` IS called (to ensure the parent dir
        exists).  Then ``_dir_ensured`` is set so subsequent saves skip
        both mkdir and chmod."""
        # The parent dir already exists (recovery_dir fixture creates
        # it), but ``_save_sync`` doesn't know that — it still calls
        # mkdir on the first save because ``_dir_ensured`` is False.
        recovery.add("hello", pasted=False)
        # test-setup note: ``recovery.add()`` above triggers a
        # synchronous ``_save_sync()`` (the worker was stopped in the
        # ``recovery`` fixture), which sets ``_dir_ensured = True``.
        # Reset the flag here so the NEXT ``_save_sync()`` call (inside
        # the mkdir patch below) is treated as the "first save" for the
        # purposes of this test — i.e. mkdir WILL be called.
        recovery._dir_ensured = False
        mkdir_calls: list[str] = []
        original_mkdir = recovery._path.parent.mkdir.__func__

        def _tracking_mkdir(self_path, *args, **kwargs):
            mkdir_calls.append(str(self_path))
            return original_mkdir(self_path, *args, **kwargs)

        from pathlib import Path as _Path

        with mock.patch.object(_Path, "mkdir", _tracking_mkdir):
            recovery._save_sync()
        # mkdir was called at least once on the parent dir.
        parent_calls = [c for c in mkdir_calls if c == str(recovery._path.parent)]
        assert len(parent_calls) >= 1, (
            "AB-44: first save MUST call mkdir on the parent dir "
            "(to ensure it exists).  Got calls: " + repr(mkdir_calls)
        )
        # After the first save, _dir_ensured should be True.
        assert recovery._dir_ensured is True

    def test_mkdir_skipped_on_subsequent_saves(self, recovery, recovery_dir):
        """Subsequent saves: ``mkdir`` is NOT called (gated by
        ``_dir_ensured``).  Only the atomic write remains."""
        recovery.add("hello", pasted=False)
        # First save — populates _dir_ensured.
        recovery._save_sync()
        assert recovery._dir_ensured is True

        # Second save — mkdir should NOT be called.
        recovery.add("world", pasted=False)
        mkdir_calls: list[str] = []
        original_mkdir = recovery._path.parent.mkdir.__func__

        def _tracking_mkdir(self_path, *args, **kwargs):
            mkdir_calls.append(str(self_path))
            return original_mkdir(self_path, *args, **kwargs)

        from pathlib import Path as _Path

        with mock.patch.object(_Path, "mkdir", _tracking_mkdir):
            recovery._save_sync()
        parent_calls = [c for c in mkdir_calls if c == str(recovery._path.parent)]
        assert parent_calls == [], (
            "AB-44: subsequent saves MUST NOT call mkdir (gated by _dir_ensured).  Got calls: " + repr(parent_calls)
        )

    def test_chmod_skipped_on_subsequent_saves(self, recovery, recovery_dir):
        """Subsequent saves: ``os.chmod`` is also NOT called (gated by
        ``_dir_ensured``).  This was the pre-AB-44 behavior; AB-44
        extends the same flag to mkdir."""
        import os as _os

        recovery.add("hello", pasted=False)
        # First save — populates _dir_ensured.
        recovery._save_sync()
        assert recovery._dir_ensured is True

        # Second save — chmod should NOT be called.
        recovery.add("world", pasted=False)
        with mock.patch("voice_typer.server.crash_recovery.os.chmod", wraps=_os.chmod) as mock_chmod:
            recovery._save_sync()
        parent_chmods = [
            call for call in mock_chmod.call_args_list if call.args and str(call.args[0]) == str(recovery._path.parent)
        ]
        assert parent_chmods == [], (
            "AB-44: subsequent saves MUST NOT call chmod (gated by _dir_ensured).  Got calls: " + repr(parent_chmods)
        )


# ─── atexit / __del__ use durability=True ─────────────────────────────


class TestAtexitDelUseDurabilityTrue:
    """``_atexit_flush_all`` and ``__del__`` pass ``durability=True``
    for the final shutdown save (one-time cost, durability guarantee
    matters there)."""

    def test_atexit_flush_all_uses_durability_true(self, recovery, recovery_dir):
        """The atexit handler calls ``_save_sync(durability=True)``."""
        recovery.add("hello", pasted=False)
        with mock.patch.object(CrashRecovery, "_save_sync", wraps=recovery._save_sync) as mock_save:
            crash_recovery._atexit_flush_all()
        # _save_sync was called at least once.
        assert mock_save.called
        # The atexit handler passes durability=True.
        for call in mock_save.call_args_list:
            assert call.kwargs.get("durability") is True, (
                "AB-44: _atexit_flush_all MUST pass durability=True for "
                "the final shutdown save.  Got: " + repr(call.kwargs)
            )

    def test_del_uses_durability_true(self, recovery_dir):
        """``__del__`` calls ``_save_sync(durability=True)``."""
        cr = CrashRecovery(config_dir=recovery_dir)
        # test-setup note: stop the background save worker BEFORE
        # adding an entry. Otherwise the worker's asynchronous
        # ``_save_sync()`` call (which uses the default
        # ``durability=False``) is captured by the patch below and
        # incorrectly fails the "all captured calls must have
        # durability=True" assertion. After ``shutdown()`` the worker
        # has exited; ``add()`` then triggers a synchronous
        # ``_save_sync()`` (default ``durability=False``) BEFORE the
        # patch is applied, so it isn't captured either. The only
        # captured call is the one from ``__del__``.
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
        cr.add("hello", pasted=False)
        # Capture _save_sync calls via a mock that wraps the real method.
        with mock.patch.object(CrashRecovery, "_save_sync", wraps=cr._save_sync) as mock_save:
            # Manually invoke __del__ (calling cr.__del__() directly is
            # safe — it's just a method; the GC will call it again later
            # but the body is idempotent via _final_save_done / the
            # _stopped flag).
            cr.__del__()
        # __del__ should have called _save_sync with durability=True.
        # NOTE: depending on whether the atexit handler has fired,
        # _save_sync may short-circuit (if _final_save_done is True) —
        # but if it was called, durability=True.
        assert mock_save.called, "AB-44: __del__ MUST call _save_sync to persist the final GC save (got 0 calls)."
        for call in mock_save.call_args_list:
            assert call.kwargs.get("durability") is True, (
                "AB-44: __del__ MUST pass durability=True for the final GC save.  Got: " + repr(call.kwargs)
            )


# ─── save still works end-to-end ──────────────────────────────────────


class TestSaveStillWorks:
    """AB-44 doesn't break the save semantics — the file is still
    written atomically with the correct content."""

    def test_save_persists_entries(self, recovery, recovery_dir):
        """``_save_sync()`` (durability=False) still writes the entries
        to the recovery file."""
        recovery.add("hello", pasted=False)
        recovery.add("world", pasted=True)
        recovery._save_sync()

        recovery_file = recovery_dir / "recovery.json"
        assert recovery_file.exists()
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        assert len(entries) == 2
        texts = [e.get("text") for e in entries]
        assert "hello" in texts
        assert "world" in texts

    def test_multiple_saves_persist_latest_state(self, recovery, recovery_dir):
        """Multiple saves with durability=False all persist correctly
        (the atomic rename provides the consistency guarantee, not
        fsync)."""
        for i in range(5):
            recovery.add(f"entry-{i}", pasted=False)
            recovery._save_sync()

        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        # All 5 entries should be present (the recovery file is
        # overwritten on each save with the full _entries deque).
        assert len(entries) == 5
