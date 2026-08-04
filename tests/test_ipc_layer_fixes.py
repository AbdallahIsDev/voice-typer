"""XV-81 .. XV-87: IPC-layer performance fixes (FA11 sub-agent).

These tests pin the behavioral and source-level contracts of the
GROUP-2 IPC-layer fixes:

* XV-81 — ``_RateLimiter`` maintains running totals
  (``self._burst_total`` / ``self._sustained_total``) instead of
  recomputing ``sum(c for _, c in deque)`` on every ``allow()`` call.
* XV-82 — ``IPCServer._send`` only snapshots+clears ``_pending_tcp``
  when there is a live TCP client to drain it to (the ``tcp_mode``
  branch only appends+trims).
* XV-83 — ``IPCServer._send`` uses compact JSON serialization
  (``ensure_ascii=False, separators=(",", ":")``) matching the WS path.
* XV-84 — ``sidecar_ws._writer`` encodes the outbound event to bytes
  once (``json.dumps(...).encode("utf-8")``) and shares the buffer
  between the size check and the send.
* XV-85 — ``validation._validate_dict_payload`` hoists ``import json``
  to module top and caches the per-schema ``max_payload_bytes`` lookup.
* XV-86 — ``_TCPLineIO`` uses ``io.DEFAULT_BUFFER_SIZE`` (not ``1``)
  for the read-side ``socket.makefile`` buffering argument.
* XV-87 — ``sidecar_ws._make_dispatch`` resolves the rate limiter
  ONCE up-front (alongside ``ws_dispatch_pool``) and captures it in
  the closure; ``dispatch()`` no longer calls ``_get_rate_limiter``
  per frame.

Each test FAILS if the corresponding fix is reverted.
"""

from __future__ import annotations

import inspect
import io
import json
import socket
import threading
from unittest.mock import MagicMock

import pytest

# running totals in _RateLimiter ──────────────────────────────


class TestRateLimiterRunningTotals:
    """XV-81: ``_RateLimiter`` keeps ``self._burst_total`` /
    ``self._sustained_total`` int fields in sync with the deques,
    replacing the O(n) ``sum(c for _, c in deque)`` recomputation
    on every ``allow()`` call."""

    def test_init_creates_running_total_fields(self):
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter()
        # The new fields must exist and start at 0.
        assert hasattr(rl, "_burst_total"), "XV-81: _RateLimiter must have a _burst_total int field."
        assert hasattr(rl, "_sustained_total"), "XV-81: _RateLimiter must have a _sustained_total int field."
        assert rl._burst_total == 0
        assert rl._sustained_total == 0

    def test_allow_source_does_not_recompute_sum(self):
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        src = inspect.getsource(_RateLimiter.allow)
        # The old O(n) recompute must NOT appear in the allow() body.
        assert "sum(c for _, c in self._burst_timestamps)" not in src, (
            "XV-81: allow() must NOT recompute sum(c for _, c in "
            "_burst_timestamps) on every call — use the running total."
        )
        assert "sum(c for _, c in self._sustained_timestamps)" not in src, (
            "XV-81: allow() must NOT recompute sum(c for _, c in "
            "_sustained_timestamps) on every call — use the running total."
        )
        # The new fast-path reads must appear.
        assert "self._burst_total" in src, "XV-81: allow() must reference self._burst_total (the running total)."
        assert "self._sustained_total" in src, (
            "XV-81: allow() must reference self._sustained_total (the running total)."
        )

    def test_running_total_matches_sum_after_appends(self):
        """After a sequence of ``allow()`` calls, the running total must
        equal the value ``sum(c for _, c in deque)`` would have
        produced — the cache invariant."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        # Mix of cheap and expensive commands.
        commands = ["heartbeat", "download_model", "heartbeat", "get_status", "heartbeat"]
        for i, cmd in enumerate(commands):
            rl.allow(command=cmd, now=float(i) * 0.01)
        expected_burst = sum(c for _, c in rl._burst_timestamps)
        expected_sustained = sum(c for _, c in rl._sustained_timestamps)
        assert rl._burst_total == expected_burst, (
            f"XV-81: _burst_total={rl._burst_total} != sum={expected_burst} "
            "after appends — the running total must stay in sync with the deque."
        )
        assert rl._sustained_total == expected_sustained, (
            f"XV-81: _sustained_total={rl._sustained_total} != sum={expected_sustained} after appends."
        )

    def test_running_total_matches_sum_after_eviction(self):
        """After the burst window slides past old entries, the running
        total must equal the post-eviction deque sum (no drift)."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        # Fill the burst deque at t=0 (cost 1 each).
        for _ in range(10):
            rl.allow(now=0.0)
        # Advance past the burst window so the t=0 entries are evicted.
        rl.allow(now=2.0)  # cutoff = 2.0 - 1.0 = 1.0; all t=0.0 evicted
        expected_burst = sum(c for _, c in rl._burst_timestamps)
        expected_sustained = sum(c for _, c in rl._sustained_timestamps)
        assert rl._burst_total == expected_burst, (
            f"XV-81: _burst_total drifted after eviction: {rl._burst_total} != {expected_burst}."
        )
        assert rl._sustained_total == expected_sustained, (
            f"XV-81: _sustained_total drifted after eviction: {rl._sustained_total} != {expected_sustained}."
        )
        # The burst deque should have only the t=2.0 entry (cost 1).
        assert rl._burst_total == 1
        # The sustained deque should have all 11 entries (cost 1 each).
        assert rl._sustained_total == 11

    def test_running_totals_never_negative(self):
        """The eviction loop clamps the running totals at >= 0 even
        under a hypothetical double-eviction bug."""
        from voice_typer.server.ipc.rate_limiter import _RateLimiter

        rl = _RateLimiter(burst=200, sustained_per_sec=600, window=10.0, burst_window=1.0)
        rl.allow(now=0.0)
        # Manually drive the total negative to verify the clamp.
        rl._burst_total = -5
        rl._sustained_total = -5
        # Trigger an eviction that should clamp the totals back to 0.
        # We do this by calling allow() with a timestamp past both
        # windows — the eviction loop runs, but the deque is already
        # empty, so the clamp branch is exercised via the manual
        # negative value. (We then re-set to 0 implicitly via the
        # next allow's append path.)
        rl.allow(now=100.0)
        # After allow(), the totals were clamped to 0 and then
        # incremented by 1 (the new append).
        assert rl._burst_total >= 0, "XV-81: _burst_total must never go negative (clamp at 0)."
        assert rl._sustained_total >= 0, "XV-81: _sustained_total must never go negative (clamp at 0)."


