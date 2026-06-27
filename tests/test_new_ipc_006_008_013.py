"""Regression tests for NEW-IPC-013, NEW-IPC-006, NEW-IPC-008.

NEW-IPC-013: Two IPCServer instances in the same process used to stomp
each other via the module-level ``_push_event`` global.  The second
start() would overwrite the first server's push callable, and the
first server's stop() would clear the global entirely — leaving the
second server unable to push events.  Fix: replace the global with a
thread-safe registry (set) of push callables; ``_push_event_now`` fans
out to ALL registered servers.

NEW-IPC-006: 5 commands returned ``{type: "ack"}`` with no ``data``
field, 2 returned ``{type: "ack", data: {...}}``.  Fix: every
response now carries an explicit ``data`` field (empty dict for acks
with no payload).

NEW-IPC-008: Push events were silently dropped at DEBUG level when no
client was connected, making the ``voice-typer`` console script
useless for diagnosis.  Fix: surface non-waveform push events at INFO
level so the user can actually see state changes / errors.
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch, call  # TEST-033: unified mock import

import pytest

from voice_typer.server import ipc_server
from voice_typer.server.ipc_server import IPCServer


@pytest.fixture
def clean_registry():
    """Snapshot and clear the push-event registry for the test, restore after."""
    with ipc_server._push_event_registry_lock:
        original = set(ipc_server._push_event_registry)
        ipc_server._push_event_registry.clear()
    yield
    with ipc_server._push_event_registry_lock:
        ipc_server._push_event_registry.clear()
        ipc_server._push_event_registry.update(original)


@pytest.fixture
def server_with_mock_app():
    app = MagicMock()
    return IPCServer(app)


class TestPushEventRegistryMultiInstance:
    """NEW-IPC-013: multiple IPCServer instances in the same process."""

    def test_two_servers_can_register_simultaneously(self, clean_registry):
        """Both servers' push callables must coexist in the registry."""
        calls_a: list = []
        calls_b: list = []
        ipc_server._set_push_event(calls_a.append)
        ipc_server._set_push_event(calls_b.append)

        # Both should be registered.
        with ipc_server._push_event_registry_lock:
            assert len(ipc_server._push_event_registry) == 2

        # _push_event_now fans out to both.
        result = ipc_server._push_event_now({"type": "test"})
        assert result is True
        assert calls_a == [{"type": "test"}]
        assert calls_b == [{"type": "test"}]

    def test_stop_one_server_does_not_clear_other(self, clean_registry):
        """Stopping server A must not affect server B's push registration.

        This is the core regression: previously stop() set the global
        to None, killing the other server's push channel.
        """
        calls_a: list = []
        calls_b: list = []
        ipc_server._set_push_event(calls_a.append)
        ipc_server._set_push_event(calls_b.append)

        # Server A stops — unregister just its callable.
        ipc_server._clear_push_event(calls_a.append)

        # Server B must still be registered.
        with ipc_server._push_event_registry_lock:
            assert len(ipc_server._push_event_registry) == 1
            assert calls_b.append in ipc_server._push_event_registry

        # _push_event_now must still reach server B.
        result = ipc_server._push_event_now({"type": "after_a_stop"})
        assert result is True
        assert calls_a == []  # A stopped, didn't receive
        assert calls_b == [{"type": "after_a_stop"}]

    def test_set_push_event_with_none_is_noop(self, clean_registry):
        """_set_push_event(None) must NOT clear the registry (it's a no-op).

        Previously _set_push_event(None) was used as a 'clear all'
        shorthand; with the new registry semantics, None is rejected
        and the registry is untouched.  Use _clear_push_event(fn) to
        unregister a specific callable.
        """
        calls_a: list = []
        ipc_server._set_push_event(calls_a.append)
        assert len(ipc_server._push_event_registry) == 1

        # _set_push_event(None) must be a no-op (not a clear).
        ipc_server._set_push_event(None)
        assert len(ipc_server._push_event_registry) == 1

    def test_clear_push_event_idempotent(self, clean_registry):
        """Clearing an unregistered callable must be a no-op (no error)."""
        fn = lambda msg: None  # noqa: E731
        # Not yet registered — clear must not raise.
        ipc_server._clear_push_event(fn)
        # Register and clear.
        ipc_server._set_push_event(fn)
        ipc_server._clear_push_event(fn)
        ipc_server._clear_push_event(fn)  # Second clear is also safe.
        assert len(ipc_server._push_event_registry) == 0

    def test_push_event_now_thread_safe(self, clean_registry):
        """Concurrent _push_event_now calls must all succeed without
        raising or losing events."""
        import threading

        received: list = []
        lock = threading.Lock()

        def listener(msg):
            with lock:
                received.append(msg)

        ipc_server._set_push_event(listener)

        def worker():
            for i in range(50):
                ipc_server._push_event_now({"type": "test", "i": i})

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # No exceptions, all 400 (8 threads × 50) events delivered.
        assert len(received) == 400


