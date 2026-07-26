"""CR-79 regression tests: ``_pending_tcp`` snapshot must be re-merged
on write failure / shutdown short-circuit / drain-cap overflow.

The bug (S2-CR-79 / finding #161)
---------------------------------
``IPCServer._send`` (in ``voice_typer/server/ipc_server.py``) snapshots
``_pending_tcp`` (clearing it under the lock) BEFORE attempting to write
to the TCP client. If any of the following happens AFTER the
snapshot+clear, the snapshotted events are silently lost:

1. **First-write failure** (``TimeoutError`` / ``OSError`` — dead
   client). The whole snapshot was cleared; the ``except`` block closed
   the client but did NOT re-merge the snapshot.
2. **Drain-cap overflow**. The drain loop only writes the last 100
   entries of the snapshot; the older entries (up to 900 of 1000) are
   dropped silently.
3. **Drain failure mid-way**. If the drain loop breaks on a write
   failure, the not-yet-written suffix is lost.
4. **Shutdown short-circuit**. The ``_is_shutting_down`` branch closes
   the client and returns without writing the snapshot.

The fix
-------
- Track which snapshot entries were NOT written to the client
  (``_undrained``) and re-merge them into ``_pending_tcp`` after the
  write block.
- In the shutdown short-circuit branch, re-merge the snapshot before
  returning.

These tests verify each path. Each test FAILS if the corresponding
re-merge is removed.
"""

from __future__ import annotations

import inspect
import socket
import threading
from unittest.mock import MagicMock

import pytest
from voice_typer.server.ipc_server import IPCServer, _TCPLineIO

# ─── CR-79 part 1: re-merge on first-write failure ─────────────────────


class TestRemergeOnWriteFailure:
    """CR-79: when the first ``tcp_client.write(line)`` fails with
    ``TimeoutError`` / ``OSError``, the entire pending snapshot must be
    re-merged into ``_pending_tcp`` so the next reconnect's drain can
    pick it up."""

    def test_send_source_remerges_on_write_failure(self):
        """The source of ``_send`` must re-merge ``pending`` (or
        ``_undrained``) back into ``_pending_tcp`` when the first
        write fails."""
        src = inspect.getsource(IPCServer._send)
        # The re-merge must be present in the source. The exact
        # expression uses ``_undrained`` (the local accumulator) so
        # we look for the canonical re-merge form.
        assert "_undrained = list(pending)" in src, (
            "CR-79: _send must set _undrained = list(pending) when the "
            "first write fails so the snapshot is not silently dropped."
        )
        assert "self._pending_tcp = _undrained + self._pending_tcp" in src, (
            "CR-79: _send must re-merge _undrained back into "
            "_pending_tcp (FIFO: undrained first, then concurrent appends)."
        )

    def test_write_failure_remerges_pending(self):
        """When ``tcp_client.write(line)`` raises ``OSError``, the
        snapshotted pending entries must survive in ``_pending_tcp``
        for the next reconnect's drain (not be silently dropped)."""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._tcp_mode = True
        server._cached_shutting_down = False

        # Pre-populate _pending_tcp with three entries — the snapshot
        # will clear them, and the first write will fail (broken
        # socket). The re-merge must put them back.
        pending_snapshot = [
            '{"event": "old1"}',
            '{"event": "old2"}',
            '{"event": "old3"}',
        ]
        server._pending_tcp = list(pending_snapshot)

        # Use a closed socketpair so the first write raises OSError.
        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client
            # Close both ends so write fails immediately.
            srv.close()
            cli.close()

            # Send a push event — first write should fail with OSError.
            server._send({"type": "test_event", "id": 99})

            # CR-79: the three pre-existing pending entries must still
            # be in _pending_tcp (re-merged after the write failure).
            # The new event itself is a dispatch response (has ``id``)
            # and is NOT re-merged — only the pending snapshot is.
            assert len(server._pending_tcp) == 3, (
                f"CR-79: expected 3 re-merged pending entries after write "
                f"failure, got {len(server._pending_tcp)}: "
                f"{server._pending_tcp!r}"
            )
            for original, current in zip(pending_snapshot, server._pending_tcp, strict=False):
                assert current == original, (
                    f"CR-79: re-merged pending entry mismatch — expected {original!r}, got {current!r}"
                )
            # Client must have been marked dead.
            assert server._tcp_client is None, "CR-79: client should be marked dead after write failure"
        finally:
            with __import__("contextlib").suppress(Exception):
                srv.close()
            with __import__("contextlib").suppress(Exception):
                cli.close()


