"""regression guard: verify GDPR right-to-delete."""

from __future__ import annotations

import json
import os
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
    """Create every personal-data artifact that GDPR-delete must erase."""
    artifacts: dict[str, Path] = {}

    # 1. history.db, sqlite file (use real history_db so clear_all works).
    from voice_typer.server.history_db import HistoryDB

    hdb = HistoryDB(db_path=tmp_path / "history.db")
    hdb.add_transcription("secret transcript 1")
    hdb.add_transcription("secret transcript 2")
    hdb.flush()
    hdb.close()
    artifacts["history.db"] = tmp_path / "history.db"

    # 2. recovery.json
    rec_path = tmp_path / "recovery.json"
    rec_path.write_text(json.dumps({"entries": [{"text": "recovered pii"}]}))
    artifacts["recovery.json"] = rec_path

    # 3. config.json, with consent + secrets
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
    vocab_path = tmp_path / "vocabulary.json"
    vocab_path.write_text(json.dumps({"custom": ["my-secret-term"]}))
    artifacts["vocabulary.json"] = vocab_path

    # 6. templates.json
    tmpl_path = tmp_path / "templates.json"
    tmpl_path.write_text(json.dumps({"greeting": "Hi <name>"}))
    artifacts["templates.json"] = tmpl_path

    # 7. mic-test-*.wav (at least one)
    mic_test_path = tmp_path / "mic-test-20240101-120000.wav"
    mic_test_path.write_bytes(b"RIFF\x00\x00\x00\x00WAVEfmt ")
    artifacts["mic-test.wav"] = mic_test_path

    # 8. voice-typer.log (active log file)
    log_path = tmp_path / "voice-typer.log"
    log_path.write_text("2024-01-01 12:00:00 INFO [SERVICE] transcript='secret text'\n")
    artifacts["voice-typer.log"] = log_path

    # 8b. : voice-typer.log.{1,2}, rotated backups produced by
    log1_path = tmp_path / "voice-typer.log.1"
    log1_path.write_text("2024-01-01 11:00:00 DEBUG transcript='rotated secret 1'\n")
    artifacts["voice-typer.log.1"] = log1_path
    log2_path = tmp_path / "voice-typer.log.2"
    log2_path.write_text("2024-01-01 10:00:00 DEBUG transcript='rotated secret 2'\n")
    artifacts["voice-typer.log.2"] = log2_path

    # 9. : real crash files, crash_diagnostics.<PID>.txt
    _pid = os.getpid()
    crash_diag_path = tmp_path / f"crash_diagnostics.{_pid}.txt"
    crash_diag_path.write_text(f"VEH crash dump for PID {_pid}\nstack trace with secret='pii'\n")
    artifacts["crash_diagnostics.txt"] = crash_diag_path

    py_crash_path = tmp_path / f"python_crash.{_pid}.txt"
    py_crash_path.write_text(f"Python excepthook marker for PID {_pid}\ntraceback with secret='pii'\n")
    artifacts["python_crash.txt"] = py_crash_path

    # 10. : prewarm.log + rotated backup (prewarm process
    prewarm_path = tmp_path / "prewarm.log"
    prewarm_path.write_text("2024-01-01 12:00:00 INFO [PREWARM] warming model with secret='pii'\n")
    artifacts["prewarm.log"] = prewarm_path
    prewarm1_path = tmp_path / "prewarm.log.1"
    prewarm1_path.write_text("2024-01-01 11:00:00 DEBUG [PREWARM] rotated secret\n")
    artifacts["prewarm.log.1"] = prewarm1_path

    # 11. : Rust host logs/ subdirectory (written by
    rust_logs_dir = tmp_path / "logs"
    rust_logs_dir.mkdir(parents=True, exist_ok=True)
    rust_log_path = rust_logs_dir / "voice-typer.log"
    rust_log_path.write_text("2024-01-01 12:00:00 INFO [rust] transcript='secret from rust'\n")
    artifacts["logs/voice-typer.log"] = rust_log_path
    rust_log1_path = rust_logs_dir / "voice-typer.log.1"
    rust_log1_path.write_text("2024-01-01 11:00:00 INFO [rust] rotated secret\n")
    artifacts["logs/voice-typer.log.1"] = rust_log1_path

    return artifacts


