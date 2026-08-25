"""§8.8 — Disk space check before download.

Spec (§8.8):

  Reuse ``asr_utils._check_disk_space_for_download()`` with pack size
  (180 MB compressed + 450 MB unpacked = 630 MB required). If
  insufficient, show one tray notification + defer.

Tested behaviors:

  1. ``PACK_REQUIRED_MB == 630`` (180 + 450).
  2. ``PACK_COMPRESSED_MB == 180``.
  3. ``PACK_UNPACKED_MB == 450``.
  4. ``check_pack_disk_space`` raises ``RuntimeError`` when free space
     < 630 MB.
  5. ``check_pack_disk_space`` returns None when free space >= 630 MB.
  6. ``check_pack_disk_space`` swallows ``OSError`` from
     ``shutil.disk_usage`` (best-effort — don't block the download
     on a failed stat).
  7. The error message mentions both the compressed and unpacked
     sizes (so the user knows why 630 MB is needed for a "180 MB"
     download).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service import offline_pack


class TestPackSizeConstants:
    """§8.8 — pack size budget."""

    def test_required_mb_is_630(self):
        assert offline_pack.OFFLINE_PACK_REQUIRED_MB == 630

    def test_compressed_mb_is_180(self):
        assert offline_pack.OFFLINE_PACK_COMPRESSED_MB == 180

    def test_unpacked_mb_is_450(self):
        assert offline_pack.OFFLINE_PACK_UNPACKED_MB == 450

    def test_required_equals_compressed_plus_unpacked(self):
        assert offline_pack.OFFLINE_PACK_REQUIRED_MB == (
            offline_pack.OFFLINE_PACK_COMPRESSED_MB + offline_pack.OFFLINE_PACK_UNPACKED_MB
        )


class TestCheckPackDiskSpace:
    """§8.8 — disk space check."""

    def test_insufficient_space_raises(self, tmp_path: Path, monkeypatch):
        """When free space < 630 MB, RuntimeError is raised."""
        fake_usage = MagicMock(free=100 * 1024 * 1024)  # 100 MB free
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake_usage)
        with pytest.raises(RuntimeError) as exc_info:
            offline_pack.check_offline_pack_disk_space(tmp_path)
        msg = str(exc_info.value)
        assert "630" in msg or "100" in msg  # mentions required or available

    def test_sufficient_space_passes(self, tmp_path: Path, monkeypatch):
        """When free space >= 630 MB, no exception."""
        fake_usage = MagicMock(free=2048 * 1024 * 1024)  # 2 GB free
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake_usage)
        # Should not raise.
        offline_pack.check_offline_pack_disk_space(tmp_path)

    def test_custom_required_mb(self, tmp_path: Path, monkeypatch):
        """Caller can override the required size."""
        fake_usage = MagicMock(free=500 * 1024 * 1024)  # 500 MB free
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake_usage)
        # 1000 MB required, only 500 MB free → raise.
        with pytest.raises(RuntimeError):
            offline_pack.check_offline_pack_disk_space(tmp_path, required_mb=1000)
        # 400 MB required, 500 MB free → pass.
        offline_pack.check_offline_pack_disk_space(tmp_path, required_mb=400)

    def test_disk_usage_oserror_swallowed(self, tmp_path: Path, monkeypatch):
        """A failed ``disk_usage`` stat does NOT block the download."""
        monkeypatch.setattr(shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("stat failed")))
        # Should NOT raise — best-effort check.
        offline_pack.check_offline_pack_disk_space(tmp_path)

    def test_error_message_mentions_compressed_and_unpacked(self, tmp_path: Path, monkeypatch):
        """The error message explains WHY 630 MB is needed (180 + 450)."""
        fake_usage = MagicMock(free=10 * 1024 * 1024)  # 10 MB free
        monkeypatch.setattr(shutil, "disk_usage", lambda p: fake_usage)
        with pytest.raises(RuntimeError) as exc_info:
            offline_pack.check_offline_pack_disk_space(tmp_path)
        msg = str(exc_info.value)
        assert "180" in msg
        assert "450" in msg
        assert "compressed" in msg.lower()
        assert "unpacked" in msg.lower()


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
