"""Cursor (keyset) pagination wiring tests: service + handler layers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.service.history import HistoryMixin

from tests.fixtures.ipc_test_helpers import (
    make_fake_app,
    make_fake_service,
)


@pytest.fixture
def fake_app() -> MagicMock:
    return make_fake_app()


@pytest.fixture
def fake_service() -> MagicMock:
    return make_fake_service()


@pytest.fixture
def ipc_server(fake_app: MagicMock, fake_service: MagicMock):
    from voice_typer.server.ipc_server import IPCServer

    server = IPCServer(fake_app, service=fake_service)
    fake_app._ipc_server = server
    return server


def _make_history_mixin() -> tuple[HistoryMixin, MagicMock]:
    """Build a bare ``HistoryMixin`` with ``self._app.history_db`` mocked."""
    mixin = HistoryMixin()
    mock_app = MagicMock()
    mock_app.history_db = MagicMock()
    mixin._app = mock_app  # type: ignore[assignment]
    return mixin, mock_app


class TestServiceForwardsCursor:
    """Service layer forwards cursor params to ``history_db``."""

    def test_get_history_with_cursor_forwards_to_get_recent(self) -> None:
        """``HistoryMixin.get_history`` with both cursor params forwards"""
        mixin, mock_app = _make_history_mixin()
        mock_app.history_db.get_recent.return_value = [{"id": 1, "text": "row"}]

        mixin.get_history(
            limit=10,
            offset=0,
            before_timestamp="2026-01-01 12:00:00",
            before_id=42,
        )

        mock_app.history_db.get_recent.assert_called_once_with(
            10,
            0,
            raise_on_error=True,
            before_timestamp="2026-01-01 12:00:00",
            before_id=42,
        )

    def test_get_history_without_cursor_forwards_none_to_get_recent(self) -> None:
        """``HistoryMixin.get_history`` with no cursor params forwards"""
        mixin, mock_app = _make_history_mixin()
        mock_app.history_db.get_recent.return_value = []

        mixin.get_history(limit=50, offset=0)

        # The service forwards None for both cursor params, the DB layer
        mock_app.history_db.get_recent.assert_called_once_with(
            50,
            0,
            raise_on_error=True,
            before_timestamp=None,
            before_id=None,
        )

    def test_search_history_with_cursor_forwards_to_search(self) -> None:
        """``HistoryMixin.search_history`` forwards cursor params to"""
        mixin, mock_app = _make_history_mixin()
        mock_app.history_db.search.return_value = []

        mixin.search_history(
            query="hello",
            limit=20,
            offset=0,
            before_timestamp="2026-02-02 08:30:00",
            before_id=99,
        )

        mock_app.history_db.search.assert_called_once_with(
            "hello",
            20,
            0,
            raise_on_error=True,
            before_timestamp="2026-02-02 08:30:00",
            before_id=99,
        )

    def test_get_favorites_with_cursor_forwards_to_get_favorites(self) -> None:
        """``HistoryMixin.get_favorites`` forwards cursor params to"""
        mixin, mock_app = _make_history_mixin()
        mock_app.history_db.get_favorites.return_value = []

        mixin.get_favorites(
            limit=5,
            offset=0,
            before_timestamp="2026-03-03 16:45:00",
            before_id=7,
        )

        mock_app.history_db.get_favorites.assert_called_once_with(
            5,
            0,
            raise_on_error=True,
            before_timestamp="2026-03-03 16:45:00",
            before_id=7,
        )


class TestHandlerExtractsCursor:
    """Handler extracts cursor params from the ``data`` payload."""

    def test_get_history_handler_passes_cursor_to_service(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``_handle_get_history`` extracts ``before_timestamp`` /"""
        fake_service.get_history.return_value = []
        payload = {
            "limit": 25,
            "offset": 0,
            "before_timestamp": "2026-01-01 12:00:00",
            "before_id": 42,
        }

        resp = ipc_server._handle_get_history(payload, {})

        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(
            25,
            0,
            before_timestamp="2026-01-01 12:00:00",
            before_id=42,
        )

    def test_search_history_handler_passes_cursor_to_service(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``_handle_search_history`` extracts cursor params and passes"""
        fake_service.search_history.return_value = []
        payload = {
            "query": "term",
            "limit": 10,
            "offset": 0,
            "before_timestamp": "2026-04-04 00:00:00",
            "before_id": 5,
        }

        resp = ipc_server._handle_search_history(payload, {})

        assert resp["type"] == "history"
        fake_service.search_history.assert_called_once_with(
            "term",
            10,
            0,
            before_timestamp="2026-04-04 00:00:00",
            before_id=5,
        )

    def test_get_favorites_handler_passes_cursor_to_service(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``_handle_get_favorites`` extracts cursor params and passes"""
        fake_service.get_favorites.return_value = []
        payload = {
            "limit": 15,
            "offset": 0,
            "before_timestamp": "2026-05-05 09:15:30",
            "before_id": 88,
        }

        resp = ipc_server._handle_get_favorites(payload, {})

        assert resp["type"] == "history"
        fake_service.get_favorites.assert_called_once_with(
            15,
            0,
            before_timestamp="2026-05-05 09:15:30",
            before_id=88,
        )


class TestHandlerOffsetFallback:
    """
    Handler falls back to OFFSET when cursor params are absent.
    The handler MUST NOT splat cursor kwargs into the service call when
    """

    def test_get_history_no_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """Empty payload → service called with positional ``(limit, offset)``"""
        fake_service.get_history.return_value = []
        ipc_server._handle_get_history({}, {})

        fake_service.get_history.assert_called_once_with(50, 0)

    def test_get_history_explicit_null_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """Explicit ``null`` cursor values normalize to ``None`` via the"""
        fake_service.get_history.return_value = []
        payload = {"before_timestamp": None, "before_id": None}
        ipc_server._handle_get_history(payload, {})

        # Explicit null → normalized to None → no cursor kwargs splatted.
        fake_service.get_history.assert_called_once_with(50, 0)

    def test_get_history_partial_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """Only ``before_timestamp`` supplied (no ``before_id``) →"""
        fake_service.get_history.return_value = []
        payload = {"before_timestamp": "2026-01-01 12:00:00"}
        ipc_server._handle_get_history(payload, {})

        # Partial cursor → no kwargs splatted → OFFSET path.
        fake_service.get_history.assert_called_once_with(50, 0)

    def test_get_favorites_no_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``get_favorites`` empty payload → OFFSET fallback."""
        fake_service.get_favorites.return_value = []
        ipc_server._handle_get_favorites({}, {})

        fake_service.get_favorites.assert_called_once_with(50, 0)

    def test_search_history_no_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``search_history`` empty payload → OFFSET fallback."""
        fake_service.search_history.return_value = []
        ipc_server._handle_search_history({}, {})

        fake_service.search_history.assert_called_once_with("", 50, 0)


class TestHandlerCursorValidation:
    """Handler validates cursor param types / ranges."""

    def test_before_id_negative_returns_invalid_field(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``before_id < 0`` is rejected with ``client.invalid_field``."""
        payload = {
            "before_timestamp": "2026-01-01 12:00:00",
            "before_id": -1,
        }
        resp = ipc_server._handle_get_history(payload, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "before_id"
        fake_service.get_history.assert_not_called()

    def test_before_id_bool_returns_invalid_field(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``before_id: true`` is rejected (bool subclasses int but is"""
        payload = {
            "before_timestamp": "2026-01-01 12:00:00",
            "before_id": True,
        }
        resp = ipc_server._handle_get_history(payload, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "before_id"
        fake_service.get_history.assert_not_called()

    def test_before_timestamp_wrong_type_returns_invalid_field(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``before_timestamp`` must be a string, an int is rejected."""
        payload = {"before_timestamp": 12345, "before_id": 42}
        resp = ipc_server._handle_get_history(payload, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "before_timestamp"
        fake_service.get_history.assert_not_called()

    def test_before_id_zero_is_accepted(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``before_id=0`` is a valid cursor (id 0 doesn't exist, so"""
        fake_service.get_history.return_value = []
        payload = {
            "limit": 10,
            "offset": 0,
            "before_timestamp": "2026-01-01 12:00:00",
            "before_id": 0,
        }
        resp = ipc_server._handle_get_history(payload, {})

        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(
            10,
            0,
            before_timestamp="2026-01-01 12:00:00",
            before_id=0,
        )