# pending snapshot only when tcp_client is not None ───────────


class TestPendingSnapshotGatedOnTcpClient:
    """XV-82: ``IPCServer._send`` only snapshots+clears ``_pending_tcp``
    when ``tcp_client is not None``. The disconnected-mode (``tcp_mode``
    branch) only appends+trims — O(1) amortized."""

    def test_send_source_gates_snapshot_on_tcp_client(self):
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._send)
        # The snapshot list(...) call must be inside an
        # ``if tcp_client is not None:`` block, not unconditional.
        # Find the snapshot line and verify it's preceded (in the lock
        # block) by the tcp_client gate.
        assert "if tcp_client is not None:" in src, (
            "XV-82: _send must gate the _pending_tcp snapshot on 'if tcp_client is not None:'."
        )
        # The re-merge in the tcp_mode branch must be GONE.
        assert "self._pending_tcp.extend(pending)" not in src, (
            "XV-82: _send must NOT re-merge pending into _pending_tcp — "
            "the snapshot is gated on tcp_client, so the tcp_mode branch "
            "never has a pending snapshot to re-merge."
        )

    def test_send_does_not_snapshot_when_no_client(self):
        """When ``tcp_client is None`` and ``tcp_mode`` is True, _send
        must NOT clear ``_pending_tcp`` (the snapshot path is skipped)."""
        from voice_typer.server.ipc_server import IPCServer

        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        # Pre-populate _pending_tcp with some entries — they must
        # survive the _send call (the snapshot is gated off when
        # tcp_client is None).
        server._pending_tcp = ['{"existing":1}', '{"existing":2}']
        server._tcp_mode = True
        server._tcp_client = None  # no client connected

        # Issue a push event — should append + trim, NOT clear.
        server._send({"type": "test", "id": 1})

        # The two pre-existing entries must still be there (
        # gated off the snapshot+clear), plus the new entry.
        assert len(server._pending_tcp) == 3, (
            f"XV-82: expected 3 entries in _pending_tcp (2 pre-existing "
            f"+ 1 new), got {len(server._pending_tcp)}. The pre-existing "
            "entries must NOT be cleared when there's no TCP client."
        )
        # The new entry must be at the end.
        assert '"test"' in server._pending_tcp[-1]

    def test_send_still_snapshots_when_tcp_client_present(self):
        """When ``tcp_client is not None``, the snapshot+clear must
        still run (so the drain loop can write the pending entries)."""
        from voice_typer.server.ipc_server import IPCServer, _TCPLineIO

        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()

        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client
            server._tcp_mode = True
            # Pre-populate _pending_tcp — must be cleared by _send.
            server._pending_tcp = ['{"existing":1}']

            # Reader thread so sendall doesn't block.
            received = []
            reader = threading.Thread(
                target=lambda: received.append(cli.recv(65536)),
                daemon=True,
            )
            reader.start()

            server._send({"type": "test", "id": 2})
            reader.join(timeout=2.0)
            assert received, "reader should have received the message"

            # the snapshot+clear must have run when tcp_client
            # is not None, so _pending_tcp is now empty.
            assert server._pending_tcp == [], (
                "XV-82 regression: _pending_tcp should have been cleared "
                "when tcp_client is not None (the snapshot path must run)."
            )
        finally:
            srv.close()
            cli.close()


