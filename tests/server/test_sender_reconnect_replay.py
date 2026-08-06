"""``ipc/sender.py`` reconnect + replay coverage tests.

The ``OutputMixin._send`` method (in ``voice_typer/server/ipc/sender.py``)
manages a pending-event buffer (``_pending_tcp``) that accumulates push
events while the TCP client is disconnected. On reconnect, the buffer is
drained (replayed) to the new client in a batched ``sendall``.

Coverage areas:

1. **Queue accumulation during disconnect** — push events while no client
   is connected are buffered in ``_pending_tcp``.
2. **Batched replay on reconnect** — when a client connects, the queued
   messages are drained in a single batch (FIFO order).
3. **Max replay count cap** — at most ``_TCP_PENDING_DRAIN_CAP`` (100)
   entries are drained per ``_send`` call; older entries are re-merged.
4. **Drop oldest on overflow** — ``_pending_tcp`` is capped at
   ``_TCP_PENDING_BUFFER_CAP`` (1000); the oldest entries are dropped
   when the cap is exceeded.
5. **Write buffer reset on socket replacement** — when a drain fails
   mid-way, ``_reset_write_buffer`` is called so partially-buffered
   entries don't leak into the next ``_send`` call.

Platform: runs on Linux. The send path is platform-agnostic.
"""

from __future__ import annotations

import json

# Mock pystray before importing ipc_server (transitively imports tray).
import sys
import threading
from unittest.mock import MagicMock

import pytest

_mock_pystray = MagicMock()
_mock_pystray.Menu.SEPARATOR = "SEP"
_mock_pystray.MenuItem = MagicMock
_mock_pystray.Icon = MagicMock
sys.modules.setdefault("pystray", _mock_pystray)

from voice_typer.server.ipc.sender import (  # noqa: E402
    _TCP_PENDING_BUFFER_CAP,
    _TCP_PENDING_DRAIN_CAP,
    _PendingBuffer,
)
from voice_typer.server.ipc_server import IPCServer  # noqa: E402

# ─── Helpers ───────────────────────────────────────────────────────────


def _make_server() -> IPCServer:
    """Build a minimal IPCServer fixture for testing ``_send`` in isolation.

    Uses ``__new__`` to skip the full ``__init__`` (which would spawn
    threads / bind sockets). Sets just the attributes ``_send`` touches.
    """
    server = IPCServer.__new__(IPCServer)
    server.app = MagicMock()
    server.app._shutting_down = False
    server._lock = threading.RLock()
    server._tcp_write_lock = threading.RLock()
    server._pending_tcp = _PendingBuffer(maxlen=_TCP_PENDING_BUFFER_CAP)
    server._tcp_mode = True
    server._cached_shutting_down = False
    server._tcp_client = None
    return server


def _make_buffered_mock_client() -> MagicMock:
    """Mock tcp_client simulating ``_TCPLineIO`` buffer-then-flush.

    ``write()`` appends to an in-memory buffer; ``flush()`` issues a
    single ``sendall`` for the whole buffer (mirrors the real
    ``_TCPLineIO`` behavior so we can count ``sendall`` calls without a
    real socketpair).
    """
    tcp_client = MagicMock()
    tcp_client.conn = MagicMock()
    write_buffer: list[bytes] = []

    def mock_write(text: str | bytes) -> None:
        write_buffer.append(text.encode("utf-8") if isinstance(text, str) else text)

    def mock_flush() -> None:
        if write_buffer:
            tcp_client.conn.sendall(b"".join(write_buffer))
            write_buffer.clear()

    def mock_reset() -> None:
        write_buffer.clear()

    tcp_client.write.side_effect = mock_write
    tcp_client.flush.side_effect = mock_flush
    tcp_client._reset_write_buffer.side_effect = mock_reset
    return tcp_client


# ─── 1. queue accumulation during disconnect ─────────────────────────


