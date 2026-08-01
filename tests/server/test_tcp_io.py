"""TCP I/O tests: pending-buffer cap, lock-split send, write timeout, ack shape.

Classes:
- TestPendingTcpBufferCappedAtThousand  — SEC-008 _pending_tcp cap
- TestAckShapeConsistency               — NEW-IPC-006 ack shape (data field)
- TestSendDoesNotHoldLockDuringWrite    — NEW-IPC-014/CONC-001/CONC-003 send() lock split
- TestWriteTimeoutConstant              — NEW-CONC-003 timeout constant
- TestSendStillDeliversMessages         — regression: lock-split doesn't break delivery

Split out from the original monolithic tests/test_server.py (DT-37, Phase 4.5).
"""

import json
import threading
import time
from unittest.mock import MagicMock

import pytest

from tests.server.conftest import (  # noqa: F401
    _TCP_WRITE_TIMEOUT_SECONDS,
    IPCServer,
    mock_app,
    server,
    server_with_mock_app__006,
    server_with_mock_app__014,
)

# ── SEC-008: _pending_tcp cap ────────────────────────────────────────────


class TestPendingTcpBufferCappedAtThousand:
    """SEC-008: when the TCP client disconnects, push events accumulate
    in ``_pending_tcp``.  Without a cap, a 16 Hz waveform bubble
    source could grow the list to GB within minutes.  The fix caps
    the list at 1000 entries, dropping the oldest."""

    def test_pending_tcp_capped_at_1000(self, server, mock_app):
        """Pushing > 1000 events while disconnected must cap the list."""
        # Set up server in TCP mode with no client connected
        server._tcp_mode = True
        server._tcp_client = None
        server._pending_tcp.clear()
        # Push 1500 events
        for i in range(1500):
            server.push({"type": "test", "data": {"i": i}})
        assert len(server._pending_tcp) <= 1000, f"expected <= 1000, got {len(server._pending_tcp)}"
        # The most recent entries should be preserved
        last = server._pending_tcp[-1]
        import json

        assert json.loads(last)["data"]["i"] == 1499

    def test_pending_tcp_does_not_grow_unboundedly(self, server, mock_app):
        """Even with sustained pushing, the list size stays bounded."""
        server._tcp_mode = True
        server._tcp_client = None
        server._pending_tcp.clear()
        for _ in range(10000):
            server.push({"type": "test"})
        assert len(server._pending_tcp) <= 1000


# === , ,  === (ack shape subset)


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
    def test_ack_commands_include_data_field(self, server_with_mock_app__006, cmd):
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
        assert "data" in result, f"ack response for {cmd} missing `data` field: {result}"
        assert isinstance(result["data"], dict), f"`data` must be a dict for {cmd}: {result}"

    def test_ack_with_payload_keeps_data(self, server_with_mock_app__006):
        """toggle_favorite returns ``{type: "ack", data: {favorite: bool}}``
        — the existing data must NOT be overwritten by the empty-default
        fallback."""
        srv = server_with_mock_app__006
        srv.service.toggle_favorite = lambda rec_id: True

        result = srv._dispatch({"id": 1, "type": "toggle_favorite", "data": {"id": 42}})
        assert result["type"] == "ack"
        assert result["data"] == {"favorite": True}

    def test_error_responses_keep_data(self, server_with_mock_app__006):
        """Error responses must keep their existing ``data`` field."""
        srv = server_with_mock_app__006
        srv.service.toggle_dictation = MagicMock(side_effect=RuntimeError("boom"))

        result = srv._dispatch({"id": 1, "type": "toggle_dictation"})
        assert result["type"] == "error"
        # (): generic envelope, no str(e) leak.
        assert result["data"]["code"] == "server.internal_error"
        assert result["data"]["message"] == "internal error"


# === , ,  ===
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


class TestSendDoesNotHoldLockDuringWrite:
    """NEW-IPC-014: ``sendall`` must run OUTSIDE ``self._lock``."""

    def test_concurrent_send_and_dispatch_do_not_serialize(self, server_with_mock_app__014):
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
            f"Lock took {lock_grab_latency:.3f}s to acquire — _send is still holding the lock during the slow write"
        )

    def test_settimeout_called_on_tcp_socket(self, server_with_mock_app__014):
        """NEW-CONC-003 / CR-2: _send must call settimeout before sendall so a
        stalled client can't block the worker forever, and must restore the
        PREVIOUS timeout (NOT clobber to None) so the dispatch-loop readline
        keeps its auth-read deadline and the connection can be reaped on
        cleanup."""
        srv = server_with_mock_app__014

        fake_conn = MagicMock()
        fake_conn.settimeout = MagicMock()
        # gettimeout() is called to capture the previous timeout
        # before overwriting it.  Mock it to return a distinctive
        # sentinel so we can verify the restore.
        _PREV_TIMEOUT = 7.0  # noqa: N806  simulates an auth-read deadline
        fake_conn.gettimeout = MagicMock(return_value=_PREV_TIMEOUT)

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
        # restored to _PREV_TIMEOUT (NOT None) in the finally block.
        assert fake_conn.settimeout.call_count >= 2, (
            f"settimeout must be called at least twice (set + restore): {fake_conn.settimeout.call_count}"
        )
        # First call sets the timeout.
        first_call = fake_conn.settimeout.call_args_list[0]
        assert first_call[0][0] == _TCP_WRITE_TIMEOUT_SECONDS, (
            f"first settimeout must be {_TCP_WRITE_TIMEOUT_SECONDS}, got {first_call[0][0]}"
        )
        # last call must restore _PREV_TIMEOUT (the auth-read
        # deadline), NOT None.  Restoring None was the root cause of
        # the auth-timeout/close deadlock: a blocking socket could
        # never time out, so the reader thread never exited and
        # _TCPLineIO.close() deadlocked against the in-progress recv.
        last_call = fake_conn.settimeout.call_args_list[-1]
        assert last_call[0][0] == _PREV_TIMEOUT, (
            f"last settimeout must restore the PREVIOUS timeout "
            f"({_PREV_TIMEOUT}), got {last_call[0][0]!r}. Restoring None "
            "clobbers the auth-read deadline and re-introduces the CR-2 "
            "deadlock."
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
        assert srv._tcp_client is None, "_send should have cleared _tcp_client after write failure"


class TestWriteTimeoutConstant:
    """NEW-CONC-003: the timeout constant must be defined and reasonable."""

    def test_timeout_is_positive_and_bounded(self):
        assert 0.5 <= _TCP_WRITE_TIMEOUT_SECONDS <= 10.0, (
            f"TCP write timeout must be in a sensible range (0.5–10s); got {_TCP_WRITE_TIMEOUT_SECONDS}"
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
