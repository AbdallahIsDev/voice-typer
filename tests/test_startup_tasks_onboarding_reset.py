"""Regression tests for XZ-R12-15 — `reset_onboarding_complete` marker
consistency.

``voice_typer/server/startup_tasks.py::reset_onboarding_complete`` is the
backend primitive for the "Re-run setup wizard" affordance in Settings →
Advanced. The review entry XZ-R12-15 (Low) found that this function only
deleted ``.onboarding_complete`` while ``OnboardingController.reset``
deletes BOTH ``.onboarding_complete`` AND ``.onboarding_started``.

The asymmetry left a stale ``.onboarding_started`` marker on disk. The
XA-11-2 auto-heal in ``startup_sequence.py`` treats a surviving
``.onboarding_started`` marker as "mid-wizard crash" and SKIPS the
auto-heal — so the wizard would NOT re-appear on the next launch even
though the user explicitly requested a re-run via the IPC handler.

These tests pin both marker deletions so a future refactor cannot
silently reintroduce the inconsistency.
"""

from __future__ import annotations

from pathlib import Path

from voice_typer.server.startup_tasks import reset_onboarding_complete


def _patch_config_dir(tmp_path: Path, monkeypatch) -> None:
    """Point both ``config._config_dir`` and the
    ``startup_sequence._config_dir`` alias at ``tmp_path`` so the
    fallback ``Config.load()`` call inside ``reset_onboarding_complete``
    doesn't read or write the real user config dir.
    """
    from voice_typer.server import config as _config_mod

    _config_mod._config_dir.cache_clear()
    monkeypatch.setattr(_config_mod, "_config_dir", lambda: tmp_path)


class TestResetOnboardingCompleteMarkerConsistency:
    """XZ-R12-15: the IPC reset path must delete BOTH markers."""

    def test_deletes_onboarding_started_marker(self, tmp_path, monkeypatch):
        """``.onboarding_started`` must be deleted, not just
        ``.onboarding_complete``.

        Pre-fix: only ``.onboarding_complete`` was unlinked, leaving a
        stale ``.onboarding_started`` that caused the XA-11-2 auto-heal
        to treat the next launch as a mid-wizard crash and skip
        re-running the wizard.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        # Seed BOTH markers.
        (tmp_path / ".onboarding_complete").write_text("1", encoding="utf-8")
        (tmp_path / ".onboarding_started").write_text("1", encoding="utf-8")
        assert (tmp_path / ".onboarding_complete").exists()
        assert (tmp_path / ".onboarding_started").exists()

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / ".onboarding_complete").exists(), (
            "reset_onboarding_complete must delete .onboarding_complete"
        )
        assert not (tmp_path / ".onboarding_started").exists(), (
            "reset_onboarding_complete must ALSO delete .onboarding_started — "
            "stale marker would cause startup_sequence auto-heal to treat the "
            "next launch as a mid-wizard crash and skip the wizard re-run"
        )

    def test_deletes_only_complete_marker_when_started_absent(self, tmp_path, monkeypatch):
        """If ``.onboarding_started`` is already absent, the function is
        idempotent — it still deletes ``.onboarding_complete`` and does
        NOT raise on the missing started marker.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        (tmp_path / ".onboarding_complete").write_text("1", encoding="utf-8")
        # .onboarding_started is intentionally absent.
        assert not (tmp_path / ".onboarding_started").exists()

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / ".onboarding_complete").exists()
        # Still absent (was absent before; idempotent).
        assert not (tmp_path / ".onboarding_started").exists()

    def test_idempotent_when_both_markers_already_absent(self, tmp_path, monkeypatch):
        """Calling reset when neither marker exists must succeed and not
        raise (the affordance is a user-triggered "re-run setup wizard"
        button — it must be safe to click multiple times)."""
        _patch_config_dir(tmp_path, monkeypatch)
        assert not (tmp_path / ".onboarding_complete").exists()
        assert not (tmp_path / ".onboarding_started").exists()

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result == {"reset": True, "error": None}
        assert not (tmp_path / ".onboarding_complete").exists()
        assert not (tmp_path / ".onboarding_started").exists()

    def test_returns_error_dict_if_config_dir_unlinkable(self, tmp_path, monkeypatch):
        """If the marker deletion fails (e.g. permission denied), the
        function returns ``{"reset": False, "error": str(exc)}`` rather
        than propagating. This contract is preserved by the new
        ``.onboarding_started`` deletion because it's inside the same
        ``try`` block.
        """
        _patch_config_dir(tmp_path, monkeypatch)
        # Seed both markers.
        complete = tmp_path / ".onboarding_complete"
        started = tmp_path / ".onboarding_started"
        complete.write_text("1", encoding="utf-8")
        started.write_text("1", encoding="utf-8")

        # Patch Path.unlink for ``.onboarding_complete`` to raise — the
        # outer ``except Exception`` must catch it and return the error
        # dict rather than propagating.
        original_unlink = Path.unlink

        def raising_unlink(self, *args, **kwargs):
            if self.name == ".onboarding_complete":
                raise PermissionError("simulated permission denied")
            return original_unlink(self, *args, **kwargs)

        monkeypatch.setattr(Path, "unlink", raising_unlink)

        result = reset_onboarding_complete(config_dir=tmp_path)

        assert result["reset"] is False
        assert "permission denied" in result["error"].lower()
        # The .onboarding_started marker was NOT reached because
        # .onboarding_complete raised first. This is fine — the user
        # sees the error and can retry; both markers are still present.
        assert complete.exists()
        assert started.exists()