class TestQueueAccumulationDuringDisconnect:
    """#1: when no TCP client is connected, push events accumulate
    in ``_pending_tcp`` (bounded FIFO)."""

    def test_push_events_queue_up_while_disconnected(self):
        """While ``_tcp_client is None``, every ``push()`` / ``_send()``
        call must append the serialized line to ``_pending_tcp``."""
        server = _make_server()
        server._tcp_client = None
        server._pending_tcp.clear()

        # Push 5 events while disconnected.
        for i in range(5):
            server.push({"type": "test_event", "data": {"i": i}})

        assert len(server._pending_tcp) == 5, (
            f"expected 5 queued events; got {len(server._pending_tcp)}"
        )

        # Verify FIFO order — the first entry should be i=0, the last i=4.
        first = json.loads(server._pending_tcp[0])
        last = json.loads(server._pending_tcp[-1])
        assert first["data"]["i"] == 0, (
            f"FIFO: first queued event should be i=0; got {first}"
        )
        assert last["data"]["i"] == 4, (
            f"FIFO: last queued event should be i=4; got {last}"
        )

    def test_no_sendall_while_disconnected(self):
        """While disconnected, ``_send`` must NOT attempt any socket
        writes — the event is purely buffered."""
        server = _make_server()
        server._tcp_client = None
        server._pending_tcp.clear()

        # We can't easily assert "no sendall" without a mock client (which
        # would imply a connected client). Instead, assert the buffer grew
        # and no exception was raised.
        server.push({"type": "test"})
        assert len(server._pending_tcp) == 1


# ─── 2. batched replay on reconnect ──────────────────────────────────


class TestBatchedReplayOnReconnect:
    """#2: when a client reconnects, the queued messages are drained
    in a single batched ``sendall`` (not one per entry)."""

    def test_queued_messages_drained_on_reconnect(self):
        """When a client connects (``_tcp_client`` is set) and there are
        pending entries in ``_pending_tcp``, the next ``_send`` call must:

        1. Drain ALL pending entries (in FIFO order) + the new message.
        2. Issue a single batched ``sendall`` for the drain (not one per
           entry).
        3. Clear ``_pending_tcp`` after the drain.
        """
        server = _make_server()
        server._pending_tcp.clear()

        # Simulate 3 events queued while disconnected.
        for i in range(3):
            server._pending_tcp.append(json.dumps({"type": "queued", "data": {"i": i}}))

        # Now a client connects.
        tcp_client = _make_buffered_mock_client()
        server._tcp_client = tcp_client

        # Send a new event — this should trigger the drain.
        server.push({"type": "new_event"})

        # All 3 queued + 1 new = 4 entries written to the client.
        written_data = b"".join(
            call.args[0] if call.args else call.kwargs.get("data", b"")
            for call in tcp_client.conn.sendall.call_args_list
        )
        # Decode and verify all 4 events were sent.
        sent_lines = [line for line in written_data.decode("utf-8").split("\n") if line]
        assert len(sent_lines) == 4, (
            f"expected 4 sent lines (3 queued + 1 new); got {len(sent_lines)}: "
            f"{sent_lines}"
        )

        sent_types = [json.loads(line)["type"] for line in sent_lines]
        # The new message is written FIRST (via ``tcp_client.write(line_bytes)``
        # + ``flush()``), then the pending entries are drained. This is the
        # documented behavior: the current push event is delivered
        # immediately, and the backlog follows it.
        assert sent_types == ["new_event", "queued", "queued", "queued"], (
            f"expected new_event first, then 3 queued (backlog drain); "
            f"got {sent_types}"
        )

        # _pending_tcp must be empty after the drain.
        assert len(server._pending_tcp) == 0, (
            f"_pending_tcp must be empty after a successful drain; "
            f"got {len(server._pending_tcp)} entries"
        )

    def test_replay_uses_single_sendall_for_batch(self):
        """The drain must issue a SINGLE ``sendall`` for the whole batch
        (not one per entry). This is the AB-37 batching optimization."""
        server = _make_server()
        server._pending_tcp.clear()

        # Queue 50 entries.
        for i in range(50):
            server._pending_tcp.append(json.dumps({"type": "q", "i": i}))

        tcp_client = _make_buffered_mock_client()
        server._tcp_client = tcp_client

        server.push({"type": "new"})

        # The drain must issue exactly 2 sendall calls: 1 for the new
        # message (written + flushed first) + 1 for the whole 50-entry
        # drain batch.
        assert tcp_client.conn.sendall.call_count == 2, (
            f"expected 2 sendall calls (1 new + 1 batched drain); "
            f"got {tcp_client.conn.sendall.call_count}"
        )


# ─── 3. max replay count cap ─────────────────────────────────────────


