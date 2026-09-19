"""§4.6 + §8.8: Per-file size cap in ``pack-manifest.json`` schema."""

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
    """Build a minimal valid manifest with files of the given sizes."""
    return {
        "version": version,
        "sha256": _sha256(),
        "files": [{"name": f"file_{i}.bin", "sha256": _sha256(), "size": size} for i, size in enumerate(file_sizes)],
        "min_proto_version": 1,
    }


class TestPerFileSizeCapConstant:
    """``PACK_MAX_PER_FILE_BYTES``, the per-file size cap constant."""

    def test_cap_is_500_mb(self):
        """The cap is 500 MB (500 * 1024 * 1024 bytes)."""
        assert offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES == 500 * 1024 * 1024

    def test_cap_is_under_pack_required_total(self):
        """The per-file cap (500 MB) is less than the pack total disk"""
        assert offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES < offline_pack.OFFLINE_PACK_REQUIRED_MB * 1024 * 1024

    def test_cap_is_over_largest_legitimate_file(self):
        """The cap (500 MB) is well above the largest legitimate pack"""
        largest_legitimate_file_bytes = 80 * 1024 * 1024  # 80 MB (worker exe)
        assert largest_legitimate_file_bytes < offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES


class TestPerFileSizeCapRejection:
    """``load_pack_manifest``, per-file size cap rejection."""

    def test_manifest_with_oversized_file_is_rejected(self, tmp_path: Path):
        """500 MB cap) is rejected: ``load_pack_manifest`` returns ``None``"""
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
        """One oversized file fails the whole manifest (fail-closed) —"""
        manifest = _valid_manifest(
            [
                1024,  # 1 KB (valid)
                600 * 1024 * 1024,  # 600 MB (over cap, fails the manifest)
                50 * 1024 * 1024,  # 50 MB (valid)
            ]
        )
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None


class TestPerFileSizeCapAcceptance:
    """``load_pack_manifest``, per-file size cap acceptance (positive path)."""

    def test_manifest_with_valid_file_sizes_is_accepted(self, tmp_path: Path):
        """A manifest with all files under the cap is accepted —"""
        manifest = _valid_manifest(
            [
                80 * 1024 * 1024,  # 80 MB (worker exe, the largest legitimate file)
                2 * 1024 * 1024,  # 2 MB (silero VAD ONNX)
                200 * 1024 * 1024,  # 200 MB (engine binary)
                100 * 1024 * 1024,  # 100 MB (engine binary 2)
                50 * 1024 * 1024,  # 50 MB (engine binary 3)
            ]
        )
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None, (
            "expected load_pack_manifest to accept a manifest with all files under the 500 MB cap, got None"
        )
        assert result["version"] == "v1.2.3"
        assert len(result["files"]) == 5

    def test_manifest_with_file_size_at_cap_is_accepted(self, tmp_path: Path):
        """A file with ``size`` exactly equal to the cap is accepted"""
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
    """Boundary tests, exactly cap vs cap+1."""

    def test_cap_plus_one_is_rejected(self, tmp_path: Path):
        """A file with ``size = cap + 1`` is rejected (the cap is inclusive)."""
        manifest = _valid_manifest([offline_pack.OFFLINE_PACK_MAX_PER_FILE_BYTES + 1])
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None

    def test_zero_size_is_accepted(self, tmp_path: Path):
        """A file with ``size = 0`` is accepted (existing behavior —"""
        manifest = _valid_manifest([0])
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None


class TestVersionStringSafety:
    """``load_pack_manifest``, the ``version`` string must be a single"""

    @pytest.mark.parametrize(
        "version",
        [
            "../evil",
            "..",
            ".",
            "./1.2.3",
            "v1/../../evil",
            "v1/..",
            "/abs/path",
            "C:\\evil",
            "C:/evil",
            "\\\\server\\share",
            "v1\nx",
            "v 1",
            ".hidden",
            "-leading-hyphen",
            "v1\x00",
        ],
    )
    def test_unsafe_version_fails_closed(self, tmp_path: Path, version: str):
        """A version that is not a single safe path component →"""
        manifest = _valid_manifest([1024], version=version)
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is None, f"expected load_pack_manifest to reject unsafe version {version!r}, got {result!r}"

    @pytest.mark.parametrize(
        "version",
        ["1.2.3", "v1.2.3", "1.2.3-rc1", "1.2.3_build.7", "v1", "a", "A-b_9", "0"],
    )
    def test_safe_version_is_accepted(self, tmp_path: Path, version: str):
        """A clean single-component version still loads."""
        manifest = _valid_manifest([1024], version=version)
        path = _write_manifest(tmp_path, manifest)
        result = offline_pack.load_offline_pack_manifest(path)
        assert result is not None, f"expected version {version!r} to be accepted, got None"
        assert result["version"] == version

    def test_traversal_version_cannot_reach_the_pack_root(self, tmp_path: Path):
        """hand-written manifest under a nested dir is rejected BEFORE any"""
        root = tmp_path / "runtime-pack"
        evil_dir = root / "1.2.3"  # the dir the manifest claims to be for
        evil_dir.mkdir(parents=True)
        manifest = _valid_manifest([1024], version="../../escape")
        (evil_dir / "pack-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        assert offline_pack.load_offline_pack_manifest(evil_dir / "pack-manifest.json") is None
        # Nothing was written outside the pack root by the load itself.
        assert not (tmp_path / "escape").exists()


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
