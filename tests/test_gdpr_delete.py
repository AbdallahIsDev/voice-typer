"""CR-87 regression guard — verify GDPR right-to-delete.

Finding CR-87 (High): GDPR Art. 17 (right-to-erasure) is incomplete —
``service.clear_history()`` only deletes rows in ``history.db``. The
following personal-data artifacts are NOT deletable today:

- ``voice-typer-recovery.json`` (crash-recovery buffer with last 10
  unpasted transcriptions — pure PII).
- ``config.json`` (user config + consent flags).
- ``voice-typer-corrections.json`` (user customizations — PII).
- ``vocabulary.json`` (user-added vocabulary — PII).
- ``templates.json`` (user templates — PII).
- ``mic-test-*.wav`` (mic-test recordings — voice biometric data).
- ``voice-typer.log`` (log file — PII redacted but still personal).
- ``crash-*.dmp`` (crash dumps — may contain memory snapshots).

Model artifacts (``<config_dir>/models/`` and
``<config_dir>/huggingface/``) are explicitly OUT OF SCOPE — model
weights are not personal data.

Fix-D adds a new ``service.delete_all_personal_data()`` method that
erases every artifact above and returns
``{"success": bool, "erased": [list of artifact paths]}``.

This is a Fix-T test (coordinates with Fix-D). It is expected to
FAIL until Fix-D lands the new method.
"""

from __future__ import annotations

import json
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

    # Redirect config_dir lookups to tmp_path.
    import pytest as _pt  # local alias

    mp = _pt.MonkeyPatch()
    mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
    return svc, mp


def _seed_personal_data(tmp_path: Path) -> dict[str, Path]:
    """Create every personal-data artifact that GDPR-delete must erase.

    Returns a dict mapping artifact-name → path so each test can
    assert the file no longer exists after delete.
    """
    artifacts: dict[str, Path] = {}

    # 1. history.db — sqlite file (use real history_db so clear_all works).
    from voice_typer.server.history_db import HistoryDB

    hdb = HistoryDB(db_path=tmp_path / "history.db")
    hdb.add_transcription("secret transcript 1")
    hdb.add_transcription("secret transcript 2")
    hdb.flush()
    hdb.close()
    artifacts["history.db"] = tmp_path / "history.db"

    # 2. voice-typer-recovery.json
    rec_path = tmp_path / "voice-typer-recovery.json"
    rec_path.write_text(json.dumps({"entries": [{"text": "recovered pii"}]}))
    artifacts["recovery.json"] = rec_path

    # 3. config.json — with consent + secrets
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(
        json.dumps(
            {
                "onboarding_completed": True,
                "llm_api_key": "sk-test-123",
                "auto_punctuation": True,
            }
        )
    )
    artifacts["config.json"] = cfg_path

    # 4. voice-typer-corrections.json
    corr_path = tmp_path / "voice-typer-corrections.json"
    corr_path.write_text(json.dumps({"recieve": "receive"}))
    artifacts["corrections.json"] = corr_path

    # 5. vocabulary.json
    vocab_path = tmp_path / "voice-typer-vocabulary.json"
    vocab_path.write_text(json.dumps({"custom": ["my-secret-term"]}))
    artifacts["vocabulary.json"] = vocab_path

    # 6. templates.json
    tmpl_path = tmp_path / "voice-typer-templates.json"
    tmpl_path.write_text(json.dumps({"greeting": "Hi <name>"}))
    artifacts["templates.json"] = tmpl_path

    # 7. mic-test-*.wav (at least one)
    mic_test_path = tmp_path / "mic-test-20240101-120000.wav"
    mic_test_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    artifacts["mic-test.wav"] = mic_test_path

    # 8. voice-typer.log
    log_path = tmp_path / "voice-typer.log"
    log_path.write_text("2024-01-01 12:00:00 INFO [SERVICE] transcript='secret text'\n")
    artifacts["voice-typer.log"] = log_path

    # 9. crash-*.dmp
    crash_path = tmp_path / "crash-20240101-120000.dmp"
    crash_path.write_bytes(b"\x00\x01\x02MDMP")
    artifacts["crash.dmp"] = crash_path

    return artifacts


def _seed_model_artifacts(tmp_path: Path) -> dict[str, Path]:
    """Create model artifacts that MUST NOT be deleted by GDPR-delete
    (model weights are not personal data per the spec)."""
    artifacts: dict[str, Path] = {}
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    model_file = models_dir / "faster-whisper-small.en.bin"
    model_file.write_bytes(b"\x00" * 1024)
    artifacts["models/faster-whisper-small.en.bin"] = model_file

    hf_dir = tmp_path / "huggingface" / "hub" / "models--test--subdir"
    hf_dir.mkdir(parents=True, exist_ok=True)
    hf_file = hf_dir / "model.safetensors"
    hf_file.write_bytes(b"\x00" * 1024)
    artifacts["huggingface/.../model.safetensors"] = hf_file

    return artifacts


