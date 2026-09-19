"""whole onboarding status document."""

from __future__ import annotations

from pathlib import Path

from voice_typer.server import onboarding_status
from voice_typer.server.startup_tasks import reset_onboarding_complete


def _patch_config_dir(tmp_path: Path, monkeypatch) -> None:
    """Point ``config._config_dir`` at ``tmp_path`` so the fallback"""
    from voice_typer.server import config as _config_mod

    _config_mod._reset_config_dir_cache()
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: tmp_path)


class TestResetOnboardingStatusDocument:
    """XZ-R12-15: the reset path must delete the whole status document"""

    def test_deletes_status_document_with_both_flags(self, tmp_path, monkeypatch):
        """The status document (with ``started`` and ``completed`` both"""
        _patch_config_dir(tmp_path, monkeypatch)
        onboarding_status.write_status(tmp_path, started=True, completed=True)
        assert (tmp_path / onboarding_status.ONBOARDING_STATUS_FILENAME).exists()

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / onboarding_status.ONBOARDING_STATUS_FILENAME).exists(), (
            "reset_onboarding_complete must delete the whole status document "
            "so no stale started/completed flag survives"
        )

    def test_idempotent_when_status_already_absent(self, tmp_path, monkeypatch):
        """not raise (the affordance is a user-triggered \"re-run setup"""
        _patch_config_dir(tmp_path, monkeypatch)
        assert not (tmp_path / onboarding_status.ONBOARDING_STATUS_FILENAME).exists()

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / onboarding_status.ONBOARDING_STATUS_FILENAME).exists()

    def test_also_removes_legacy_markers(self, tmp_path, monkeypatch):
        """Legacy ``.onboarding_complete`` / ``.onboarding_started``"""
        _patch_config_dir(tmp_path, monkeypatch)
        (tmp_path / ".onboarding_complete").write_text("1", encoding="utf-8")
        (tmp_path / ".onboarding_started").write_text("1", encoding="utf-8")

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / ".onboarding_complete").exists()
        assert not (tmp_path / ".onboarding_started").exists()

    def test_returns_error_dict_if_reset_fails(self, tmp_path, monkeypatch):
        """If the status-document deletion fails (e.g. permission"""
        _patch_config_dir(tmp_path, monkeypatch)
        onboarding_status.write_status(tmp_path, started=True)

        original_unlink = Path.unlink

        def raising_unlink(self, *args, **kwargs):
            if self.name == onboarding_status.ONBOARDING_STATUS_FILENAME:
                raise PermissionError("simulated permission denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", raising_unlink)

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result["reset"] is False
        assert "onboarding status" in result["error"].lower()