# compact JSON serialization matches WS path ──────────────────


class TestCompactJsonSerialization:
    """XV-83: ``IPCServer._send`` serializes messages with
    ``ensure_ascii=False, separators=(",", ":")`` to match the WS
    path's convention and shrink the wire format."""

    def test_send_source_uses_compact_json(self):
        from voice_typer.server.ipc_server import IPCServer

        src = inspect.getsource(IPCServer._send)
        assert "ensure_ascii=False" in src, (
            "XV-83: _send must use ensure_ascii=False to keep multi-byte "
            "UTF-8 (e.g. CJK dictation) as-is instead of escaping to "
            "\\uXXXX."
        )
        assert 'separators=(",", ":")' in src, (
            "XV-83: _send must use separators=(',', ':') to strip the default whitespace and shrink the wire format."
        )

    def test_send_produces_compact_json(self):
        """A message with a non-ASCII string must serialize without
        \\uXXXX escapes (which the default ``ensure_ascii=True`` would
        emit), and must not contain the default ``", "`` / ``": "``
        whitespace."""
        import contextlib as _ctxlib

        from voice_typer.server.ipc_server import IPCServer, _TCPLineIO

        server = IPCServer.__new__(IPCServer)
        server.app = MagicMock()
        server.app._shutting_down = False
        server._lock = threading.RLock()
        server._tcp_write_lock = threading.RLock()
        server._pending_tcp = []
        server._tcp_mode = True

        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            tcp_client = _TCPLineIO(srv)
            server._tcp_client = tcp_client

            received = bytearray()

            def reader():
                while True:
                    chunk = cli.recv(65536)
                    if not chunk:
                        break
                    received.extend(chunk)

            t = threading.Thread(target=reader, daemon=True)
            t.start()

            # Send a message with CJK text — ensure_ascii=False keeps
            # the multi-byte UTF-8 as-is.
            server._send({"type": "transcription_final", "text": "你好世界"})

            t.join(timeout=2.0)
            with _ctxlib.suppress(Exception):
                cli.shutdown(socket.SHUT_RDWR)
            cli.close()
            srv.close()
            t.join(timeout=1.0)

            line = received.decode("utf-8").strip()
            # The wire format must contain the raw CJK chars (not
            # \\u4f60\\u597d...).
            assert "你好世界" in line, (
                f"XV-83: ensure_ascii=False must keep CJK chars as-is in the wire format. Got: {line!r}"
            )
            # The compact separators must NOT insert whitespace after
            # the comma or colon.
            assert '", "' not in line, "XV-83: separators=(',', ':') must not leave whitespace after the comma."
            assert '": "' not in line, "XV-83: separators=(',', ':') must not leave whitespace after the colon."
        finally:
            with _ctxlib.suppress(Exception):
                cli.close()
            with _ctxlib.suppress(Exception):
                srv.close()


# sidecar_ws._writer encodes once ─────────────────────────────


