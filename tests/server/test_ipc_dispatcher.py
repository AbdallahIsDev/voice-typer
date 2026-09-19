"""Behavioral tests for :class:`voice_typer.server.ipc.dispatcher.DispatcherMixin`."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes
from tests.server.conftest import (  # noqa: F401  (re-exported fixtures)
    IPCServer,
    mock_app,
    server,
)


class TestDispatchUnknownCommand:
    """``_handle_unknown_command`` and return a structured"""

    def test_unknown_command_returns_unknown_command_envelope(self, server):
        """A ``type`` not present in ``_COMMAND_REGISTRY`` must return"""
        result = server._dispatch({"type": "unknown_cmd", "id": 1})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["command"] == "unknown_cmd"
        assert "Unknown command: unknown_cmd" in result["data"]["message"]
        assert result["id"] == 1

    def test_empty_dict_routes_to_unknown_command_with_none_cmd(self, server):
        """source coerces ``cmd`` to ``\"\"`` only for the registry lookup;"""
        result = server._dispatch({})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["command"] is None
        assert "id" not in result

    def test_unicode_command_name_routes_to_unknown_command(self, server):
        """A non-ASCII (CJK) command name must NOT crash the dispatcher"""
        result = server._dispatch({"type": "测试", "id": 7})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["command"] == "测试"
        assert "测试" in result["data"]["message"]
        assert result["id"] == 7

    def test_unknown_command_with_none_id_omits_id(self, server):
        """If ``msg[\"id\"]`` is explicitly ``None``, the source treats"""
        result = server._dispatch({"type": "frobnicate", "id": None})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result.get("id") is None


class TestDispatchNonDictMsg:
    """``_dispatch`` is annotated ``msg: dict`` and guards against"""

    def test_dispatch_none_msg_returns_structured_envelope(self, server):
        """``_dispatch(None)`` must return a structured"""
        result = server._dispatch(None)  # type: ignore[arg-type]
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["message"] == "message must be a JSON object"
        assert result["data"]["command"] is None

    def test_dispatch_string_msg_returns_structured_envelope(self, server):
        """``_dispatch(\"not-a-dict\")`` must return the structured"""
        result = server._dispatch("not-a-dict")  # type: ignore[arg-type]
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["message"] == "message must be a JSON object"
        assert result["data"]["command"] == "not-a-dict"

    def test_dispatch_int_msg_returns_structured_envelope(self, server):
        """``_dispatch(42)`` must return the structured envelope"""
        result = server._dispatch(42)  # type: ignore[arg-type]
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["message"] == "message must be a JSON object"
        assert result["data"]["command"] == 42


class TestDispatchShuttingDown:
    """When ``self._cached_shutting_down is True``, ``_dispatch`` must"""

    def test_dispatch_after_shutdown_returns_shutting_down_envelope(self, server):
        server._cached_shutting_down = True
        result = server._dispatch({"type": "get_status", "id": 5})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        assert result["data"]["message"] == "server is shutting down"
        assert result["id"] == 5

    def test_dispatch_after_shutdown_echoes_id(self, server):
        server._cached_shutting_down = True
        result = server._dispatch({"type": "toggle_dictation", "id": 42})
        assert result["id"] == 42

    def test_dispatch_after_shutdown_with_no_id_omits_id(self, server):
        """The ``_shutting_down_error`` helper only sets ``id`` when"""
        server._cached_shutting_down = True
        result = server._dispatch({"type": "heartbeat"})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        assert "id" not in result

    def test_dispatch_after_shutdown_short_circuits_before_handler(self, server):
        """dispatching a command whose handler has an observable side"""
        server._cached_shutting_down = True
        server._dispatch({"type": "toggle_dictation", "id": 1})
        assert server.app.toggle_called is False

    def test_dispatch_after_shutdown_truthy_mock_does_not_short_circuit(self, server):
        """A truthy-but-not-``is True`` value (e.g. a child MagicMock)"""
        # Set to a truthy non-True value.
        server._cached_shutting_down = MagicMock()  # truthy, not `is True`
        result = server._dispatch({"type": "frobnicate", "id": 1})
        # Dispatch proceeded normally → unknown_command, NOT shutting_down.
        assert result["data"]["code"] == "server.unknown_command"


