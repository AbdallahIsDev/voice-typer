"""History domain mixin for LausuService."""

import logging

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class HistoryMixin(ServiceMixinBase):
    """History-domain service methods."""

    def get_history(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Return recent transcriptions."""
        return self._app.history_db.get_recent(
            limit,
            offset,
            raise_on_error=True,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

    def search_history(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        *,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Search transcriptions by text."""
        return self._app.history_db.search(
            query,
            limit,
            offset,
            raise_on_error=True,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

    def get_today_stats(self) -> dict[str, object]:
        """Return today's transcription statistics.

        raise_on_error=True: see ``get_history``.
        """
        return self._app.history_db.get_today_stats(raise_on_error=True)

    def delete_history(self, rec_id: int) -> bool:
        """Delete a history record by ID.

        raise_on_error=True: see ``get_history``.
        """
        return self._app.history_db.delete(rec_id, raise_on_error=True)

    def restore_history(self, record: dict) -> int:
        """Re-insert a previously-deleted history record.

        Returns the new row id (or -1 on failure, the renderer
        """
        if not isinstance(record, dict):
            raise ValueError("record must be a dict")
        # Require at least a non-empty text field, restoring an empty
        if not str(record.get("text", "")).strip():
            raise ValueError("record.text must be a non-empty string")
        return self._app.history_db.restore(record, raise_on_error=True)

    def clear_history(self) -> bool:
        """Clear all history records.

        raise_on_error=True: see ``get_history``.
        """
        return self._app.history_db.clear_all(raise_on_error=True)

    def toggle_favorite(self, rec_id: int) -> bool:
        """Toggle favorite status of a history record.

        raise_on_error=True: see ``get_history``.
        """
        return self._app.history_db.toggle_favorite(rec_id, raise_on_error=True)

    def get_favorites(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Return favorited transcriptions."""
        return self._app.history_db.get_favorites(
            limit,
            offset,
            raise_on_error=True,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

    def get_history_count(self) -> int:
        """Return the total number of transcription rows."""
        return self._app.history_db.get_history_count(raise_on_error=True)

    def get_transcription_text(self, transcription_id: int) -> dict:
        """Return the FULL text of a single transcription row."""
        return self._app.history_db.get_transcription_text(transcription_id, raise_on_error=True)