# ─── CR-79 part 2: re-merge on drain-cap overflow ──────────────────────


class TestRemergeOnDrainCapOverflow:
    """CR-79: when the pending snapshot exceeds the drain cap (100),
    the older entries (those before the cap) must be re-merged into
    ``_pending_tcp`` so they survive for the next drain."""

    def test_send_source_tracks_older_entries(self):
        """The source must split ``pending`` into ``older`` and
        ``recent`` and re-merge ``older`` if the drain succeeds for
        ``recent``."""
        src = inspect.getsource(IPCServer._send)
        assert "older = list(pending[:-_drain_cap])" in src, (
            "CR-79: _send must split the snapshot into older (entries "
            "exceeding the drain cap) and recent (the last _drain_cap)."
        )
        assert "_undrained = older" in src, (
            "CR-79: _send must re-merge `older` (entries that exceeded the drain cap) into _pending_tcp via _undrained."
        )

    def test_older_entries_survive_drain_cap(self):
        """When ``len(pending) > _drain_cap`` (100), the first
        ``len(pending) - 100`` entries (the oldest) must be re-merged
        into ``_pending_tcp`` after the drain succeeds for the recent
        100 entries."""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._tcp_mode = True
        server._cached_shutting_down = False

        # Build a snapshot of 105 entries — 5 older + 100 recent.
        older_entries = [f'{{"old": {i}}}' for i in range(5)]
        recent_entries = [f'{{"recent": {i}}}' for i in range(100)]
        server._pending_tcp = older_entries + recent_entries

        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client

            # Reader thread drains the socket so sendall doesn't block.
            received = bytearray()
            reader_stop = threading.Event()

            def reader():
                while not reader_stop.is_set():
                    try:
                        chunk = cli.recv(65536)
                        if not chunk:
                            break
                        received.extend(chunk)
                    except OSError:
                        break

            reader_thread = threading.Thread(target=reader, daemon=True)
            reader_thread.start()

            # Send a push event — the 100 recent entries should drain,
            # the 5 older entries should be re-merged.
            server._send({"type": "test_event", "id": 42})

            # Give the reader a moment to drain.
            reader_stop.set()
            reader_thread.join(timeout=2.0)

            # CR-79: the 5 older entries must be in _pending_tcp.
            assert len(server._pending_tcp) == 5, (
                f"CR-79: expected 5 re-merged older entries after drain-cap "
                f"overflow, got {len(server._pending_tcp)}: "
                f"{server._pending_tcp!r}"
            )
            for i, entry in enumerate(older_entries):
                assert entry in server._pending_tcp, (
                    f"CR-79: older entry {entry!r} (index {i}) must be "
                    f"re-merged into _pending_tcp after drain-cap overflow"
                )
        finally:
            with __import__("contextlib").suppress(Exception):
                srv.close()
            with __import__("contextlib").suppress(Exception):
                cli.close()


# ─── CR-79 part 3: re-merge on drain failure mid-way ───────────────────


