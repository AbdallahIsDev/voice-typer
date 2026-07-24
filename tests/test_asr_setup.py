"""Tests for ASR auto-setup utilities.

GT-15 / GT-B2-4 (Session 8 — Group 5): tests for the download-failure
traceback capture (``sys.exc_info()`` into the return tuple, full
traceback logged via ``exc_info=True``) and the
``_verify_model_integrity -> (ok, details)`` tuple return.
"""

import logging
import sys
import types

from voice_typer.server import asr_setup
from voice_typer.server.asr_setup import (
    _verify_model_integrity,
    download_parakeet_weights,
)


def _install_hf_stub():
    """Install a stub huggingface_hub module so the import succeeds."""
    if "huggingface_hub" not in sys.modules:
        stub = types.ModuleType("huggingface_hub")

        def fake_snapshot(*args, **kwargs):
            return None

        stub.snapshot_download = fake_snapshot
        sys.modules["huggingface_hub"] = stub


class TestVerifyModelIntegrityReturnsDetails:
    """GT-B2-4: ``_verify_model_integrity`` returns ``(ok, details)``."""

    def test_returns_two_tuple(self, tmp_path):
        result = _verify_model_integrity(
            "nvidia/parakeet-tdt-0.6b-v3",
            str(tmp_path / "does-not-exist"),
        )
        assert isinstance(result, tuple)
        assert len(result) == 2
        ok, details = result
        assert isinstance(ok, bool)
        assert isinstance(details, dict)

    def test_details_dict_has_expected_keys_on_failure(self, tmp_path):
        ok, details = _verify_model_integrity(
            "nvidia/parakeet-tdt-0.6b-v3",
            str(tmp_path / "does-not-exist"),
        )
        assert ok is False
        for key in (
            "failed_file",
            "expected_hash",
            "actual_hash",
            "allow_pattern_matched",
        ):
            assert key in details, (
                f"details dict must include the {key!r} key so callers "
                "can surface a useful diagnostic when the integrity "
                "check fails."
            )


class TestDownloadParakeetWeightsCapturesTraceback:
    """GT-15: download-failure path captures ``sys.exc_info()`` and
    logs the full traceback via ``exc_info=True``."""

    def test_return_shape_is_three_tuple(self, monkeypatch):
        _install_hf_stub()

        def fake_download_with_retry(*args, **kwargs):
            raise RuntimeError("simulated HF 429 rate-limit")

        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            fake_download_with_retry,
            raising=False,
        )
        monkeypatch.setattr(
            "voice_typer.server.transcription._check_disk_space_for_download",
            lambda *a, **kw: None,
            raising=False,
        )

        result = download_parakeet_weights(progress_callback=lambda msg: None)
        assert isinstance(result, tuple)
        assert len(result) == 3, "GT-15: download_parakeet_weights must return a 3-tuple."
        success, reason, exc_info = result
        assert success is False
        assert reason == "download_retry_exhausted"
        assert exc_info is not None
        assert exc_info[0] is RuntimeError
        assert isinstance(exc_info[1], RuntimeError)
        assert "simulated HF 429 rate-limit" in str(exc_info[1])
        assert exc_info[2] is not None

    def test_log_error_uses_exc_info_true(self, monkeypatch, caplog):
        _install_hf_stub()

        def fake_download_with_retry(*args, **kwargs):
            raise RuntimeError("simulated network failure for log test")

        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            fake_download_with_retry,
            raising=False,
        )
        monkeypatch.setattr(
            "voice_typer.server.transcription._check_disk_space_for_download",
            lambda *a, **kw: None,
            raising=False,
        )

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.asr_setup"):
            download_parakeet_weights(progress_callback=lambda msg: None)

        error_records = [
            r for r in caplog.records if r.levelno == logging.ERROR and "download attempts failed" in r.getMessage()
        ]
        assert error_records, "GT-15: download failure must produce an ERROR log record."
        rec = error_records[-1]
        assert rec.exc_info is not None, (
            "GT-15: log.error must be called with exc_info=True so the full traceback is written to the log file."
        )
        assert rec.exc_info[0] is RuntimeError


class TestDownloadParakeetWeightsIntegrityCheckLogsDetails:
    """GT-B2-4: integrity-check failure logs the diagnostic details at
    ERROR before ``_cleanup_failed_cache`` removes the offending files."""

    def test_integrity_check_failure_logs_failed_file(self, monkeypatch, caplog, tmp_path):
        _install_hf_stub()

        monkeypatch.setattr(
            "voice_typer.server.transcription._download_with_retry",
            lambda *a, **kw: str(tmp_path / "fake-download"),
            raising=False,
        )
        monkeypatch.setattr(
            "voice_typer.server.transcription._check_disk_space_for_download",
            lambda *a, **kw: None,
            raising=False,
        )
        fake_details = {
            "failed_file": "config.json",
            "expected_hash": "abcdef0123456789" * 4,
            "actual_hash": "deadbeefdeadbeef" * 4,
            "allow_pattern_matched": True,
        }
        monkeypatch.setattr(
            asr_setup,
            "_verify_model_integrity",
            lambda repo_id, local_dir: (False, fake_details),
        )
        monkeypatch.setattr(asr_setup, "_cleanup_failed_cache", lambda repo_id: None)

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.asr_setup"):
            result = download_parakeet_weights(progress_callback=lambda msg: None)

        success, reason, exc_info = result
        assert success is False
        assert reason == "integrity_check_failed"

        integrity_errors = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "integrity check failed after download" in r.getMessage()
        ]
        assert integrity_errors, "GT-B2-4: integrity-check failure must produce an ERROR log record."
        msg = integrity_errors[-1].getMessage()
        assert "config.json" in msg, "failed_file must be in the log message"
        assert "allow_pattern_matched=True" in msg


class TestConsentGateReturnShape:
    """GT-15: the consent-gate return path also returns a 3-tuple
    (exc_info=None — no exception was raised)."""

    def test_consent_false_returns_three_tuple_with_none_exc_info(self):
        class _Config:
            huggingface_consent = False

        result = download_parakeet_weights(config=_Config())
        assert isinstance(result, tuple)
        assert len(result) == 3
        success, reason, exc_info = result
        assert success is False
        assert reason == "huggingface_consent_false"
        assert exc_info is None
