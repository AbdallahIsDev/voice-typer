"""VoiceTyperService: service layer between IPC and domain logic.

ARCH-005: previously ipc_server.py directly called VoiceTyperApp
methods (26 call sites).  This service layer provides a clean
boundary so a second transport (CLI, gRPC, REST) can be added
without duplicating app glue.

The service is a thin facade — it delegates to the app but provides
a stable interface that doesn't leak VoiceTyperApp's internal API.
"""

import logging
from typing import Any, Optional

log = logging.getLogger(__name__)


class VoiceTyperService:
    """Service facade over VoiceTyperApp.

    This class wraps the app's public methods in a transport-agnostic
    interface.  The IPC server (or any future transport) calls these
    methods instead of touching the app directly.
    """

    def __init__(self, app) -> None:
        self._app = app

    # ── Status ──────────────────────────────────────────────────

    def get_status(self) -> str:
        """Return the current app state as a string."""
        return self._app.tray.state.value

    # ── Dictation ───────────────────────────────────────────────

    def toggle_dictation(self) -> None:
        """Start or stop dictation."""
        self._app.toggle_dictation()

    def undo_last(self) -> None:
        """Undo the last transcription via backspace keystrokes."""
        self._app.undo_last()

    def repaste_last(self) -> None:
        """Re-paste the last transcription."""
        self._app.repaste_last()

    # ── Config ──────────────────────────────────────────────────

    def get_config(self) -> dict:
        """Return the sanitized config (API keys redacted)."""
        from voice_typer.server.ipc_server import _sanitize_config_for_ipc
        return _sanitize_config_for_ipc(self._app.config)

    def get_defaults(self) -> dict:
        """Return default config values (sanitized)."""
        from voice_typer.server.config import Config
        from voice_typer.server.ipc_server import _sanitize_config_for_ipc
        return _sanitize_config_for_ipc(Config())

    def set_config(self, updates: dict) -> tuple[dict, list]:
        """Validate and apply config updates. Returns (validated, errors)."""
        from voice_typer.server.config import validate_config_update
        return validate_config_update(updates)

    def save_config(self) -> bool:
        """Persist config to disk."""
        return self._app.config.save()

    # ── History ─────────────────────────────────────────────────

    def get_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return recent transcriptions."""
        return self._app.history_db.get_recent(limit, offset)

    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Search transcriptions by text."""
        return self._app.history_db.search(query, limit, offset)

    def get_today_stats(self) -> dict:
        """Return today's transcription statistics."""
        return self._app.history_db.get_today_stats()

    def delete_history(self, rec_id: int) -> bool:
        """Delete a history record by ID."""
        return self._app.history_db.delete(rec_id)

    def clear_history(self) -> bool:
        """Clear all history records."""
        return self._app.history_db.clear_all()

    def toggle_favorite(self, rec_id: int) -> bool:
        """Toggle favorite status of a history record."""
        return self._app.history_db.toggle_favorite(rec_id)

    def get_favorites(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return favorited transcriptions."""
        return self._app.history_db.get_favorites(limit, offset)

    # ── Microphones ─────────────────────────────────────────────

    def get_microphones(self) -> list[dict]:
        """Return available microphones."""
        return self._app._microphones

    # ── Lifecycle ───────────────────────────────────────────────

    def restart(self) -> None:
        """Restart the application."""
        self._app.restart_app()

    def quit(self) -> None:
        """Quit the application."""
        self._app.quit_app()

    # ── Templates (#6) ─────────────────────────────────────────

    def get_templates(self) -> list[dict]:
        """Return saved templates from config."""
        templates = getattr(self._app.config, 'templates_data', None)
        return templates if isinstance(templates, list) else []

    def save_templates(self, templates: list[dict]) -> bool:
        """Save templates to config and persist to disk."""
        self._app.config.templates_data = templates
        return self._app.config.save()
