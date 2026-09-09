"""§8.2 — Pack arrives corrupted: recovery via discard + re-download.

Spec (§8.2):

  ``verify_pack_or_skip()`` (modeled on ``verify_tauri_binary_or_skip``
  from ``autostart_launcher.py``). Mismatch → the partial is discarded
  and ``offline_pack_corrupt`` is published; the next trigger/launch
  retries the download.

Tested behaviors:

  1. A tampered pack file (one byte flipped) fails SHA-256 verification
     → ``verify_pack_or_skip`` returns False.
  2. A missing manifest → fail-closed (returns False).
  3. A manifest with a missing declared file → fail-closed.
  4. A manifest with a structurally-invalid schema → fail-closed
     (returns None from ``load_pack_manifest``).
  5. A manifest entry above the per-file size cap → fail-closed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_valid_pack(tmp_path: Path, version: str = "v1") -> Path:
    """Write a valid pack at ``tmp_path / version /`` with manifest."""
    root = tmp_path / version
    root.mkdir(parents=True)
    worker = root / "worker.exe"
    worker.write_bytes(b"worker-binary-content")
    vad = root / "silero_vad.onnx"
    vad.write_bytes(b"vad-onnx-blob")
    manifest = {
        "version": version,
        "sha256": _sha256(b"aggregate-hash-placeholder"),
        "files": [
            {"name": "worker.exe", "sha256": _sha256(worker.read_bytes()), "size": len(worker.read_bytes())},
            {"name": "silero_vad.onnx", "sha256": _sha256(vad.read_bytes()), "size": len(vad.read_bytes())},
        ],
        "min_proto_version": 1,
    }
    (root / "pack-manifest.json").write_text(json.dumps(manifest))
    return root


class TestVerifyPackOrSkip:
    """§8.2 — verify_pack_or_skip fail-closed semantics."""

    def test_valid_pack_passes(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is True

    def test_tampered_file_fails_closed(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Flip one byte in the worker.exe — SHA-256 must mismatch.
        worker = tmp_path / "v1" / "worker.exe"
        worker.write_bytes(b"DIFFERENT-content")
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False

    def test_missing_manifest_fails_closed(self, tmp_path: Path):
        # No manifest written — should fail closed.
        (tmp_path / "v1").mkdir()
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False

    def test_missing_declared_file_fails_closed(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Delete one declared file.
        (tmp_path / "v1" / "silero_vad.onnx").unlink()
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False

    def test_malformed_manifest_fails_closed(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        # Malformed JSON.
        (root / "pack-manifest.json").write_text("{not valid json")
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False

    def test_manifest_missing_field_fails_closed(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        # Missing "files" field.
        bad = {"version": "v1", "sha256": _sha256(b"x"), "min_proto_version": 1}
        (root / "pack-manifest.json").write_text(json.dumps(bad))
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False

    def test_manifest_file_entry_missing_name_fails(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        bad = {
            "version": "v1",
            "sha256": _sha256(b"x"),
            "files": [{"sha256": _sha256(b"x"), "size": 1}],  # no name
            "min_proto_version": 1,
        }
        (root / "pack-manifest.json").write_text(json.dumps(bad))
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False

    def test_manifest_with_oversized_entry_fails_closed(self, tmp_path: Path):
        """A manifest entry above the per-file cap is rejected at load
        time — the install stage re-checks the cap at extraction."""
        root = tmp_path / "v1"
        root.mkdir()
        (root / "huge.bin").write_bytes(b"x")
        bad = {
            "version": "v1",
            "sha256": _sha256(b"x"),
            "files": [
                {"name": "huge.bin", "sha256": _sha256(b"x"), "size": 10**12},
            ],
            "min_proto_version": 1,
        }
        (root / "pack-manifest.json").write_text(json.dumps(bad))
        assert offline_pack.verify_offline_pack_or_skip("v1", root=tmp_path) is False


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
