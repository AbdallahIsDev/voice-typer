"""G4-L-25 regression guard — ``VoiceTyperService.reset_config_to_defaults``.

Finding G4-L-25 (Low): there's no IPC command to factory-reset the
config to defaults.  Users who want a clean slate have to manually
delete ``config.json`` and restart the app, which is error-prone (they
may forget to back it up, or may also delete ``vocabulary.json`` /
``templates.json`` / ``history.db`` along with it).

G4-L-25 fix adds ``VoiceTyperService.reset_config_to_defaults()`` which:

  1. Acquires ``app._config_mutation_lock`` (concurrent ``set_config``
     can't interleave).
  2. Snapshots the current ``config.json`` to ``config.json.bak`` so
     the user can recover if they clicked "Reset" by mistake.
  3. Constructs a fresh ``Config()`` (all defaults).
  4. Preserves the 5 API-key fields (``openai_api_key`` /
     ``groq_api_key`` / ``deepgram_api_key`` / ``cloud_api_key`` /
     ``llm_api_key``) from the pre-reset config so the user doesn't
     have to re-enter their keys after a reset.  Pass
     ``preserve_api_keys=False`` to also wipe API keys.
  5. Calls ``Config.save_strict()`` so a disk failure surfaces as a
     ``RuntimeError`` rather than a silent success.
  6. Does NOT touch ``history.db`` / vocabulary / templates / logs /
     keychain entries — only the in-memory + on-disk config is reset.

Agent 2-j wires the IPC handler that calls this method.

This test file coordinates with the service.py change (Fix 2-c).
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _build_service(tmp_path: Path):
    """Build a real VoiceTyperService backed by a tmp config dir."""
    from voice_typer.server import config as cfg_mod
    from voice_typer.server.config import Config
    from voice_typer.server.service import VoiceTyperService

    app = MagicMock()
    # Real Config dataclass so setattr actually persists values (a
    # MagicMock would silently accept any setattr, hiding regressions).
    cfg = Config()
    app.config = cfg
    # Real lock — MagicMock would silently accept the `with` statement
    # but not actually serialize, hiding concurrency bugs.
    app._config_mutation_lock = threading.Lock()
    app.tray.notify = MagicMock()
    svc = VoiceTyperService(app)

    import pytest as _pt

    mp = _pt.MonkeyPatch()
    mp.setattr(cfg_mod, "_config_dir", lambda: tmp_path)
    return svc, mp, cfg


def test_reset_config_to_defaults_method_exists() -> None:
    """``VoiceTyperService.reset_config_to_defaults`` must exist."""
    from voice_typer.server.service import VoiceTyperService

    assert hasattr(VoiceTyperService, "reset_config_to_defaults"), (
        "VoiceTyperService must define reset_config_to_defaults — see G4-L-25."
    )


def test_reset_config_to_defaults_creates_backup(tmp_path) -> None:
    """G4-L-25: the current config.json must be backed up to
    config.json.bak before the reset so the user can recover."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        # Seed a config.json with user settings.
        (tmp_path / "config.json").write_text(
            json.dumps(
                {
                    "hotkey": "<f5>",
                    "model_size": "medium.en",
                    "language": "fr",
                    "auto_punctuation": True,
                }
            )
        )

        result = svc.reset_config_to_defaults()
        assert result["success"] is True
        backup_path = Path(result["backup_path"])
        assert backup_path.exists(), "config.json.bak must be created"
        # The backup must contain the original user settings.
        data = json.loads(backup_path.read_text())
        assert data["hotkey"] == "<f5>"
        assert data["model_size"] == "medium.en"
        assert data["language"] == "fr"
    finally:
        mp.undo()


