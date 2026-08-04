"""AB-37 regression tests: the TCP drain loop must batch all pending entries
into a SINGLE ``sendall`` syscall (instead of one per entry).

The bug (AB-37)
---------------
``sender._send`` (in ``voice_typer/server/ipc/sender.py``) previously issued
a separate ``tcp_client.write(p + "\\n")`` + ``tcp_client.flush()`` per
pending entry in the drain loop. ``_TCPLineIO.flush()`` was a no-op, so
every ``write()`` went straight to ``socket.sendall``. After a disconnect
that filled ``_pending_tcp`` to its 1000-entry cap, each subsequent push
issued 1 (current msg) + 100 (drain) = 101 sendall syscalls under
``_tcp_write_lock``, blocking the deferred-executor worker thread that
serializes ALL RT-thread event_bus publishes and freezing the waveform
bubble during reconnect catch-up.

The fix
-------
- ``_TCPLineIO.write()`` now appends to an internal ``list[bytes]`` buffer
  instead of calling ``sendall`` directly.
- ``_TCPLineIO.flush()`` does a SINGLE ``sendall`` for the whole buffer
  (concatenated via ``b"".join``).
- The drain loop in ``_send`` buffers all ``recent`` entries without
  flushing per-entry, then flushes ONCE at the end.

This reduces the syscall count from 101 to 2 per ``_send`` call (1 for the
current line + 1 for the whole drain batch).
"""

from __future__ import annotations

import inspect
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer, _TCPLineIO


class TestTCPLineIOBuffering:
    """AB-37: ``_TCPLineIO`` must buffer writes in memory and flush via a
    single ``sendall`` (not one per write)."""

    def test_write_does_not_call_sendall(self):
        """``_TCPLineIO.write()`` must NOT call ``sendall`` — it must
        append to an internal buffer."""
        srv = MagicMock()
        io_obj = _TCPLineIO.__new__(_TCPLineIO)
        # Initialize only the fields write/flush touch (skip the real
        # ``makefile`` reader — we don't exercise reads here).
        io_obj.conn = srv
        io_obj._write_buffer = []
        io_obj._reader = MagicMock()

        io_obj.write("line1\n")
        io_obj.write("line2\n")
        io_obj.write("line3\n")

        assert srv.sendall.call_count == 0, (
            "AB-37: _TCPLineIO.write() must buffer in memory; it must NOT "
            f"call sendall. Got {srv.sendall.call_count} sendall calls after 3 writes."
        )
        assert io_obj._write_buffer == [b"line1\n", b"line2\n", b"line3\n"], (
            "AB-37: _write_buffer must contain the encoded bytes for each write."
        )

    def test_flush_issues_single_sendall_for_all_buffered_data(self):
        """``_TCPLineIO.flush()`` must do exactly ONE ``sendall`` with all
        buffered data concatenated."""
        srv = MagicMock()
        io_obj = _TCPLineIO.__new__(_TCPLineIO)
        io_obj.conn = srv
        io_obj._write_buffer = []
        io_obj._reader = MagicMock()

        io_obj.write("line1\n")
        io_obj.write("line2\n")
        io_obj.write("line3\n")
        io_obj.flush()

        assert srv.sendall.call_count == 1, (
            f"AB-37: _TCPLineIO.flush() must do exactly ONE sendall call "
            f"for all buffered data. Got {srv.sendall.call_count} calls."
        )
        srv.sendall.assert_called_once_with(b"line1\nline2\nline3\n")
        assert io_obj._write_buffer == [], "AB-37: _write_buffer must be cleared after a successful flush."

    def test_flush_is_noop_when_buffer_empty(self):
        """``flush()`` on an empty buffer must be a no-op (no sendall).
        This preserves the old ``flush()`` semantics for callers that
        call write+flush with nothing buffered."""
        srv = MagicMock()
        io_obj = _TCPLineIO.__new__(_TCPLineIO)
        io_obj.conn = srv
        io_obj._write_buffer = []
        io_obj._reader = MagicMock()

        io_obj.flush()
        assert srv.sendall.call_count == 0, "AB-37: flush() on empty buffer must be a no-op (no sendall)."

    def test_flush_failure_preserves_buffer(self):
        """If ``sendall`` raises during ``flush()``, the buffer must be
        preserved so the caller can decide to retry or drop."""
        srv = MagicMock()
        srv.sendall.side_effect = OSError("broken pipe")
        io_obj = _TCPLineIO.__new__(_TCPLineIO)
        io_obj.conn = srv
        io_obj._write_buffer = []
        io_obj._reader = MagicMock()

        io_obj.write("line1\n")
        io_obj.write("line2\n")
        with pytest.raises(OSError):
            io_obj.flush()
        # Buffer must be preserved on failure (not cleared) so callers
        # can retry or reset via _reset_write_buffer.
        assert io_obj._write_buffer == [b"line1\n", b"line2\n"], (
            "AB-37: _write_buffer must be preserved on flush failure so the "
            "caller can decide to retry or drop via _reset_write_buffer."
        )

    def test_reset_write_buffer_discards_buffered_data(self):
        """``_reset_write_buffer()`` must discard buffered data without
        calling sendall. Used by the drain-failure path in ``_send``."""
        srv = MagicMock()
        io_obj = _TCPLineIO.__new__(_TCPLineIO)
        io_obj.conn = srv
        io_obj._write_buffer = []
        io_obj._reader = MagicMock()

        io_obj.write("line1\n")
        io_obj.write("line2\n")
        io_obj._reset_write_buffer()
        assert srv.sendall.call_count == 0, "AB-37: _reset_write_buffer must NOT call sendall."
        assert io_obj._write_buffer == [], "AB-37: _reset_write_buffer must clear the write buffer."


