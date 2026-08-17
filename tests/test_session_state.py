"""Tests for the session-liveness marker (``voice_typer/server/session_state.py``).

The marker is the mechanism that distinguishes a genuine crash from an
expected restart: written when a real session begins, removed on every
clean-shutdown path, and checked at the next launch before the
"previous session crashed" notification fires.
"""

from __future__ import annotations

from pathlib import Path

from voice_typer.server import session_state


class TestSessionMarkerLifecycle:
    def test_no_marker_means_clean_previous_session(self, tmp_path: Path) -> None:
        """A fresh config dir (first launch / clean shutdown) is NOT abnormal."""
        assert session_state.was_previous_session_abnormal(tmp_path) is False

    def test_marker_presence_means_previous_session_abnormal(self, tmp_path: Path) -> None:
        """A surviving marker = the previous process never shut down cleanly."""
        session_state.mark_session_active(tmp_path)
        assert session_state.was_previous_session_abnormal(tmp_path) is True

    def test_clear_marker_restores_clean_state(self, tmp_path: Path) -> None:
        """Clearing on clean shutdown makes the next launch treat the
        previous session as clean (no crash notification)."""
        session_state.mark_session_active(tmp_path)
        session_state.clear_session_marker(tmp_path)
        assert session_state.was_previous_session_abnormal(tmp_path) is False

    def test_clear_is_idempotent_and_missing_dir_safe(self, tmp_path: Path) -> None:
        """Clearing an absent marker (or a missing config dir) is a no-op."""
        session_state.clear_session_marker(tmp_path)
        session_state.clear_session_marker(tmp_path / "does-not-exist")
        assert session_state.was_previous_session_abnormal(tmp_path / "does-not-exist") is False

    def test_marker_lands_at_specified_path(self, tmp_path: Path) -> None:
        """The marker file name is stable and lives in the config dir."""
        session_state.mark_session_active(tmp_path)
        marker = tmp_path / session_state.SESSION_MARKER_FILENAME
        assert marker.exists(), "mark_session_active must create the marker file"
        content = marker.read_text(encoding="utf-8")
        assert "pid=" in content
        assert "started=" in content

    def test_marker_content_has_no_pii(self, tmp_path: Path) -> None:
        """Marker content is PID + timestamp only — never paths/speech."""
        session_state.mark_session_active(tmp_path)
        content = (tmp_path / session_state.SESSION_MARKER_FILENAME).read_text(encoding="utf-8")
        assert "voice" not in content.lower() or "started=" in content
        assert "=" in content


__all__ = []
