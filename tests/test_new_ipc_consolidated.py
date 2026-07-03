"""Consolidated regression tests for the NEW-IPC-xxx series (IPC server hardening).

Merges:
- tests/test_new_ipc_001_tcp_accept_stop.py
- tests/test_new_ipc_006_008_013.py
- tests/test_new_ipc_014_conc_001_003.py
"""

# === Common imports (deduplicated from all source files) ===

from __future__ import annotations

import socket

import threading

import time

from unittest.mock import MagicMock, patch, call

import pytest

from voice_typer.server.ipc_server import IPCServer

import logging

from voice_typer.server import ipc_server

import json

from voice_typer.server.ipc_server import IPCServer, _TCP_WRITE_TIMEOUT_SECONDS

# === Common helpers / fixtures (identical across files) ===

@pytest.fixture
def server_with_mock_app():
    """Construct an IPCServer with a mocked app (no real VoiceTyperApp)."""
    app = MagicMock()
    # Avoid the service.py import side-effects on real VoiceTyperApp.
    # The IPCServer constructor only needs `app` to attach to .service.
    srv = IPCServer(app)
    return srv

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

# === Source: tests/test_new_ipc_001_tcp_accept_stop.py ===

"""Regression tests for NEW-IPC-001: TCP accept loop must be unblockable
by stop().

Previous defects:
1. The accept loop checked ``getattr(self, '_stopped', False)`` but
   ``_stopped`` was never set on the IPCServer instance.  ``stop()``
   only set ``self._running = False``.
2. The listening socket was a local variable in ``_accept_tcp`` with
   no instance reference, so ``stop()`` could not close it to unblock
   ``accept()``.
3. Result: ``stop()`` while no client was connected left the daemon
   thread blocked forever in ``server.accept()``.  Threads and sockets
   leaked across test start/stop cycles.

These tests verify the fix:
- ``stop()`` actually closes the listening socket.
- The accept loop checks ``self._running`` (not the never-set ``_stopped``).
- A real start_tcp → stop cycle exits the accept thread within a
  reasonable deadline.
"""

