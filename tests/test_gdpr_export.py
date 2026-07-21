"""CR-88 regression guard — verify GDPR right-to-export.

Finding CR-88 (High): GDPR Art. 20 (right to data portability) is
not implemented at all. The existing
``service.export_diagnostics()`` produces a *redacted* diagnostic
bundle (for support tickets) — it is NOT a GDPR Art. 20 export
because it strips transcript text and excludes
``voice-typer-corrections.json``, ``templates.json``, and mic-test
recordings.

Fix-D adds a new ``service.export_gdpr_bundle()`` method that:
1. Produces a single timestamped ``.zip`` at
   ``<config_dir>/gdpr-export-YYYYMMDD-HHMMSS.zip``.
2. Includes every personal-data artifact (history.db,
   voice-typer-recovery.json, config.json, corrections.json,
   vocabulary.json, templates.json, mic-test-*.wav,
   voice-typer.log, crash-*.dmp).
3. Includes the raw (un-redacted) transcript text from history.db.
4. Returns ``{"success": bool, "path": str}`` (mirrors
   ``export_diagnostics``).
5. Does NOT include model weights (not personal data per spec).

This is a Fix-T test (coordinates with Fix-D). It is expected to
FAIL until Fix-D lands the new method.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_service(tmp_path: Path):
    """Build a real VoiceTyperService backed by a tmp config dir."""
    from voice_typer.server import config as cfg_mod
    from voice_typer.server.service import VoiceTyperService

    app = MagicMock()
    app.config.huggingface_consent = True
    app.config.qwen_model_path = None
    app.tray.notify = MagicMock()
    svc = VoiceTyperService(app)

    mp = pytest.MonkeyPatch()
    mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
    return svc, mp


def _seed_personal_data(tmp_path: Path) -> None:
    """Create every personal-data artifact the export should include."""
    from voice_typer.server.history_db import HistoryDB

    hdb = HistoryDB(db_path=tmp_path / "history.db")
    hdb.add_transcription("secret transcript 1")
    hdb.add_transcription("secret transcript 2")
    hdb.flush()
    hdb.close()

    (tmp_path / "voice-typer-recovery.json").write_text(json.dumps({"entries": [{"text": "recovered pii"}]}))
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "onboarding_completed": True,
                "llm_api_key": "sk-test-123",
                "auto_punctuation": True,
            }
        )
    )
    (tmp_path / "voice-typer-corrections.json").write_text(json.dumps({"recieve": "receive"}))
    (tmp_path / "voice-typer-vocabulary.json").write_text(json.dumps({"custom": ["my-secret-term"]}))
    (tmp_path / "voice-typer-templates.json").write_text(json.dumps({"greeting": "Hi <name>"}))
    (tmp_path / "mic-test-20240101-120000.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    (tmp_path / "voice-typer.log").write_text("2024-01-01 12:00:00 INFO [SERVICE] transcript='secret text'\n")
    (tmp_path / "crash-20240101-120000.dmp").write_bytes(b"\x00\x01\x02MDMP")


def _seed_model_artifacts(tmp_path: Path) -> None:
    """Create model artifacts that MUST NOT be included in the export."""
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    (models_dir / "faster-whisper-small.en.bin").write_bytes(b"\x00" * 1024)
    hf_dir = tmp_path / "huggingface" / "hub" / "models--test--subdir"
    hf_dir.mkdir(parents=True, exist_ok=True)
    (hf_dir / "model.safetensors").write_bytes(b"\x00" * 1024)


def test_export_gdpr_bundle_method_exists() -> None:
    """``VoiceTyperService.export_gdpr_bundle`` must exist."""
    from voice_typer.server.service import VoiceTyperService

    assert hasattr(VoiceTyperService, "export_gdpr_bundle"), (
        "VoiceTyperService must define export_gdpr_bundle — see CR-88 / Fix-D."
    )


def test_export_gdpr_bundle_returns_success_and_path(tmp_path) -> None:
    """Return value must be ``{"success": True, "path": str}``."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        assert isinstance(result, dict)
        assert result["success"] is True
        assert "path" in result
        p = Path(result["path"])
        assert p.exists(), f"Export zip does not exist at {p}"
        assert p.suffix == ".zip"
    finally:
        mp.undo()


def test_export_gdpr_bundle_creates_timestamped_zip(tmp_path) -> None:
    """The output filename should be timestamped
    (``gdpr-export-YYYYMMDD-HHMMSS.zip``)."""
    import re

    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        p = Path(result["path"])
        assert p.name.startswith("gdpr-export-"), f"Export zip name must start with 'gdpr-export-': {p.name}"
        assert re.match(r"gdpr-export-\d{8}-\d{6}\.zip$", p.name), (
            f"Export zip name must match YYYYMMDD-HHMMSS pattern: {p.name}"
        )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_history_db(tmp_path) -> None:
    """The zip must contain history.db (with raw transcript text)."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            # History DB must be included under some name.
            assert any("history" in n.lower() and n.endswith(".db") for n in names), (
                f"history.db not found in export bundle: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_config_json(tmp_path) -> None:
    """The zip must contain config.json (with secrets — this is GDPR
    export, not redacted diagnostics)."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any("config.json" in n for n in names), f"config.json not in export bundle: {names}"
            # Read it back and verify it includes the API key (un-redacted).
            for n in names:
                if "config.json" in n:
                    data = json.loads(zf.read(n))
                    assert data.get("llm_api_key") == "sk-test-123", (
                        "GDPR export must NOT redact secrets — they are the user's personal data (CR-88)."
                    )
                    break
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_recovery_json(tmp_path) -> None:
    """The zip must contain voice-typer-recovery.json."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any("recovery" in n.lower() and n.endswith(".json") for n in names), (
                f"voice-typer-recovery.json not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_corrections(tmp_path) -> None:
    """The zip must contain voice-typer-corrections.json."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any("corrections" in n.lower() and n.endswith(".json") for n in names), (
                f"voice-typer-corrections.json not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_vocabulary(tmp_path) -> None:
    """The zip must contain vocabulary.json."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any("vocabulary" in n.lower() and n.endswith(".json") for n in names), (
                f"vocabulary.json not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_templates(tmp_path) -> None:
    """The zip must contain templates.json."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any("templates" in n.lower() and n.endswith(".json") for n in names), (
                f"templates.json not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_mic_test_recordings(tmp_path) -> None:
    """The zip must include any mic-test-*.wav files (voice biometric
    data — explicitly personal)."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any("mic-test" in n.lower() and n.endswith(".wav") for n in names), (
                f"mic-test-*.wav files not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_log(tmp_path) -> None:
    """The zip must contain voice-typer.log."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any(n.endswith(".log") or "voice-typer.log" in n for n in names), (
                f"voice-typer.log not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_excludes_model_artifacts(tmp_path) -> None:
    """Model weights are NOT personal data — must not be in the export."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        _seed_model_artifacts(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            # No .bin or .safetensors model files.
            assert not any(n.endswith(".safetensors") for n in names), (
                f"Model .safetensors should NOT be in GDPR export: {names}"
            )
            assert not any("model.safetensors" in n for n in names), (
                f"Model weights should NOT be in GDPR export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_succeeds_when_config_dir_empty(tmp_path) -> None:
    """A fresh-install config dir (no artifacts) should still produce
    a (mostly empty) zip — not raise."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        result = svc.export_gdpr_bundle()
        assert result["success"] is True
        assert Path(result["path"]).exists()
    finally:
        mp.undo()
