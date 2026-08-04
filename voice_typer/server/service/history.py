"""History domain mixin for VoiceTyperService.

Extracted verbatim from the original ``service.py`` god class
( split). Methods here delegate to ``self._app.history_db``
and expose the history-export / clear / search / favorites surface.
"""

import logging

from voice_typer.server.service._base import ServiceMixinBase

log = logging.getLogger(__name__)


class HistoryMixin(ServiceMixinBase):
    """History-domain service methods.

        All methods delegate to ``self._app.history_db`` and pass
        ``raise_on_error=True`` so the IPC layer can distinguish an empty
    result from an operational failure ().
    """

    # ── History ─────────────────────────────────────────────────

    def get_history(
        self,
        limit: int = 50,
        offset: int = 0,
        *,
        before_timestamp: str | None = None,
        before_id: int | None = None,
    ) -> list[dict]:
        """Return recent transcriptions.

        raise_on_error=True so the IPC layer can distinguish
                "empty result" from "operation failed" and surface an error
                to the renderer.

        keyset pagination: ``before_timestamp`` + ``before_id``
        together form the keyset cursor — the WHERE clause restricts
        to rows strictly older than ``(before_timestamp, before_id)``
        in (timestamp DESC, id DESC) order, which is O(log N) via
        ``idx_timestamp`` (vs OFFSET which is O(offset)). Both cursor
        values must be supplied to use keyset pagination; otherwise
        the OFFSET fallback fires (backward-compat with the
        pre-cursor contract). The IPC layer extracts the cursor from
        ``data.before_timestamp`` / ``data.before_id`` in
        :mod:`voice_typer.server.handlers.history_handlers`.
        """
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
        """Search transcriptions by text.

        raise_on_error=True — see ``get_history``. Cursor
        pagination matches ``get_history`` — see that docstring for
        the O(log N) vs O(offset) tradeoff.
        """
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

        raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.get_today_stats(raise_on_error=True)

    def delete_history(self, rec_id: int) -> bool:
        """Delete a history record by ID.

        raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.delete(rec_id, raise_on_error=True)

    def restore_history(self, record: dict) -> int:
        """Re-insert a previously-deleted history record.

        supports the Undo-delete toast in the renderer.
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

        raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.clear_all(raise_on_error=True)

    def toggle_favorite(self, rec_id: int) -> bool:
        """Toggle favorite status of a history record.

        raise_on_error=True — see ``get_history``.
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
        """Return favorited transcriptions.

        raise_on_error=True — see ``get_history``. Cursor
        pagination matches ``get_history`` — see that docstring for
        the O(log N) vs O(offset) tradeoff.
        """
        return self._app.history_db.get_favorites(
            limit,
            offset,
            raise_on_error=True,
            before_timestamp=before_timestamp,
            before_id=before_id,
        )

    # ── on-demand full-text + total-count accessors ──

    def get_history_count(self) -> int:
        """Return the total number of transcription rows.

                Wraps :meth:`HistoryDB.get_history_count` (TTL-cached for 60s
                to avoid a full ``COUNT(*)`` scan on every Dashboard refresh).
        raise_on_error=True so the IPC layer can distinguish
                "zero rows" from "DB error".
        """
        return self._app.history_db.get_history_count(raise_on_error=True)

    def get_transcription_text(self, transcription_id: int) -> dict:
        """Return the FULL text of a single transcription row.

                Wraps :meth:`HistoryDB.get_transcription_text`. Companion to
                the 500-char ``text`` preview returned by ``get_history`` /
                ``get_favorites`` / ``search_history`` — the renderer fetches
                the full text on demand when the user expands a History row.
        raise_on_error=True — see ``get_history``.
        """
        return self._app.history_db.get_transcription_text(transcription_id, raise_on_error=True)