class TestStopUnblocksAcceptLoop:
    """NEW-IPC-001: stop() must be able to wake a blocked accept()."""

    def test_running_flag_flipped_by_stop(self, server_with_mock_app):
        """stop() sets _running = False (the flag the accept loop checks)."""
        server_with_mock_app._running = True
        server_with_mock_app.stop()
        assert server_with_mock_app._running is False

    def test_stop_closes_listening_socket(self, server_with_mock_app):
        """stop() closes the stored listening socket and clears the ref."""
        fake_sock = MagicMock()
        server_with_mock_app._tcp_server_socket = fake_sock
        server_with_mock_app.stop()
        fake_sock.close.assert_called_once()
        assert server_with_mock_app._tcp_server_socket is None

    def test_stop_is_idempotent(self, server_with_mock_app):
        """Calling stop() multiple times must not raise."""
        server_with_mock_app._running = True
        server_with_mock_app.stop()
        # Second call should be a no-op (no exception).
        server_with_mock_app.stop()
        assert server_with_mock_app._running is False
        assert server_with_mock_app._tcp_server_socket is None

    def test_accept_loop_exits_on_stop(self):
        """End-to-end: start_tcp on a real port, then stop() must
        unblock the accept thread within a deadline.

        Previously this test would have hung forever because stop()
        couldn't close the listening socket.
        """
        app = MagicMock()
        srv = IPCServer(app)

        # Pick a free port by binding a temporary socket first.
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        # Disable auth so the test doesn't need a token.  The accept
        # loop doesn't care about auth — it just needs to listen and
        # accept; we never actually connect a client.
        with patch.dict("os.environ", {}, clear=False):
            # Make sure VOICE_TYPER_IPC_TOKEN is not set so the loop
            # logs the warning instead of bailing out.
            import os
            old = os.environ.pop("VOICE_TYPER_IPC_TOKEN", None)
            try:
                srv._running = True
                srv.start_tcp(port)

                # Give the accept thread a moment to bind and start
                # listening.  Poll the socket reference to know it's
                # ready.
                deadline = time.monotonic() + 2.0
                while (
                    srv._tcp_server_socket is None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                assert srv._tcp_server_socket is not None, (
                    "accept thread did not store the listening socket"
                )

                # Now stop() must unblock the accept loop.
                srv.stop()

                # The accept thread should exit promptly.  We can't
                # join a daemon thread we didn't keep a reference to,
                # but we CAN verify the socket is closed and the
                # _running flag is False.  We also wait briefly and
                # confirm the socket reference is cleared by the loop
                # exit path.
                deadline = time.monotonic() + 2.0
                while (
                    srv._tcp_server_socket is not None
                    and time.monotonic() < deadline
                ):
                    time.sleep(0.02)
                # The accept loop's exit path clears the reference.
                assert srv._tcp_server_socket is None, (
                    "accept loop did not clear _tcp_server_socket on exit"
                )
                assert srv._running is False
            finally:
                if old is not None:
                    import os
                    os.environ["VOICE_TYPER_IPC_TOKEN"] = old
                # Belt-and-suspenders cleanup.
                srv.stop()

    def test_accept_loop_checks_running_not_stopped(self):
        """The accept loop's ``while`` condition must reference
        ``self._running``, not the never-set legacy flag.  We strip
        comments and docstrings before checking so explanatory text
        that mentions the old pattern doesn't trip the assertion.
        """
        import inspect
        import re
        source = inspect.getsource(IPCServer._accept_tcp)
        # Strip comment lines (lines whose first non-whitespace is #).
        code_lines = []
        for line in source.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                continue
            # Strip inline comments.
            if "#" in line:
                # Naive split — good enough for this static check.
                line = line.split("#", 1)[0]
            code_lines.append(line)
        code_only = "\n".join(code_lines)

        assert "while self._running" in code_only, (
            "_accept_tcp must use `while self._running:` as its loop "
            "condition (the canonical flag set by stop())"
        )
        # The legacy getattr pattern must NOT appear in actual code.
        assert 'getattr(self' not in code_only, (
            "_accept_tcp still uses the legacy getattr(self, ...) pattern"
        )

    def test_stop_clears_listening_socket_ref(self, server_with_mock_app):
        """The instance must store _tcp_server_socket (not just a local
        var) so stop() can close it.  This is a static check.
        """
        import inspect
        init_src = inspect.getsource(IPCServer.__init__)
        assert "_tcp_server_socket" in init_src, (
            "IPCServer.__init__ must initialize _tcp_server_socket"
        )
        accept_src = inspect.getsource(IPCServer._accept_tcp)
        assert "self._tcp_server_socket = server" in accept_src, (
            "_accept_tcp must store the listening socket on self"
        )
        stop_src = inspect.getsource(IPCServer.stop)
        assert "_tcp_server_socket" in stop_src, (
            "stop() must close the listening socket"
        )

if __name__ == "__main__":
    pytest.main([__file__, "-v"])

# === Source: tests/test_new_ipc_006_008_013.py ===

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

@pytest.fixture
def server_with_mock_app__006():
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
        self, server_with_mock_app__006, cmd
    ):
        """Commands that previously returned ``{type: "ack"}`` with no
        data must now include ``data: {}`` for shape consistency.
        """
        srv = server_with_mock_app__006
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

    def test_ack_with_payload_keeps_data(self, server_with_mock_app__006):
        """toggle_favorite returns ``{type: "ack", data: {favorite: bool}}``
        — the existing data must NOT be overwritten by the empty-default
        fallback."""
        srv = server_with_mock_app__006
        srv.service.toggle_favorite = lambda rec_id: True

        result = srv._dispatch({
            "id": 1, "type": "toggle_favorite", "data": {"id": 42}
        })
        assert result["type"] == "ack"
        assert result["data"] == {"favorite": True}

    def test_error_responses_keep_data(self, server_with_mock_app__006):
        """Error responses must keep their existing ``data`` field."""
        srv = server_with_mock_app__006
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
        self, server_with_mock_app__006, caplog
    ):
        """A status_change push event with no client must produce an
        INFO-level log entry, not just DEBUG."""
        srv = server_with_mock_app__006
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
        self, server_with_mock_app__006, caplog
    ):
        """High-frequency waveform events must stay at DEBUG to avoid
        log flooding."""
        srv = server_with_mock_app__006
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

    def test_start_registers_instance_push_fn(self, server_with_mock_app__006):
        srv = server_with_mock_app__006
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

    def test_stop_clears_instance_push_fn(self, server_with_mock_app__006):
        srv = server_with_mock_app__006
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