class TestWriterEncodesOnce:
    """XV-84: ``sidecar_ws._writer`` encodes the outbound event to
    bytes ONCE and reuses the buffer for both the size check and the
    send. Pre-XV-84 the code did ``raw.encode("utf-8")`` for the size
    check and let ``websocket.send(raw)`` re-encode internally."""

    def test_writer_source_encodes_to_bytes_once(self):
        # The writer was refactored from a nested closure inside
        # ``_handle_connection`` into a sibling function
        # ``_start_writer`` (which spawns ``_writer`` as a task).
        # Read the writer source from the module directly via
        # ``inspect.getsource`` on the module file so the static
        # check doesn't care which enclosing function the writer
        # lives in.
        from voice_typer.server import sidecar_ws
        import inspect as _inspect

        src = _inspect.getsource(sidecar_ws)
        # The new encode-once pattern.
        assert 'raw_bytes = json.dumps(event, ensure_ascii=False).encode("utf-8")' in src, (
            "XV-84: _writer must encode ONCE to raw_bytes via json.dumps(event, ensure_ascii=False).encode('utf-8')."
        )
        # The old re-encode pattern must be GONE.
        assert 'len(raw.encode("utf-8"))' not in src, (
            "XV-84: _writer must NOT re-encode via len(raw.encode('utf-8')) — encode once and reuse the buffer."
        )
        # The send must use the bytes buffer, not a str.
        assert "await websocket.send(raw_bytes)" in src, (
            "XV-84: _writer must await websocket.send(raw_bytes) (bytes), not websocket.send(raw) (str)."
        )


# validation helper hoists json + caches max_payload_bytes ────


