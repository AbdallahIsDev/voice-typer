"""Tests for ASR auto-setup utilities.

GT-15 / GT-B2-4 (Session 8 — Group 5): tests for the download-failure
traceback capture (``sys.exc_info()`` into the return tuple, full
traceback logged via ``exc_info=True``) and the
``_verify_model_integrity -> (ok, details)`` tuple return.
"""

import logging
import sys
import types
from unittest.mock import MagicMock, patch

from voice_typer.server import asr_setup
from voice_typer.server.asr_setup import (
    _verify_model_integrity,
    download_parakeet_weights,
)


def _install_hf_stub():
    """Install a package-shaped stub ``huggingface_hub`` so imports succeed.

    The download-gate tqdm subclass imports
    ``huggingface_hub.utils.tqdm`` lazily, so the stub must be a real
    package in ``sys.modules`` (``huggingface_hub`` + ``.utils`` +
    ``.utils.tqdm`` with a minimal ``tqdm`` class) — a bare module
    would make that import raise ``ModuleNotFoundError`` (``'huggingface_hub'
    is not a package``) before the patched ``snapshot_download`` ever runs.
    """
    if "huggingface_hub" in sys.modules:
        return

    def fake_snapshot(*args, **kwargs):
        return None

    stub = types.ModuleType("huggingface_hub")
    stub.snapshot_download = fake_snapshot
    stub.__path__ = []  # mark as package so submodule imports resolve
    sys.modules["huggingface_hub"] = stub

    utils = types.ModuleType("huggingface_hub.utils")
    utils.__path__ = []
    sys.modules["huggingface_hub.utils"] = utils

    hf_tqdm = types.ModuleType("huggingface_hub.utils.tqdm")

    # Mirrors the real submodule's export name (lowercase `tqdm`), which
    # the gate subclass imports by that exact name. The minimal stand-in
    # only needs the constructor/protocol surface the gate touches.
    class _StubTqdm:
        def __init__(self, *args, **kwargs):
            self.total = kwargs.get("total")
            self.n = 0

        def update(self, n=1):
            self.n += n

        def close(self):
            pass

    hf_tqdm.tqdm = _StubTqdm
    sys.modules["huggingface_hub.utils.tqdm"] = hf_tqdm


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

        result = download_parakeet_weights(progress_callback=lambda msg: None, force=True)
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
            download_parakeet_weights(progress_callback=lambda msg: None, force=True)

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
            result = download_parakeet_weights(progress_callback=lambda msg: None, force=True)

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


# consent gate safe default (config=None → consent not given) ──
#
# These tests pin the  safe-default behaviour: when ``config`` is
# ``None`` the consent gate MUST treat consent as NOT given (GDPR Art.
# 6/13), aligned with ``parakeet_engine.ParakeetEngine.load``'s safe
# default.  Pre-fix, a ``None`` config silently bypassed the gate.
#
# NOTE: the return shape here follows the  3-tuple contract
# ``(success, reason, exc_info)`` — ``exc_info`` is ``None`` for the
# consent-gate path because no exception was raised. If the production
# ``download_parakeet_weights`` is reverted to the 2-tuple contract,
# these assertions must be updated in lock-step.


class TestConsentGateSafeDefault:
    """DE-58: ``download_parakeet_weights`` must treat ``config=None``
    as "consent NOT given" (safe default per GDPR Art. 6/13), aligned
    with ``parakeet_engine.ParakeetEngine.load``'s safe default.

    Pre-fix: when ``config`` was ``None`` the consent gate was silently
    SKIPPED — the function proceeded straight to ``snapshot_download``,
    leaking the user's IP to HuggingFace and pulling ~2.5 GB over the
    network without explicit opt-in.  Any future refactor that invoked
    ``download_parakeet_weights`` from a production path without
    forwarding ``config`` silently disabled the consent gate.
    """

    def test_config_none_returns_consent_false(self):
        """``download_parakeet_weights(config=None)`` MUST return
        ``(False, "huggingface_consent_false", None)`` and MUST NOT touch the
        network — even though no exception is raised.
        """
        with patch("huggingface_hub.snapshot_download") as mock_sd:
            result = download_parakeet_weights(config=None)

        assert result == (False, "huggingface_consent_false", None)
        # The HuggingFace network call must NOT fire when consent is
        # implicitly not given.
        assert mock_sd.call_count == 0, (
            "DE-58: snapshot_download must not be invoked when config=None (safe default: consent not given)."
        )

    def test_no_args_returns_consent_false(self):
        """Calling ``download_parakeet_weights()`` with no args (the
        legacy signature) MUST also default to consent not given.

        This pins the defense-in-depth guarantee: a future refactor that
        drops the ``config`` argument from a call site cannot silently
        bypass the gate.
        """
        with patch("huggingface_hub.snapshot_download") as mock_sd:
            result = download_parakeet_weights()

        assert result == (False, "huggingface_consent_false", None)
        assert mock_sd.call_count == 0

    def test_config_with_consent_false_returns_consent_false(self):
        """When ``config.huggingface_consent`` is explicitly False, the
        gate refuses and returns the consent-false reason code.
        """
        config = MagicMock()
        config.huggingface_consent = False

        with patch("huggingface_hub.snapshot_download") as mock_sd:
            result = download_parakeet_weights(config=config)

        assert result == (False, "huggingface_consent_false", None)
        assert mock_sd.call_count == 0

    def test_force_true_bypasses_gate(self):
        """``force=True`` is the explicit escape hatch for legacy / test
        paths that have already verified consent upstream and cannot
        forward a real Config object.  It must reach the snapshot_download
        call (mocked here) instead of short-circuiting at the gate.
        """
        # Make snapshot_download's cache probe succeed so the function
        # returns (True, "", None) without actually downloading.
        with (
            patch(
                "huggingface_hub.snapshot_download",
                return_value="/fake/cache/path",
            ) as mock_sd,
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=(True, {}),
            ),
        ):
            result = download_parakeet_weights(force=True)

        assert result == (True, "", None)
        # snapshot_download must have been invoked at least once
        # (the cache probe is the first call).
        assert mock_sd.call_count >= 1, "DE-58: force=True must bypass the consent gate and reach snapshot_download."

    def test_force_true_does_not_require_config(self):
        """``force=True`` works even when ``config`` is ``None`` (the
        legacy bypass scenario) — but the bypass is now EXPLICIT at the
        call site, not implicit.
        """
        with (
            patch("huggingface_hub.snapshot_download", return_value="/fake/cache/path"),
            patch(
                "voice_typer.server.asr_setup._verify_model_integrity",
                return_value=(True, {}),
            ),
        ):
            # Both config=None AND force=True — force wins.
            result = download_parakeet_weights(config=None, force=True)

        assert result == (True, "", None)


class TestConsentGateProgressCallback:
    """DE-58: when the consent gate refuses, the progress_callback (if
    provided) MUST be invoked with a human-readable consent message so
    the renderer / tray can surface the reason to the user.
    """

    def test_progress_callback_invoked_on_consent_false(self):
        progress_messages: list[str] = []
        download_parakeet_weights(
            progress_callback=progress_messages.append,
            config=None,
        )

        assert progress_messages, (
            "DE-58: progress_callback must be invoked with a consent-required message when the gate refuses."
        )
        assert any("consent" in msg.lower() for msg in progress_messages), (
            f"DE-58: progress_callback message must mention consent; got: {progress_messages!r}"
        )
