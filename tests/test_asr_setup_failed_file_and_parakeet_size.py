"""Regression tests for AP-30 and AP-43."""

import logging
from pathlib import Path
from unittest.mock import patch

import pytest
from voice_typer.server.asr_setup import _verify_model_integrity
from voice_typer.server.asr_utils import _MODEL_SIZE_MB


@pytest.fixture(autouse=True)
def _override_integrity_cache_path(tmp_path, monkeypatch):
    """Pin the integrity cache inside tmp_path."""
    from voice_typer.server import security

    cache_path = tmp_path / "cache" / "integrity_cache.json"
    monkeypatch.setattr(security, "_integrity_cache_path_override", cache_path)
    yield
    monkeypatch.setattr(security, "_integrity_cache_path_override", None)


class TestVerifyModelIntegrityRecordsUnhashableFile:
    """AP-30: when ``compute_file_sha256`` raises, ``details[\"failed_file\"]``"""

    def test_failed_file_recorded_and_actual_hash_none(self, tmp_path):
        repo_id = "test-org/ap30-unhashable-repo"
        local_dir = tmp_path / "model"
        local_dir.mkdir()
        # Pinned files in the manifest. The hash loop will iterate these.
        pinned_files = {
            "config.json": "deadbeef" * 8,
            "model.bin": "cafebabe" * 8,
        }
        # Create the files so the ``not file_path.exists()`` branch is
        (local_dir / "config.json").write_text("{}")
        (local_dir / "model.bin").write_bytes(b"\x00" * 16)

        manifest = {"revision": "test-revision", "files": pinned_files}

        with (
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=False,
            ),
            patch.dict(
                "voice_typer.server.security.MODEL_HASHES",
                {repo_id: manifest},
                clear=False,
            ),
            patch(
                "voice_typer.server.security.compute_file_sha256",
                side_effect=OSError("permission denied (simulated)"),
            ),
        ):
            ok, details = _verify_model_integrity(repo_id, str(local_dir))

        # The integrity check must fail.
        assert ok is False, (
            "AP-30: when compute_file_sha256 raises, _verify_model_integrity must return ok=False (not silently pass)."
        )
        # Pre-fix: failed_file was left as None because the except branch
        assert details["failed_file"] == "config.json", (
            "AP-30: details['failed_file'] must be the filename whose hash "
            f"could not be computed; got {details['failed_file']!r}."
        )
        # ``actual_hash`` must be None to signify "could not compute"
        assert details["actual_hash"] is None, (
            "AP-30: details['actual_hash'] must be None when the hash could "
            f"not be computed; got {details['actual_hash']!r}."
        )
        # The expected_hash from the manifest must be propagated too —
        assert details["expected_hash"] == pinned_files["config.json"]

    def test_breaks_on_first_unhashable_file(self, tmp_path):
        """AP-30: the loop must ``break`` (not ``continue``) on the first"""
        repo_id = "test-org/ap30-break-on-first"
        local_dir = tmp_path / "model"
        local_dir.mkdir()
        pinned_files = {
            "config.json": "a" * 64,
            "model.bin": "b" * 64,
            "tokenizer.json": "c" * 64,
        }
        for name in pinned_files:
            (local_dir / name).write_text("payload")

        manifest = {"revision": "r1", "files": pinned_files}

        # ``compute_file_sha256`` raises ONLY on the second file
        call_count = {"n": 0}

        def fake_compute(file_path: Path) -> str:
            call_count["n"] += 1
            if file_path.name == "model.bin":
                raise OSError("simulated I/O error on model.bin")
            # Return the EXPECTED hash for config.json so the loop
            return pinned_files.get(file_path.name, "0" * 64)

        with (
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=False,
            ),
            patch.dict(
                "voice_typer.server.security.MODEL_HASHES",
                {repo_id: manifest},
                clear=False,
            ),
            patch(
                "voice_typer.server.security.compute_file_sha256",
                side_effect=fake_compute,
            ),
        ):
            ok, details = _verify_model_integrity(repo_id, str(local_dir))

        assert ok is False
        assert details["failed_file"] == "model.bin", (
            f"AP-30: loop must break on the FIRST unhashable file (model.bin); got {details['failed_file']!r}."
        )
        # The third file (tokenizer.json) must NOT have been visited —
        assert call_count["n"] == 2, (
            "AP-30: loop must break (not continue) after the first "
            f"unhashable file; compute_file_sha256 was called "
            f"{call_count['n']} times (expected 2)."
        )

    def test_log_escalated_to_warning(self, tmp_path, caplog):
        """AP-30: the diagnostic must be logged at WARNING (not DEBUG)"""
        repo_id = "test-org/ap30-warning-log"
        local_dir = tmp_path / "model"
        local_dir.mkdir()
        (local_dir / "config.json").write_text("{}")
        pinned_files = {"config.json": "d" * 64}
        manifest = {"revision": "r1", "files": pinned_files}

        with (
            patch(
                "voice_typer.server.security.verify_model_integrity",
                return_value=False,
            ),
            patch.dict(
                "voice_typer.server.security.MODEL_HASHES",
                {repo_id: manifest},
                clear=False,
            ),
            patch(
                "voice_typer.server.security.compute_file_sha256",
                side_effect=OSError("permission denied (simulated)"),
            ),
            caplog.at_level(logging.WARNING, logger="voice_typer.server.asr_setup"),
        ):
            _verify_model_integrity(repo_id, str(local_dir))

        # At least one WARNING-level record must mention the unhashable
        warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("could not compute hash" in r.getMessage() for r in warning_records), (
            "AP-30: hash-computation failure must be logged at WARNING "
            "(not DEBUG) so it's visible in production logs. "
            f"Got records: {[r.getMessage() for r in warning_records]!r}"
        )


