"""Unit tests for ``HistoryHandlersMixin`` (CR-12).

Covers the 8 history IPC handlers defined in
``voice_typer/server/handlers/history_handlers.py``:

- ``_handle_get_history``, bounded limit/offset pagination.
- ``_handle_get_today_stats``, returns today's transcription stats.
- ``_handle_delete_history``, validates ``id``, deletes row, broadcasts
  ``history_changed`` event.
- ``_handle_restore_history``, validates ``record`` dict, re-inserts.
- ``_handle_clear_history``, clears all rows, broadcasts event.
- ``_handle_toggle_favorite``, validates ``id``, toggles fav flag.
- ``_handle_get_favorites``, bounded pagination of favorites only.
- ``_handle_search_history``, bounded pagination of search results.

Most validation goes through the shared ``_validate_dict_payload``
helper, so the error responses have the structured
``{code: missing_field|invalid_field|invalid_payload, field: <name>}``
shape.  The limit/offset handlers use ``_bound_history_limit`` /
``_bound_history_offset`` which clamp bad values to safe defaults
rather than rejecting them.
"""

from __future__ import annotations

import pytest
from voice_typer.server import event_bus


class TestGetHistory:
    """``_handle_get_history``, bounded limit/offset pagination."""

    def test_happy_path_returns_history_type(self, ipc_server, fake_service):
        fake_service.get_history.return_value = [{"id": 1, "text": "hello"}]
        resp = ipc_server._handle_get_history({"limit": 10, "offset": 0}, {})
        assert resp["type"] == "history"
        assert resp["data"] == [{"id": 1, "text": "hello"}]
        # The handler must pass the bounded limit/offset to the service.
        fake_service.get_history.assert_called_once_with(10, 0)

    def test_default_limit_offset_when_payload_missing(self, ipc_server, fake_service):
        """Empty payload → default limit=50, offset=0."""
        resp = ipc_server._handle_get_history({}, {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(50, 0)

    def test_huge_limit_is_clamped_to_max(self, ipc_server, fake_service):
        """SEC-010: ``limit > 500`` is clamped to 500 (DoS protection).

        The handler doesn't reject the request, it silently clamps
        so a misbehaving caller gets a valid (but bounded) response.
        """
        resp = ipc_server._handle_get_history({"limit": 1000000}, {})
        assert resp["type"] == "history"
        # _HISTORY_LIMIT_MAX = 500.
        fake_service.get_history.assert_called_once_with(500, 0)

    def test_non_dict_data_falls_back_to_defaults(self, ipc_server, fake_service):
        """Non-dict ``data`` (list/string) → defaults (not an error).

        The handler's ``raw = (data or {}) if isinstance(data, dict) else {}``
        guard converts non-dict payloads to an empty dict so the
        bounding helpers see their defaults.
        """
        resp = ipc_server._handle_get_history(["not", "a", "dict"], {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(50, 0)


class TestGetTodayStats:
    """``_handle_get_today_stats``, returns today's stats dict."""

    def test_happy_path_returns_today_stats(self, ipc_server, fake_service):
        fake_service.get_today_stats.return_value = {"count": 5, "chars": 250}
        resp = ipc_server._handle_get_today_stats({}, {})
        assert resp["type"] == "today_stats"
        assert resp["data"] == {"count": 5, "chars": 250}

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_today_stats.side_effect = RuntimeError("db locked")
        resp = ipc_server._handle_get_today_stats({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestDeleteHistory:
    """``_handle_delete_history``, validates ``id``, deletes, broadcasts."""

    def test_happy_path_returns_ack_and_broadcasts_history_changed(self, ipc_server, fake_service):
        captured: list[dict] = []
        sub = captured.append
        event_bus.subscribe(sub)
        try:
            resp = ipc_server._handle_delete_history({"id": 42}, {})
        finally:
            event_bus.unsubscribe(sub)

        assert resp["type"] == "ack"
        fake_service.delete_history.assert_called_once_with(42)
        # F11-FIX: a delete must broadcast history_changed so cached
        # renderer pages (Home, History, Dashboard) invalidate.
        assert any(
            e.get("type") == "history_changed" and e.get("data", {}).get("reason") == "deleted" for e in captured
        ), f"expected history_changed/deleted event, got: {captured}"

    def test_missing_id_returns_missing_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_delete_history({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "id"
        fake_service.delete_history.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_delete_history(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.delete_history.assert_not_called()

    def test_string_id_is_accepted(self, ipc_server, fake_service):
        """The schema declares ``id: (int, str)``, string IDs are valid.

        HistoryDB row IDs are ints, but the renderer sometimes sends
        them as strings (form inputs).  The handler accepts both.
        """
        resp = ipc_server._handle_delete_history({"id": "42"}, {})
        assert resp["type"] == "ack"
        fake_service.delete_history.assert_called_once_with("42")


class TestRestoreHistory:
    """``_handle_restore_history``, re-inserts a previously-deleted record."""

    def test_happy_path_returns_ack_with_new_id(self, ipc_server, fake_service):
        fake_service.restore_history.return_value = 99
        record = {"id": 1, "text": "restored"}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"id": 99}
        fake_service.restore_history.assert_called_once_with(record)

    def test_missing_record_returns_missing_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_restore_history({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "record"

    def test_non_dict_record_returns_invalid_field_error(self, ipc_server, fake_service):
        """``record`` must be a dict, the schema rejects ints/strings/lists."""
        resp = ipc_server._handle_restore_history({"record": "not-a-dict"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "record"


class TestClearHistory:
    """``_handle_clear_history``, clears all rows, broadcasts event."""

    def test_happy_path_returns_ack_and_broadcasts_cleared(self, ipc_server, fake_service):
        captured: list[dict] = []
        event_bus.subscribe(captured.append)
        try:
            resp = ipc_server._handle_clear_history({}, {})
        finally:
            event_bus.unsubscribe(captured.append)

        assert resp["type"] == "ack"
        fake_service.clear_history.assert_called_once_with()
        assert any(
            e.get("type") == "history_changed" and e.get("data", {}).get("reason") == "cleared" for e in captured
        ), f"expected history_changed/cleared event, got: {captured}"

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.clear_history.side_effect = RuntimeError("db error")
        resp = ipc_server._handle_clear_history({}, {})
        assert resp["type"] == "error"
        # generic WS-path envelope (no ``str(exc)`` leak).
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestToggleFavorite:
    """``_handle_toggle_favorite``, validates ``id``, toggles fav flag."""

    def test_happy_path_returns_ack_with_new_favorite_value(self, ipc_server, fake_service):
        fake_service.toggle_favorite.return_value = True
        resp = ipc_server._handle_toggle_favorite({"id": 7}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"favorite": True}
        fake_service.toggle_favorite.assert_called_once_with(7)

    def test_missing_id_returns_missing_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_toggle_favorite({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "id"


class TestGetFavorites:
    """``_handle_get_favorites``, bounded pagination of favorites only."""

    def test_happy_path_returns_history_type_with_favorites(self, ipc_server, fake_service):
        fake_service.get_favorites.return_value = [{"id": 1, "text": "fav", "favorite": 1}]
        resp = ipc_server._handle_get_favorites({"limit": 5, "offset": 0}, {})
        assert resp["type"] == "history"
        assert resp["data"] == [{"id": 1, "text": "fav", "favorite": 1}]
        fake_service.get_favorites.assert_called_once_with(5, 0)

    def test_default_limit_offset(self, ipc_server, fake_service):
        ipc_server._handle_get_favorites({}, {})
        fake_service.get_favorites.assert_called_once_with(50, 0)


class TestSearchHistory:
    """``_handle_search_history``, bounded pagination of search results."""

    def test_happy_path_returns_history_type_with_results(self, ipc_server, fake_service):
        fake_service.search_history.return_value = [{"id": 1, "text": "match"}]
        resp = ipc_server._handle_search_history({"query": "hello", "limit": 10, "offset": 0}, {})
        assert resp["type"] == "history"
        assert resp["data"] == [{"id": 1, "text": "match"}]
        fake_service.search_history.assert_called_once_with("hello", 10, 0)

    def test_empty_query_returns_results(self, ipc_server, fake_service):
        """Empty query string → service is called with "" (returns all)."""
        fake_service.search_history.return_value = []
        resp = ipc_server._handle_search_history({}, {})
        assert resp["type"] == "history"
        fake_service.search_history.assert_called_once_with("", 50, 0)

    def test_non_dict_data_uses_empty_query(self, ipc_server, fake_service):
        """Non-dict ``data`` → empty query, default limit/offset."""
        ipc_server._handle_search_history(None, {})
        fake_service.search_history.assert_called_once_with("", 50, 0)


# ──────────────────────────────────────────────────────────────────────
# Keyset-cursor (``before_timestamp`` / ``before_id``) contract shared
# by the three list-returning handlers.
# ──────────────────────────────────────────────────────────────────────

_CURSOR_TIMESTAMP = "2026-06-06 10:11:12"

# (handler attribute, service method, base payload, expected positional
# service args). ``search_history`` carries the query as its first
# positional argument; the other two take ``(limit, offset)`` directly.
_CURSOR_SITES = [
    ("_handle_get_history", "get_history", {}, (50, 0)),
    ("_handle_get_favorites", "get_favorites", {}, (50, 0)),
    ("_handle_search_history", "search_history", {"query": "hello"}, ("hello", 50, 0)),
]


class TestHistoryKeysetCursor:
    """Keyset-cursor extraction shared by the three list handlers.

    Pins the cursor contract for ALL THREE handlers (the older cursor
    suite in ``tests/test_cursor_pagination.py`` exercises the
    validation rejects only on ``get_history``): a full cursor enables
    the keyset kwargs, a numeric-string ``before_id`` is coerced to
    int, a partial cursor degrades to the OFFSET path, and a negative
    ``before_id`` is rejected with the exact ``client.invalid_field``
    envelope (code + field + message) without touching the service.
    """

    @pytest.mark.parametrize(
        ("handler_name", "service_method", "base_payload", "positional_args"),
        _CURSOR_SITES,
    )
    def test_string_before_id_is_coerced_to_int(
        self, ipc_server, fake_service, handler_name, service_method, base_payload, positional_args
    ):
        """``before_id: "42"`` (numeric string from a form input) is
        coerced to ``42`` and still enables the keyset path."""
        service = getattr(fake_service, service_method)
        service.return_value = []
        payload = {**base_payload, "before_timestamp": _CURSOR_TIMESTAMP, "before_id": "42"}

        resp = getattr(ipc_server, handler_name)(payload, {})

        assert resp["type"] == "history"
        service.assert_called_once_with(*positional_args, before_timestamp=_CURSOR_TIMESTAMP, before_id=42)

    @pytest.mark.parametrize(
        ("handler_name", "service_method", "base_payload", "positional_args"),
        _CURSOR_SITES,
    )
    def test_negative_int_before_id_rejected(
        self, ipc_server, fake_service, handler_name, service_method, base_payload, positional_args
    ):
        """``before_id: -1`` is rejected with the exact invalid-field
        envelope; the service is never called."""
        service = getattr(fake_service, service_method)
        payload = {**base_payload, "before_timestamp": _CURSOR_TIMESTAMP, "before_id": -1}

        resp = getattr(ipc_server, handler_name)(payload, {})

        assert resp["type"] == "error"
        assert resp["data"] == {
            "code": "client.invalid_field",
            "field": "before_id",
            "message": "before_id must be non-negative",
        }
        service.assert_not_called()

    @pytest.mark.parametrize(
        ("handler_name", "service_method", "base_payload", "positional_args"),
        _CURSOR_SITES,
    )
    def test_negative_string_before_id_rejected(
        self, ipc_server, fake_service, handler_name, service_method, base_payload, positional_args
    ):
        """``before_id: "-5"`` (numeric string) narrows to ``-5`` and is
        rejected by the same non-negative invariant."""
        service = getattr(fake_service, service_method)
        payload = {**base_payload, "before_timestamp": _CURSOR_TIMESTAMP, "before_id": "-5"}

        resp = getattr(ipc_server, handler_name)(payload, {})

        assert resp["type"] == "error"
        assert resp["data"] == {
            "code": "client.invalid_field",
            "field": "before_id",
            "message": "before_id must be non-negative",
        }
        service.assert_not_called()

    @pytest.mark.parametrize(
        ("handler_name", "service_method", "base_payload", "positional_args"),
        _CURSOR_SITES,
    )
    def test_bool_before_id_rejected_by_schema(
        self, ipc_server, fake_service, handler_name, service_method, base_payload, positional_args
    ):
        """``before_id: true`` is rejected by the schema's ``reject_bool``
        rule (bool subclasses int), invalid-field on ``before_id``."""
        service = getattr(fake_service, service_method)
        payload = {**base_payload, "before_timestamp": _CURSOR_TIMESTAMP, "before_id": True}

        resp = getattr(ipc_server, handler_name)(payload, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "before_id"
        service.assert_not_called()

    @pytest.mark.parametrize(
        ("handler_name", "service_method", "base_payload", "positional_args"),
        _CURSOR_SITES,
    )
    def test_timestamp_only_uses_offset_path(
        self, ipc_server, fake_service, handler_name, service_method, base_payload, positional_args
    ):
        """Only ``before_timestamp`` supplied (no ``before_id``) → no
        cursor kwargs splatted; the service takes the OFFSET branch."""
        service = getattr(fake_service, service_method)
        service.return_value = []
        payload = {**base_payload, "before_timestamp": _CURSOR_TIMESTAMP}

        resp = getattr(ipc_server, handler_name)(payload, {})

        assert resp["type"] == "history"
        service.assert_called_once_with(*positional_args)

    @pytest.mark.parametrize(
        ("handler_name", "service_method", "base_payload", "positional_args"),
        _CURSOR_SITES,
    )
    def test_before_id_only_uses_offset_path(
        self, ipc_server, fake_service, handler_name, service_method, base_payload, positional_args
    ):
        """Only ``before_id`` supplied (no ``before_timestamp``) → no
        cursor kwargs splatted; the service takes the OFFSET branch."""
        service = getattr(fake_service, service_method)
        service.return_value = []
        payload = {**base_payload, "before_id": 42}

        resp = getattr(ipc_server, handler_name)(payload, {})

        assert resp["type"] == "history"
        service.assert_called_once_with(*positional_args)
