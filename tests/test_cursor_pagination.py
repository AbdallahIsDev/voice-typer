"""Cursor (keyset) pagination wiring tests — service + handler layers.

Verifies ``before_timestamp`` / ``before_id`` cursor pagination params
are forwarded end-to-end from the IPC handler → service mixin →
``HistoryDB`` keyset WHERE clause. The DB layer
(``history_db.get_recent`` / ``search`` / ``get_favorites``) already
implements the keyset WHERE clause; this test file verifies the service
mixin and IPC handler layers forward the params correctly and fall back
to the OFFSET path when the cursor is absent (backward-compat).

Test layout:

* :class:`TestServiceForwardsCursor` — service-level forwarding
  (mocks ``history_db``, verifies ``before_timestamp`` / ``before_id``
  are passed through to ``get_recent`` / ``search`` / ``get_favorites``).
* :class:`TestHandlerExtractsCursor` — handler-level extraction
  (mocks the service, verifies the handler parses the ``data`` payload
  and passes the cursor kwargs to the service call).
* :class:`TestHandlerOffsetFallback` — handler with no cursor params
  falls back to OFFSET (verifies ``before_timestamp=None`` is effectively
  forwarded via the service defaults — no cursor kwargs splatted).
* :class:`TestHandlerCursorValidation` — ``before_id < 0`` and
  ``before_id`` as a bool are rejected with ``client.invalid_field``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from voice_typer.server.service.history import HistoryMixin

from tests.fixtures.ipc_test_helpers import (
    make_fake_app,
    make_fake_service,
)

# ── Shared fixtures ──────────────────────────────────────────────────
#
# These mirror the ``ipc_server`` / ``fake_service`` fixtures in
# ``tests/handlers/conftest.py`` but are declared locally so this test
# file is self-contained at the tests/ root (the handlers conftest is
# only auto-loaded for tests under ``tests/handlers/``).


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


# ── Service-layer forwarding ─────────────────────────────────────────


def _make_history_mixin() -> tuple[HistoryMixin, MagicMock]:
    """Build a bare ``HistoryMixin`` with ``self._app.history_db`` mocked.

    ``ServiceMixinBase`` declares ``_app`` as a bare PEP 526 annotation
    (NOT ``ClassVar``), so we can freely bind it on a vanilla instance
    without going through ``VoiceTyperService.__init__``.
    """
    mixin = HistoryMixin()
    mock_app = MagicMock()
    mock_app.history_db = MagicMock()
    mixin._app = mock_app  # type: ignore[assignment]
    return mixin, mock_app


class TestServiceForwardsCursor:
    """Service layer forwards cursor params to ``history_db``."""

    def test_get_history_with_cursor_forwards_to_get_recent(self) -> None:
        """``HistoryMixin.get_history`` with both cursor params forwards
        them verbatim to ``history_db.get_recent``."""
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
        """``HistoryMixin.get_history`` with no cursor params forwards
        ``before_timestamp=None`` / ``before_id=None`` to ``get_recent``,
        which triggers the OFFSET fallback path."""
        mixin, mock_app = _make_history_mixin()
        mock_app.history_db.get_recent.return_value = []

        mixin.get_history(limit=50, offset=0)

        # The service forwards None for both cursor params — the DB layer
        # then takes the OFFSET branch (backward-compat with pre-cursor
        # callers).
        mock_app.history_db.get_recent.assert_called_once_with(
            50,
            0,
            raise_on_error=True,
            before_timestamp=None,
            before_id=None,
        )

    def test_search_history_with_cursor_forwards_to_search(self) -> None:
        """``HistoryMixin.search_history`` forwards cursor params to
        ``history_db.search``."""
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
        """``HistoryMixin.get_favorites`` forwards cursor params to
        ``history_db.get_favorites``."""
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


# ── Handler-level extraction ─────────────────────────────────────────


class TestHandlerExtractsCursor:
    """Handler extracts cursor params from the ``data`` payload."""

    def test_get_history_handler_passes_cursor_to_service(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """``_handle_get_history`` extracts ``before_timestamp`` /
        ``before_id`` from the ``data`` dict and passes them as kwargs
        to ``service.get_history``."""
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
        """``_handle_search_history`` extracts cursor params and passes
        them to ``service.search_history``."""
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
        """``_handle_get_favorites`` extracts cursor params and passes
        them to ``service.get_favorites``."""
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


# ── Handler OFFSET fallback ──────────────────────────────────────────


class TestHandlerOffsetFallback:
    """Handler falls back to OFFSET when cursor params are absent.

    The handler MUST NOT splat cursor kwargs into the service call when
    either cursor value is ``None`` — this preserves the exact pre-cursor
    call shape (``service.get_history(limit, offset)``) so the service
    defaults forward ``None`` to ``history_db``, which takes the OFFSET
    branch (backward-compat). See :func:`_build_cursor_kwargs` in
    ``history_handlers.py``.
    """

    def test_get_history_no_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """Empty payload → service called with positional ``(limit, offset)``
        only — no cursor kwargs splatted, so ``before_timestamp=None`` is
        effectively forwarded via the service defaults (OFFSET path)."""
        fake_service.get_history.return_value = []
        ipc_server._handle_get_history({}, {})

        # No cursor kwargs splatted — the service defaults take over and
        # forward ``before_timestamp=None`` to ``history_db.get_recent``,
        # which takes the OFFSET branch.
        fake_service.get_history.assert_called_once_with(50, 0)

    def test_get_history_explicit_null_cursor_uses_offset_path(
        self,
        ipc_server,
        fake_service,
    ) -> None:
        """Explicit ``null`` cursor values normalize to ``None`` via the
        schema's ``default: None`` rule → OFFSET fallback."""
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
        """Only ``before_timestamp`` supplied (no ``before_id``) →
        OFFSET fallback. The DB layer requires BOTH cursor values to take
        the keyset path; a partial cursor is silently downgraded to
        OFFSET rather than erroring."""
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


# ── Handler cursor validation ────────────────────────────────────────


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
        """``before_id: true`` is rejected (bool subclasses int but is
        semantically a toggle, not a cursor id)."""
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
        """``before_timestamp`` must be a string — an int is rejected."""
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
        """``before_id=0`` is a valid cursor (id 0 doesn't exist, so
        the keyset returns the first page in DESC order — equivalent to
        OFFSET 0 but using the index)."""
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
