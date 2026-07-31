"""Tests for ``_TCPLineIO.write_raw`` — the direct-to-socket batched write.

``write_raw`` complements the ``write`` + ``flush`` buffer-then-send
pattern with a single-call API for callers that have ALREADY
concatenated a batch of lines into one string. It issues exactly one
``sendall`` syscall (the common case — buffer empty) so a caller
building a 100-entry drain batch as a single string gets one kernel
transition instead of 100 ``write`` appends + 1 ``flush``.

These tests pin the three behavioural contracts:

1. **Empty buffer → single sendall** — the documented fast path.
2. **Non-empty buffer → flush-then-send** — preserves publish order
   when a caller mixes ``write`` and ``write_raw`` in the same cycle.
3. **sendall failure propagates** — the raw text is NOT buffered for
   retry (callers must treat the connection as dead).
"""

from __future__ import annotations

import pytest
from voice_typer.server.ipc_server import _TCPLineIO


def _make_io():
    """Build a ``_TCPLineIO`` with a mock socket (no real reader)."""
    from unittest.mock import MagicMock

    io_obj = _TCPLineIO.__new__(_TCPLineIO)
    io_obj.conn = MagicMock()
    io_obj._write_buffer = []
    io_obj._reader = MagicMock()
    return io_obj


class TestWriteRawEmptyBuffer:
    """``write_raw`` with an empty buffer must issue exactly one
    ``sendall`` with the encoded text."""

    def test_single_sendall_for_batch_string(self):
        io_obj = _make_io()
        io_obj.write_raw("line1\nline2\nline3\n")
        assert io_obj.conn.sendall.call_count == 1
        io_obj.conn.sendall.assert_called_once_with(b"line1\nline2\nline3\n")

    def test_empty_string_is_still_one_sendall(self):
        # An empty string is a degenerate case but the contract is
        # "exactly one sendall" — we do NOT short-circuit because the
        # caller explicitly asked for a raw write (contrast with
        # ``flush`` which no-ops on an empty buffer).
        io_obj = _make_io()
        io_obj.write_raw("")
        assert io_obj.conn.sendall.call_count == 1
        io_obj.conn.sendall.assert_called_once_with(b"")

    def test_unicode_is_utf8_encoded(self):
        io_obj = _make_io()
        io_obj.write_raw("héllo\n世界\n")
        assert io_obj.conn.sendall.call_count == 1
        io_obj.conn.sendall.assert_called_once_with("héllo\n世界\n".encode())

    def test_buffer_stays_empty_after_write_raw(self):
        io_obj = _make_io()
        io_obj.write_raw("line1\n")
        assert io_obj._write_buffer == [], "write_raw must NOT append to _write_buffer — it writes directly."


class TestWriteRawNonEmptyBuffer:
    """When the buffer is non-empty, ``write_raw`` must flush it FIRST
    (preserving publish order) then send the raw text."""

    def test_flushes_buffer_before_raw_send(self):
        io_obj = _make_io()
        io_obj._write_buffer = [b"buffered1\n", b"buffered2\n"]
        io_obj.write_raw("raw\n")
        assert io_obj.conn.sendall.call_count == 2
        calls = io_obj.conn.sendall.call_args_list
        assert calls[0] == ((b"buffered1\nbuffered2\n",),)
        assert calls[1] == ((b"raw\n",),)

    def test_clears_buffer_after_flush(self):
        io_obj = _make_io()
        io_obj._write_buffer = [b"buffered\n"]
        io_obj.write_raw("raw\n")
        assert io_obj._write_buffer == [], (
            "Buffer must be cleared after the flush so a subsequent write_raw doesn't re-send the same buffered data."
        )


class TestWriteRawFailure:
    """``write_raw`` must propagate ``sendall`` failures without
    buffering the raw text for retry."""

    def test_sendall_failure_propagates(self):
        io_obj = _make_io()
        io_obj.conn.sendall.side_effect = OSError("broken pipe")
        with pytest.raises(OSError, match="broken pipe"):
            io_obj.write_raw("raw\n")

    def test_raw_text_not_buffered_on_failure(self):
        io_obj = _make_io()
        io_obj.conn.sendall.side_effect = OSError("broken pipe")
        with pytest.raises(OSError):
            io_obj.write_raw("raw\n")
        assert io_obj._write_buffer == [], (
            "write_raw must NOT buffer the raw text on failure — the "
            "caller is responsible for treating the connection as dead."
        )


class TestWriteRawExists:
    """Sanity: the method exists on the class and is callable."""

    def test_method_exists(self):
        assert hasattr(_TCPLineIO, "write_raw"), "_TCPLineIO must expose a write_raw method for direct batched writes."
        assert callable(_TCPLineIO.write_raw)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
