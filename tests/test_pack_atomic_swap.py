"""§8.3: Atomic swap (Windows + POSIX variants)."""

from __future__ import annotations

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import offline_pack


class TestPosixAtomicSwap:
    """POSIX: ``os.replace`` is atomic; no worker stop/start needed."""

    def test_posix_replaces_current_with_new(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        (new_dir / "worker").write_bytes(b"new-worker")
        cur_dir.mkdir()
        (cur_dir / "worker").write_bytes(b"old-worker")
        trash = offline_pack.atomic_swap_offline_pack(new_dir, cur_dir)
        # POSIX returns the trash path too (best-effort cleanup
        assert trash is not None
        assert str(trash).endswith("current.trash")
        # The new content is now at cur_dir.
        assert (cur_dir / "worker").read_bytes() == b"new-worker"
        assert not new_dir.exists()
        assert not Path(trash).exists()

    def test_posix_does_not_call_stop_or_start(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        stop = MagicMock()
        start = MagicMock()
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        cur_dir.mkdir()
        offline_pack.atomic_swap_offline_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        stop.assert_not_called()
        start.assert_not_called()

    def test_posix_second_rename_failure_restores_previous_pack(self, tmp_path: Path, monkeypatch):
        """POSIX rollback: when the SECOND rename (new → current) fails"""
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        (new_dir / "worker").write_bytes(b"new-worker")
        cur_dir.mkdir()
        (cur_dir / "worker").write_bytes(b"old-worker")
        trash = Path(str(cur_dir) + ".trash")

        original_replace = os.replace
        dst_current_calls = {"n": 0}

        def flaky_replace(src, dst, *args, **kwargs):
            if Path(dst) == cur_dir:
                dst_current_calls["n"] += 1
                if dst_current_calls["n"] == 1:
                    raise OSError("simulated second-rename failure")
            return original_replace(src, dst, *args, **kwargs)

        monkeypatch.setattr(os, "replace", flaky_replace)
        with pytest.raises(OSError):
            offline_pack.atomic_swap_offline_pack(new_dir, cur_dir)

        # The PREVIOUS pack was restored at cur_dir (not left missing).
        assert (cur_dir / "worker").read_bytes() == b"old-worker"
        # The trash was consumed by the restore.
        assert not trash.exists()
        # The staging dir was not consumed by the failed rename.
        assert (new_dir / "worker").read_bytes() == b"new-worker"


class TestWindowsAtomicSwap:
    """Windows: stop worker → rename → start worker."""

    def test_windows_calls_stop_then_start(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        stop = MagicMock()
        start = MagicMock()
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        (new_dir / "worker.exe").write_bytes(b"new-worker")
        cur_dir.mkdir()
        (cur_dir / "worker.exe").write_bytes(b"old-worker")
        trash = offline_pack.atomic_swap_offline_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        stop.assert_called_once()
        start.assert_called_once()
        # New content at cur_dir.
        assert (cur_dir / "worker.exe").read_bytes() == b"new-worker"
        assert trash is not None
        assert not Path(trash).exists()

    def test_windows_creates_and_deletes_trash(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        (new_dir / "worker.exe").write_bytes(b"new")
        cur_dir.mkdir()
        (cur_dir / "worker.exe").write_bytes(b"old")
        trash = offline_pack.atomic_swap_offline_pack(new_dir, cur_dir)
        assert trash is not None
        assert str(trash).endswith("current.trash")
        # Trash deleted after successful swap.
        assert not Path(trash).exists()

    def test_windows_removes_preexisting_trash(self, tmp_path: Path, monkeypatch):
        """A leftover trash from a previous failed swap must be cleaned."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        trash_path = tmp_path / "current.trash"
        trash_path.mkdir()
        (trash_path / "stale.bin").write_bytes(b"stale")
        new_dir.mkdir()
        (new_dir / "worker.exe").write_bytes(b"new")
        cur_dir.mkdir()
        (cur_dir / "worker.exe").write_bytes(b"old")
        offline_pack.atomic_swap_offline_pack(new_dir, cur_dir)
        assert not trash_path.exists()

    def test_windows_rollback_on_new_to_current_failure(self, tmp_path: Path, monkeypatch):
        """If ``rename new → current`` fails, restore trash as current."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        stop = MagicMock()
        start = MagicMock()
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        (new_dir / "worker.exe").write_bytes(b"new")
        cur_dir.mkdir()
        (cur_dir / "worker.exe").write_bytes(b"old-content")
        # Make the second os.replace (new → current) fail by making
        original_replace = os.replace
        call_count = {"n": 0}

        def flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated rename failure")
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        with pytest.raises(OSError):
            offline_pack.atomic_swap_offline_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        # Worker was stopped, then started again on rollback.
        stop.assert_called_once()
        start.assert_called_once()
        assert (cur_dir / "worker.exe").read_bytes() == b"old-content"

    def test_windows_starts_worker_on_first_rename_failure(self, tmp_path: Path, monkeypatch):
        """If the first rename (current → trash) fails, the worker is restarted."""
        monkeypatch.setattr(platform, "system", lambda: "Windows")
        stop = MagicMock()
        start = MagicMock()
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        cur_dir.mkdir()
        # Patch os.replace to always fail.
        monkeypatch.setattr(os, "replace", lambda s, d: (_ for _ in ()).throw(OSError("nope")))
        with pytest.raises(OSError):
            offline_pack.atomic_swap_offline_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        stop.assert_called_once()
        start.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