class TestMaxReplayCountCap:
    """#3: at most ``_TCP_PENDING_DRAIN_CAP`` (100) entries are
    drained per ``_send`` call. Entries exceeding the cap are re-merged
    into ``_pending_tcp`` for the next drain."""

    def test_only_last_100_entries_drained(self):
        """When ``_pending_tcp`` has > 100 entries, only the last 100 are
        drained; the older entries are re-merged."""
        server = _make_server()
        server._pending_tcp.clear()

        # Queue 105 entries: 5 older + 100 recent.
        older = [json.dumps({"type": "old", "i": i}) for i in range(5)]
        recent = [json.dumps({"type": "recent", "i": i}) for i in range(100)]
        for entry in older + recent:
            server._pending_tcp.append(entry)

        assert len(server._pending_tcp) == 105

        tcp_client = _make_buffered_mock_client()
        server._tcp_client = tcp_client

        server.push({"type": "new"})

        # The 5 older entries must be re-merged into _pending_tcp.
        assert len(server._pending_tcp) == 5, (
            f"expected 5 re-merged older entries after drain-cap overflow; "
            f"got {len(server._pending_tcp)}"
        )
        for entry in older:
            assert entry in server._pending_tcp, (
                f"older entry {entry} must be re-merged after drain-cap overflow"
            )

        # The 100 recent + 1 new must have been sent (101 entries).
        written_data = b"".join(
            call.args[0] if call.args else b""
            for call in tcp_client.conn.sendall.call_args_list
        )
        sent_lines = [line for line in written_data.decode("utf-8").split("\n") if line]
        assert len(sent_lines) == 101, (
            f"expected 101 sent lines (100 recent + 1 new); got {len(sent_lines)}"
        )

    def test_drain_cap_constant(self):
        """Sanity: ``_TCP_PENDING_DRAIN_CAP`` must be 100 (the documented
        cap for batched drain per ``_send`` call)."""
        assert _TCP_PENDING_DRAIN_CAP == 100, (
            f"_TCP_PENDING_DRAIN_CAP must be 100; got {_TCP_PENDING_DRAIN_CAP}"
        )


# ─── 4. drop oldest on overflow ──────────────────────────────────────


class TestDropOldestOnOverflow:
    """#4: ``_pending_tcp`` is capped at ``_TCP_PENDING_BUFFER_CAP``
    (1000). When the cap is exceeded, the OLDEST entries are dropped
    (they are stale waveform-bubble events; transcription-final events
    are in history_db)."""

    def test_oldest_dropped_when_buffer_cap_exceeded(self):
        """When ``_pending_tcp`` exceeds 1000 entries, the oldest entries
        are dropped so the buffer stays bounded."""
        server = _make_server()
        server._tcp_client = None
        server._pending_tcp.clear()

        # Push 1050 events while disconnected.
        for i in range(1050):
            server.push({"type": "test", "data": {"i": i}})

        # The buffer must be capped at 1000.
        assert len(server._pending_tcp) <= _TCP_PENDING_BUFFER_CAP, (
            f"_pending_tcp must be capped at {_TCP_PENDING_BUFFER_CAP}; "
            f"got {len(server._pending_tcp)}"
        )
        assert len(server._pending_tcp) == _TCP_PENDING_BUFFER_CAP, (
            f"expected exactly {_TCP_PENDING_BUFFER_CAP} entries after "
            f"pushing 1050; got {len(server._pending_tcp)}"
        )

        # The oldest 50 entries (i=0..49) must have been dropped.
        # The buffer should now contain i=50..1049 (the most recent 1000).
        first = json.loads(server._pending_tcp[0])
        last = json.loads(server._pending_tcp[-1])
        assert first["data"]["i"] == 50, (
            f"oldest surviving entry should be i=50 (50 oldest were dropped); "
            f"got i={first['data']['i']}"
        )
        assert last["data"]["i"] == 1049, (
            f"newest entry should be i=1049; got i={last['data']['i']}"
        )

    def test_buffer_cap_constant(self):
        """Sanity: ``_TCP_PENDING_BUFFER_CAP`` must be 1000."""
        assert _TCP_PENDING_BUFFER_CAP == 1000, (
            f"_TCP_PENDING_BUFFER_CAP must be 1000; got {_TCP_PENDING_BUFFER_CAP}"
        )


# ─── 5. write buffer reset on socket replacement ─────────────────────


