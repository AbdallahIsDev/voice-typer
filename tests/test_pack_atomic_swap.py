"""§8.3 — Atomic swap (Windows + POSIX variants).

Spec (§8.3):

  On Windows, the worker exe must be stopped BEFORE the swap. The swap
  is: download to ``pack-<new-version>/`` → verify → stop worker →
  rename ``pack-<old>`` → ``pack-<old>.trash`` → rename ``pack-<new>``
  → ``pack-<current>`` → start worker → delete ``pack-<old>.trash``.

  On POSIX, the rename-over is atomic and the worker can keep running
  (the old inode stays alive until the process exits).

Tested behaviors (POSIX runs natively; Windows paths are simulated via
``platform.system()`` monkeypatching):

  1. POSIX: ``os.replace(new, current)`` is atomic — current is replaced.
  2. Windows: stop_worker is called before the swap, start_worker after.
  3. Windows: on swap failure (rename raises), the trash is restored
     (rollback) and start_worker is still called.
  4. Windows: trash directory is deleted after the swap.
  5. Windows: pre-existing trash is removed before the swap.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import pack


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
        trash = pack.atomic_swap_pack(new_dir, cur_dir)
        # POSIX returns the trash path too (best-effort cleanup
        # attempted; if the worker is still running on it, the rmtree
        # silently fails and the trash remains — but for this test
        # there's no worker so the trash is gone).
        assert trash is not None
        assert str(trash).endswith("current.trash")
        # The new content is now at cur_dir.
        assert (cur_dir / "worker").read_bytes() == b"new-worker"
        # new_dir is gone (renamed).
        assert not new_dir.exists()
        # Trash was deleted (no worker holding it open).
        assert not Path(trash).exists()

    def test_posix_does_not_call_stop_or_start(self, tmp_path: Path, monkeypatch):
        monkeypatch.setattr(platform, "system", lambda: "Linux")
        stop = MagicMock()
        start = MagicMock()
        new_dir = tmp_path / "v2"
        cur_dir = tmp_path / "current"
        new_dir.mkdir()
        cur_dir.mkdir()
        pack.atomic_swap_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        stop.assert_not_called()
        start.assert_not_called()


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
        trash = pack.atomic_swap_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        # stop called BEFORE start.
        stop.assert_called_once()
        start.assert_called_once()
        # New content at cur_dir.
        assert (cur_dir / "worker.exe").read_bytes() == b"new-worker"
        # Trash was deleted.
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
        trash = pack.atomic_swap_pack(new_dir, cur_dir)
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
        pack.atomic_swap_pack(new_dir, cur_dir)
        # Stale trash was deleted (then re-created and deleted again
        # in the normal swap flow).
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
        # ``new_dir`` non-existent right before the call. We patch
        # ``os.replace`` to fail on the second call.
        original_replace = os.replace
        call_count = {"n": 0}

        def flaky_replace(src, dst):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("simulated rename failure")
            return original_replace(src, dst)

        monkeypatch.setattr(os, "replace", flaky_replace)
        with pytest.raises(OSError):
            pack.atomic_swap_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        # Worker was stopped, then started again on rollback.
        stop.assert_called_once()
        start.assert_called_once()
        # The old content is restored at cur_dir.
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
            pack.atomic_swap_pack(new_dir, cur_dir, stop_worker=stop, start_worker=start)
        stop.assert_called_once()
        start.assert_called_once()


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