def _seed_model_artifacts(tmp_path: Path) -> dict[str, Path]:
    """Create model artifacts that MUST NOT be deleted by GDPR-delete"""
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
        "VoiceTyperService must define delete_all_personal_data: see CR-87 / Fix-D."
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
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_recovery_json(tmp_path) -> None:
    """recovery.json must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["recovery.json"].exists(), (
            "recovery.json must be deleted, contains unpasted transcript PII (CR-87)."
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


def test_delete_all_personal_data_erases_rotated_log_backups(tmp_path) -> None:
    """PI-4: voice-typer.log.{1,2} rotated backups must be unlinked."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["voice-typer.log.1"].exists(), (
            "voice-typer.log.1 (rotated backup) must be deleted, may contain "
            "user-spoken text per XZ-PII-01/XZ-PRIV-04 (PI-4)."
        )
        assert not artifacts["voice-typer.log.2"].exists(), (
            "voice-typer.log.2 (rotated backup) must be deleted, may contain "
            "user-spoken text per XZ-PII-01/XZ-PRIV-04 (PI-4)."
        )
        # ALL voice-typer.log.* files should be gone, not just .1 and .2.
        remaining = list(tmp_path.glob("voice-typer.log.*"))
        assert remaining == [], f"voice-typer.log.* rotated backups still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_crash_dumps(tmp_path) -> None:
    """PI-5: crash_diagnostics.<PID>.txt + python_crash.<PID>.txt must"""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["crash_diagnostics.txt"].exists(), (
            "crash_diagnostics.<PID>.txt must be deleted, Windows VEH crash "
            "file written by crash_handler.py:722 (PI-5)."
        )
        assert not artifacts["python_crash.txt"].exists(), (
            "python_crash.<PID>.txt must be deleted, Python excepthook marker written by crash_handler.py:1190 (PI-5)."
        )
        # No crash_diagnostics.*.txt or python_crash.*.txt should remain.
        remaining_diag = list(tmp_path.glob("crash_diagnostics.*.txt"))
        assert remaining_diag == [], f"crash_diagnostics.*.txt files still present after GDPR delete: {remaining_diag}"
        remaining_py = list(tmp_path.glob("python_crash.*.txt"))
        assert remaining_py == [], f"python_crash.*.txt files still present after GDPR delete: {remaining_py}"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_prewarm_log(tmp_path) -> None:
    """PI-6: prewarm.log + rotated backups must be deleted."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["prewarm.log"].exists(), "prewarm.log must be deleted, prewarm process log (PI-6)."
        assert not artifacts["prewarm.log.1"].exists(), (
            "prewarm.log.1 (rotated backup) must be deleted, prewarm process log rotation (PI-6)."
        )
        remaining = list(tmp_path.glob("prewarm.log*"))
        assert remaining == [], f"prewarm.log* files still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_rust_logs_subdir(tmp_path) -> None:
    """PI-6: ``<config_dir>/logs/`` (Rust host rotating log) must be"""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_personal_data(tmp_path)
        svc.delete_all_personal_data()
        rust_log = artifacts["logs/voice-typer.log"]
        rust_log1 = artifacts["logs/voice-typer.log.1"]
        assert not rust_log.exists(), (
            "<config_dir>/logs/voice-typer.log must be deleted, Rust host log with no PII redaction (PI-6, XZ-LOG-02)."
        )
        assert not rust_log1.exists(), "<config_dir>/logs/voice-typer.log.1 (rotated) must be deleted (PI-6)."
        # The entire logs/ subdirectory should be gone (rmtree).
        assert not (tmp_path / "logs").exists(), (
            "<config_dir>/logs/ subdirectory still exists after GDPR delete, should have been rmtree'd (PI-6)."
        )
    finally:
        mp.undo()


def test_delete_all_personal_data_preserves_model_artifacts(tmp_path) -> None:
    """spec: model weights are NOT personal data, must be preserved."""
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
                "GDPR delete, model weights are not personal data (CR-87)."
            )
    finally:
        mp.undo()


def test_delete_all_personal_data_returns_erased_list(tmp_path) -> None:
    """Return value must be ``{\"success\": bool, \"erased\": [paths]}``."""
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


# The original GDPR inventory missed several personal-data artifacts.


def _seed_xz_sec_03_artifacts(tmp_path: Path) -> dict[str, Path]:
    """Create every artifact added by XZ-SEC-03."""
    artifacts: dict[str, Path] = {}

    bak_path = tmp_path / "config.json.bak"
    bak_path.write_text(json.dumps({"llm_api_key": "sk-test-123"}))
    artifacts["config.json.bak"] = bak_path

    lock_path = tmp_path / "config.json.lock"
    lock_path.write_text(f"pid={os.getpid()}\nowner=test-user\n")
    artifacts["config.json.lock"] = lock_path

    # .restart_token, defensive entry.
    token_path = tmp_path / ".restart_token"
    token_path.write_text("restart-token-secret-pii")
    artifacts[".restart_token"] = token_path

    corrupt_path = tmp_path / "history.db.corrupt-20240101-120000"
    corrupt_path.write_bytes(b"corrupt sqlite plaintext secret='pii'")
    artifacts["history.db.corrupt-*"] = corrupt_path

    diag_path = tmp_path / "voice-typer-diagnostics-20240101-120000.zip"
    diag_path.write_bytes(b"PK\x03\x04 fake zip with pii markers")
    artifacts["voice-typer-diagnostics-*.zip"] = diag_path

    export_path = tmp_path / "gdpr-export-20240101-120000.zip"
    export_path.write_bytes(b"PK\x03\x04 fake gdpr export with pii")
    artifacts["gdpr-export-*.zip"] = export_path

    return artifacts


def test_delete_all_personal_data_erases_config_json_bak(tmp_path) -> None:
    """XZ-SEC-03: ``config.json.bak`` must be erased."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["config.json.bak"].exists(), (
            "config.json.bak must be deleted, retains plaintext API keys (XZ-SEC-03)."
        )
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_config_json_lock(tmp_path) -> None:
    """XZ-SEC-03: ``config.json.lock`` must be erased."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["config.json.lock"].exists(), (
            "config.json.lock must be deleted, holds stale PID + writer username (XZ-SEC-03)."
        )
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_restart_token(tmp_path) -> None:
    """XZ-SEC-03: ``.restart_token`` must be erased if present."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts[".restart_token"].exists(), (
            ".restart_token must be deleted, historically held restart auth secret (XZ-SEC-03)."
        )
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_history_db_corrupt(tmp_path) -> None:
    """XZ-SEC-03: ``history.db.corrupt-*`` must be erased."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["history.db.corrupt-*"].exists(), (
            "history.db.corrupt-* must be deleted, retains dictated plaintext (XZ-SEC-03)."
        )
        remaining = list(tmp_path.glob("history.db.corrupt-*"))
        assert remaining == [], f"history.db.corrupt-* files still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_diagnostics_zip(tmp_path) -> None:
    """XZ-SEC-03: ``voice-typer-diagnostics-*.zip`` must be erased."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["voice-typer-diagnostics-*.zip"].exists(), (
            "voice-typer-diagnostics-*.zip must be deleted, contains history + log fragments (XZ-SEC-03)."
        )
        remaining = list(tmp_path.glob("voice-typer-diagnostics-*.zip"))
        assert remaining == [], f"voice-typer-diagnostics-*.zip files still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_gdpr_export_zip(tmp_path) -> None:
    """XZ-SEC-03: ``gdpr-export-*.zip`` must be erased."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        svc.delete_all_personal_data()
        assert not artifacts["gdpr-export-*.zip"].exists(), (
            "gdpr-export-*.zip must be deleted, contains user's full personal data (XZ-SEC-03)."
        )
        remaining = list(tmp_path.glob("gdpr-export-*.zip"))
        assert remaining == [], f"gdpr-export-*.zip files still present after GDPR delete: {remaining}"
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_all_xz_sec_03_artifacts(tmp_path) -> None:
    """XZ-SEC-03: all six artifacts must be erased in a single call."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        artifacts = _seed_xz_sec_03_artifacts(tmp_path)
        result = svc.delete_all_personal_data()
        assert result["success"] is True
        for name, path in artifacts.items():
            assert not path.exists(), f"{name} survived GDPR delete, XZ-SEC-03 regression."
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_config_backup_classes_xe_10_4(tmp_path) -> None:
    """GDPR Art. 17 delete."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        secret_payload = json.dumps({"openai_api_key": "sk-xe-10-4-secret"})
        backup_paths = [
            tmp_path / "config.json.v5-1785432251-20197-511458.bak",
            tmp_path / "config.json.v5.bak",
            tmp_path / "config.json.pre-migration-v4-1785432251-20197-511458.bak",
            tmp_path / "config.json.bak.failed-migration-1785432251-to-v5",
            tmp_path / "config.json.corrupt-1785432251-20197-511458",
        ]
        for p in backup_paths:
            p.write_text(secret_payload)
            assert p.exists(), f"setup failed: {p}"

        svc.delete_all_personal_data()

        for p in backup_paths:
            assert not p.exists(), (
                f"XE-10-4 regression: {p.name} survived GDPR delete, "
                f"plaintext API keys remain on disk (GDPR Art. 17 "
                f"violation)."
            )
    finally:
        mp.undo()


def test_delete_all_personal_data_erases_history_pre_migration_bak_xe_6_3(tmp_path) -> None:
    """XE-6-3: ``history.db.pre-migration-v*`` must be erased by GDPR"""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "delete_all_personal_data"):
            pytest.skip("Fix-D not yet landed")
        backup_path = tmp_path / "history.db.pre-migration-v2-1785432251-20197-511458.bak"
        backup_path.write_bytes(b"sqlite plaintext dictated text from v2 migration")
        assert backup_path.exists()

        svc.delete_all_personal_data()

        assert not backup_path.exists(), (
            f"XE-6-3 regression: {backup_path.name} survived GDPR delete, "
            f"plaintext dictated text remains on disk (GDPR Art. 17 "
            f"violation)."
        )
    finally:
        mp.undo()