# === Source: tests/test_new_ipc_014_conc_001_003.py ===

"""Regression tests for NEW-IPC-014, NEW-CONC-001, NEW-CONC-003.

NEW-IPC-014: ``_send`` used to hold ``self._lock`` through the entire
``json.dumps + sendall + pending drain`` path.  This blocked every
other IPC dispatcher while a slow Electron renderer drained its TCP
receive buffer.

NEW-CONC-001: same root cause — bubble push from the audio callback
worker held the same lock as ``_dispatch``, so user-visible commands
like ``get_microphones`` lagged during recording.

NEW-CONC-003: ``sendall`` had no write timeout, so a stalled renderer
could block the worker thread indefinitely, causing an XRUN storm.

Fix:
1. Snapshot transport state under the lock, then release it.
2. Perform ``sendall`` outside the lock.
3. Set a write timeout so a stalled client can't block forever.
"""

@pytest.fixture
def server_with_mock_app__014():
    app = MagicMock()
    return IPCServer(app)

class TestSendDoesNotHoldLockDuringWrite:
    """NEW-IPC-014: ``sendall`` must run OUTSIDE ``self._lock``."""

    def test_concurrent_send_and_dispatch_do_not_serialize(
        self, server_with_mock_app__014
    ):
        """A slow ``_send`` (simulated via a blocking socket) must NOT
        block another thread from acquiring ``self._lock`` for an
        unrelated operation.

        Previously _send held the lock through sendall, so a slow
        client could stall every other dispatcher.
        """
        srv = server_with_mock_app__014

        # Build a fake TCP client whose write() blocks for 500ms.
        # We don't need a real socket — just an object whose write()
        # takes a long time.
        class SlowClient:
            def __init__(self):
                self.write_delay = 0.5
                self.conn = MagicMock()
                self.conn.settimeout = lambda x: None

            def write(self, _text):
                time.sleep(self.write_delay)

            def flush(self):
                pass

            def close(self):
                pass

        srv._tcp_client = SlowClient()
        srv._tcp_mode = True

        # Thread A: slow _send (500ms).
        def slow_send():
            srv._send({"type": "test", "data": {"slow": True}})

        # Thread B: try to acquire the lock for an unrelated op.
        lock_acquired_at = []
        def grab_lock():
            with srv._lock:
                lock_acquired_at.append(time.monotonic())

        t_send = threading.Thread(target=slow_send)
        t_lock = threading.Thread(target=grab_lock)

        start = time.monotonic()
        t_send.start()
        # Give the send thread a moment to enter _send.
        time.sleep(0.05)
        t_lock.start()

        t_send.join(timeout=2.0)
        t_lock.join(timeout=2.0)

        # The lock-grabber must have acquired the lock WELL BEFORE the
        # slow send finished.  If _send still held the lock during
        # sendall, the grabber would only acquire after ~500ms.
        assert lock_acquired_at, "lock grabber never acquired the lock"
        lock_grab_latency = lock_acquired_at[0] - start
        assert lock_grab_latency < 0.3, (
            f"Lock took {lock_grab_latency:.3f}s to acquire — _send is "
            "still holding the lock during the slow write"
        )

    def test_settimeout_called_on_tcp_socket(self, server_with_mock_app__014):
        """NEW-CONC-003: _send must call settimeout before sendall so a
        stalled client can't block the worker forever."""
        srv = server_with_mock_app__014

        fake_conn = MagicMock()
        fake_conn.settimeout = MagicMock()

        class FakeClient:
            def __init__(self):
                self.conn = fake_conn

            def write(self, _text):
                pass

            def flush(self):
                pass

            def close(self):
                pass

        srv._tcp_client = FakeClient()
        srv._tcp_mode = True

        srv._send({"type": "test"})

        # settimeout must have been called with the write timeout, then
        # restored to None (blocking) in the finally block.
        assert fake_conn.settimeout.call_count >= 2, (
            "settimeout must be called at least twice (set + restore): "
            f"{fake_conn.settimeout.call_count}"
        )
        # First call sets the timeout.
        first_call = fake_conn.settimeout.call_args_list[0]
        assert first_call[0][0] == _TCP_WRITE_TIMEOUT_SECONDS, (
            f"first settimeout must be {_TCP_WRITE_TIMEOUT_SECONDS}, "
            f"got {first_call[0][0]}"
        )
        # Last call restores to None (blocking).
        last_call = fake_conn.settimeout.call_args_list[-1]
        assert last_call[0][0] is None, (
            f"last settimeout must restore None, got {last_call[0][0]}"
        )

    def test_write_failure_drops_client(self, server_with_mock_app__014):
        """NEW-CONC-003: when sendall raises (timeout or OSError), the
        client must be marked dead so the accept loop can pick up the
        next reconnect."""
        srv = server_with_mock_app__014

        fake_conn = MagicMock()
        fake_conn.settimeout = lambda x: None

        class FailingClient:
            def __init__(self):
                self.conn = fake_conn

            def write(self, _text):
                raise OSError("simulated connection lost")

            def flush(self):
                pass

            def close(self):
                pass

        failing = FailingClient()
        srv._tcp_client = failing
        srv._tcp_mode = True

        # _send must not raise — it should swallow the OSError and drop
        # the client.
        srv._send({"type": "test"})

        # _tcp_client must now be None (dropped).
        assert srv._tcp_client is None, (
            "_send should have cleared _tcp_client after write failure"
        )

