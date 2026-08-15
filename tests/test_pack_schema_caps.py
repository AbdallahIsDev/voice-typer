"""§4.6 + §8.8 — Per-file size cap in ``pack-manifest.json`` schema.

Defense-in-depth: ``load_pack_manifest`` rejects a manifest entry whose
``size`` field exceeds ``PACK_MAX_PER_FILE_BYTES`` (500 MB). Pre-fix, the
schema only validated that ``size`` was a non-negative int — no upper
bound. A malicious manifest could declare a 100 GB file size; while the
SHA-256 check would catch a mismatched-size file and the 630 MB disk-space
check would block the download, the unbounded ``size`` field was a DoS
vector (e.g. an attacker could publish a manifest with a 1 TB file size
to trip the disk-space check repeatedly, or to crash consumers that
pre-allocate based on the manifest).

The cap is set to 500 MB — generous enough to allow any legitimate file
in the pack (the largest is the worker exe at ~80 MB; the pack total is
~530 MB compressed+unpacked per §5.5) but small enough to reject
patological entries.

Mitigations already in place (the cap is defense-in-depth):
  * Per-file SHA-256 verification (``verify_pack_or_skip``) — a file
    whose actual bytes don't match the manifest hash is rejected.
  * 630 MB disk-space check (``check_pack_disk_space``) — the pack
    download aborts when free space < 630 MB.
  * 1 MiB max-bytes cap on the REMOTE manifest itself (``MAX_MANIFEST_BYTES``
    in ``update_check``) — a malicious server cannot ship a multi-GB
    manifest body.

Tested behaviors:

  1. ``PACK_MAX_PER_FILE_BYTES == 500 * 1024 * 1024`` (500 MB).
  2. A manifest with a file whose ``size`` exceeds the cap → fail-closed
     (returns ``None`` from ``load_pack_manifest``).
  3. A manifest with all files under the cap → accepted (returns the
     parsed manifest).
  4. A manifest with a file whose ``size`` is exactly at the cap →
     accepted (the cap is inclusive — ``size <= cap`` passes).
  5. A manifest with a file whose ``size`` is exactly cap+1 → rejected.
  6. The oversized-file rejection happens regardless of other valid
     entries in the manifest (one bad file fails the whole manifest).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


def _sha256(b: bytes = b"x") -> str:
    """Return a 64-char SHA-256 hex string for the manifest schema."""
    return hashlib.sha256(b).hexdigest()


def _write_manifest(tmp_path: Path, manifest: dict) -> Path:
    """Write a manifest dict to ``tmp_path/pack-manifest.json`` and return the path."""
    path = tmp_path / "pack-manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _valid_manifest(file_sizes: list[int], *, version: str = "v1.2.3") -> dict:
    """Build a minimal valid manifest with files of the given sizes.

    All SHA-256 fields are valid 64-char hex strings (the schema check
    only validates length, not the actual hash — that's the per-file
    SHA-256 check's job, exercised by ``test_pack_corruption_recovery``).
    """
    return {
        "version": version,
        "sha256": _sha256(),
        "files": [
            {"name": f"file_{i}.bin", "sha256": _sha256(), "size": size}
            for i, size in enumerate(file_sizes)
        ],
        "min_proto_version": 1,
    }


class TestPerFileSizeCapConstant:
    """``PACK_MAX_PER_FILE_BYTES`` — the per-file size cap constant."""

    def test_cap_is_500_mb(self):
        """The cap is 500 MB (500 * 1024 * 1024 bytes)."""
        assert offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES == 500 * 1024 * 1024

    def test_cap_is_under_pack_required_total(self):
        """The per-file cap (500 MB) is less than the pack total disk
        budget (630 MB) — a single file cannot exceed the entire pack
        budget (defense-in-depth — the disk-space check would block
        the download anyway, but the per-file cap rejects the manifest
        earlier, before any download attempt)."""
        assert offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES < offline_pack.OFFLINE_PACK_REQUIRED_MB * 1024 * 1024

    def test_cap_is_over_largest_legitimate_file(self):
        """The cap (500 MB) is well above the largest legitimate pack
        file (~80 MB worker exe) — legitimate manifests always pass."""
        largest_legitimate_file_bytes = 80 * 1024 * 1024  # 80 MB (worker exe)
        assert largest_legitimate_file_bytes < offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES


class TestPerFileSizeCapRejection:
    """``load_pack_manifest`` — per-file size cap rejection."""

    def test_manifest_with_oversized_file_is_rejected(self, tmp_path: Path):
        """A manifest with one file having ``size = 600 MB`` (over the
        500 MB cap) is rejected — ``load_pack_manifest`` returns ``None``
        (fail-closed).

        This test FAILS on revert: without the per-file size cap, the
        manifest would be accepted (returns the parsed dict). The
        SHA-256 + disk-space mitigations would still apply at download
        time, but the manifest itself would pass structural validation.
        """
        manifest = _valid_manifest([600 * 1024 * 1024])  # 600 MB (over 500 MB cap)
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None, (
            "expected load_pack_manifest to return None for a manifest with "
            f"a 600 MB file (over the 500 MB cap), got {result!r}"
        )

    def test_manifest_with_huge_size_is_rejected(self, tmp_path: Path):
        """A pathological 100 GB file size is rejected (DoS vector)."""
        manifest = _valid_manifest([100 * 1024 * 1024 * 1024])  # 100 GB
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None

    def test_oversized_file_rejected_even_with_other_valid_files(self, tmp_path: Path):
        """One oversized file fails the whole manifest (fail-closed) —
        the check is per-entry, not aggregate."""
        manifest = _valid_manifest(
            [
                1024,  # 1 KB (valid)
                600 * 1024 * 1024,  # 600 MB (over cap — fails the manifest)
                50 * 1024 * 1024,  # 50 MB (valid)
            ]
        )
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None


class TestPerFileSizeCapAcceptance:
    """``load_pack_manifest`` — per-file size cap acceptance (positive path)."""

    def test_manifest_with_valid_file_sizes_is_accepted(self, tmp_path: Path):
        """A manifest with all files under the cap is accepted —
        ``load_pack_manifest`` returns the parsed dict.

        The file sizes mirror realistic pack contents (worker exe, VAD
        model, engine binaries) — all well under the 500 MB cap.
        """
        manifest = _valid_manifest(
            [
                80 * 1024 * 1024,  # 80 MB (worker exe — the largest legitimate file)
                2 * 1024 * 1024,  # 2 MB (silero VAD ONNX)
                200 * 1024 * 1024,  # 200 MB (engine binary)
                100 * 1024 * 1024,  # 100 MB (engine binary 2)
                50 * 1024 * 1024,  # 50 MB (engine binary 3)
            ]
        )
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None, (
            "expected load_pack_manifest to accept a manifest with all files "
            "under the 500 MB cap, got None"
        )
        assert result["version"] == "v1.2.3"
        assert len(result["files"]) == 5

    def test_manifest_with_file_size_at_cap_is_accepted(self, tmp_path: Path):
        """A file with ``size`` exactly equal to the cap is accepted
        (the cap is inclusive — ``size <= cap`` passes)."""
        manifest = _valid_manifest([offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES])  # exactly 500 MB
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None, (
            "expected load_pack_manifest to accept a file size exactly at the "
            f"cap ({offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES} bytes), got None"
        )

    def test_manifest_with_file_size_just_under_cap_is_accepted(self, tmp_path: Path):
        """A file with ``size = cap - 1`` is accepted."""
        manifest = _valid_manifest([offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES - 1])
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None


class TestPerFileSizeCapBoundary:
    """Boundary tests — exactly cap vs cap+1."""

    def test_cap_plus_one_is_rejected(self, tmp_path: Path):
        """A file with ``size = cap + 1`` is rejected (the cap is inclusive)."""
        manifest = _valid_manifest([offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES + 1])
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None

    def test_zero_size_is_accepted(self, tmp_path: Path):
        """A file with ``size = 0`` is accepted (existing behavior —
        the pre-existing ``size >= 0`` check is preserved; the new cap
        does not regress this)."""
        manifest = _valid_manifest([0])
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