class TestAckShapeConsistency:
    """NEW-IPC-006: every response must include a ``data`` field."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "toggle_dictation",
            "undo_last",
            "delete_history",
            "clear_history",
        ],
    )
    def test_ack_commands_include_data_field(
        self, server_with_mock_app, cmd
    ):
        """Commands that previously returned ``{type: "ack"}`` with no
        data must now include ``data: {}`` for shape consistency.
        """
        srv = server_with_mock_app
        # Stub the service methods these commands call so they succeed.
        srv.service.toggle_dictation = lambda: None
        srv.service.undo_last = lambda: None
        srv.service.delete_history = lambda rec_id: None
        srv.service.clear_history = lambda: None

        # Build a minimal request.  delete_history needs an id.
        data = {"id": 1} if cmd == "delete_history" else None
        msg = {"id": 1, "type": cmd}
        if data is not None:
            msg["data"] = data

        result = srv._dispatch(msg)
        assert result is not None
        assert result["type"] == "ack", f"unexpected type: {result}"
        assert "data" in result, (
            f"ack response for {cmd} missing `data` field: {result}"
        )
        assert isinstance(result["data"], dict), (
            f"`data` must be a dict for {cmd}: {result}"
        )

    def test_ack_with_payload_keeps_data(self, server_with_mock_app):
        """toggle_favorite returns ``{type: "ack", data: {favorite: bool}}``
        — the existing data must NOT be overwritten by the empty-default
        fallback."""
        srv = server_with_mock_app
        srv.service.toggle_favorite = lambda rec_id: True

        result = srv._dispatch({
            "id": 1, "type": "toggle_favorite", "data": {"id": 42}
        })
        assert result["type"] == "ack"
        assert result["data"] == {"favorite": True}

    def test_error_responses_keep_data(self, server_with_mock_app):
        """Error responses must keep their existing ``data`` field."""
        srv = server_with_mock_app
        srv.service.toggle_dictation = MagicMock(
            side_effect=RuntimeError("boom")
        )

        result = srv._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result["type"] == "error"
        assert result["data"] == {"message": "boom"}


class TestConsoleModePushVisibility:
    """NEW-IPC-008: push events must be visible at INFO level when no
    client is connected (console mode)."""

    def test_non_waveform_push_logged_at_info(
        self, server_with_mock_app, caplog
    ):
        """A status_change push event with no client must produce an
        INFO-level log entry, not just DEBUG."""
        srv = server_with_mock_app
        # No TCP client, no TCP mode → falls into the "no client" branch.
        srv._tcp_client = None
        srv._tcp_mode = False

        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            srv.push({"type": "status_change", "data": {"status": "idle"}})

        # At least one INFO log entry must mention the dropped event.
        info_records = [
            r for r in caplog.records
            if r.levelno >= logging.INFO
            and "no client" in r.getMessage().lower()
        ]
        assert info_records, (
            "Expected an INFO-level log for dropped push event, got: "
            f"{[r.getMessage() for r in caplog.records]}"
        )

    def test_waveform_push_kept_at_debug(
        self, server_with_mock_app, caplog
    ):
        """High-frequency waveform events must stay at DEBUG to avoid
        log flooding."""
        srv = server_with_mock_app
        srv._tcp_client = None
        srv._tcp_mode = False

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"):
            srv.push({"type": "bubble_level", "data": {"level": 0.5}})

        # No INFO-level records should be emitted for high-freq events.
        info_records = [
            r for r in caplog.records
            if r.levelno >= logging.INFO
            and "no client" in r.getMessage().lower()
        ]
        assert not info_records, (
            "Waveform events should not be logged at INFO: "
            f"{[r.getMessage() for r in info_records]}"
        )


class TestGetInstancePushFnTracking:
    """NEW-IPC-013: each IPCServer tracks its own push callable so
    stop() can unregister just that one without affecting others."""

    def test_start_registers_instance_push_fn(self, server_with_mock_app):
        srv = server_with_mock_app
        # Avoid the real _hook_tray_set_state + _run thread.
        srv.app.tray = MagicMock()
        srv._hook_tray_set_state = lambda: None
        srv._stdin_thread = MagicMock()

        srv.start()
        try:
            assert srv._push_fn is not None
            with ipc_server._push_event_registry_lock:
                assert srv._push_fn in ipc_server._push_event_registry
        finally:
            srv.stop()

    def test_stop_clears_instance_push_fn(self, server_with_mock_app):
        srv = server_with_mock_app
        srv.app.tray = MagicMock()
        srv._hook_tray_set_state = lambda: None
        srv._stdin_thread = MagicMock()

        srv.start()
        srv.stop()
        assert srv._push_fn is None
        with ipc_server._push_event_registry_lock:
            assert len(ipc_server._push_event_registry) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
