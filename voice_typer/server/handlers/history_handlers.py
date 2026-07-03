"""History IPC handler mixin: get_history, get_today_stats, delete_history,
restore_history, clear_history, toggle_favorite, get_favorites, search_history.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import (
    log,
    _bound_history_limit,
    _bound_history_offset,
)


class HistoryHandlersMixin:
    """Mixin: history-related IPC handlers (get_history / delete_history / ...)."""

    # ARCH-REFAC-002 / TASK-10: pyrefly null-safety fix.
    # These attributes are provided at runtime by the IPCServer host
    # class via multiple inheritance. Declaring them as ``Any`` here
    # lets pyrefly type-check the mixin methods in isolation without
    # requiring a Protocol that would couple the mixin to a specific
    # service/app implementation (MagicMock fixtures in tests rely on
    # the loose typing).
    service: "Any"
    app: "Any"
    _send: "Any"

    def _handle_get_history(self, data, resp) -> dict | None:
        """Handle the ``get_history`` IPC command."""
        try:
            # SEC-010: bound limit/offset to prevent DoS via huge values.
            raw = (data or {}) if isinstance(data, dict) else {}
            limit = _bound_history_limit(raw.get("limit", 50))
            offset = _bound_history_offset(raw.get("offset", 0))
            resp["type"] = "history"
            resp["data"] = self.service.get_history(limit, offset)
        except Exception as e:
            log.error("[IPC] get_history failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_today_stats(self, data, resp) -> dict | None:
        """Handle the ``get_today_stats`` IPC command."""
        try:
            resp["type"] = "today_stats"
            resp["data"] = self.service.get_today_stats()
        except Exception as e:
            log.error("[IPC] get_today_stats failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_delete_history(self, data, resp) -> dict | None:
        """Handle the ``delete_history`` IPC command."""
        try:
            rec_id = data.get("id") if isinstance(data, dict) else None
            if rec_id is None:
                raise ValueError("Missing 'id'")
            self.service.delete_history(rec_id)
            resp["type"] = "ack"
        except Exception as e:
            log.error("[IPC] delete_history failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_restore_history(self, data, resp) -> dict | None:
        """Handle the ``restore_history`` IPC command."""
        # NEW-UX-004: re-insert a previously-deleted record so the
        # renderer's Undo-delete toast can recover the entry.
        try:
            record = data.get("record") if isinstance(data, dict) else None
            if not isinstance(record, dict):
                raise ValueError("Missing 'record' dict")
            new_id = self.service.restore_history(record)
            resp["type"] = "ack"
            resp["data"] = {"id": new_id}
        except Exception as e:
            log.error("[IPC] restore_history failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_clear_history(self, data, resp) -> dict | None:
        """Handle the ``clear_history`` IPC command."""
        try:
            self.service.clear_history()
            resp["type"] = "ack"
        except Exception as e:
            log.error("[IPC] clear_history failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_toggle_favorite(self, data, resp) -> dict | None:
        """Handle the ``toggle_favorite`` IPC command."""
        try:
            rec_id = data.get("id") if isinstance(data, dict) else None
            if rec_id is None:
                raise ValueError("Missing 'id'")
            new_val = self.service.toggle_favorite(rec_id)
            resp["type"] = "ack"
            resp["data"] = {"favorite": new_val}
        except Exception as e:
            log.error("[IPC] toggle_favorite failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_get_favorites(self, data, resp) -> dict | None:
        """Handle the ``get_favorites`` IPC command."""
        try:
            # SEC-010: bound limit/offset.
            raw = (data or {}) if isinstance(data, dict) else {}
            limit = _bound_history_limit(raw.get("limit", 50))
            offset = _bound_history_offset(raw.get("offset", 0))
            resp["type"] = "history"
            resp["data"] = self.service.get_favorites(limit, offset)
        except Exception as e:
            log.error("[IPC] get_favorites failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_search_history(self, data, resp) -> dict | None:
        """Handle the ``search_history`` IPC command."""
        try:
            raw = data if isinstance(data, dict) else {}
            query = raw.get("query", "")
            # SEC-010: bound limit/offset.
            limit = _bound_history_limit(raw.get("limit", 50))
            offset = _bound_history_offset(raw.get("offset", 0))
            resp["type"] = "history"
            resp["data"] = self.service.search_history(query, limit, offset)
        except Exception as e:
            log.error("[IPC] search_history failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
