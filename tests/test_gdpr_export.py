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
   voice-typer.log + rotated backups (PI-4),
   crash_diagnostics.<PID>.txt + python_crash.<PID>.txt (PI-5)).
3. Includes the raw (un-redacted) transcript text from history.db.
4. Returns ``{"success": bool, "path": str}`` (mirrors
   ``export_diagnostics``).
5. Does NOT include model weights (not personal data per spec).

This is a Fix-T test (coordinates with Fix-D). It is expected to
FAIL until Fix-D lands the new method.
"""

from __future__ import annotations

import json
import os
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
    # rotated log backups produced by RotatingFileHandler(backupCount=5)
    # in voice_typer/server/log.py:911-915.  Per  /  these
    # may contain user-spoken text, so they MUST be included in the export.
    (tmp_path / "voice-typer.log.1").write_text("2024-01-01 11:00:00 DEBUG transcript='rotated secret 1'\n")
    (tmp_path / "voice-typer.log.2").write_text("2024-01-01 10:00:00 DEBUG transcript='rotated secret 2'\n")
    # real crash files (not the fictional ``crash-*.dmp``).
    #   * ``crash_diagnostics.<PID>.txt`` — Windows VEH handler (crash_handler.py:722)
    #   * ``python_crash.<PID>.txt``     — Python excepthook marker (crash_handler.py:1190)
    _pid = os.getpid()
    (tmp_path / f"crash_diagnostics.{_pid}.txt").write_text(
        f"VEH crash dump for PID {_pid}\nstack trace with secret='pii'\n"
    )
    (tmp_path / f"python_crash.{_pid}.txt").write_text(
        f"Python excepthook marker for PID {_pid}\ntraceback with secret='pii'\n"
    )


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


def test_export_gdpr_bundle_includes_rotated_log_backups(tmp_path) -> None:
    """PI-4: the zip must contain voice-typer.log.{1,2} rotated backups.

    ``RotatingFileHandler(backupCount=5)`` in ``voice_typer/server/log.py``
    produces ``voice-typer.log.1`` .. ``voice-typer.log.5``.  Per
    XZ-PII-01 / XZ-PRIV-04 these backups may contain user-spoken text
    via ``_crash_excepthook``'s CRITICAL log + per-segment DEBUG logs,
    so the GDPR Art. 20 export must include them.
    """
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert "voice-typer.log.1" in names, f"voice-typer.log.1 (rotated backup) not in export: {names} (PI-4)"
            assert "voice-typer.log.2" in names, f"voice-typer.log.2 (rotated backup) not in export: {names} (PI-4)"
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_crash_files(tmp_path) -> None:
    """PI-5: the zip must contain crash_diagnostics.<PID>.txt and
    python_crash.<PID>.txt (the REAL crash file names written by
    production code), not the fictional ``crash-*.dmp``.
    """
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any(n.startswith("crash_diagnostics.") and n.endswith(".txt") for n in names), (
                f"crash_diagnostics.<PID>.txt not in export: {names} (PI-5)"
            )
            assert any(n.startswith("python_crash.") and n.endswith(".txt") for n in names), (
                f"python_crash.<PID>.txt not in export: {names} (PI-5)"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_is_atomic_no_partial_tmp(tmp_path) -> None:
    """PI-14: on success, no ``.zip.tmp`` partial artifact should
    remain in the config dir (the temp file is renamed into place
    via ``os.replace``).
    """
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        result = svc.export_gdpr_bundle()
        assert result["success"] is True
        # No leftover .zip.tmp file in the config dir.
        leftover = list(tmp_path.glob("*.zip.tmp"))
        assert leftover == [], (
            f"PI-14: leftover .zip.tmp partial artifact after successful "
            f"export (should have been os.replace'd): {leftover}"
        )
        # The final zip exists.
        assert Path(result["path"]).exists()
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


def test_export_gdpr_bundle_includes_rust_logs_subdir(tmp_path) -> None:
    """The zip must include the Rust host's ``logs/`` subdirectory.

    The Rust host (``src-tauri/src/platform/logging.rs:30-34``) writes
    ``<config_dir>/logs/voice-typer.log`` plus rotated backups
    ``.log.1``..``.log.4``.  The Rust logger has no PII redaction, so
    dictated-text fragments may be present.  The GDPR delete path
    rmtree's this directory (see
    ``PrivacyMixin._gdpr_rmtree_rust_logs``), so the export path must
    walk the same directory recursively so the Art. 20 portability
    copy matches the Art. 17 erasure set.
    """
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "voice-typer.log").write_text("2024-01-01 12:00:00 INFO [rust] transcript='secret from rust'\n")
        (logs_dir / "voice-typer.log.1").write_text("2024-01-01 11:00:00 INFO [rust] rotated secret\n")
        result = svc.export_gdpr_bundle()
        assert result["success"] is True
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert "logs/voice-typer.log" in names, f"logs/voice-typer.log not in export: {names}"
            assert "logs/voice-typer.log.1" in names, f"logs/voice-typer.log.1 (rotated backup) not in export: {names}"
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_crash_archive_subdir(tmp_path) -> None:
    """The zip must include the ``crash_diagnostics_archive/`` subdir.

    The crash handler moves processed crash dumps into
    ``<config_dir>/crash_diagnostics_archive/`` for retention (see
    ``voice_typer/server/crash_handler/_diagnostics_archive.py``).
    The GDPR delete path rmtree's this directory (see
    ``PrivacyMixin._gdpr_rmtree_crash_archive``), so the export path
    must walk the same directory recursively so the Art. 20
    portability copy matches the Art. 17 erasure set.
    """
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        archive_dir = tmp_path / "crash_diagnostics_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        _pid = os.getpid()
        archived_veh = archive_dir / f"crash_diagnostics.{_pid}.txt"
        archived_veh.write_text(f"archived VEH crash dump for PID {_pid}\nstack trace with secret='pii'\n")
        archived_py = archive_dir / f"python_crash.{_pid}.txt"
        archived_py.write_text(f"archived Python excepthook marker for PID {_pid}\ntraceback with secret='pii'\n")
        result = svc.export_gdpr_bundle()
        assert result["success"] is True
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert any(n.startswith("crash_diagnostics_archive/") and n.endswith(".txt") for n in names), (
                f"crash_diagnostics_archive/ files not in export: {names}"
            )
            assert any(n.startswith(f"crash_diagnostics_archive/crash_diagnostics.{_pid}.") for n in names), (
                f"crash_diagnostics_archive/crash_diagnostics.<PID>.txt not in export: {names}"
            )
            assert any(n.startswith(f"crash_diagnostics_archive/python_crash.{_pid}.") for n in names), (
                f"crash_diagnostics_archive/python_crash.<PID>.txt not in export: {names}"
            )
    finally:
        mp.undo()


def test_export_gdpr_bundle_includes_subdir_nested_files(tmp_path) -> None:
    """The recursive subdir walk must descend into nested subdirectories
    and preserve the relative path as the zip arcname.

    A nested file at ``<config_dir>/logs/sub/deep.log`` must appear in
    the zip as ``logs/sub/deep.log`` — the on-disk structure is
    preserved inside the zip so the user can navigate the export.
    """
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        nested_dir = tmp_path / "logs" / "nested"
        nested_dir.mkdir(parents=True, exist_ok=True)
        (nested_dir / "deep.log").write_text("nested rust log with secret='pii'\n")
        result = svc.export_gdpr_bundle()
        assert result["success"] is True
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            assert "logs/nested/deep.log" in names, f"nested subdir file logs/nested/deep.log not in export: {names}"
    finally:
        mp.undo()


def test_export_gdpr_bundle_no_partial_subdir_walk_when_missing(tmp_path) -> None:
    """A fresh-install config dir (no ``logs/`` / ``crash_diagnostics_archive/``
    subdirs) must not raise — the recursive subdir walk is a silent no-op
    for missing subdirs."""
    svc, mp = _build_service(tmp_path)
    try:
        if not hasattr(svc, "export_gdpr_bundle"):
            pytest.skip("Fix-D not yet landed")
        _seed_personal_data(tmp_path)
        # NOTE: deliberately NOT creating logs/ or crash_diagnostics_archive/.
        result = svc.export_gdpr_bundle()
        assert result["success"] is True
        with zipfile.ZipFile(result["path"]) as zf:
            names = zf.namelist()
            # No subdir-prefixed entries should exist when the subdirs
            # are absent — the walk is a no-op.
            assert not any(n.startswith("logs/") for n in names), (
                f"logs/ entries leaked into export with no logs/ dir on disk: {names}"
            )
            assert not any(n.startswith("crash_diagnostics_archive/") for n in names), (
                f"crash_diagnostics_archive/ entries leaked with no archive dir on disk: {names}"
            )
    finally:
        mp.undo()