def test_delete_all_personal_data_method_exists() -> None:
    """``VoiceTyperService.delete_all_personal_data`` must exist."""
    from voice_typer.server.service import VoiceTyperService

    assert hasattr(VoiceTyperService, "delete_all_personal_data"), (
        "VoiceTyperService must define delete_all_personal_data — see CR-87 / Fix-D."
    )


def test_delete_all_personal_data_erases_history_db(tmp_path) -> None:
    """history.db must be deleted (or emptied)."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        result = svc.delete_all_personal_data()
        assert result["success"] is True
        # History DB either gone or empty.
        db_path = artifacts["history.db"]
        if db_path.exists():
            # If the file still exists, it must be empty (zero rows).
            from voice_typer.server.history_db import HistoryDB

            hdb = HistoryDB(db_path=db_path)
            try:
                rows = hdb.get_recent(limit=100)
                assert rows == [], f"history.db still has {len(rows)} rows after delete"
            finally:
                hdb.close()
        # else: file was deleted — also acceptable.
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_recovery_json(tmp_path) -> None:
    """voice-typer-recovery.json must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["recovery.json"].exists(), (
            "voice-typer-recovery.json must be deleted — contains unpasted transcript PII (CR-87)."
        )
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_config_json(tmp_path) -> None:
    """config.json must be deleted (or reset to defaults)."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        # Either the file is gone OR it has been reset to defaults
        # (no secrets, no onboarding_completed).
        cfg_path = artifacts["config.json"]
        if cfg_path.exists():
            data = json.loads(cfg_path.read_text())
            assert not data.get("llm_api_key"), "config.json still contains llm_api_key after GDPR delete"
            assert not data.get("onboarding_completed"), "config.json still has onboarding_completed=True after delete"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_corrections(tmp_path) -> None:
    """voice-typer-corrections.json must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["corrections.json"].exists()
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_vocabulary(tmp_path) -> None:
    """vocabulary.json must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["vocabulary.json"].exists()
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_templates(tmp_path) -> None:
    """templates.json must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["templates.json"].exists()
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_mic_test_recordings(tmp_path) -> None:
    """mic-test-*.wav files must be deleted (voice biometric data)."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["mic-test.wav"].exists()
        # ALL mic-test-*.wav files should be gone, not just the first one.
        remaining = list(tmp_path.glob("mic-test-*.wav"))
        assert remaining == [], f"mic-test-*.wav files still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_truncates_log(tmp_path) -> None:
    """voice-typer.log must be deleted or truncated."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        log_path = artifacts["voice-typer.log"]
        if log_path.exists():
            assert log_path.stat().st_size == 0, "voice-typer.log still has content after GDPR delete"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_crash_dumps(tmp_path) -> None:
    """crash-*.dmp files must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["crash.dmp"].exists()
        remaining = list(tmp_path.glob("crash-*.dmp"))
        assert remaining == [], f"crash-*.dmp files still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_preserves_model_artifacts(tmp_path) -> None:
    """CR-87 spec: model weights are NOT personal data — must be preserved."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        model_artifacts = _seed_model_artifacts(tmp_path)
        svc.delete_all_personal_data()
        for name, path in model_artifacts.items():
            assert path.exists(), (
                f"Model artifact {name} ({path}) must NOT be deleted by "
                "GDPR delete — model weights are not personal data (CR-87)."
            )
    finally:
        mp.undo()


def test_delete_all_personal_data_returns_erased_list(tmp_path) -> None:
    """Return value must be ``{"success": bool, "erased": [paths]}``."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.delete_all_personal_data()
        assert isinstance(result, dict)
        assert "success" in result
        assert result["success"] is True
        assert "erased" in result
        assert isinstance(result["erased"], list)
        # At least the personal-data artifacts should be listed.
        assert len(result["erased"]) >= 5, f"Expected at least 5 erased paths, got: {result['erased']}"
    finally:
        mp.undo()


def test_delete_all_personal_data_succeeds_when_nothing_exists(tmp_path) -> None:
    """If the config dir is empty (fresh install), the call still succeeds."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        result = svc.delete_all_personal_data()
        assert result["success"] is True
    finally:
        mp.undo()
