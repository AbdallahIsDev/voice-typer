"""IPC dispatch tests for history commands (get_history / favorites / search).

Classes:
- TestDispatchGetHistory                  — get_history dispatcher
- TestHistoryLimitBoundingClampsCallerInput — SEC-010 limit clamping

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401
    mock_app,
    server,
)


class TestDispatchGetHistory:
    def test_returns_recent_history(self, server, mock_app):
        result = server._dispatch({"id": 1, "type": "get_history"})
        assert result["type"] == "history"
        assert result["id"] == 1
        assert len(result["data"]) == 1
        assert result["data"][0]["text"] == "hello world"

    def test_passes_limit_param(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": 10},
            }
        )
        mock_app.history_db.get_recent.assert_called_with(
            10, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_default_limit_is_50(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch({"id": 1, "type": "get_history"})
        mock_app.history_db.get_recent.assert_called_with(
            50, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )


# ── SEC-010: history limit bounding ──────────────────────────────────────


class TestHistoryLimitBoundingClampsCallerInput:
    """SEC-010: ``get_history``, ``get_favorites``, ``search_history``
    must clamp caller-supplied ``limit`` to ``[1, 500]`` to prevent
    DoS via ``{"limit": 100000000}``."""

    def test_get_history_with_huge_limit_is_clamped(self, server, mock_app):
        """A 100M limit must be clamped to 500, not passed through."""
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": 100_000_000, "offset": 0},
            }
        )
        # get_recent must be called with 500, not 100M
        mock_app.history_db.get_recent.assert_called_once_with(
            500, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_get_history_with_zero_limit_clamped_to_1(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": 0},
            }
        )
        mock_app.history_db.get_recent.assert_called_once_with(
            1, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_get_history_with_negative_limit_clamped_to_1(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": -100},
            }
        )
        mock_app.history_db.get_recent.assert_called_once_with(
            1, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_get_history_with_string_limit_accepted(self, server, mock_app):
        """Numeric strings from form inputs must be accepted."""
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": "25"},
            }
        )
        mock_app.history_db.get_recent.assert_called_once_with(
            25, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_get_history_with_garbage_limit_uses_default(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"limit": "not-a-number"},
            }
        )
        mock_app.history_db.get_recent.assert_called_once_with(
            50, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_get_history_with_negative_offset_clamped_to_0(self, server, mock_app):
        mock_app.history_db.get_recent = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_history",
                "data": {"offset": -50},
            }
        )
        mock_app.history_db.get_recent.assert_called_once_with(
            50, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_get_favorites_with_huge_limit_clamped(self, server, mock_app):
        mock_app.history_db.get_favorites = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "get_favorites",
                "data": {"limit": 10**9},
            }
        )
        mock_app.history_db.get_favorites.assert_called_once_with(
            500, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )

    def test_search_history_with_huge_limit_clamped(self, server, mock_app):
        mock_app.history_db.search = MagicMock(return_value=[])
        server._dispatch(
            {
                "id": 1,
                "type": "search_history",
                "data": {"query": "hello", "limit": 10**9},
            }
        )
        mock_app.history_db.search.assert_called_once_with(
            "hello", 500, 0, raise_on_error=True, before_timestamp=None, before_id=None
        )
