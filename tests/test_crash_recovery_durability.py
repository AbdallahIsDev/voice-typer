"""AB-44 regression: ``crash_recovery._save_sync`` uses ``durability=False``"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from voice_typer.server import crash_recovery
from voice_typer.server.crash_recovery import CrashRecovery


@pytest.fixture(autouse=True)
def _mock_recovery_owner_acl(monkeypatch):
    """Never run real icacls during crash-recovery tests on Windows hosts."""
    from unittest.mock import MagicMock

    monkeypatch.setattr(
        "voice_typer.server.config._enforce_windows_owner_only_acl",
        MagicMock(return_value=True),
    )


@pytest.fixture
def recovery_dir(tmp_path: Path) -> Path:
    """Return a clean config_dir for a CrashRecovery instance."""
    d = tmp_path / "recovery"
    d.mkdir(parents=True, exist_ok=True)
    return d


@pytest.fixture
def recovery(recovery_dir: Path) -> CrashRecovery:
    """Return a CrashRecovery instance whose save thread is stopped so"""
    cr = CrashRecovery(config_dir=recovery_dir)
    cr.shutdown()
    if cr._save_thread is not None:
        cr._save_thread.join(timeout=2.0)
    # Reset the dir_ensured flag so each test starts fresh.
    cr._dir_ensured = False
    return cr


class TestSaveSyncDurabilityFalse:
    """``_save_sync()`` (default call) passes ``durability=False`` to"""

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
        """``_save_sync(durability=True)`` is allowed (used by the atexit"""
        recovery.add("hello", pasted=False)
        with mock.patch.object(
            crash_recovery, "_secure_atomic_write", wraps=crash_recovery._secure_atomic_write
        ) as mock_atomic:
            recovery._save_sync(durability=True)
        mock_atomic.assert_called_once()
        assert mock_atomic.call_args.kwargs.get("durability") is True

    def test_save_sync_explicit_durability_false(self, recovery, recovery_dir):
        """``_enqueue_save``."""
        recovery.add("hello", pasted=False)
        with mock.patch.object(
            crash_recovery, "_secure_atomic_write", wraps=crash_recovery._secure_atomic_write
        ) as mock_atomic:
            recovery._save_sync(durability=False)
        mock_atomic.assert_called_once()
        assert mock_atomic.call_args.kwargs.get("durability") is False


class TestMkdirGatedByFlag:
    """chmod).  The first save does mkdir+chmod+write; subsequent saves"""

    def test_mkdir_called_on_first_save(self, recovery, recovery_dir):
        """First save: ``mkdir`` IS called (to ensure the parent dir"""
        # The parent dir already exists (recovery_dir fixture creates
        recovery.add("hello", pasted=False)
        recovery._dir_ensured = False
        mkdir_calls: list[str] = []
        original_mkdir = recovery._path.parent.mkdir.__func__

        def _tracking_mkdir(self_path, *args, **kwargs):
            mkdir_calls.append(str(self_path))
            return original_mkdir(self_path, *args, **kwargs)

        from pathlib import Path as _Path

        with mock.patch.object(_Path, "mkdir", _tracking_mkdir):
            recovery._save_sync()
        parent_calls = [c for c in mkdir_calls if c == str(recovery._path.parent)]
        assert len(parent_calls) >= 1, (
            "AB-44: first save MUST call mkdir on the parent dir "
            "(to ensure it exists).  Got calls: " + repr(mkdir_calls)
        )
        # After the first save, _dir_ensured should be True.
        assert recovery._dir_ensured is True

    def test_mkdir_skipped_on_subsequent_saves(self, recovery, recovery_dir):
        """Subsequent saves: ``mkdir`` is NOT called (gated by"""
        recovery.add("hello", pasted=False)
        # First save, populates _dir_ensured.
        recovery._save_sync()
        assert recovery._dir_ensured is True

        # Second save, mkdir should NOT be called.
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
        """``_dir_ensured``).  This was the pre-AB-44 behavior; AB-44"""
        import os as _os

        recovery.add("hello", pasted=False)
        # First save, populates _dir_ensured.
        recovery._save_sync()
        assert recovery._dir_ensured is True

        # Second save, chmod should NOT be called.
        recovery.add("world", pasted=False)
        with mock.patch("voice_typer.server.crash_recovery.os.chmod", wraps=_os.chmod) as mock_chmod:
            recovery._save_sync()
        parent_chmods = [
            call for call in mock_chmod.call_args_list if call.args and str(call.args[0]) == str(recovery._path.parent)
        ]
        assert parent_chmods == [], (
            "AB-44: subsequent saves MUST NOT call chmod (gated by _dir_ensured).  Got calls: " + repr(parent_chmods)
        )


class TestAtexitDelUseDurabilityTrue:
    """``_atexit_flush_all`` and ``__del__`` pass ``durability=True``"""

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
        cr.shutdown()
        if cr._save_thread is not None:
            cr._save_thread.join(timeout=2.0)
        cr.add("hello", pasted=False)
        # Capture _save_sync calls via a mock that wraps the real method.
        with mock.patch.object(CrashRecovery, "_save_sync", wraps=cr._save_sync) as mock_save:
            # Manually invoke __del__ (calling cr.__del__() directly is
            cr.__del__()
        # NOTE: depending on whether the atexit handler has fired,
        assert mock_save.called, "AB-44: __del__ MUST call _save_sync to persist the final GC save (got 0 calls)."
        for call in mock_save.call_args_list:
            assert call.kwargs.get("durability") is True, (
                "AB-44: __del__ MUST pass durability=True for the final GC save.  Got: " + repr(call.kwargs)
            )


class TestSaveStillWorks:
    """AB-44 doesn't break the save semantics, the file is still"""

    def test_save_persists_entries(self, recovery, recovery_dir):
        """``_save_sync()`` (durability=False) still writes the entries"""
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
        """Multiple saves with durability=False all persist correctly"""
        for i in range(5):
            recovery.add(f"entry-{i}", pasted=False)
            recovery._save_sync()

        recovery_file = recovery_dir / "recovery.json"
        data = json.loads(recovery_file.read_text(encoding="utf-8"))
        entries = data.get("entries", [])
        # All 5 entries should be present (the recovery file is
        assert len(entries) == 5