class TestSendDrainBatching:
    """AB-37: the drain loop in ``_send`` must batch all recent entries
    into a single ``sendall`` (not one per entry)."""

    def test_send_source_does_not_flush_per_entry(self):
        """The drain loop source must NOT call ``tcp_client.flush()``
        per-entry; it must buffer all entries and flush ONCE after the
        loop."""
        src = inspect.getsource(IPCServer._send)
        assert "for _i, p in enumerate(recent):" in src, "AB-37: _send drain loop must iterate over recent entries."
        for_loop_start = src.index("for _i, p in enumerate(recent):")
        # The for-loop body ends at the next "if _drain_failed_at is None:"
        # which is the post-loop batched flush call site in the new code.
        for_loop_end = src.index("if _drain_failed_at is None:", for_loop_start)
        for_loop_body = src[for_loop_start:for_loop_end]
        assert 'tcp_client.write(p + "\\n")' in for_loop_body, (
            'AB-37: drain loop must call tcp_client.write(p + "\\n") to buffer each entry.'
        )
        assert "tcp_client.flush()" not in for_loop_body, (
            "AB-37: drain loop must NOT call tcp_client.flush() per-entry "
            "(that defeats the batching). The flush must happen ONCE after "
            "the loop."
        )
        # After the loop, there must be a single flush() call.
        after_loop = src[for_loop_end:]
        assert "tcp_client.flush()" in after_loop, (
            "AB-37: after the drain loop, _send must call tcp_client.flush() "
            "ONCE to send all buffered entries in a single sendall."
        )

    def _make_server(self, pending_entries):
        """Build a minimal IPCServer fixture for drain-batching tests."""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._tcp_mode = True
        server._cached_shutting_down = False
        server._pending_tcp = list(pending_entries)
        return server

    def _make_buffered_mock_client(self):
        """Build a mock tcp_client whose ``write``/``flush``/``reset``
        simulate the real ``_TCPLineIO`` buffer-then-flush behavior, so
        we can count ``sendall`` calls without a real socketpair.

        ``write`` accepts both ``str`` and ``bytes`` — production
        passes already-encoded bytes (the encode-once refactor
        upstream); legacy / str callers pass a ``str`` that we encode
        here for compatibility.
        """
        tcp_client = MagicMock()
        tcp_client.conn = MagicMock()
        tcp_client.conn.gettimeout.return_value = None
        write_buffer: list[bytes] = []

        def mock_write(text):
            # ``text`` may be ``str`` (legacy) or ``bytes`` (the
            # encode-once refactor — pre-encoding the line so the
            # flush+sendall path doesn't re-encode).
            if isinstance(text, str):
                text = text.encode("utf-8")
            write_buffer.append(text)

        def mock_flush():
            if write_buffer:
                tcp_client.conn.sendall(b"".join(write_buffer))
                write_buffer.clear()

        def mock_reset():
            write_buffer.clear()

        tcp_client.write.side_effect = mock_write
        tcp_client.flush.side_effect = mock_flush
        tcp_client._reset_write_buffer.side_effect = mock_reset
        return tcp_client

    def test_drain_loop_issues_one_sendall_for_100_entries(self):
        """When ``_pending_tcp`` has exactly 100 entries (the drain cap)
        and a client is connected, ``_send`` must issue exactly 2
        ``sendall`` syscalls: 1 for the current line + 1 for the whole
        drain batch. Previously this issued 101."""
        pending = [f'{{"pending": {i}}}' for i in range(100)]
        server = self._make_server(pending)
        tcp_client = self._make_buffered_mock_client()
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 1})

        assert tcp_client.conn.sendall.call_count == 2, (
            f"AB-37: expected 2 sendall calls (1 for current line + 1 for "
            f"the whole 100-entry drain batch), got {tcp_client.conn.sendall.call_count}."
        )
        # All 100 entries drained — none re-merged.
        assert len(server._pending_tcp) == 0, (
            f"AB-37: expected 0 re-merged entries after successful drain, "
            f"got {len(server._pending_tcp)}: {server._pending_tcp!r}"
        )

    def test_drain_loop_issues_one_sendall_for_partial_drain(self):
        """When ``_pending_tcp`` has fewer than the drain cap (e.g. 50),
        ``_send`` must still issue exactly 2 ``sendall`` syscalls (1 for
        the current line + 1 for the batched drain)."""
        pending = [f'{{"pending": {i}}}' for i in range(50)]
        server = self._make_server(pending)
        tcp_client = self._make_buffered_mock_client()
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 1})

        assert tcp_client.conn.sendall.call_count == 2, (
            f"AB-37: expected 2 sendall calls for 50 pending entries, got {tcp_client.conn.sendall.call_count}."
        )
        assert len(server._pending_tcp) == 0

    def test_drain_loop_issues_two_sendall_for_over_cap(self):
        """When ``_pending_tcp`` has MORE than the drain cap (e.g. 105),
        ``_send`` still issues exactly 2 ``sendall`` syscalls — the 5
        overflow entries are re-merged without any sendall."""
        older = [f'{{"old": {i}}}' for i in range(5)]
        recent = [f'{{"recent": {i}}}' for i in range(100)]
        server = self._make_server(older + recent)
        tcp_client = self._make_buffered_mock_client()
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 1})

        assert tcp_client.conn.sendall.call_count == 2, (
            f"AB-37: expected 2 sendall calls (1 for current line + 1 for "
            f"the 100-entry drain batch) even when pending > drain cap; got "
            f"{tcp_client.conn.sendall.call_count}."
        )
        # The 5 older entries (overflow) must be re-merged.
        assert len(server._pending_tcp) == 5, (
            f"AB-37: expected 5 re-merged older entries after drain-cap "
            f"overflow, got {len(server._pending_tcp)}: {server._pending_tcp!r}"
        )
        for entry in older:
            assert entry in server._pending_tcp, (
                f"AB-37: older entry {entry!r} must be re-merged after drain-cap overflow; got {server._pending_tcp!r}"
            )

    def test_no_sendall_when_no_pending_entries(self):
        """When ``_pending_tcp`` is empty, ``_send`` must issue exactly 1
        ``sendall`` (just for the current line)."""
        server = self._make_server([])
        tcp_client = self._make_buffered_mock_client()
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 1})

        assert tcp_client.conn.sendall.call_count == 1, (
            f"AB-37: expected 1 sendall call (just the current line) when "
            f"there are no pending entries; got {tcp_client.conn.sendall.call_count}."
        )

    def test_batched_flush_failure_remerges_all_recent(self):
        """When the batched ``flush()`` raises (real-world failure mode:
        broken pipe / write timeout during the drain batch sendall),
        ALL recent entries must be re-merged (we can't tell which were
        actually transmitted by ``sendall`` because it may have partially
        succeeded before raising)."""
        pending = [
            '{"pending": 1}',
            '{"pending": 2}',
            '{"pending": 3}',
        ]
        server = self._make_server(pending)
        tcp_client = MagicMock()
        tcp_client.conn = MagicMock()
        tcp_client.conn.gettimeout.return_value = None
        write_buffer: list[bytes] = []

        def mock_write(text):
            # ``text`` may be ``str`` (legacy) or ``bytes`` (the
            # encode-once refactor).
            if isinstance(text, str):
                text = text.encode("utf-8")
            write_buffer.append(text)

        # First flush (for the current line) succeeds; second flush
        # (for the drain batch) raises OSError.
        flush_calls = [0]

        def mock_flush():
            flush_calls[0] += 1
            if flush_calls[0] >= 2:
                # Drain-batch flush fails.
                raise OSError("simulated client disconnect during drain flush")
            if write_buffer:
                tcp_client.conn.sendall(b"".join(write_buffer))
                write_buffer.clear()

        def mock_reset():
            write_buffer.clear()

        tcp_client.write.side_effect = mock_write
        tcp_client.flush.side_effect = mock_flush
        tcp_client._reset_write_buffer.side_effect = mock_reset
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 7})

        # + : all 3 recent entries must be re-merged because
        # the batched flush failed.
        assert len(server._pending_tcp) == 3, (
            f"AB-37: expected 3 re-merged entries after batched flush "
            f"failure, got {len(server._pending_tcp)}: {server._pending_tcp!r}"
        )
        for entry in pending:
            assert entry in server._pending_tcp, (
                f"AB-37: entry {entry!r} must be re-merged after batched flush failure; got {server._pending_tcp!r}"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
