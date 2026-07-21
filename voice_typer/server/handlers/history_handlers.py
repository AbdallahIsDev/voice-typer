"""History IPC handler mixin: get_history, get_today_stats, delete_history,
restore_history, clear_history, toggle_favorite, get_favorites, search_history.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.history_bounds import (
    _bound_history_limit,
    _bound_history_offset,
)
from voice_typer.server.ipc.validation import _validate_dict_payload


class HistoryHandlersMixin(HandlerBase):
    """Mixin: history-related IPC handlers (get_history / delete_history / ...).

    CR-20: this mixin is one of the four "representative" handlers
    migrated to :meth:`HandlerBase._respond_with_error` for the
    catch-all ``except Exception`` path. See
    ``voice_typer/server/handlers/_base.py`` for the migration plan.
    """

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
            # IPC-3: validate ``limit`` / ``offset`` types via the
            # shared ``_validate_dict_payload`` helper. Non-dict
            # ``data`` is pre-coerced to ``{}`` so the
            # ``test_non_dict_data_falls_back_to_defaults`` contract
            # (list → defaults) still holds. ``required: False`` (no
            # default) means absent fields fall through to the
            # ``_bound_history_limit`` / ``_bound_history_offset``
            # clamping helpers (50 / 0) — preserving the existing
            # ``test_default_limit_offset_when_payload_missing``
            # contract.
            #
            # The schema accepts ``(int, str)`` for ``limit`` /
            # ``offset`` because the renderer sometimes sends numeric
            # strings from form inputs (see
            # ``test_get_history_with_string_limit_accepted`` in
            # ``tests/test_server.py``); the ``_bound_history_limit``
            # helper coerces the string to int. A non-int, non-str
            # value (e.g. a list or dict) is rejected with
            # ``invalid_field``.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "limit": {"type": (int, str), "required": False},
                    "offset": {"type": (int, str), "required": False},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            # SEC-010: bound limit/offset to prevent DoS via huge values.
            limit = _bound_history_limit(validated.get("limit", 50))
            offset = _bound_history_offset(validated.get("offset", 0))
            resp["type"] = "history"
            resp["data"] = self.service.get_history(limit, offset)
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_history")
        return resp

    def _handle_get_today_stats(self, data, resp) -> dict | None:
        """Handle the ``get_today_stats`` IPC command."""
        try:
            resp["type"] = "today_stats"
            resp["data"] = self.service.get_today_stats()
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_today_stats")
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
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "delete_history")
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
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "restore_history")
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
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "clear_history")
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
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "toggle_favorite")
        return resp

    def _handle_get_favorites(self, data, resp) -> dict | None:
        """Handle the ``get_favorites`` IPC command."""
        try:
            # IPC-3: validate ``limit`` / ``offset`` types via the
            # shared ``_validate_dict_payload`` helper. Same pattern as
            # ``_handle_get_history`` (above) — ``(int, str)`` accepts
            # numeric strings from form inputs.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "limit": {"type": (int, str), "required": False},
                    "offset": {"type": (int, str), "required": False},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            # SEC-010: bound limit/offset.
            limit = _bound_history_limit(validated.get("limit", 50))
            offset = _bound_history_offset(validated.get("offset", 0))
            resp["type"] = "history"
            resp["data"] = self.service.get_favorites(limit, offset)
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_favorites")
        return resp

    def _handle_search_history(self, data, resp) -> dict | None:
        """Handle the ``search_history`` IPC command."""
        try:
            # IPC-3: validate ``query`` / ``limit`` / ``offset`` types
            # via the shared ``_validate_dict_payload`` helper. Non-dict
            # ``data`` is pre-coerced to ``{}`` so the
            # ``test_non_dict_data_uses_empty_query`` contract (None →
            # empty query, default limit/offset) still holds.
            # ``limit`` / ``offset`` accept ``(int, str)`` for the same
            # form-input coercion reason as ``_handle_get_history``.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "query": {"type": str, "required": False, "default": ""},
                    "limit": {"type": (int, str), "required": False},
                    "offset": {"type": (int, str), "required": False},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            query = validated.get("query", "")
            # SEC-010: bound limit/offset.
            limit = _bound_history_limit(validated.get("limit", 50))
            offset = _bound_history_offset(validated.get("offset", 0))
            resp["type"] = "history"
            resp["data"] = self.service.search_history(query, limit, offset)
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "search_history")
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