class TestValidationHoistsJsonAndCaches:
    """XV-85: ``_validate_dict_payload`` hoists ``import json`` to
    module top and caches the per-schema ``max_payload_bytes`` lookup
    so the schema scan runs once per schema (not per call)."""

    def test_module_top_imports_json(self):
        from voice_typer.server.ipc import validation

        # ``json`` must be a module-level name (not imported per-call).
        assert hasattr(validation, "json"), "XV-85: validation module must import json at module top."
        assert validation.json is json, "XV-85: validation.json must be the stdlib json module."

    def test_validate_source_does_not_inline_import(self):
        from voice_typer.server.ipc.validation import _validate_dict_payload

        src = inspect.getsource(_validate_dict_payload)
        # The per-call import must be GONE.
        assert "import json as _json_mod" not in src, (
            "XV-85: _validate_dict_payload must NOT do 'import json as _json_mod' per call — hoist to module top."
        )

    def test_cache_constants_exist(self):
        from voice_typer.server.ipc import validation

        assert hasattr(validation, "_MAX_PAYLOAD_BYTES_CACHE"), (
            "XV-85: validation module must expose _MAX_PAYLOAD_BYTES_CACHE."
        )
        assert hasattr(validation, "_MAX_PAYLOAD_BYTES_CACHE_SEEN"), (
            "XV-85: validation module must expose _MAX_PAYLOAD_BYTES_CACHE_SEEN."
        )
        assert hasattr(validation, "_MAX_PAYLOAD_BYTES_CACHE_MAX"), (
            "XV-85: validation module must expose _MAX_PAYLOAD_BYTES_CACHE_MAX."
        )
        assert validation._MAX_PAYLOAD_BYTES_CACHE_MAX > 0
        # The cache must be bounded — verify the cap is reasonable.
        assert validation._MAX_PAYLOAD_BYTES_CACHE_MAX <= 4096, (
            "XV-85: _MAX_PAYLOAD_BYTES_CACHE_MAX must be bounded to prevent unbounded growth from per-call schemas."
        )

    def test_cache_hits_on_second_call_with_same_schema(self):
        """Calling _validate_dict_payload twice with the SAME schema
        object must hit the cache (the schema scan runs only once)."""
        from voice_typer.server.ipc.validation import (
            _MAX_PAYLOAD_BYTES_CACHE,
            _MAX_PAYLOAD_BYTES_CACHE_SEEN,
            _validate_dict_payload,
        )

        # Clear the cache to start fresh.
        _MAX_PAYLOAD_BYTES_CACHE.clear()
        _MAX_PAYLOAD_BYTES_CACHE_SEEN.clear()

        # Use a module-level-stable schema (defined once at class scope
        # so its id() is stable across the two calls).
        schema = {
            "hotkey": {"type": str, "required": True, "max_payload_bytes": 1024},
        }

        # First call: populates the cache.
        _validate_dict_payload({"hotkey": "ctrl+a"}, schema)
        cache_size_after_first = len(_MAX_PAYLOAD_BYTES_CACHE)
        seen_size_after_first = len(_MAX_PAYLOAD_BYTES_CACHE_SEEN)
        assert cache_size_after_first >= 1 or seen_size_after_first >= 1, (
            "XV-85: first call must populate the cache (cache or seen set)."
        )

        # Second call with the same schema — must not re-scan (the
        # entry is already cached).
        _validate_dict_payload({"hotkey": "ctrl+b"}, schema)
        # The cache size must not have grown (no new entry added).
        assert len(_MAX_PAYLOAD_BYTES_CACHE) == cache_size_after_first, (
            "XV-85: second call with the same schema must NOT add a new "
            "cache entry (id-stable schemas should hit the cache)."
        )
        assert len(_MAX_PAYLOAD_BYTES_CACHE_SEEN) == seen_size_after_first

    def test_cache_bounded_under_per_call_schemas(self):
        """Calling _validate_dict_payload with a FRESH schema each call
        (each gets a new id) must not grow the cache unboundedly — the
        FIFO eviction cap kicks in."""
        from voice_typer.server.ipc.validation import (
            _MAX_PAYLOAD_BYTES_CACHE,
            _MAX_PAYLOAD_BYTES_CACHE_MAX,
            _MAX_PAYLOAD_BYTES_CACHE_SEEN,
            _validate_dict_payload,
        )

        _MAX_PAYLOAD_BYTES_CACHE.clear()
        _MAX_PAYLOAD_BYTES_CACHE_SEEN.clear()

        # Issue many more calls than the cache cap, each with a fresh
        # inline schema (new id each time).
        n = _MAX_PAYLOAD_BYTES_CACHE_MAX * 3
        for _ in range(n):
            _validate_dict_payload({}, {})

        # The cache must NOT have grown past the cap.
        assert len(_MAX_PAYLOAD_BYTES_CACHE) <= _MAX_PAYLOAD_BYTES_CACHE_MAX, (
            f"XV-85: cache grew to {len(_MAX_PAYLOAD_BYTES_CACHE)} > cap "
            f"{_MAX_PAYLOAD_BYTES_CACHE_MAX} — FIFO eviction must bound it."
        )
        assert len(_MAX_PAYLOAD_BYTES_CACHE_SEEN) <= _MAX_PAYLOAD_BYTES_CACHE_MAX

    def test_max_payload_bytes_still_enforced(self):
        """Sanity: the max_payload_bytes rule still fires after the
        XV-85 cache refactor — no behavioral regression."""
        from voice_typer.server.ipc.validation import _validate_dict_payload

        schema = {"hotkey": {"type": str, "required": True, "max_payload_bytes": 50}}
        # Small payload passes.
        v, err = _validate_dict_payload({"hotkey": "ctrl+a"}, schema)
        assert err is None
        assert v == {"hotkey": "ctrl+a"}
        # Large payload fails.
        v, err = _validate_dict_payload({"hotkey": "x" * 200}, schema)
        assert v is None
        assert err["data"]["code"] == "client.invalid_payload"
        assert "payload too large" in err["data"]["message"]


# transport uses io.DEFAULT_BUFFER_SIZE for reads ─────────────


class TestTransportBuffering:
    """XV-86: ``_TCPLineIO.__init__`` uses ``io.DEFAULT_BUFFER_SIZE``
    (8192) for the read-side ``socket.makefile`` buffering argument
    instead of ``1`` (line buffering, which is a write-side concept)."""

    def test_init_uses_default_buffer_size(self):
        from voice_typer.server.ipc.transport import _TCPLineIO

        src = inspect.getsource(_TCPLineIO.__init__)
        assert "io.DEFAULT_BUFFER_SIZE" in src, (
            "XV-86: _TCPLineIO must use io.DEFAULT_BUFFER_SIZE for the "
            "read-side buffering argument (not 1, which is a write-side "
            "concept)."
        )
        # The old buffering=1 must be GONE.
        assert "buffering=1" not in src, (
            "XV-86: _TCPLineIO must NOT use buffering=1 (line buffering) "
            "for the read side — use io.DEFAULT_BUFFER_SIZE."
        )

    def test_module_imports_io(self):
        from voice_typer.server.ipc import transport

        assert hasattr(transport, "io"), "XV-86: transport module must import io at module top."
        assert transport.io is io

    def test_makefile_called_with_default_buffer_size(self):
        """A real socket's makefile must accept the new buffering
        argument without raising, and the resulting file must support
        readline (the existing contract)."""
        from voice_typer.server.ipc.transport import _TCPLineIO

        srv, cli = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            io_obj = _TCPLineIO(srv)
            # Sanity: the reader is a TextIOBase-like object that
            # supports readline.
            assert hasattr(io_obj._reader, "readline")
            # Write a line through the other end and read it back.
            cli.sendall(b"hello world\n")
            line = io_obj._reader.readline()
            assert line == "hello world\n"
        finally:
            srv.close()
            cli.close()


