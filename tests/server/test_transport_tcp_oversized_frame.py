"""oversized outbound TCP frame rejection.

The TCP transport's outbound send path (``ipc/sender.py:OutputMixin._send``)
caps each frame at ``_TCP_MAX_OUTBOUND_BYTES`` (1 MiB). A frame that
exceeds the cap is:

1. **Dropped** — never written to ``tcp_client.write`` / ``sendall``.
2. **Logged at ERROR** — ``"[IPC] outbound TCP frame exceeds %d bytes — dropping"``.
3. **Pending snapshot re-merged** — the snapshot taken at the top of
   ``_send`` is re-merged into ``_pending_tcp`` so the dropped frame's
   would-be-drained entries survive for the next reconnect.

These tests pin the cap boundary: frames strictly larger than the cap
are rejected; frames exactly at the cap are accepted.

Platform: runs on Linux (and all other platforms — the send path is
platform-agnostic).
"""

from __future__ import annotations

import json
import logging

# Mock pystray before importing ipc_server (transitively imports tray).
import pytest
from voice_typer.server.ipc.sender import _TCP_MAX_OUTBOUND_BYTES  # noqa: E402
from voice_typer.server.ipc_server import IPCServer  # noqa: E402

from tests.fixtures.ipc_test_helpers import (  # noqa: E402
    make_bare_ipc_server,
    make_buffered_mock_tcp_client,
)


def _make_server() -> IPCServer:
    """Canonical bare send-path IPCServer fixture for ``_send`` tests.

    ``send_path=True`` initializes exactly the instance state
    ``_send`` touches (locks, ``_PendingBuffer`` pending queue, TCP
    mode flags) without running ``__init__`` (no threads / sockets).
    """
    return make_bare_ipc_server(send_path=True)


# ─── Tests ────────────────────────────────────────────────────────────


class TestOversizedFrameRejected:
    """#1: a frame whose encoded byte count exceeds
    ``_TCP_MAX_OUTBOUND_BYTES`` is dropped (never reaches ``sendall``)
    and logged at ERROR level."""

    def test_oversized_frame_dropped_and_logged(self, caplog):
        """An outbound frame > ``_TCP_MAX_OUTBOUND_BYTES`` is:

        - NOT written to the TCP client (``sendall`` not called).
        - Logged at ERROR with the cap value.
        - The pending snapshot is re-merged into ``_pending_tcp``.
        """
        server = _make_server()
        tcp_client = make_buffered_mock_tcp_client()
        server._tcp_client = tcp_client

        # Pre-populate _pending_tcp so we can verify the re-merge.
        server._pending_tcp.append('{"pending": "old1"}')
        server._pending_tcp.append('{"pending": "old2"}')

        # Build a message whose JSON-encoded UTF-8 byte count exceeds the
        # cap. A single long ASCII string is the simplest way (ASCII → 1
        # byte/char, so char count == byte count).
        huge_payload = "x" * (_TCP_MAX_OUTBOUND_BYTES + 1024)
        msg = {"type": "test_oversized", "data": {"blob": huge_payload}}

        # Sanity: the encoded frame must exceed the cap.
        encoded = json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        assert len(encoded) > _TCP_MAX_OUTBOUND_BYTES, (
            f"test setup: the frame must exceed _TCP_MAX_OUTBOUND_BYTES "
            f"({_TCP_MAX_OUTBOUND_BYTES}); got {len(encoded)} bytes"
        )

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.handlers._log"):
            server._send(msg)

        # The oversized frame must NOT have been sent — ``sendall`` must
        # have ZERO calls (the frame was dropped before the write path).
        tcp_client.conn.sendall.assert_not_called()

        # An ERROR log must have been emitted mentioning the size cap.
        error_records = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert error_records, "the oversized frame drop must be logged at ERROR level"
        assert any(
            "exceeds" in r.getMessage() and str(_TCP_MAX_OUTBOUND_BYTES) in r.getMessage() for r in error_records
        ), (
            f"the ERROR log must mention the cap ({_TCP_MAX_OUTBOUND_BYTES}) "
            f"and 'exceeds'; got {[(r.levelname, r.getMessage()) for r in error_records]!r}"
        )

        # The pending snapshot must be re-merged — the 2 pre-existing
        # entries must survive in ``_pending_tcp``.
        assert len(server._pending_tcp) == 2, (
            f"pending snapshot must be re-merged after the oversized drop; "
            f"got {len(server._pending_tcp)} entries: {list(server._pending_tcp)!r}"
        )
        assert '{"pending": "old1"}' in server._pending_tcp
        assert '{"pending": "old2"}' in server._pending_tcp

        # The oversized frame itself must NOT be in _pending_tcp — it
        # would just be dropped again on the next attempt.
        oversized_line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        assert oversized_line not in server._pending_tcp, (
            "the oversized frame must NOT be re-merged into _pending_tcp "
            "(it would be dropped again on the next attempt)"
        )


class TestFrameAtLimitAccepted:
    """#2: a frame whose encoded byte count is EXACTLY
    ``_TCP_MAX_OUTBOUND_BYTES`` is accepted (the cap check uses ``>``
    not ``>=``)."""

    def test_frame_at_limit_is_sent(self):
        """A frame whose encoded byte count == ``_TCP_MAX_OUTBOUND_BYTES``
        is accepted — ``sendall`` is called and the frame reaches the
        wire."""
        server = _make_server()
        tcp_client = make_buffered_mock_tcp_client()
        server._tcp_client = tcp_client

        # Build a message whose JSON-encoded byte count is EXACTLY the
        # cap. The JSON wrapper adds ~30 bytes (``{"type":"x","data":{"v":""}}``),
        # so the payload string must be cap - ~30 chars.
        # The sender also appends a ``\n`` to the line before checking the
        # size (``line_bytes = (line + "\n").encode("utf-8")``), so we must
        # account for that 1 byte.
        wrapper_overhead = len(
            json.dumps(
                {"type": "x", "data": {"v": ""}},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        # payload + wrapper + newline == cap
        payload_size = _TCP_MAX_OUTBOUND_BYTES - wrapper_overhead - 1
        assert payload_size > 0, (
            f"test setup: the JSON wrapper overhead ({wrapper_overhead}) + newline "
            f"must be less than the cap ({_TCP_MAX_OUTBOUND_BYTES})"
        )

        payload = "x" * payload_size
        msg = {"type": "x", "data": {"v": payload}}

        # Verify the encoded size (line + "\n") is EXACTLY the cap.
        line = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        line_bytes = (line + "\n").encode("utf-8")
        assert len(line_bytes) == _TCP_MAX_OUTBOUND_BYTES, (
            f"test setup: the encoded frame (line + newline) must be EXACTLY "
            f"{_TCP_MAX_OUTBOUND_BYTES} bytes; got {len(line_bytes)}"
        )

        server._send(msg)

        # The frame must have been sent — ``sendall`` must have been called
        # at least once (the frame itself; no pending entries to drain).
        tcp_client.conn.sendall.assert_called()

        # The client must still be alive (no error occurred).
        assert server._tcp_client is tcp_client, "client must stay alive when the frame is at the limit (not over)"

        # _pending_tcp must be empty (no re-merge needed — the frame was
        # sent successfully and there were no pending entries).
        assert len(server._pending_tcp) == 0, (
            f"_pending_tcp must be empty after a successful at-limit send; got {len(server._pending_tcp)} entries"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--timeout=30"])
