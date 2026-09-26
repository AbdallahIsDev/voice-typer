"""Push-event tests: registry, multi-instance fan-out, visibility, lifecycle."""

import logging
import threading
from unittest.mock import MagicMock

from tests.server.conftest import (  # noqa: F401
    clean_registry,
    event_bus,
    ipc_server,
    mock_app,
    server,
    server_with_mock_app_for_push_events,
)


class TestPushEvents:
    def test_push_sends_unsolicited_message(self, server):
        server._send = MagicMock()
        server.push({"type": "status_change", "data": {"status": "recording"}})
        server._send.assert_called_once_with(
            {
                "type": "status_change",
                "data": {"status": "recording"},
            }
        )

    def test_tray_set_state_triggers_push(self, server, mock_app, monkeypatch):
        from voice_typer.server import event_bus as bus
        from voice_typer.server.tray import AppState

        published: list[dict] = []
        monkeypatch.setattr(bus, "publish", lambda msg: published.append(msg))
        server._hook_tray_set_state()

        mock_app.tray.set_state(AppState.RECORDING, "Recording...")

        # The original set_state should have been called
        assert len(mock_app.tray.set_state_calls) == 1
        assert mock_app.tray.set_state_calls[0][0] == AppState.RECORDING

        assert len(published) == 1
        assert published[0] == {
            "type": "status_change",
            "data": {"status": "recording", "message": "Recording..."},
        }

    def test_tray_set_state_forwards_empty_message(self, server, mock_app, monkeypatch):
        """Regression: the default empty-string message must still"""
        from voice_typer.server import event_bus as bus
        from voice_typer.server.tray import AppState

        published: list[dict] = []
        monkeypatch.setattr(bus, "publish", lambda msg: published.append(msg))
        server._hook_tray_set_state()

        # No explicit message, relies on the ``message=""`` default.
        mock_app.tray.set_state(AppState.IDLE)

        assert len(published) == 1
        assert published[0] == {
            "type": "status_change",
            "data": {"status": "idle", "message": ""},
        }

    def test_tray_set_state_forwards_error_message(self, server, mock_app, monkeypatch):
        """Regression: a multi-line error message set via ``set_state``"""
        from voice_typer.server import event_bus as bus
        from voice_typer.server.tray import AppState

        published: list[dict] = []
        monkeypatch.setattr(bus, "publish", lambda msg: published.append(msg))
        server._hook_tray_set_state()

        mock_app.tray.set_state(AppState.ERROR, "Transcription failed: model crashed")

        assert len(published) == 1
        assert published[0] == {
            "type": "status_change",
            "data": {
                "status": "error",
                "message": "Transcription failed: model crashed",
            },
        }


class TestPushEventNow:
    """_push_event_now sends events to the active IPC server instance."""

    def test_returns_false_when_no_server(self, monkeypatch):
        """With no active push function, should return False."""
        import voice_typer.server.ipc_server as ipc_mod

        # Snapshot and clear the registry so the test sees an empty
        with event_bus._lock:
            original = set(event_bus._subscribers)
            event_bus._subscribers.clear()
        try:
            result = ipc_mod._push_event_now({"type": "show_window"})
            assert result is False
        finally:
            with event_bus._lock:
                event_bus._subscribers.update(original)

    def test_returns_true_when_server_active(self, server, monkeypatch):
        """With an active server, _push_event_now should succeed."""
        server._send = MagicMock()
        server.start()
        import voice_typer.server.ipc_server as ipc_mod

        result = ipc_mod._push_event_now({"type": "show_window"})
        assert result is True
        server.stop()

    def test_show_window_message_reaches_push(self, server, monkeypatch):
        """The show_window message type used by tray.open_app_window"""
        server._send = MagicMock()
        server.start()
        import voice_typer.server.ipc_server as ipc_mod

        ipc_mod._push_event_now({"type": "show_window"})
        # _push_event_now delegates to server.push → _send
        server._send.assert_called()
        server.stop()

    def test_exception_in_push_returns_false(self, server, monkeypatch, clean_registry):
        """If the push function raises, _push_event_now should return False."""
        import voice_typer.server.ipc_server as ipc_mod

        def broken_fn(msg):
            raise RuntimeError("broken")

        event_bus.subscribe(broken_fn)
        try:
            result = ipc_mod._push_event_now({"type": "show_window"})
            assert result is False
        finally:
            event_bus.unsubscribe(broken_fn)


# === , ,  ===
"""Regression tests for the fix, the fix, the fix.

Two IPCServer instances in the same process used to stomp
each other via the module-level ``_push_event`` global.  The second
start() would overwrite the first server's push callable, and the
first server's stop() would clear the global entirely, leaving the
second server unable to push events.  Fix: replace the global with a
thread-safe registry (set) of push callables; ``_push_event_now`` fans
out to ALL registered servers.

5 commands returned ``{type: "ack"}`` with no ``data``
field, 2 returned ``{type: "ack", data: {...}}``.  Fix: every
response now carries an explicit ``data`` field (empty dict for acks
with no payload).

Push events were silently dropped at DEBUG level when no
client was connected, making the ``lausu`` console script
useless for diagnosis.  Fix: surface non-waveform push events at INFO
level so the user can actually see state changes / errors.
"""


