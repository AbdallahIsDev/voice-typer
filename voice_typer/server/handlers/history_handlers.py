"""History IPC handler mixin: get_history, get_today_stats, delete_history,
restore_history, clear_history, toggle_favorite, get_favorites, search_history.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.ipc_server import (
    _bound_history_limit,
    _bound_history_offset,
    _validate_dict_payload,
    log,
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
            validated, error = _validate_dict_payload(
                data,
                {
                    "id": {"type": (int, str), "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            self.service.delete_history(validated["id"])
            resp["type"] = "ack"
            # F11-FIX (b-review Finding 11): broadcast history_changed so
            # every renderer page that keeps a module-level history cache
            # (Home, History, Dashboard) invalidates it. Without this, an
            # external delete (tray menu, another window, CLI) left ghost
            # records in the cache until the next transcription_final /
            # manual refresh. clear_history already does the same above.
            _publish_history_changed("deleted")
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
            validated, error = _validate_dict_payload(
                data,
                {
                    "record": {"type": dict, "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            new_id = self.service.restore_history(validated["record"])
            resp["type"] = "ack"
            resp["data"] = {"id": new_id}
            # F11-FIX (b-review Finding 11): a restored record must also
            # invalidate the history caches (see _publish_history_changed).
            _publish_history_changed("restored")
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
            # F11-FIX (b-review Finding 11): broadcast a `history_changed`
            # event so every renderer page that keeps a module-level cache
            # of history (Home, History, Dashboard) invalidates it. Without
            # this, clearing history from outside the page (e.g. a tray-menu
            # action or another window) left ghost records in the cache until
            # the next transcription_final / manual refresh.
            _publish_history_changed("cleared")
        except Exception as e:
            log.error("[IPC] clear_history failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_toggle_favorite(self, data, resp) -> dict | None:
        """Handle the ``toggle_favorite`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "id": {"type": (int, str), "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            new_val = self.service.toggle_favorite(validated["id"])
            resp["type"] = "ack"
            resp["data"] = {"favorite": new_val}
            # F11-FIX (b-review Finding 11): a favorite toggle changes which
            # records show under the "Favorites only" filter and the favorites
            # count on the Dashboard, so invalidate history caches too.
            _publish_history_changed("favorite_toggled")
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


def _publish_history_changed(reason: str) -> None:
    """Broadcast a ``history_changed`` push event via the in-process event bus.

    Best-effort: any failure is logged at DEBUG and swallowed so it never
    breaks the IPC handler's own response. Subscribers (renderer pages) use
    this to invalidate their module-level history caches.
    """
    try:
        from voice_typer.server import event_bus

        event_bus.publish({"type": "history_changed", "data": {"reason": reason}})
    except Exception:
        log.debug("[IPC] history_changed push failed", exc_info=True)