class TestHandleTrayClickValidation:
    """``_validate_dict_payload`` with the schema"""

    @staticmethod
    def _base_resp() -> dict:
        """The default response dict ``_dispatch`` hands to handlers."""
        return {"type": "result", "id": 1, "data": {}}

    def test_none_data_returns_invalid_payload(self, server):
        """``data=None`` (or any non-dict) must return"""
        resp = self._base_resp()
        result = server._handle_tray_click(None, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_payload"
        assert result["data"]["message"] == "data must be an object"

    def test_string_data_returns_invalid_payload(self, server):
        """A string ``data`` is not a dict → ``client.invalid_payload``."""
        resp = self._base_resp()
        result = server._handle_tray_click("not a dict", resp)
        assert result["data"]["code"] == "client.invalid_payload"

    def test_missing_id_returns_missing_field(self, server):
        """``data={}`` (dict present, ``id`` absent) →"""
        resp = self._base_resp()
        result = server._handle_tray_click({}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.missing_field"
        assert result["data"]["field"] == "id"

    def test_int_id_returns_invalid_field(self, server):
        """``data={\"id\": 42}`` (id present, wrong type) →"""
        resp = self._base_resp()
        result = server._handle_tray_click({"id": 42}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field"
        assert result["data"]["field"] == "id"

    def test_none_id_returns_invalid_field(self, server):
        """``data={\"id\": None}``, explicit ``None`` is not a valid"""
        resp = self._base_resp()
        result = server._handle_tray_click({"id": None}, resp)
        assert result["data"]["code"] == "client.invalid_field"

    def test_list_id_returns_invalid_field(self, server):
        """A list ``id`` → ``client.invalid_field``."""
        resp = self._base_resp()
        result = server._handle_tray_click({"id": ["a", "b"]}, resp)
        assert result["data"]["code"] == "client.invalid_field"


class TestHandleShutdownIdempotency:
    """``_handle_shutdown`` must be idempotent: the first invocation"""

    @pytest.fixture
    def shutdown_server(self, monkeypatch):
        """Build a server with a fake service, patch ``Thread`` to run"""
        srv, fake_app, fake_service = make_ipc_server_with_fakes()
        # Disable the thread-registry path: ``getattr(self.app,
        fake_app._thread_registry = None

        class _SyncThread:
            """A Thread stand-in that runs ``target`` synchronously."""

            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self) -> None:
                if self._target is not None:
                    self._target()

        # The dispatcher accesses ``Thread`` via the ``threading``
        import voice_typer.server.ipc.dispatcher as dispatcher_mod

        monkeypatch.setattr(dispatcher_mod.threading, "Thread", _SyncThread)
        return srv, fake_app, fake_service

    def test_first_call_returns_ack_envelope(self, shutdown_server):
        srv, _, _ = shutdown_server
        resp = {"id": 1}
        result = srv._handle_shutdown(None, resp)
        assert result["type"] == "result"
        assert result["data"] == {"ack": True}
        # The ack is stamped on the caller-supplied ``resp`` dict
        assert resp is result

    def test_second_call_is_noop(self, shutdown_server):
        """The second ``_handle_shutdown`` call must return the same"""
        srv, _, fake_service = shutdown_server
        srv._handle_shutdown(None, {"id": 1})
        assert fake_service.quit.call_count == 1
        srv._handle_shutdown(None, {"id": 2})
        assert fake_service.quit.call_count == 1

    def test_second_call_returns_ack_envelope(self, shutdown_server):
        """The second call's response shape must match the first"""
        srv, _, _ = shutdown_server
        srv._handle_shutdown(None, {"id": 1})
        result2 = srv._handle_shutdown(None, {"id": 2})
        assert result2["type"] == "result"
        assert result2["data"] == {"ack": True}

    def test_shutdown_started_event_set_after_first_call(self, shutdown_server):
        """The ``_shutdown_started`` ``threading.Event`` must be set"""
        srv, _, _ = shutdown_server
        assert srv._shutdown_started.is_set() is False
        srv._handle_shutdown(None, {"id": 1})
        assert srv._shutdown_started.is_set() is True

    def test_service_quit_runs_on_background_thread(self, shutdown_server):
        """Even though our test patches ``Thread`` to run synchronously,"""
        srv, _, fake_service = shutdown_server
        srv._handle_shutdown(None, {"id": 1})
        fake_service.quit.assert_called_once()


class TestDispatchOversizedId:
    """A very large integer ``id`` (larger than any native int width)"""

    def test_oversized_id_with_known_command_echoes_back(self, server):
        """A readonly command (``get_status``) with a 10**100 id must"""
        huge_id = 10**100
        result = server._dispatch({"type": "get_status", "id": huge_id})
        assert result is not None
        # ``get_status`` returns ``{"type": "status", ...}``; the id
        assert result["id"] == huge_id

    def test_oversized_id_with_unknown_command_echoes_back(self, server):
        """``server.unknown_command`` envelope with the id echoed."""
        huge_id = 10**100 + 7
        result = server._dispatch({"type": "no_such_cmd", "id": huge_id})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["id"] == huge_id

    def test_oversized_id_with_shutting_down_echoes_back(self, server):
        """The shutting_down path also echoes the id (via"""
        server._cached_shutting_down = True
        huge_id = 2**512  # larger than any 64-bit int
        result = server._dispatch({"type": "toggle_dictation", "id": huge_id})
        assert result is not None
        assert result["data"]["code"] == "server.shutting_down"
        assert result["id"] == huge_id

    def test_string_id_is_carried_through(self, server):
        """A string ``id`` (the TS / Rust host sometimes sends string"""
        result = server._dispatch({"type": "get_status", "id": "req-abc-123"})
        assert result is not None
        assert result["id"] == "req-abc-123"


class TestShuttingDownErrorEnvelope:
    """initial gate and the per-handler TOCTOU re-check). Pin its shape"""

    def test_envelope_shape_with_id(self, server):
        result = server._shutting_down_error({"type": "x", "id": 99})
        assert result == {
            "type": "error",
            "data": {
                "code": "server.shutting_down",
                "message": "server is shutting down",
            },
            "id": 99,
        }

    def test_envelope_shape_without_id(self, server):
        result = server._shutting_down_error({"type": "x"})
        assert result == {
            "type": "error",
            "data": {
                "code": "server.shutting_down",
                "message": "server is shutting down",
            },
        }
        assert "id" not in result

    def test_envelope_shape_with_none_msg(self, server):
        """``_shutting_down_error(None)``, the ``isinstance(msg, dict)``"""
        result = server._shutting_down_error(None)  # type: ignore[arg-type]
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        assert "id" not in result
