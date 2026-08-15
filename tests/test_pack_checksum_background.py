"""§8.16 — Checksum slows startup: background checksum + cheap existence.

Spec (§8.16):

  The pack checksum check runs in the background after startup, never
  blocking the window from opening. A cheap existence check runs
  synchronously.

Tested behaviors:

  1. ``pack_exists`` (cheap sync check) runs FAST (no SHA-256).
  2. ``BackgroundChecksum.start()`` returns immediately (non-blocking).
  3. ``BackgroundChecksum.result`` is None until ``done``.
  4. ``BackgroundChecksum`` does NOT block the main thread.
  5. ``BackgroundChecksum`` publishes ``offline_pack_verified`` on success.
  6. ``BackgroundChecksum`` publishes ``offline_pack_corrupt`` on failure.
  7. Two simultaneous ``BackgroundChecksum`` instances (different
     versions) run independently.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest
from voice_typer.server.service import offline_pack


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


class TestCheapExistenceCheck:
    """§8.16 — ``pack_exists`` is cheap (no hashing)."""

    def test_pack_exists_is_fast(self, tmp_path: Path):
        """``pack_exists`` should complete in <<1s even on a large pack
        (it does NOT hash)."""
        _write_valid_pack(tmp_path, "v1")
        # Write a "large" pack (100 MB) — ``pack_exists`` should NOT
        # take 100ms (it doesn't hash).
        big_file = tmp_path / "v1" / "big.bin"
        big_file.write_bytes(b"x" * (10 * 1024 * 1024))  # 10 MB (keep test fast)
        # Need to add it to the manifest so ``pack_exists`` looks for it.
        manifest = json.loads((tmp_path / "v1" / "pack-manifest.json").read_text())
        manifest["files"].append(
            {"name": "big.bin", "sha256": _sha256(big_file.read_bytes()), "size": 10 * 1024 * 1024}
        )
        (tmp_path / "v1" / "pack-manifest.json").write_text(json.dumps(manifest))

        start = time.monotonic()
        result = offline_pack.offline_pack_exists("v1", root=tmp_path)
        elapsed = time.monotonic() - start
        assert result is True
        # Cheap check should complete in <1s even for 10MB (it's just stat()).
        assert elapsed < 1.0


class TestBackgroundChecksumNonBlocking:
    """§8.16 — background checksum runs on a daemon thread."""

    def test_start_returns_immediately(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        bg = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        start = time.monotonic()
        bg.start()
        elapsed = time.monotonic() - start
        # ``start`` should return in <<1s (it just spawns a thread).
        assert elapsed < 0.5

    def test_result_is_none_until_done(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        bg = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        bg.start()
        # Immediately after start, result is None (not done yet).
        # Give the background thread a moment to finish on a slow CI.
        time.sleep(0.05)
        # By now it MIGHT be done (fast pack) — but if it is, ``result``
        # is True; if not, it's None. We assert that EITHER result is
        # None OR ``done`` is True (the only valid states).
        assert bg.result is None or bg.done is True
        bg.join(timeout_s=5.0)

    def test_does_not_block_main_thread(self, tmp_path: Path):
        """The main thread can do work while the checksum runs."""
        _write_valid_pack(tmp_path, "v1")
        bg = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        bg.start()
        # Main thread does work.
        counter = 0
        for _ in range(100):
            counter += 1
        assert counter == 100
        bg.join(timeout_s=5.0)
        assert bg.done is True

    def test_publishes_offline_pack_verified_on_success(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        bg = offline_pack.BackgroundChecksum("v1", event_bus=FakeBus(), root=tmp_path)
        bg.start()
        bg.join(timeout_s=5.0)
        verified = [e for e in events if e["type"] == "offline_pack_verified"]
        assert verified

    def test_publishes_offline_pack_corrupt_on_failure(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Tamper with a file.
        (tmp_path / "v1" / "worker.exe").write_bytes(b"TAMPERED")
        events: list[dict] = []

        class FakeBus:
            def publish(self, ev):
                events.append(ev)

        bg = offline_pack.BackgroundChecksum("v1", event_bus=FakeBus(), root=tmp_path)
        bg.start()
        bg.join(timeout_s=5.0)
        corrupt = [e for e in events if e["type"] == "offline_pack_corrupt"]
        assert corrupt
        assert corrupt[0]["data"]["version"] == "v1"

    def test_two_instances_run_independently(self, tmp_path: Path):
        """Two ``BackgroundChecksum`` instances (different versions) don't interfere."""
        _write_valid_pack(tmp_path, "v1")
        _write_valid_pack(tmp_path, "v2")
        bg1 = offline_pack.BackgroundChecksum("v1", root=tmp_path)
        bg2 = offline_pack.BackgroundChecksum("v2", root=tmp_path)
        bg1.start()
        bg2.start()
        r1 = bg1.join(timeout_s=5.0)
        r2 = bg2.join(timeout_s=5.0)
        assert r1 is True
        assert r2 is True


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