def test_reset_config_to_defaults_writes_defaults_to_disk(tmp_path) -> None:
    """G4-L-25: after reset, config.json must contain the default values
    (not the user's pre-reset values)."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        (tmp_path / "config.json").write_text(
            json.dumps({"hotkey": "<f5>", "model_size": "medium.en", "language": "fr"})
        )

        result = svc.reset_config_to_defaults()
        assert result["success"] is True

        # config.json now has defaults.
        data = json.loads((tmp_path / "config.json").read_text())
        from voice_typer.server.config import Config

        defaults = Config()
        assert data["hotkey"] == defaults.hotkey
        assert data["model_size"] == defaults.model_size
        assert data["language"] == defaults.language
    finally:
        mp.undo()


def test_reset_config_to_defaults_preserves_api_keys_by_default(tmp_path) -> None:
    """G4-L-25: by default (``preserve_api_keys=True``), the 5 API-key
    fields are preserved from the pre-reset config so the user doesn't
    have to re-enter their keys."""
    svc, mp, cfg = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        # Seed in-memory Config with API keys (these are the REAL
        # values, not keyring:// reference tokens — see Config.load).
        cfg.openai_api_key = "sk-preserve-me"
        cfg.groq_api_key = "gsk-preserve-me"
        cfg.llm_api_key = "llm-preserve-me"
        # And seed config.json with a non-default setting that should
        # be reset.
        (tmp_path / "config.json").write_text(json.dumps({"hotkey": "<f5>", "openai_api_key": "sk-preserve-me"}))

        result = svc.reset_config_to_defaults()
        assert result["success"] is True

        # The new in-memory Config has preserved API keys.
        new_cfg = svc._app.config
        assert new_cfg.openai_api_key == "sk-preserve-me"
        assert new_cfg.groq_api_key == "gsk-preserve-me"
        assert new_cfg.llm_api_key == "llm-preserve-me"
        # But other fields are reset to defaults.
        from voice_typer.server.config import Config

        defaults = Config()
        assert new_cfg.hotkey == defaults.hotkey
    finally:
        mp.undo()


def test_reset_config_to_defaults_wipes_api_keys_whenAsked(tmp_path) -> None:  # noqa: N802
    """G4-L-25: ``preserve_api_keys=False`` also wipes the API key
    fields (rare; the GDPR delete path is the right tool for that —
    it also clears the keychain)."""
    svc, mp, cfg = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        cfg.openai_api_key = "sk-wipe-me"
        cfg.groq_api_key = "gsk-wipe-me"
        cfg.llm_api_key = "llm-wipe-me"
        (tmp_path / "config.json").write_text(json.dumps({"openai_api_key": "sk-wipe-me"}))

        result = svc.reset_config_to_defaults(preserve_api_keys=False)
        assert result["success"] is True

        new_cfg = svc._app.config
        assert new_cfg.openai_api_key == ""
        assert new_cfg.groq_api_key == ""
        assert new_cfg.llm_api_key == ""
    finally:
        mp.undo()


def test_reset_config_to_defaults_does_not_touch_history_db(tmp_path) -> None:
    """G4-L-25: reset must NOT touch history.db (transcription history
    is preserved — GDPR Art. 17 delete is a separate, intentional action)."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        # Seed history.db with a real transcription.
        from voice_typer.server.history_db import HistoryDB

        hdb = HistoryDB(db_path=tmp_path / "history.db")
        hdb.add_transcription("must survive reset")
        hdb.flush()
        hdb.close()

        svc.reset_config_to_defaults()

        # history.db still exists and has the row.
        hdb2 = HistoryDB(db_path=tmp_path / "history.db")
        rows = hdb2.get_recent(limit=10)
        hdb2.close()
        assert any(r.get("text") == "must survive reset" for r in rows), (
            "history.db must NOT be touched by reset_config_to_defaults — "
            "G4-L-25 (history is preserved across factory reset)."
        )
    finally:
        mp.undo()


def test_reset_config_to_defaults_does_not_touch_vocabulary_or_templates(tmp_path) -> None:
    """G4-L-25: reset must NOT touch vocabulary.json / templates.json /
    corrections.json (user customizations are preserved)."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        (tmp_path / "voice-typer-vocabulary.json").write_text(json.dumps({"custom": ["my-secret-term"]}))
        (tmp_path / "voice-typer-templates.json").write_text(json.dumps({"greeting": "Hi <name>"}))
        (tmp_path / "voice-typer-corrections.json").write_text(json.dumps({"recieve": "receive"}))

        svc.reset_config_to_defaults()

        assert (tmp_path / "voice-typer-vocabulary.json").exists()
        assert (tmp_path / "voice-typer-templates.json").exists()
        assert (tmp_path / "voice-typer-corrections.json").exists()
        vocab = json.loads((tmp_path / "voice-typer-vocabulary.json").read_text())
        assert vocab["custom"] == ["my-secret-term"]
    finally:
        mp.undo()


def test_reset_config_to_defaults_invalidates_cached_llm_polisher(tmp_path) -> None:
    """G4-L-25: the cached LLMPolisher must be invalidated so the next
    polish request rebuilds with the reset config (not the stale
    pre-reset credentials/settings)."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        sentinel = object()
        svc._app._llm_polisher = sentinel

        svc.reset_config_to_defaults()

        assert svc._app._llm_polisher is None, "app._llm_polisher must be set to None by reset — G4-L-25."
    finally:
        mp.undo()


def test_reset_config_to_defaults_succeeds_when_no_config_exists(tmp_path) -> None:
    """G4-L-25: if config.json doesn't exist (fresh install), the reset
    must still succeed (no backup, just write defaults)."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        assert not (tmp_path / "config.json").exists()

        result = svc.reset_config_to_defaults()
        assert result["success"] is True
        # config.json now exists with defaults.
        assert (tmp_path / "config.json").exists()
        # No backup was created (nothing to back up).
        assert result["backup_path"] == ""
    finally:
        mp.undo()


def test_reset_config_to_defaults_acquires_config_mutation_lock(tmp_path) -> None:
    """G4-L-25: the reset must hold ``app._config_mutation_lock`` for
    the entire backup + reset + save sequence so a concurrent
    ``set_config`` IPC call can't interleave."""
    svc, mp, _ = _build_service(tmp_path)
    try:
        if not hasattr(svc, "reset_config_to_defaults"):
            pytest.skip("G4-L-25 not yet landed")
        (tmp_path / "config.json").write_text(json.dumps({"hotkey": "<f5>"}))

        # Replace the lock with one that records whether it was held.
        held_during_reset: list[bool] = []
        original_lock = svc._app._config_mutation_lock

        class _RecordingLock:
            def __enter__(self):
                held_during_reset.append(True)
                return original_lock.__enter__()

            def __exit__(self, *a):
                return original_lock.__exit__(*a)

        svc._app._config_mutation_lock = _RecordingLock()

        result = svc.reset_config_to_defaults()
        assert result["success"] is True
        assert held_during_reset == [True], (
            "reset_config_to_defaults must acquire app._config_mutation_lock "
            "for the backup + reset + save sequence — G4-L-25."
        )
    finally:
        mp.undo()
