"""§8.10 — Pack missing on launch: cheap existence check + background checksum.

Spec (§8.10):

  At every launch, the slim-core sidecar checks ``pack-<version>/worker.exe``
  existence. Missing → silent re-download. A full checksum check runs
  in the background so startup is never slowed.

Tested behaviors:

  1. ``pack_exists`` returns True when manifest + all declared files
     are present (NO hashing — cheap check).
  2. ``pack_exists`` returns False when manifest is missing.
  3. ``pack_exists`` returns False when a declared file is missing.
  4. ``pack_exists`` returns False when manifest is malformed (fail
     closed — never trust a partial).
  5. ``BackgroundChecksum`` runs on a daemon thread; ``result`` is
     True (verified) or False (corrupt).
  6. ``BackgroundChecksum`` publishes ``pack_verified`` on success and
     ``pack_corrupt`` on failure.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from voice_typer.server.service import pack


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_valid_pack(tmp_path: Path, version: str = "v1") -> Path:
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


class TestPackExists:
    """§8.10 — cheap existence check (no hashing)."""

    def test_present_pack_returns_true(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        assert pack.pack_exists("v1", root=tmp_path) is True

    def test_missing_manifest_returns_false(self, tmp_path: Path):
        (tmp_path / "v1").mkdir()  # dir exists, no manifest
        assert pack.pack_exists("v1", root=tmp_path) is False

    def test_missing_file_returns_false(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        (tmp_path / "v1" / "worker.exe").unlink()
        assert pack.pack_exists("v1", root=tmp_path) is False

    def test_malformed_manifest_returns_false(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        (root / "pack-manifest.json").write_text("{broken json")
        assert pack.pack_exists("v1", root=tmp_path) is False

    def test_completely_missing_dir_returns_false(self, tmp_path: Path):
        # No version directory at all.
        assert pack.pack_exists("nonexistent-version", root=tmp_path) is False


class TestBackgroundChecksum:
    """§8.10 / §8.16 — background checksum on a daemon thread."""

    def test_valid_pack_publishes_pack_verified(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        bg = pack.BackgroundChecksum("v1", event_bus=FakeBus(), root=tmp_path)
        bg.start()
        result = bg.join(timeout_s=5.0)
        assert result is True
        verified = [e for e in events if e["type"] == "pack_verified"]
        assert verified
        assert verified[0]["data"]["version"] == "v1"

    def test_corrupt_pack_publishes_pack_corrupt(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Tamper with the worker.exe — SHA-256 will mismatch.
        (tmp_path / "v1" / "worker.exe").write_bytes(b"TAMPERED")
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        bg = pack.BackgroundChecksum("v1", event_bus=FakeBus(), root=tmp_path)
        bg.start()
        result = bg.join(timeout_s=5.0)
        assert result is False
        corrupt = [e for e in events if e["type"] == "pack_corrupt"]
        assert corrupt
        assert corrupt[0]["data"]["version"] == "v1"

    def test_done_property_clears_after_run(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        bg = pack.BackgroundChecksum("v1", root=tmp_path)
        assert bg.done is False
        bg.start()
        bg.join(timeout_s=5.0)
        assert bg.done is True

    def test_join_before_start_returns_none(self, tmp_path: Path):
        bg = pack.BackgroundChecksum("v1", root=tmp_path)
        # No start() call → join returns None (no thread to join).
        assert bg.join(timeout_s=0.1) is None

    def test_start_idempotent(self, tmp_path: Path):
        """Calling ``start()`` twice does not spawn two threads."""
        _write_valid_pack(tmp_path, "v1")
        bg = pack.BackgroundChecksum("v1", root=tmp_path)
        bg.start()
        first_thread = bg._thread
        bg.start()
        assert bg._thread is first_thread  # same thread
        bg.join(timeout_s=5.0)


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