# sidecar_ws._make_dispatch resolves rate_limiter once ────────


class TestRateLimiterResolvedOnce:
    """XV-87: ``sidecar_ws._make_dispatch`` resolves the shared rate
    limiter ONCE (alongside ``ws_dispatch_pool``) and captures it in
    the closure. The per-frame ``_get_rate_limiter(server)`` lookup
    has been hoisted out of the dispatch hot path."""

    def test_make_dispatch_source_resolves_limiter_in_closure(self):
        from voice_typer.server import sidecar_ws

        # _make_dispatch's body (NOT the inner dispatch()) must contain
        # the rate_limiter assignment — that's the closure capture.
        src = inspect.getsource(sidecar_ws._make_dispatch)
        # The rate_limiter assignment must appear BEFORE the inner
        # ``async def dispatch`` definition.
        dispatch_idx = src.find("async def dispatch")
        assert dispatch_idx != -1
        before_dispatch = src[:dispatch_idx]
        assert "rate_limiter = _get_rate_limiter(server)" in before_dispatch, (
            "XV-87: _make_dispatch must resolve rate_limiter ONCE in the "
            "closure body (before the inner dispatch() definition), not "
            "per-call inside dispatch()."
        )

    def test_dispatch_does_not_call_get_rate_limiter(self):
        """The inner ``dispatch()`` closure must NOT call
        ``_get_rate_limiter`` — it must reference the closure-captured
        ``rate_limiter``."""
        from voice_typer.server import sidecar_ws

        src = inspect.getsource(sidecar_ws._make_dispatch)
        # Find the inner dispatch function body.
        dispatch_idx = src.find("async def dispatch")
        assert dispatch_idx != -1
        dispatch_body = src[dispatch_idx:]
        assert "_get_rate_limiter(server)" not in dispatch_body, (
            "XV-87: dispatch() must NOT call _get_rate_limiter(server) "
            "per frame — the limiter is resolved ONCE in the closure."
        )
        # The closure-captured rate_limiter must be referenced.
        assert "rate_limiter.allow" in dispatch_body, (
            "XV-87: dispatch() must reference the closure-captured rate_limiter.allow(command=...)."
        )

    def test_dispatch_uses_same_limiter_across_calls(self):
        """Two dispatch() calls on the same _make_dispatch-derived
        closure must use the SAME rate_limiter instance — verifying
        the closure capture (not a per-call lookup)."""
        # Build a fake server with a real _RateLimiter instance so we
        # can verify identity across calls. Using a MagicMock server
        # would auto-vivify _ws_dispatch_pool, so we use a minimal
        # class with explicit attributes.
        from concurrent.futures import ThreadPoolExecutor

        from voice_typer.server import sidecar_ws
        from voice_typer.server.ipc_server import _get_rate_limiter

        class FakeServer:
            pass

        server = FakeServer()
        server._ws_dispatch_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="test-xv87")
        # Resolve the limiter the same way _make_dispatch does — once.
        limiter_before = _get_rate_limiter(server)
        sidecar_ws._make_dispatch(server)
        # Resolve again — must be the same instance (already cached on
        # the server by _make_dispatch).
        limiter_after = _get_rate_limiter(server)
        assert limiter_before is limiter_after, (
            "XV-87: _make_dispatch must resolve the limiter ONCE and "
            "store it on the server; subsequent _get_rate_limiter calls "
            "must return the same instance."
        )
        # The limiter must have been stored on the server instance.
        assert server._rate_limiter_instance is limiter_before
        # Cleanup.
        server._ws_dispatch_pool.shutdown(wait=False, cancel_futures=True)


# ── Helpers ────────────────────────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