class TestRemergeOnDrainFailureMidway:
    """CR-79: when the drain loop breaks on a write failure mid-way
    through ``recent``, the not-yet-written suffix must be re-merged."""

    def test_send_source_tracks_drain_failure_index(self):
        """The source must track the index where the drain failed and
        re-merge the not-yet-written suffix."""
        src = inspect.getsource(IPCServer._send)
        assert "_drain_failed_at" in src, (
            "CR-79: _send must track the drain-failure index so the not-yet-written suffix can be re-merged."
        )
        assert "_undrained = older + recent[_drain_failed_at:]" in src, (
            "CR-79: _send must re-merge older + the unwritten suffix of recent when the drain fails mid-way."
        )

    def test_drain_failure_remerges_unwritten_suffix(self):
        """When the drain loop fails on the 2nd entry of ``recent``,
        the remaining entries + any ``older`` entries must be
        re-merged into ``_pending_tcp``. Uses a mock TCP client whose
        ``write`` raises ``OSError`` on the 3rd call (the new event
        write + the first pending drain succeed; the 2nd pending
        drain fails; the 3rd pending entry is re-merged)."""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._tcp_mode = True
        server._cached_shutting_down = False

        # Build a snapshot of 3 entries. The first write (the new
        # event) succeeds, then the drain writes the first pending
        # entry, then fails on the second. The third entry must be
        # re-merged.
        server._pending_tcp = [
            '{"pending": 1}',
            '{"pending": 2}',
            '{"pending": 3}',
        ]

        # Mock TCP client that fails on the 3rd write call
        # (new event + first pending drain succeed, 2nd drain fails).
        tcp_client = MagicMock()
        write_calls = [0]

        def mock_write(_data):
            write_calls[0] += 1
            if write_calls[0] >= 3:
                raise OSError("simulated client disconnect")

        tcp_client.write.side_effect = mock_write
        # ``conn`` needs ``gettimeout`` / ``settimeout`` for the
        # settimeout dance in _send.
        tcp_client.conn = MagicMock()
        tcp_client.conn.gettimeout.return_value = None
        server._tcp_client = tcp_client

        server._send({"type": "test_event", "id": 7})

        # CR-79: the 3rd pending entry (which the drain never reached)
        # must be re-merged. The 2nd entry (which the drain attempted
        # but failed) is also re-merged because it was never confirmed
        # written. So _pending_tcp should contain at least 1 entry,
        # including the 3rd.
        assert len(server._pending_tcp) >= 1, (
            f"CR-79: expected at least 1 re-merged entry after mid-drain "
            f"failure, got {len(server._pending_tcp)}: "
            f"{server._pending_tcp!r}"
        )
        # The last entry (which the drain never reached) must be
        # in the re-merge.
        assert '{"pending": 3}' in server._pending_tcp, (
            f"CR-79: the last pending entry must be re-merged after mid-drain failure; got {server._pending_tcp!r}"
        )
        # Note: the mid-drain-failure path does NOT mark the client
        # dead (only the first-write-failure path does). The client
        # is marked dead on the next _send call when the first write
        # fails again.


# ─── CR-79 part 4: re-merge on shutdown short-circuit ──────────────────


class TestRemergeOnShutdownShortCircuit:
    """CR-79: when the shutdown short-circuit closes the client without
    writing, the pending snapshot must be re-merged into
    ``_pending_tcp`` (not silently dropped)."""

    def test_send_source_remerges_on_shutdown(self):
        """The shutdown short-circuit branch must re-merge ``pending``
        back into ``_pending_tcp``."""
        src = inspect.getsource(IPCServer._send)
        # The shutdown branch must reference both ``pending`` and
        # ``_pending_tcp`` re-merge. The exact expression uses
        # ``self._pending_tcp = pending + self._pending_tcp`` (FIFO).
        assert "self._pending_tcp = pending + self._pending_tcp" in src, (
            "CR-79: _send shutdown short-circuit branch must re-merge pending back into _pending_tcp (FIFO order)."
        )

    def test_shutdown_short_circuit_remerges_pending(self):
        """When ``_cached_shutting_down is True`` and a non-allowlisted
        push event is sent, the pending snapshot must be re-merged
        into ``_pending_tcp`` (not silently dropped)."""
        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = True
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._tcp_mode = True
        # The shutdown gate uses ``getattr(self, "_cached_shutting_down",
        # False) is True`` — must be exactly ``True``.
        server._cached_shutting_down = True

        # Pre-populate _pending_tcp with entries that should survive.
        pending_snapshot = [
            '{"event": "shutdown_old1"}',
            '{"event": "shutdown_old2"}',
        ]
        server._pending_tcp = list(pending_snapshot)

        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client

            # Send a NON-allowlisted push event (no ``id`` field, type
            # not in _SHUTDOWN_ALLOWLIST) — should hit the shutdown
            # short-circuit branch.
            server._send({"type": "bubble_level", "level": 0.5})

            # CR-79: the two pre-existing entries must be re-merged.
            assert len(server._pending_tcp) == 2, (
                f"CR-79: expected 2 re-merged pending entries after "
                f"shutdown short-circuit, got "
                f"{len(server._pending_tcp)}: {server._pending_tcp!r}"
            )
            for original, current in zip(pending_snapshot, server._pending_tcp, strict=False):
                assert current == original, f"CR-79: re-merged entry mismatch — expected {original!r}, got {current!r}"
            # Client must have been closed.
            assert server._tcp_client is None, "CR-79: client should be closed after shutdown short-circuit"
        finally:
            with __import__("contextlib").suppress(Exception):
                srv.close()
            with __import__("contextlib").suppress(Exception):
                cli.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