class TestModelSizeMbIncludesParakeet:
    """AP-43: ``_MODEL_SIZE_MB`` must include a ``\"parakeet\"`` entry so"""

    def test_parakeet_key_exists(self):
        """The ``\"parakeet\"`` key must exist in ``_MODEL_SIZE_MB``."""
        assert "parakeet" in _MODEL_SIZE_MB, (
            "AP-43: _MODEL_SIZE_MB must include a 'parakeet' entry so the "
            "disk-space pre-check uses the correct estimate. Pre-fix the "
            "lookup fell through to the 500 MB default."
        )

    def test_parakeet_size_at_least_1000_mb(self):
        """The Parakeet estimate must be at least 1000 MB (the lower"""
        assert "parakeet" in _MODEL_SIZE_MB, "AP-43: missing 'parakeet' key"
        assert _MODEL_SIZE_MB["parakeet"] >= 1000, (
            "AP-43: _MODEL_SIZE_MB['parakeet'] must be >= 1000 (the ONNX "
            "fp16 export is ~1.28 GB uncompressed); got "
            f"{_MODEL_SIZE_MB['parakeet']} MB."
        )

    def test_parakeet_size_matches_registry(self):
        """disk-space pre-check and the UI's download-size display agree."""
        from voice_typer.server.model_registry import get_model_metadata

        meta = get_model_metadata("parakeet")
        assert meta is not None, "model_registry must define 'parakeet' metadata"
        assert _MODEL_SIZE_MB["parakeet"] == meta.download_size_mb, (
            "AP-43: _MODEL_SIZE_MB['parakeet'] must match "
            "model_registry's download_size_mb so the pre-check and the "
            f"UI agree; got _MODEL_SIZE_MB={_MODEL_SIZE_MB['parakeet']} "
            f"vs registry={meta.download_size_mb}."
        )

    def test_parakeet_pre_check_requires_1775mb_free(self, monkeypatch, tmp_path):
        """End-to-end: ``_check_disk_space_for_download(repo, 'parakeet')``"""
        import shutil

        from voice_typer.server.asr_utils import _check_disk_space_for_download

        fake_hf_constants = type("FakeHFConstants", (), {"HF_HUB_CACHE": str(tmp_path)})()
        monkeypatch.setitem(
            __import__("sys").modules,
            "huggingface_hub",
            type("FakeHF", (), {"constants": fake_hf_constants})(),
        )

        # Simulate ~1.5 GB free (1536 MB), must RAISE (1775 MB required).
        usage_1_5gb = shutil._ntuple_diskusage(0, 0, 1536 * 1024 * 1024)
        monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage_1_5gb)

        with pytest.raises(RuntimeError) as excinfo:
            _check_disk_space_for_download("nvidia/parakeet-tdt-0.6b-v3", "parakeet")

        msg = str(excinfo.value)
        assert "Insufficient disk space" in msg
        assert "1275 MB" in msg, (
            f"AP-43: error message must show the 1275 MB parakeet estimate (not the 500 MB default); got: {msg!r}"
        )

        # And with ~2 GB free (2048 MB), the check must PASS.
        usage_2gb = shutil._ntuple_diskusage(0, 0, 2048 * 1024 * 1024)
        monkeypatch.setattr(shutil, "disk_usage", lambda _p: usage_2gb)
        # Must not raise.
        _check_disk_space_for_download("nvidia/parakeet-tdt-0.6b-v3", "parakeet")