class TestWriteTimeoutConstant:
    """NEW-CONC-003: the timeout constant must be defined and reasonable."""

    def test_timeout_is_positive_and_bounded(self):
        assert 0.5 <= _TCP_WRITE_TIMEOUT_SECONDS <= 10.0, (
            "TCP write timeout must be in a sensible range "
            f"(0.5–10s); got {_TCP_WRITE_TIMEOUT_SECONDS}"
        )

class TestSendStillDeliversMessages:
    """Regression: the lock-split refactor must not break delivery."""

    def test_message_reaches_tcp_client(self, server_with_mock_app__014):
        srv = server_with_mock_app__014
        received: list = []

        class CapturingClient:
            def __init__(self):
                self.conn = MagicMock()
                self.conn.settimeout = lambda x: None

            def write(self, text):
                received.append(text)

            def flush(self):
                pass

            def close(self):
                pass

        srv._tcp_client = CapturingClient()
        srv._tcp_mode = True

        srv._send({"type": "test_event", "data": {"x": 1}})

        assert len(received) == 1
        msg = json.loads(received[0])
        assert msg["type"] == "test_event"
        assert msg["data"] == {"x": 1}

    def test_pending_drained_after_message(self, server_with_mock_app__014):
        """When _pending_tcp has entries, the next _send must drain them
        after the new message."""
        srv = server_with_mock_app__014
        received: list = []

        class CapturingClient:
            def __init__(self):
                self.conn = MagicMock()
                self.conn.settimeout = lambda x: None

            def write(self, text):
                received.append(text)

            def flush(self):
                pass

            def close(self):
                pass

        srv._tcp_client = CapturingClient()
        srv._tcp_mode = True

        # Simulate pending entries left from a previous disconnect.
        with srv._lock:
            srv._pending_tcp.append(json.dumps({"type": "pending_1"}))
            srv._pending_tcp.append(json.dumps({"type": "pending_2"}))

        srv._send({"type": "new_message"})

        # The new message plus both pending must be delivered.
        assert len(received) == 3
        types = [json.loads(r)["type"] for r in received]
        assert "new_message" in types
        assert "pending_1" in types
        assert "pending_2" in types
        # Pending queue must be empty after drain.
        assert srv._pending_tcp == []

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
