"""§8.1 — Partial download resume.

Spec (plan-runtime-pack-split.md §8.1):

  The pack downloader reuses the resume pattern from
  ``service/model.py:_download_whisper_family``. The partial file is
  saved as ``pack-<version>.partial``; on next launch, it continues
  from the byte offset. The partial file is never trusted — only a
  fully downloaded, checksum-verified pack is ever used.

Tested behaviors:

  1. A partial file at ``pack-<version>.partial`` causes the next
     download to send ``Range: bytes=<offset>-`` (the fake transport
     records the request offset).
  2. The resumed download appends to the partial (not truncates).
  3. The final SHA-256 covers the whole file (partial + appended
     bytes) — a wrong SHA-256 → False + partial deleted.
  4. A correct SHA-256 → True + ``pack_download_completed`` event.
  5. A truncated/corrupt partial (re-hash fails) restarts from 0.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack

# ── Fake transport ─────────────────────────────────────────────────────


def _make_fake_transport(full_body: bytes, *, expected_sha256: str):
    """Build a fake ``http_get`` callable for ``download_pack_with_resume``.

    The fake:

    * Records the requested ``offset`` on each call (so the test can
      assert the ``Range:`` header was sent).
    * Returns the body slice starting at ``offset`` (mirrors HTTP 206).
    * Reports ``Content-Length`` as the remaining-body size.
    """
    calls: list[dict] = []

    def fake(url, *, offset=0):
        calls.append({"url": url, "offset": offset})
        if offset > len(full_body):
            raise RuntimeError(f"offset {offset} past EOF {len(full_body)}")
        remaining = full_body[offset:]
        return {
            "content_length": len(remaining),
            "iter_chunks": lambda chunk_bytes: _iter_chunks(remaining, chunk_bytes),
        }

    return fake, calls


def _iter_chunks(buf: bytes, chunk_bytes: int):
    pos = 0
    while pos < len(buf):
        nxt = min(pos + chunk_bytes, len(buf))
        yield buf[pos:nxt]
        pos = nxt


# ── Tests ──────────────────────────────────────────────────────────────


class TestResumeFromPartial:
    """§8.1 — a partial file triggers a resumed download."""

    def test_resume_sends_range_header(self, tmp_path: Path):
        """A partial file at ``pack-v1.partial`` causes the next call to
        request ``offset=<partial size>``."""
        full = b"x" * 4096
        expected = hashlib.sha256(full).hexdigest()
        partial = tmp_path / "pack-v1.partial"
        partial.write_bytes(full[:1024])  # 1024 bytes already on disk
        fake, calls = _make_fake_transport(full, expected_sha256=expected)
        dest = tmp_path / "pack-v1.partial"
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        assert ok
        assert calls[0]["offset"] == 1024

    def test_resume_appends_not_truncates(self, tmp_path: Path):
        """The resumed download appends to the existing partial (not
        overwrites from 0)."""
        full = b"abcdefghijklmnopqrstuvwxyz" * 100
        expected = hashlib.sha256(full).hexdigest()
        partial = tmp_path / "pack-v1.partial"
        prefix = full[:500]
        partial.write_bytes(prefix)
        fake, _ = _make_fake_transport(full, expected_sha256=expected)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        assert ok
        assert partial.read_bytes() == full  # full file present

    def test_wrong_sha256_deletes_partial(self, tmp_path: Path):
        """A wrong SHA-256 → False + partial deleted."""
        full = b"hello world" * 1000
        wrong_sha = "0" * 64  # obviously wrong
        partial = tmp_path / "pack-v1.partial"
        partial.write_bytes(full[:200])  # partial
        fake, _ = _make_fake_transport(full, expected_sha256=wrong_sha)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=wrong_sha,
            version="v1",
            http_get=fake,
        )
        assert ok is False
        assert not partial.exists()

    def test_correct_sha256_publishes_completed(self, tmp_path: Path):
        """A correct SHA-256 → True + ``pack_download_completed`` event."""
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        full = b"good" * 1000
        expected = hashlib.sha256(full).hexdigest()
        dest = tmp_path / "pack-v1.partial"
        fake, _ = _make_fake_transport(full, expected_sha256=expected)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            dest,
            expected_sha256=expected,
            version="v1",
            event_bus=FakeBus(),
            http_get=fake,
        )
        assert ok
        completed = [e for e in events if e["type"] == "offline_pack_download_completed"]
        assert completed
        assert completed[0]["data"]["version"] == "v1"
        assert completed[0]["data"]["sha256"] == expected

    def test_corrupt_partial_restarts_from_zero(self, tmp_path: Path, monkeypatch):
        """A partial whose on-disk bytes are UNREADABLE (corrupt inode,
        permission denied, etc.) MUST be discarded and the download
        restarted from offset 0.

        The implementation tries to re-hash the existing partial; if
        the open/read raises ``OSError``, it deletes the partial and
        starts from 0.
        """
        full = b"abc" * 500
        expected = hashlib.sha256(full).hexdigest()
        partial = tmp_path / "pack-v1.partial"
        partial.write_bytes(b"BAD" * 100)  # garbage on disk
        fake, calls = _make_fake_transport(full, expected_sha256=expected)

        # Make ``partial.open("rb")`` raise OSError on the FIRST call
        # only (the resume-read path). The next open (for "wb"/"ab")
        # must succeed so the download can write the new bytes.
        real_open = Path.open
        call_count = {"n": 0}

        def fake_open(self, mode, *args, **kwargs):
            if self == partial and "b" in mode and "r" in mode:
                call_count["n"] += 1
                if call_count["n"] == 1:
                    raise OSError("simulated read failure on resume")
            return real_open(self, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", fake_open)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        # The download should have restarted from offset 0 (since the
        # resume-read failed).
        assert calls[0]["offset"] == 0
        # And the final file should match the full body.
        assert ok
        assert partial.read_bytes() == full


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