class TestPushEventRegistryMultiInstance:
    """multiple IPCServer instances in the same process."""

    def test_two_servers_can_register_simultaneously(self, clean_registry):
        """Both servers' push callables must coexist in the registry."""
        calls_a: list = []
        calls_b: list = []
        event_bus.subscribe(calls_a.append)
        event_bus.subscribe(calls_b.append)

        # Both should be registered.
        with event_bus._lock:
            assert len(event_bus._subscribers) == 2

        # _push_event_now fans out to both.
        result = ipc_server._push_event_now({"type": "test"})
        assert result is True
        assert calls_a == [{"type": "test"}]
        assert calls_b == [{"type": "test"}]

    def test_stop_one_server_does_not_clear_other(self, clean_registry):
        """Stopping server A must not affect server B's push registration."""
        calls_a: list = []
        calls_b: list = []
        event_bus.subscribe(calls_a.append)
        event_bus.subscribe(calls_b.append)

        # Server A stops, unregister just its callable.
        event_bus.unsubscribe(calls_a.append)

        # Server B must still be registered.
        with event_bus._lock:
            assert len(event_bus._subscribers) == 1
            assert calls_b.append in event_bus._subscribers

        # _push_event_now must still reach server B.
        result = ipc_server._push_event_now({"type": "after_a_stop"})
        assert result is True
        assert calls_a == []  # A stopped, didn't receive
        assert calls_b == [{"type": "after_a_stop"}]

    def test_set_push_event_with_none_is_noop(self, clean_registry):
        """_set_push_event(None) must NOT clear the registry (it's a no-op)."""
        calls_a: list = []
        event_bus.subscribe(calls_a.append)
        assert len(event_bus._subscribers) == 1

        # _set_push_event(None) must be a no-op (not a clear).
        event_bus.subscribe(None)
        assert len(event_bus._subscribers) == 1

    def test_clear_push_event_idempotent(self, clean_registry):
        """Clearing an unregistered callable must be a no-op (no error)."""
        fn = lambda msg: None  # noqa: E731
        # Not yet registered, clear must not raise.
        event_bus.unsubscribe(fn)
        # Register and clear.
        event_bus.subscribe(fn)
        event_bus.unsubscribe(fn)
        event_bus.unsubscribe(fn)  # Second clear is also safe.
        assert len(event_bus._subscribers) == 0

    def test_push_event_now_thread_safe(self, clean_registry):
        """Concurrent _push_event_now calls must all succeed without"""
        received: list = []
        lock = threading.Lock()

        def listener(msg):
            with lock:
                received.append(msg)

        event_bus.subscribe(listener)

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


class TestConsoleModePushVisibility:
    """push events must be visible at INFO level when no"""

    def test_non_waveform_push_logged_at_info(self, server_with_mock_app_for_push_events, caplog):
        """A status_change push event with no client must produce an"""
        srv = server_with_mock_app_for_push_events
        # No TCP client, no TCP mode → falls into the "no client" branch.
        srv._tcp_client = None
        srv._tcp_mode = False

        with caplog.at_level(logging.INFO, logger="voice_typer.server.ipc_server"):
            srv.push({"type": "status_change", "data": {"status": "idle"}})

        # At least one INFO log entry must mention the dropped event.
        info_records = [
            r for r in caplog.records if r.levelno >= logging.INFO and "no client" in r.getMessage().lower()
        ]
        assert info_records, (
            f"Expected an INFO-level log for dropped push event, got: {[r.getMessage() for r in caplog.records]}"
        )

    def test_waveform_push_kept_at_debug(self, server_with_mock_app_for_push_events, caplog):
        """High-frequency waveform events must stay at DEBUG to avoid"""
        srv = server_with_mock_app_for_push_events
        srv._tcp_client = None
        srv._tcp_mode = False

        with caplog.at_level(logging.DEBUG, logger="voice_typer.server.ipc_server"):
            srv.push({"type": "bubble_level", "data": {"level": 0.5}})

        # No INFO-level records should be emitted for high-freq events.
        info_records = [
            r for r in caplog.records if r.levelno >= logging.INFO and "no client" in r.getMessage().lower()
        ]
        assert not info_records, (
            f"Waveform events should not be logged at INFO: {[r.getMessage() for r in info_records]}"
        )


class TestGetInstancePushFnTracking:
    """each IPCServer tracks its own push callable so"""

    def test_start_registers_instance_push_fn(self, server_with_mock_app_for_push_events):
        srv = server_with_mock_app_for_push_events
        # Avoid the real _hook_tray_set_state + _run thread.
        srv.app.tray = MagicMock()
        srv._hook_tray_set_state = lambda: None
        srv._stdin_thread = MagicMock()

        srv.start()
        try:
            assert srv._push_fn is not None
            with event_bus._lock:
                assert srv._push_fn in event_bus._subscribers
        finally:
            srv.stop()

    def test_stop_clears_instance_push_fn(self, server_with_mock_app_for_push_events, clean_registry):
        srv = server_with_mock_app_for_push_events
        srv.app.tray = MagicMock()
        srv._hook_tray_set_state = lambda: None
        srv._stdin_thread = MagicMock()

        srv.start()
        srv.stop()
        assert srv._push_fn is None
        with event_bus._lock:
            assert len(event_bus._subscribers) == 0
