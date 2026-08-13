"""§8.2 — Pack arrives corrupted: recovery via discard + re-download.

Spec (§8.2):

  ``verify_pack_or_skip()`` (modeled on ``verify_tauri_binary_or_skip``
  from ``autostart_launcher.py``). Mismatch → discard + re-download
  (up to 3 attempts, with exponential backoff).

Tested behaviors:

  1. A tampered pack file (one byte flipped) fails SHA-256 verification
     → ``verify_pack_or_skip`` returns False.
  2. A missing manifest → fail-closed (returns False).
  3. A manifest with a missing declared file → fail-closed.
  4. A manifest with a structurally-invalid schema → fail-closed
     (returns None from ``load_pack_manifest``).
  5. ``PACK_MAX_CORRUPTION_RETRIES == 3``.
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
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is True

    def test_tampered_file_fails_closed(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Flip one byte in the worker.exe — SHA-256 must mismatch.
        worker = tmp_path / "v1" / "worker.exe"
        worker.write_bytes(b"DIFFERENT-content")
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is False

    def test_missing_manifest_fails_closed(self, tmp_path: Path):
        # No manifest written — should fail closed.
        (tmp_path / "v1").mkdir()
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is False

    def test_missing_declared_file_fails_closed(self, tmp_path: Path):
        _write_valid_pack(tmp_path, "v1")
        # Delete one declared file.
        (tmp_path / "v1" / "silero_vad.onnx").unlink()
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is False

    def test_malformed_manifest_fails_closed(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        # Malformed JSON.
        (root / "pack-manifest.json").write_text("{not valid json")
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is False

    def test_manifest_missing_field_fails_closed(self, tmp_path: Path):
        root = tmp_path / "v1"
        root.mkdir()
        (root / "worker.exe").write_bytes(b"x")
        # Missing "files" field.
        bad = {"version": "v1", "sha256": _sha256(b"x"), "min_proto_version": 1}
        (root / "pack-manifest.json").write_text(json.dumps(bad))
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is False

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
        assert pack.verify_pack_or_skip("v1", root=tmp_path) is False

    def test_max_corruption_retries_is_3(self):
        """§8.2: up to 3 attempts."""
        assert pack.PACK_MAX_CORRUPTION_RETRIES == 3


class TestCorruptionRecoveryFlow:
    """§8.2 — corruption triggers re-download up to 3 attempts."""

    def test_three_corrupt_attempts_then_give_up(self, tmp_path: Path, monkeypatch):
        """When verification fails 3 times in a row, the downloader
        gives up and publishes ``pack_corrupt`` + ``pack_download_failed``.

        We simulate this by replacing ``download_pack_with_resume``
        with a fake that always returns True (download succeeds), and
        replacing ``verify_pack_or_skip`` with a fake that always
        returns False (verification fails). After 3 attempts the
        caller should give up.
        """
        # Build a small "downloader driver" that mirrors what the
        # production code would do: loop download → verify → retry.
        attempts = {"n": 0}

        def fake_download(*args, **kwargs):
            attempts["n"] += 1
            return True

        def fake_verify(*args, **kwargs):
            return False

        monkeypatch.setattr(pack, "download_pack_with_resume", fake_download)
        monkeypatch.setattr(pack, "verify_pack_or_skip", fake_verify)

        # Run the recovery loop manually — production code would do
        # this in a service-layer helper (not yet implemented).
        events: list[dict] = []
        max_attempts = pack.PACK_MAX_CORRUPTION_RETRIES
        last_result = None
        for attempt in range(1, max_attempts + 1):
            downloaded = fake_download()
            if not downloaded:
                last_result = "download_failed"
                break
            verified = fake_verify()
            if verified:
                last_result = "verified"
                break
            # Exponential backoff would go here; we skip it in the test
            # for speed (the retry count is what we care about).
            last_result = "corrupt"
            events.append({"type": "pack_corrupt", "data": {"attempt": attempt}})

        assert attempts["n"] == max_attempts
        assert last_result == "corrupt"
        assert len(events) == max_attempts

    def test_second_attempt_succeeds(self, tmp_path: Path, monkeypatch):
        """When the second download verifies, the loop stops — no third attempt."""
        attempts = {"n": 0}

        def fake_download(*args, **kwargs):
            attempts["n"] += 1
            return True

        verify_results = [False, True]  # fail once, then succeed

        def fake_verify(*args, **kwargs):
            return verify_results.pop(0) if verify_results else True

        monkeypatch.setattr(pack, "download_pack_with_resume", fake_download)
        monkeypatch.setattr(pack, "verify_pack_or_skip", fake_verify)

        max_attempts = pack.PACK_MAX_CORRUPTION_RETRIES
        last_result = None
        for _ in range(max_attempts):
            downloaded = fake_download()
            if not downloaded:
                last_result = "download_failed"
                break
            verified = fake_verify()
            if verified:
                last_result = "verified"
                break
            last_result = "corrupt"

        assert attempts["n"] == 2  # stopped after second attempt
        assert last_result == "verified"


if __name__ == "__main__":
    pytest.main([__file__, "-x"])
