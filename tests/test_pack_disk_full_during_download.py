"""§8.9: Disk fills during download: graceful stop on ``OSError``."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


def _make_normal_transport(full_body: bytes):
    """Build a fake transport that yields ``full_body`` in chunks."""
    expected = hashlib.sha256(full_body).hexdigest()

    def fake(url, *, offset=0):
        remaining = full_body[offset:]

        def iter_chunks(chunk_bytes):
            pos = 0
            while pos < len(remaining):
                nxt = min(pos + chunk_bytes, len(remaining))
                yield remaining[pos:nxt]
                pos = nxt

        return {"content_length": len(remaining), "iter_chunks": iter_chunks}

    return fake, expected


def _patch_dest_to_fail_on_write(dest: Path, monkeypatch, *, fail_after_bytes: int):
    """Monkeypatch ``dest.open`` to return a fake file object whose"""
    real_open = Path.open
    bytes_written = {"n": 0}

    class FailingFile:
        def __init__(self, real_fh):
            self._fh = real_fh

        def write(self, data: bytes) -> int:
            bytes_written["n"] += len(data)
            if bytes_written["n"] > fail_after_bytes:
                raise OSError("No space left on device")
            return self._fh.write(data)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self._fh.close()

    def fake_open(self, mode, *args, **kwargs):
        fh = real_open(self, mode, *args, **kwargs)
        if "b" in mode and any(c in mode for c in ("w", "a")):
            return FailingFile(fh)
        return fh

    monkeypatch.setattr(Path, "open", fake_open)


class TestDiskFullDuringDownload:
    """§8.9, graceful stop on ``OSError`` from ``fh.write``."""

    def test_disk_full_raises_pack_disk_full_error(self, tmp_path: Path, monkeypatch):
        full = b"x" * 4096
        fake, expected = _make_normal_transport(full)
        dest = tmp_path / "pack-v1.partial"
        _patch_dest_to_fail_on_write(dest, monkeypatch, fail_after_bytes=100)
        with pytest.raises(offline_pack.OfflinePackDiskFullError) as exc_info:
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=fake,
                chunk_bytes=64,
            )
        assert exc_info.value.version == "v1"
        assert str(dest) in exc_info.value.path

    def test_disk_full_is_oserror_subclass(self, tmp_path: Path, monkeypatch):
        full = b"x" * 4096
        fake, expected = _make_normal_transport(full)
        dest = tmp_path / "pack-v1.partial"
        _patch_dest_to_fail_on_write(dest, monkeypatch, fail_after_bytes=100)
        with pytest.raises(OSError):  # PackDiskFullError is OSError
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=fake,
                chunk_bytes=64,
            )

    def test_partial_deleted_on_disk_full(self, tmp_path: Path, monkeypatch):
        """The partial file MUST be deleted (never trust a partial)."""
        full = b"x" * 4096
        fake, expected = _make_normal_transport(full)
        dest = tmp_path / "pack-v1.partial"
        _patch_dest_to_fail_on_write(dest, monkeypatch, fail_after_bytes=100)
        with pytest.raises(offline_pack.OfflinePackDiskFullError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=fake,
                chunk_bytes=64,
            )
        if dest.exists():
            assert dest.stat().st_size == 0, "partial must be deleted or empty after disk-full"

    def test_one_pack_download_failed_event_published(self, tmp_path: Path, monkeypatch):
        """Exactly one ``pack_download_failed`` event with reason ``disk_full``."""
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        full = b"x" * 4096
        fake, expected = _make_normal_transport(full)
        dest = tmp_path / "pack-v1.partial"
        _patch_dest_to_fail_on_write(dest, monkeypatch, fail_after_bytes=100)
        with pytest.raises(offline_pack.OfflinePackDiskFullError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                event_bus=FakeBus(),
                http_get=fake,
                chunk_bytes=64,
            )
        failed = [e for e in events if e["type"] == "offline_pack_download_failed"]
        assert len(failed) == 1
        assert failed[0]["data"]["reason"] == "disk_full"
        assert failed[0]["data"]["version"] == "v1"

    def test_disk_full_not_retried_automatically(self, tmp_path: Path, monkeypatch):
        """Unlike rate-limit retries, disk-full is NOT retried in the"""
        full = b"x" * 4096
        fake, expected = _make_normal_transport(full)
        dest = tmp_path / "pack-v1.partial"
        _patch_dest_to_fail_on_write(dest, monkeypatch, fail_after_bytes=100)
        call_count = {"n": 0}
        original_fake = fake

        def counting_fake(url, *, offset=0):
            call_count["n"] += 1
            return original_fake(url, offset=offset)

        monkeypatch.setattr(offline_pack.time, "sleep", lambda s: None)
        with pytest.raises(offline_pack.OfflinePackDiskFullError):
            offline_pack.download_offline_pack_with_resume(
                "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
                dest,
                expected_sha256=expected,
                version="v1",
                http_get=counting_fake,
                chunk_bytes=64,
            )
        # Only ONE http_get call, no retry.
        assert call_count["n"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
