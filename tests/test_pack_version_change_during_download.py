"""§8.12 — Pack version change during download.

Spec (§8.12):

  Partial downloads are saved per pack-version. If the needed version
  is still the same, the download continues. If the version changed,
  the old partial is discarded.

Tested behaviors:

  1. A partial for an OLD version (``v1``) is left untouched when
     downloading a NEW version (``v2``). The new download starts from
     offset 0 at its own partial path.
  2. If the user previously started downloading ``v1`` and now needs
     ``v2``, the ``v1`` partial is NOT used for the ``v2`` download
     (different paths).
  3. If a partial for ``v1`` is corrupt and the version is still
     ``v1``, the resume path attempts to re-hash and either continues
     or restarts (covered by §8.1 test).
  4. The download path is per-version — each version has its own
     ``pack-<version>.partial`` file.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


def _make_fake_transport(full_body: bytes):
    expected = hashlib.sha256(full_body).hexdigest()
    calls: list[dict] = []

    def fake(url, *, offset=0):
        calls.append({"url": url, "offset": offset})
        remaining = full_body[offset:]

        def iter_chunks(chunk_bytes):
            pos = 0
            while pos < len(remaining):
                nxt = min(pos + chunk_bytes, len(remaining))
                yield remaining[pos:nxt]
                pos = nxt

        return {"content_length": len(remaining), "iter_chunks": iter_chunks}

    return fake, calls, expected


class TestVersionChangeDuringDownload:
    """§8.12 — partial files are version-scoped."""

    def test_v1_partial_unused_for_v2_download(self, tmp_path: Path):
        """A ``pack-v1.partial`` file MUST NOT be used when downloading v2."""
        v1_body = b"v1-content" * 100
        v2_body = b"v2-content" * 100
        # Pre-create a v1 partial (simulating an interrupted v1 download).
        v1_partial = offline_pack.offline_pack_partial_path("v1", root=tmp_path)
        v1_partial.parent.mkdir(parents=True, exist_ok=True)
        v1_partial.write_bytes(v1_body[:500])

        # Now download v2 — must NOT use the v1 partial.
        v2_partial = offline_pack.offline_pack_partial_path("v2", root=tmp_path)
        fake, calls, v2_expected = _make_fake_transport(v2_body)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v2/offline_pack.zip",
            v2_partial,
            expected_sha256=v2_expected,
            version="v2",
            http_get=fake,
        )
        assert ok
        # The v2 download started from offset 0 (its own partial was
        # absent — the v1 partial was NOT reused).
        assert calls[0]["offset"] == 0
        # The v1 partial is still on disk (untouched — caller can clean
        # it up separately, but the v2 download did not delete it).
        assert v1_partial.exists()
        assert v1_partial.read_bytes() == v1_body[:500]
        # The v2 partial now has the full v2 body.
        assert v2_partial.read_bytes() == v2_body

    def test_each_version_has_own_partial_path(self, tmp_path: Path):
        """The partial path is per-version — no collision."""
        p1 = offline_pack.offline_pack_partial_path("v1", root=tmp_path)
        p2 = offline_pack.offline_pack_partial_path("v2", root=tmp_path)
        assert p1 != p2
        assert p1.name == "pack-v1.partial"
        assert p2.name == "pack-v2.partial"
        # Each partial lives inside its version directory.
        assert p1.parent == tmp_path / "v1"
        assert p2.parent == tmp_path / "v2"
        # Both share the same pack root (grandparent).
        assert p1.parent.parent == p2.parent.parent == tmp_path

    def test_resuming_same_version_uses_partial(self, tmp_path: Path):
        """If the version is unchanged, the partial is resumed (offset > 0)."""
        full = b"same-version-content" * 200
        expected = hashlib.sha256(full).hexdigest()
        partial = offline_pack.offline_pack_partial_path("v1", root=tmp_path)
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(full[:1000])  # 1000-byte partial
        fake, calls, _ = _make_fake_transport(full)
        ok = offline_pack.download_offline_pack_with_resume(
            "https://github.com/owner/repo/releases/download/v1/offline_pack.zip",
            partial,
            expected_sha256=expected,
            version="v1",
            http_get=fake,
        )
        assert ok
        # The download resumed from offset 1000 (the existing partial).
        assert calls[0]["offset"] == 1000

    def test_version_specific_lock_files(self, tmp_path: Path):
        """The lock file is also per-version — no cross-version contention."""
        l1 = offline_pack.offline_pack_lock_path("v1", root=tmp_path)
        l2 = offline_pack.offline_pack_lock_path("v2", root=tmp_path)
        assert l1 != l2
        assert l1.name == "pack-v1.lock"
        assert l2.name == "pack-v2.lock"


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
