"""Unified history offset bound: IPC layer and DB layer share one limit.

Regression tests for the IPC-10M vs DB-1000 mismatch: deep offsets
(``offset >= 1000``) must yield a precise ``client.invalid_field``
response naming the ``offset`` field and pointing at cursor pagination,
never an opaque ``server.internal_error``. The DB guard must raise a
real exception (``ValueError``), never a bare ``assert``, so the check
survives ``python -O`` (which strips ``assert`` statements entirely).
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import MagicMock

import pytest
from voice_typer.server.history_db_internals.search import _assert_bounded_offset
from voice_typer.server.ipc.history_bounds import (
    _HISTORY_OFFSET_MAX,
    HISTORY_OFFSET_LIMIT,
    _bound_history_offset,
)

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


@pytest.fixture
def history_db(tmp_path):
    from voice_typer.server.history_db import HistoryDB

    db_instance = HistoryDB(db_path=tmp_path / "test_offset_unified.db")
    yield db_instance
    db_instance.close()


class TestSharedOffsetLimit:
    def test_shared_limit_is_999(self):
        assert HISTORY_OFFSET_LIMIT == 999

    def test_ipc_bounder_preserves_offsets_at_or_below_limit(self):
        assert _bound_history_offset(0) == 0
        assert _bound_history_offset(999) == 999

    def test_shared_limit_sits_below_dos_cap(self):
        assert HISTORY_OFFSET_LIMIT < _HISTORY_OFFSET_MAX


class TestDbGuardRaisesPreciseError:
    def test_offsets_at_or_below_limit_pass(self):
        _assert_bounded_offset(0)
        _assert_bounded_offset(999)

    def test_offset_above_limit_raises_value_error(self):
        with pytest.raises(ValueError, match="cursor pagination") as exc_info:
            _assert_bounded_offset(1000)
        assert not isinstance(exc_info.value, AssertionError)

    def test_guard_uses_real_raise_surviving_optimization(self):
        """The guard body must contain ``raise`` and no ``assert`` statement."""
        tree = ast.parse(inspect.getsource(_assert_bounded_offset))
        assert [n for n in ast.walk(tree) if isinstance(n, ast.Assert)] == []
        assert [n for n in ast.walk(tree) if isinstance(n, ast.Raise)] != []


class TestDbDeepOffset:
    def test_get_recent_deep_offset_raises_precise_error(self, history_db):
        from voice_typer.server.history_db import HistoryDBError

        with pytest.raises(HistoryDBError, match="cursor pagination"):
            history_db.get_recent(limit=10, offset=1000, raise_on_error=True)

    def test_get_recent_at_limit_succeeds(self, history_db):
        assert history_db.get_recent(limit=10, offset=999, raise_on_error=True) == []


class TestGetHistoryHandlerDeepOffset:
    def test_offset_1000_yields_precise_error_not_internal_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_get_history({"limit": 10, "offset": 1000}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "offset"
        assert "cursor" in resp["data"]["message"]
        fake_service.get_history.assert_not_called()

    def test_offset_above_limit_yields_precise_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_get_history({"limit": 10, "offset": 5000}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "offset"
        fake_service.get_history.assert_not_called()

    def test_offset_at_limit_reaches_service(self, ipc_server, fake_service):
        fake_service.get_history.return_value = []
        resp = ipc_server._handle_get_history({"limit": 10, "offset": 999}, {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(10, 999)

    def test_deep_offset_with_cursor_reaches_service(self, ipc_server, fake_service):
        """Cursor pages ignore OFFSET in the DB, so they stay allowed."""
        fake_service.get_history.return_value = []
        resp = ipc_server._handle_get_history(
            {
                "limit": 10,
                "offset": 5000,
                "before_timestamp": "2026-01-01 12:00:00",
                "before_id": 42,
            },
            {},
        )
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(10, 5000, before_timestamp="2026-01-01 12:00:00", before_id=42)
