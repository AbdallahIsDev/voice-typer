"""History domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
(ARCH-005 split). Methods here delegate to ``self._app.history_db``
and expose the history-export / clear / search / favorites surface.
"""

import logging

log = logging.getLogger(__name__)


class HistoryMixin:
    """History-domain service methods.

    All methods delegate to ``self._app.history_db`` and pass
    ``raise_on_error=True`` so the IPC layer can distinguish an empty
    result from an operational failure (ERR-013).
    """

    # ── History ─────────────────────────────────────────────────

    def get_history(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return recent transcriptions.

        ERR-013: raise_on_error=True so the IPC layer can distinguish
        "empty result" from "operation failed" and surface an error
        to the renderer.
        """
        return self._app.history_db.get_recent(limit, offset, raise_on_error=True)

    def search_history(self, query: str, limit: int = 50, offset: int = 0) -> list[dict]:
        """Search transcriptions by text.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.search(query, limit, offset, raise_on_error=True)

    def get_today_stats(self) -> dict[str, object]:
        """Return today's transcription statistics.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.get_today_stats(raise_on_error=True)

    def delete_history(self, rec_id: int) -> bool:
        """Delete a history record by ID.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.delete(rec_id, raise_on_error=True)

    def restore_history(self, record: dict) -> int:
        """Re-insert a previously-deleted history record.

        NEW-UX-004: supports the Undo-delete toast in the renderer.
        Returns the new row id (or -1 on failure — the renderer
        surfaces a "Failed to restore" toast in that case).
        """
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        # Require at least a non-empty text field — restoring an empty
        # record would silently succeed with a meaningless row.
        if not str(record.get("text", "")).strip():
            raise ValueError("record.text must be a non-empty string")
        return self._app.history_db.restore(record, raise_on_error=True)

    def clear_history(self) -> bool:
        """Clear all history records.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.clear_all(raise_on_error=True)

    def toggle_favorite(self, rec_id: int) -> bool:
        """Toggle favorite status of a history record.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.toggle_favorite(rec_id, raise_on_error=True)

    def get_favorites(self, limit: int = 50, offset: int = 0) -> list[dict]:
        """Return favorited transcriptions.

        ERR-013: raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.get_favorites(limit, offset, raise_on_error=True)
