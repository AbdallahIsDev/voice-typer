"""REF-4. The class/method names, assertion logic, and imports below are"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch


class TestModelIntegrityWarnsOnEmptyHashes:
    """SEC-audit-005."""

    def test_security_logs_warning_when_files_empty(self, tmp_path, caplog, monkeypatch):
        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        monkeypatch.setattr(security, "_integrity_cache_path_override", tmp_path / "integrity_cache.json")

        # Create a fake model directory with a model file (safetensors)
        model_dir = tmp_path / "fake-model"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"fake")

        # Patch MODEL_HASHES to return an empty files dict for our repo
        fake_manifest = {"fake/repo": {"revision": "main", "files": {}}}
        with patch.object(security, "MODEL_HASHES", fake_manifest), caplog.at_level(logging.WARNING):
            result = verify_model_integrity(local_dir=str(model_dir), repo_id="fake/repo")

        # Soft pass (structural checks pass, hash check is a no-op)
        assert result is True
        # Must have emitted a WARNING with "NO-OP"
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("NO-OP" in r.getMessage() for r in warnings), (
            "verify_model_integrity must emit a WARNING containing 'NO-OP' "
            "when the manifest's files dict is empty, so operators notice "
            "the integrity check is effectively disabled."
        )

    def test_qwen_hard_fails_when_files_empty(self, tmp_path, caplog, monkeypatch):
        """G4-H-33 / NF-R18-9: ``verify_model_integrity`` (the canonical"""
        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        # Isolate the integrity cache to tmp_path (see sibling test).
        monkeypatch.setattr(security, "_integrity_cache_path_override", tmp_path / "integrity_cache.json")

        # Create a fake qwen model dir with config.json + a model file
        model_dir = tmp_path / "fake-qwen"
        model_dir.mkdir()
        (model_dir / "config.json").write_text("{}")
        (model_dir / "model.safetensors").write_bytes(b"fake")

        # Patch MODEL_HASHES with the real qwen manifest entry shape
        fake_manifest = {"qwen": {"revision": "local", "files": {}}}
        with patch.object(security, "MODEL_HASHES", fake_manifest), caplog.at_level(logging.ERROR):
            result = verify_model_integrity(local_dir=str(model_dir), repo_id="qwen")

        assert result is False, (
            "NF-R18-9 / G4-H-33: verify_model_integrity must HARD-FAIL "
            "(return False) for a local qwen model with an empty files "
            "dict, there is no upstream SHA pin (revision='local'), so "
            "the empty-files soft-pass would let a tampered directory "
            "load unchecked. The deleted _verify_qwen_model_hashes "
            "helper used to soft-pass here (the G4-H-33 root cause); "
            "the canonical verifier must NOT."
        )
        # Must have emitted an ERROR (not WARNING) about the hard-fail.
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert any("hard-FAIL" in r.getMessage() or "hard_fail" in r.getMessage().lower() for r in errors), (
            "NF-R18-9 / G4-H-33: verify_model_integrity must emit an ERROR "
            "containing 'hard-FAIL' for a local qwen model with an empty "
            "files dict, so operators see at default log levels that the "
            "integrity check refused to soft-pass."
        )

    def test_model_hashes_json_currently_populated(self):
        """model_hashes.json now have POPULATED ``files`` dicts (at minimum"""
        import re

        commit_sha_re = re.compile(r"^[0-9a-f]{40}$")
        sha256_re = re.compile(r"^[0-9a-f]{64}$")

        manifest_path = Path(__file__).resolve().parent.parent.parent / "voice_typer" / "server" / "model_hashes.json"
        with open(manifest_path) as f:
            data = json.load(f)

        hf_repos_found = 0
        for repo_id, entry in data.items():
            if repo_id.startswith("_"):
                continue  # skip _comment and other metadata keys
            assert isinstance(entry, dict), (
                f"model_hashes.json entry {repo_id!r} must be a dict, got {type(entry).__name__}"
            )
            assert "revision" in entry, f"model_hashes.json entry {repo_id!r} must have a 'revision' key"
            assert "files" in entry, f"model_hashes.json entry {repo_id!r} must have a 'files' key"

            if repo_id == "qwen":
                assert entry["revision"] == "local", f"qwen entry revision should be 'local', got {entry['revision']!r}"
                continue

            hf_repos_found += 1
            assert commit_sha_re.match(entry["revision"]), (
                f"model_hashes.json entry {repo_id!r} revision {entry['revision']!r} "
                f"is not a 40-char hex commit SHA, must not be 'main' or any "
                f"mutable branch (supply-chain attack surface)."
            )
            assert entry["files"], (
                f"model_hashes.json entry {repo_id!r} has empty 'files' dict, "
                f"verify_model_integrity() is a no-op for this repo. "
                f"Pin at least config.json."
            )
            assert "config.json" in entry["files"], f"model_hashes.json entry {repo_id!r} must pin config.json"
            assert sha256_re.match(entry["files"]["config.json"]), (
                f"model_hashes.json entry {repo_id!r} config.json hash "
                f"{entry['files']['config.json']!r} is not a 64-char hex SHA-256."
            )

        assert hf_repos_found >= 5, f"Expected at least 5 HuggingFace repos with pinned SHAs, found {hf_repos_found}"