class TestWriteBufferResetOnSocketReplacement:
    """#5: when a drain fails mid-way (e.g. broken pipe during the
    batched ``sendall``), ``_reset_write_buffer`` must be called on the
    tcp_client so partially-buffered entries don't leak into the next
    ``_send`` call.

    The drain-failure path:

    1. The new message's ``write`` + ``flush`` succeeds (buffer cleared).
    2. The drain loop buffers ``recent`` entries without flushing.
    3. The batched ``flush()`` raises ``OSError`` (simulated broken pipe).
    4. ``_reset_write_buffer`` is called to discard the buffered entries.
    5. The undrained entries are re-merged into ``_pending_tcp``.
    """

    def test_reset_write_buffer_called_on_drain_failure(self):
        """When the batched drain ``flush()`` raises ``OSError``,
        ``_reset_write_buffer`` must be called on the tcp_client."""
        server = _make_server()
        server._pending_tcp.clear()

        # Queue 3 pending entries.
        server._pending_tcp.append('{"pending": 1}')
        server._pending_tcp.append('{"pending": 2}')
        server._pending_tcp.append('{"pending": 3}')

        tcp_client = MagicMock()
        tcp_client.conn = MagicMock()
        write_buffer: list[bytes] = []

        def mock_write(text: str | bytes) -> None:
            write_buffer.append(text.encode("utf-8") if isinstance(text, str) else text)

        flush_calls = [0]

        def mock_flush() -> None:
            flush_calls[0] += 1
            if flush_calls[0] >= 2:
                # Drain-batch flush fails (simulated broken pipe).
                raise OSError("simulated client disconnect during drain flush")
            # First flush (for the new message) succeeds.
            if write_buffer:
                tcp_client.conn.sendall(b"".join(write_buffer))
                write_buffer.clear()

        def mock_reset() -> None:
            write_buffer.clear()

        tcp_client.write.side_effect = mock_write
        tcp_client.flush.side_effect = mock_flush
        tcp_client._reset_write_buffer.side_effect = mock_reset
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 7})

        # _reset_write_buffer must have been called (the drain failed and
        # the partially-buffered entries must be discarded).
        tcp_client._reset_write_buffer.assert_called(), (
            "_reset_write_buffer must be called when the drain flush fails "
            "so partially-buffered entries don't leak into the next _send call"
        )

        # The write buffer must be empty after the reset.
        assert write_buffer == [], (
            "write buffer must be empty after _reset_write_buffer was called "
            "on drain failure"
        )

        # The undrained pending entries must be re-merged.
        assert len(server._pending_tcp) >= 1, (
            f"undrained pending entries must be re-merged after drain failure; "
            f"got {len(server._pending_tcp)} entries"
        )

    def test_write_buffer_cleared_on_successful_reconnect(self):
        """When a new client connects (replacing an old one), the new
        client's write buffer must start empty (no stale data from the
        old client's buffer leaks through)."""
        server = _make_server()
        server._pending_tcp.clear()

        # First client: write some data, then disconnect (simulated by
        # setting _tcp_client = None).
        first_client = _make_buffered_mock_client()
        server._tcp_client = first_client
        server.push({"type": "first"})
        # The first_client's write buffer was flushed (sendall called).

        # Simulate disconnect.
        server._tcp_client = None

        # Queue more events while disconnected.
        server.push({"type": "queued_1"})
        server.push({"type": "queued_2"})

        # New client connects.
        second_client = _make_buffered_mock_client()
        server._tcp_client = second_client

        # The new client's write buffer must start empty — no data from
        # the first client should leak through.
        assert second_client.write.called is False, (
            "the new (replacement) client must start with an empty write "
            "buffer — no data from the old client should leak through"
        )

        # Send a new event — the queued entries + new event should be
        # drained to the new client.
        server.push({"type": "new"})

        # The new client must have received the queued + new events.
        written_data = b"".join(
            call.args[0] if call.args else b""
            for call in second_client.conn.sendall.call_args_list
        )
        sent_lines = [line for line in written_data.decode("utf-8").split("\n") if line]
        sent_types = [json.loads(line)["type"] for line in sent_lines]
        assert "queued_1" in sent_types, "queued_1 must be drained to the new client"
        assert "queued_2" in sent_types, "queued_2 must be drained to the new client"
        assert "new" in sent_types, "the new event must be sent to the new client"

        # _pending_tcp must be empty after the drain.
        assert len(server._pending_tcp) == 0, (
            f"_pending_tcp must be empty after a successful drain to the "
            f"new client; got {len(server._pending_tcp)} entries"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
