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
from __future__ import annotations

import json
import socket
import threading
import time
from unittest import mock

import pytest

from voice_typer.server import ipc_server
from voice_typer.server.ipc_server import IPCServer, _TCP_WRITE_TIMEOUT_SECONDS


@pytest.fixture
def server_with_mock_app():
    app = mock.MagicMock()
    return IPCServer(app)


class TestSendDoesNotHoldLockDuringWrite:
    """NEW-IPC-014: ``sendall`` must run OUTSIDE ``self._lock``."""

    def test_concurrent_send_and_dispatch_do_not_serialize(
        self, server_with_mock_app
    ):
        """A slow ``_send`` (simulated via a blocking socket) must NOT
        block another thread from acquiring ``self._lock`` for an
        unrelated operation.

        Previously _send held the lock through sendall, so a slow
        client could stall every other dispatcher.
        """
        srv = server_with_mock_app

        # Build a fake TCP client whose write() blocks for 500ms.
        # We don't need a real socket — just an object whose write()
        # takes a long time.
        class SlowClient:
            def __init__(self):
                self.write_delay = 0.5
                self.conn = mock.MagicMock()
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

    def test_settimeout_called_on_tcp_socket(self, server_with_mock_app):
        """NEW-CONC-003: _send must call settimeout before sendall so a
        stalled client can't block the worker forever."""
        srv = server_with_mock_app

        fake_conn = mock.MagicMock()
        fake_conn.settimeout = mock.MagicMock()

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

    def test_write_failure_drops_client(self, server_with_mock_app):
        """NEW-CONC-003: when sendall raises (timeout or OSError), the
        client must be marked dead so the accept loop can pick up the
        next reconnect."""
        srv = server_with_mock_app

        fake_conn = mock.MagicMock()
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

    def test_message_reaches_tcp_client(self, server_with_mock_app):
        srv = server_with_mock_app
        received: list = []

        class CapturingClient:
            def __init__(self):
                self.conn = mock.MagicMock()
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

    def test_pending_drained_after_message(self, server_with_mock_app):
        """When _pending_tcp has entries, the next _send must drain them
        after the new message."""
        srv = server_with_mock_app
        received: list = []

        class CapturingClient:
            def __init__(self):
                self.conn = mock.MagicMock()
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
