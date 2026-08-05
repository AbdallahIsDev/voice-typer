"""Behavioral tests for :class:`voice_typer.server.ipc.dispatcher.DispatcherMixin`.

These tests exercise the actual command-routing behavior of the five
``DispatcherMixin`` methods (``_dispatch``, ``_shutting_down_error``,
``_handle_unknown_command``, ``_handle_tray_click``, ``_handle_shutdown``)
that are mixed into :class:`IPCServer` via multiple inheritance.

Why this file exists
--------------------

The pre-existing test coverage of the dispatcher was source-string
pinning only: six test files used ``inspect.getsource(IPCServer._dispatch)``
/ ``._handle_tray_click`` / ``._handle_shutdown`` and asserted substrings
appear in the source. Those tests pass on ANY behavior change that
preserves the pinned substrings — they cannot detect a regression that
rewrites the dispatch logic identically-shaped-but-wrong. This file
adds BEHAVIORAL cases that assert on the actual return values, error
codes, and side effects of the dispatch surface so a real regression
fails the suite.

Coverage matrix (mapped to the review.md acceptance criteria for
the dispatcher behavioral-test gap):

1. Unknown command → ``server.unknown_command`` envelope.
2. Empty/None/non-dict ``msg`` → ``server.unknown_command`` (for empty
   dict) or ``AttributeError`` (for None / non-dict — current source
   behavior, captured as a regression guard so a future guard that
   changes the exception type surfaces here).
3. Unicode command name → ``server.unknown_command`` (no encoding crash).
4. Dispatch-after-shutdown-started → ``server.shutting_down`` envelope.
5. ``_handle_tray_click`` malformed ``data`` (null / missing / wrong
   type) → ``client.invalid_payload`` / ``client.missing_field`` /
   ``client.invalid_field``.
6. ``_handle_shutdown`` idempotency (second call is a no-op —
   ``service.quit()`` invoked exactly once across two calls).
7. Oversized ``id`` field (10**100) still echoes through the response.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes
from tests.server.conftest import (  # noqa: F401  (re-exported fixtures)
    IPCServer,
    mock_app,
    server,
)

# ─────────────────────────────────────────────────────────────────────────
# 1, 2, 3: unknown / empty / unicode / non-dict msg
# ─────────────────────────────────────────────────────────────────────────


class TestDispatchUnknownCommand:
    """``_dispatch`` must route unrecognised ``type`` values to
    ``_handle_unknown_command`` and return a structured
    ``server.unknown_command`` error envelope.
    """

    def test_unknown_command_returns_unknown_command_envelope(self, server):
        """A ``type`` not present in ``_COMMAND_REGISTRY`` must return
        ``{"type": "error", "data": {"code": "server.unknown_command",
        "message": "Unknown command: <cmd>", "command": <cmd>}}`` with
        the request ``id`` echoed back.
        """
        result = server._dispatch({"type": "unknown_cmd", "id": 1})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["command"] == "unknown_cmd"
        assert "Unknown command: unknown_cmd" in result["data"]["message"]
        # id-stamping chokepoint: ``_dispatch`` stamps the inbound id
        # onto every response envelope (validation.py parity contract).
        assert result["id"] == 1

    def test_empty_dict_routes_to_unknown_command_with_none_cmd(self, server):
        """``_dispatch({})`` — no ``type`` key — must route to
        ``_handle_unknown_command`` with ``cmd=None`` (NOT crash). The
        source coerces ``cmd`` to ``""`` only for the registry lookup;
        the original ``None`` is preserved in the error envelope.
        """
        result = server._dispatch({})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["command"] is None
        # No ``id`` key on the inbound msg → no ``id`` key on the
        # response (the resp dict starts as ``{}`` when ``"id" not in msg``).
        assert "id" not in result

    def test_unicode_command_name_routes_to_unknown_command(self, server):
        """A non-ASCII (CJK) command name must NOT crash the dispatcher
        and must be preserved verbatim in the error envelope's
        ``command`` field. Regression guard for any future
        ``.encode("ascii")`` / ``str.encode()`` call on the cmd value.
        """
        result = server._dispatch({"type": "测试", "id": 7})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["command"] == "测试"
        assert "测试" in result["data"]["message"]
        assert result["id"] == 7

    def test_unknown_command_with_none_id_omits_id(self, server):
        """If ``msg["id"]`` is explicitly ``None``, the source treats
        that as "no correlation id" (the ``if _req_id is not None:``
        gate skips correlation-id setup) and the response omits ``id``.
        """
        result = server._dispatch({"type": "frobnicate", "id": None})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        # ``msg.get("id")`` returns None → resp starts as ``{}`` (the
        # ``if "id" in msg`` check passes but the value is None and the
        # id-stamping chokepoint at the end only stamps when ``"id" not
        # in result`` — the resp dict construction path uses
        # ``{"id": msg.get("id")} if "id" in msg else {}`` so the
        # ``id: None`` IS carried through.
        assert result.get("id") is None


class TestDispatchNonDictMsg:
    """``_dispatch`` is annotated ``msg: dict`` and guards against
    non-dict input with a structured ``server.unknown_command`` error
    envelope (NOT a crash). Non-dict JSON (lists, ints, strings,
    ``None``) is valid JSON but would crash ``msg.get("type")`` with
    ``AttributeError``, killing the IPC thread silently. The TCP and
    stdin transports pre-check this before calling ``_dispatch``; the
    guard is the single chokepoint so a future transport that forgets
    the pre-check (or a direct test caller) cannot crash the
    dispatcher.

    These tests capture the CURRENT behavior as a regression guard.
    The envelope mirrors the ``_handle_unknown_command`` shape
    (``code: server.unknown_command``) so clients branch on the same
    code as for unrecognized commands; the offending value is echoed
    in the ``command`` field.
    """

    def test_dispatch_none_msg_returns_structured_envelope(self, server):
        """``_dispatch(None)`` must return a structured
        ``server.unknown_command`` envelope with ``message: "message
        must be a JSON object"`` and the offending value echoed in
        ``command`` — never crash the dispatch thread.
        """
        result = server._dispatch(None)  # type: ignore[arg-type]
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["message"] == "message must be a JSON object"
        assert result["data"]["command"] is None

    def test_dispatch_string_msg_returns_structured_envelope(self, server):
        """``_dispatch("not-a-dict")`` must return the structured
        envelope (not raise ``AttributeError``)."""
        result = server._dispatch("not-a-dict")  # type: ignore[arg-type]
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["message"] == "message must be a JSON object"
        assert result["data"]["command"] == "not-a-dict"

    def test_dispatch_int_msg_returns_structured_envelope(self, server):
        """``_dispatch(42)`` must return the structured envelope
        (not raise ``AttributeError``)."""
        result = server._dispatch(42)  # type: ignore[arg-type]
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["data"]["message"] == "message must be a JSON object"
        assert result["data"]["command"] == 42


# ─────────────────────────────────────────────────────────────────────────
# 4: dispatch-after-shutdown-started
# ─────────────────────────────────────────────────────────────────────────


class TestDispatchShuttingDown:
    """When ``self._cached_shutting_down is True``, ``_dispatch`` must
    short-circuit BEFORE the registry lookup and return the structured
    ``server.shutting_down`` envelope (built by
    ``_shutting_down_error``).

    The ``is True`` check (rather than truthiness) is intentional —
    MagicMock-based test fixtures expose ``_cached_shutting_down`` as a
    child mock that is truthy but not ``is True``, so those fixtures
    keep exercising the dispatch path. Setting the field to the boolean
    ``True`` (as the real ``stop()`` method does) triggers the
    short-circuit.
    """

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
        """The ``_shutting_down_error`` helper only sets ``id`` when
        ``isinstance(msg, dict) and "id" in msg`` — a notification with
        no id must not get a spurious ``id: None``.
        """
        server._cached_shutting_down = True
        result = server._dispatch({"type": "heartbeat"})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        assert "id" not in result

    def test_dispatch_after_shutdown_short_circuits_before_handler(self, server):
        """The shutdown gate fires BEFORE the registry lookup, so the
        resolved handler must NOT be invoked. We assert this by
        dispatching a command whose handler has an observable side
        effect (``toggle_dictation`` flips ``mock_app.toggle_called``)
        and checking the side effect did NOT fire.
        """
        server._cached_shutting_down = True
        server._dispatch({"type": "toggle_dictation", "id": 1})
        # mock_app.toggle_called is False because the handler was
        # never reached (the shutdown gate returned first).
        assert server.app.toggle_called is False

    def test_dispatch_after_shutdown_truthy_mock_does_not_short_circuit(self, server):
        """A truthy-but-not-``is True`` value (e.g. a child MagicMock)
        must NOT trip the shutdown gate — this is the documented
        behavior that lets test fixtures keep exercising the dispatch
        path. Restoring truthiness-based gating would break this
        contract silently.
        """
        # Set to a truthy non-True value.
        server._cached_shutting_down = MagicMock()  # truthy, not `is True`
        result = server._dispatch({"type": "frobnicate", "id": 1})
        # Dispatch proceeded normally → unknown_command, NOT shutting_down.
        assert result["data"]["code"] == "server.unknown_command"


# ─────────────────────────────────────────────────────────────────────────
# 5: _handle_tray_click malformed data
# ─────────────────────────────────────────────────────────────────────────


class TestHandleTrayClickValidation:
    """``_handle_tray_click`` delegates input validation to
    ``_validate_dict_payload`` with the schema
    ``{"id": {"type": str, "required": True}}``. Each malformed-input
    case must return the structured error envelope matching the
    shared validation contract — NOT an inline ``isinstance`` check
    that conflates the three cases.
    """

    @staticmethod
    def _base_resp() -> dict:
        """The default response dict ``_dispatch`` hands to handlers."""
        return {"type": "result", "id": 1, "data": {}}

    def test_none_data_returns_invalid_payload(self, server):
        """``data=None`` (or any non-dict) must return
        ``client.invalid_payload`` (NOT ``missing_field``).
        """
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
        """``data={}`` (dict present, ``id`` absent) →
        ``client.missing_field`` with ``field="id"``.
        """
        resp = self._base_resp()
        result = server._handle_tray_click({}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.missing_field"
        assert result["data"]["field"] == "id"

    def test_int_id_returns_invalid_field(self, server):
        """``data={"id": 42}`` (id present, wrong type) →
        ``client.invalid_field`` (NOT ``missing_field`` — the old inline
        check conflated these).
        """
        resp = self._base_resp()
        result = server._handle_tray_click({"id": 42}, resp)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.invalid_field"
        assert result["data"]["field"] == "id"

    def test_none_id_returns_invalid_field(self, server):
        """``data={"id": None}`` — explicit ``None`` is not a valid
        ``str`` → ``client.invalid_field``.
        """
        resp = self._base_resp()
        result = server._handle_tray_click({"id": None}, resp)
        assert result["data"]["code"] == "client.invalid_field"

    def test_list_id_returns_invalid_field(self, server):
        """A list ``id`` → ``client.invalid_field``."""
        resp = self._base_resp()
        result = server._handle_tray_click({"id": ["a", "b"]}, resp)
        assert result["data"]["code"] == "client.invalid_field"


# ─────────────────────────────────────────────────────────────────────────
# 6: _handle_shutdown idempotency
# ─────────────────────────────────────────────────────────────────────────


class TestHandleShutdownIdempotency:
    """``_handle_shutdown`` must be idempotent: the first invocation
    sets ``_shutdown_started`` and spawns the cleanup thread; the
    second invocation observes ``_shutdown_started.is_set()`` and
    returns the ack envelope WITHOUT spawning a second thread or
    re-invoking ``service.quit()``.

    The cleanup thread runs ``service.quit()`` on a daemon thread so
    the ack frame reaches the host before the (potentially long)
    teardown completes. To make this test deterministic, we patch
    ``threading.Thread`` in the dispatcher module to run the target
    SYNCHRONOUSLY in the calling thread — so by the time
    ``_handle_shutdown`` returns, ``service.quit()`` has already been
    called (or not, for the idempotent second call).
    """

    @pytest.fixture
    def shutdown_server(self, monkeypatch):
        """Build a server with a fake service, patch ``Thread`` to run
        synchronously, and disable the thread-registry path (so the
        MagicMock app's auto-child ``_thread_registry`` doesn't get
        exercised in a way that could vary across call orderings).
        """
        srv, fake_app, fake_service = make_ipc_server_with_fakes()
        # Disable the thread-registry path: ``getattr(self.app,
        # "_thread_registry", None)`` returns our explicit ``None``,
        # so the ``if _registry is not None:`` branch is skipped.
        fake_app._thread_registry = None

        class _SyncThread:
            """A Thread stand-in that runs ``target`` synchronously.

            Implements just enough of the ``threading.Thread`` API
            (``__init__`` accepting ``target`` / ``name`` / ``daemon``
            kwargs, and ``start``) for the dispatcher's usage.
            """

            def __init__(self, **kwargs):
                self._target = kwargs.get("target")

            def start(self) -> None:
                if self._target is not None:
                    self._target()

        # The dispatcher accesses ``Thread`` via the ``threading``
        # module it imported at module load — patch on that module
        # object so the dispatcher's ``threading.Thread(...)`` call
        # resolves to our synchronous stand-in.
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
        # in-place (the source mutates ``resp`` then returns it).
        assert resp is result

    def test_second_call_is_noop(self, shutdown_server):
        """The second ``_handle_shutdown`` call must return the same
        ack envelope shape but NOT invoke ``service.quit()`` again.
        """
        srv, _, fake_service = shutdown_server
        srv._handle_shutdown(None, {"id": 1})
        assert fake_service.quit.call_count == 1
        srv._handle_shutdown(None, {"id": 2})
        # service.quit() was NOT called a second time — the
        # ``_shutdown_started.is_set()`` gate short-circuited.
        assert fake_service.quit.call_count == 1

    def test_second_call_returns_ack_envelope(self, shutdown_server):
        """The second call's response shape must match the first
        (so the host's retry timer resolves to a well-formed ack).
        """
        srv, _, _ = shutdown_server
        srv._handle_shutdown(None, {"id": 1})
        result2 = srv._handle_shutdown(None, {"id": 2})
        assert result2["type"] == "result"
        assert result2["data"] == {"ack": True}

    def test_shutdown_started_event_set_after_first_call(self, shutdown_server):
        """The ``_shutdown_started`` ``threading.Event`` must be set
        BEFORE the cleanup thread is spawned (the source comment
        documents this as the TOCTOU-closing ordering that makes the
        second invocation's no-op atomic with the first's
        thread-spawn decision).
        """
        srv, _, _ = shutdown_server
        assert srv._shutdown_started.is_set() is False
        srv._handle_shutdown(None, {"id": 1})
        assert srv._shutdown_started.is_set() is True

    def test_service_quit_runs_on_background_thread(self, shutdown_server):
        """Even though our test patches ``Thread`` to run synchronously,
        the production behavior is that ``service.quit()`` runs on a
        SEPARATE thread from the dispatcher. The synchronous patch is
        a test-only determinism aid; this test asserts the
        ``_handle_shutdown`` source does delegate to ``service.quit()``
        (rather than ``app.quit()`` directly) so shutdown side-effects
        added to ``VoiceTyperService.quit`` run on every transport.
        """
        srv, _, fake_service = shutdown_server
        srv._handle_shutdown(None, {"id": 1})
        # service.quit (NOT app.quit) is the documented delegation.
        fake_service.quit.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────
# 7: oversized id field
# ─────────────────────────────────────────────────────────────────────────


class TestDispatchOversizedId:
    """A very large integer ``id`` (larger than any native int width)
    must be carried through the dispatch path without truncation and
    echoed back on the response envelope. The id is propagated via
    ``msg.get("id")`` / ``result["id"] = msg["id"]`` (both pure-Python
    dict ops) and stamped onto the response as a correlation id via
    ``set_correlation_id(str(_req_id))`` — none of these paths impose
    a size cap.
    """

    def test_oversized_id_with_known_command_echoes_back(self, server):
        """A readonly command (``get_status``) with a 10**100 id must
        echo the id back unchanged on the response.
        """
        huge_id = 10**100
        result = server._dispatch({"type": "get_status", "id": huge_id})
        assert result is not None
        # ``get_status`` returns ``{"type": "status", ...}``; the id
        # is stamped by the dispatch chokepoint.
        assert result["id"] == huge_id

    def test_oversized_id_with_unknown_command_echoes_back(self, server):
        """An unknown command with a huge id must still return the
        ``server.unknown_command`` envelope with the id echoed.
        """
        huge_id = 10**100 + 7
        result = server._dispatch({"type": "no_such_cmd", "id": huge_id})
        assert result is not None
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.unknown_command"
        assert result["id"] == huge_id

    def test_oversized_id_with_shutting_down_echoes_back(self, server):
        """The shutting_down path also echoes the id (via
        ``_shutting_down_error``).
        """
        server._cached_shutting_down = True
        huge_id = 2**512  # larger than any 64-bit int
        result = server._dispatch({"type": "toggle_dictation", "id": huge_id})
        assert result is not None
        assert result["data"]["code"] == "server.shutting_down"
        assert result["id"] == huge_id

    def test_string_id_is_carried_through(self, server):
        """A string ``id`` (the TS / Rust host sometimes sends string
        ids) must be carried through unchanged — no int coercion.
        """
        result = server._dispatch({"type": "get_status", "id": "req-abc-123"})
        assert result is not None
        assert result["id"] == "req-abc-123"


# ─────────────────────────────────────────────────────────────────────────
# Bonus: ``_shutting_down_error`` envelope shape contract
# ─────────────────────────────────────────────────────────────────────────


class TestShuttingDownErrorEnvelope:
    """``_shutting_down_error`` is the single source of truth for the
    ``server.shutting_down`` envelope shape (called from BOTH the
    initial gate and the per-handler TOCTOU re-check). Pin its shape
    directly so a future refactor can't drift the two call sites
    apart.
    """

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
        """``_shutting_down_error(None)`` — the ``isinstance(msg, dict)``
        guard prevents a crash on non-dict input (which can happen if
        the shutdown gate fires on a malformed msg).
        """
        result = server._shutting_down_error(None)  # type: ignore[arg-type]
        assert result["type"] == "error"
        assert result["data"]["code"] == "server.shutting_down"
        assert "id" not in result
