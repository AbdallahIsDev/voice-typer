"""Regression tests for XZ-R12-15 — `reset_onboarding_complete` resets the
whole onboarding status document.

``voice_typer/server/startup_tasks.py::reset_onboarding_complete`` is the
backend primitive for the "Re-run setup wizard" affordance in Settings →
Advanced. Onboarding state lives in ONE JSON document,
``.onboarding_status.json`` (the ``started`` / ``completed`` flags plus
the auto-heal fail counter) — the legacy ``.onboarding_complete`` /
``.onboarding_started`` / ``.onboarding_fail_count`` markers were merged
into it.

Deleting the whole document (rather than clearing one flag) keeps the
flags consistent: if a stale ``started`` flag survived, the XA-11-2
auto-heal in ``startup_sequence.py`` would treat the next launch as a
mid-wizard crash and SKIP the auto-heal — so the wizard would NOT
re-appear on the next launch even though the user explicitly requested
a re-run via the IPC handler.

These tests pin the whole-document reset so a future refactor cannot
silently reintroduce the inconsistency.
"""

from __future__ import annotations

from pathlib import Path

from voice_typer.server import onboarding_status
from voice_typer.server.startup_tasks import reset_onboarding_complete


def _patch_config_dir(tmp_path: Path, monkeypatch) -> None:
    """Point ``config._config_dir`` at ``tmp_path`` so the fallback
    ``Config.load()`` call inside ``reset_onboarding_complete`` doesn't
    read or write the real user config dir.
    """
    from voice_typer.server import config as _config_mod

    _config_mod._config_dir.cache_clear()
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: tmp_path)


class TestResetOnboardingStatusDocument:
    """XZ-R12-15: the reset path must delete the whole status document
    (started + completed + fail counter)."""

    def test_deletes_status_document_with_both_flags(self, tmp_path, monkeypatch):
        """The status document (with ``started`` and ``completed`` both
        set) must be deleted so the wizard re-runs on next launch.

        Pre-merge, one path only unlinked ``.onboarding_complete``,
        leaving a stale ``.onboarding_started`` that caused the XA-11-2
        auto-heal to treat the next launch as a mid-wizard crash and
        skip re-running the wizard.
        """
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
        """Calling reset when no status document exists must succeed and
        not raise (the affordance is a user-triggered "re-run setup
        wizard" button — it must be safe to click multiple times)."""
        _patch_config_dir(tmp_path, monkeypatch)
        assert not (tmp_path / onboarding_status.ONBOARDING_STATUS_FILENAME).exists()

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / onboarding_status.ONBOARDING_STATUS_FILENAME).exists()

    def test_also_removes_legacy_markers(self, tmp_path, monkeypatch):
        """Legacy ``.onboarding_complete`` / ``.onboarding_started``
        markers still on disk (an upgrade that has not yet run the
        one-time migration) must also be removed by the reset so they
        can't resurrect the wizard state."""
        _patch_config_dir(tmp_path, monkeypatch)
        (tmp_path / ".onboarding_complete").write_text("1", encoding="utf-8")
        (tmp_path / ".onboarding_started").write_text("1", encoding="utf-8")

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / ".onboarding_complete").exists()
        assert not (tmp_path / ".onboarding_started").exists()

    def test_returns_error_dict_if_reset_fails(self, tmp_path, monkeypatch):
        """If the status-document deletion fails (e.g. permission
        denied), the function returns ``{"reset": False, "error":
        str(exc)}`` rather than reporting success. ``reset_status``
        surfaces the failure via its return value and the outer
        ``try`` block converts it into the error dict."""
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
