"""AB-38 regression tests: the WS sidecar outbound send path must apply
the size-cap on the ENCODED byte payload, encoding the frame exactly
ONCE.

The bug (AB-38, original)
-------------------------
``sidecar_ws``'s outbound path previously did, for every frame:

    raw = json.dumps(event, ensure_ascii=False)  # str
    if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:  # encodes str→bytes (O(n))
        ...
    await websocket.send(raw)  # websockets lib encodes str→bytes AGAIN

The ``raw.encode("utf-8")`` call produced a temporary ``bytes`` object
just to compute its length, then discarded it; ``websocket.send(raw)``
then re-encoded the str to bytes internally for the WS TEXT frame. So
every outbound frame was UTF-8 encoded TWICE — 1-5 MiB/sec of garbage
allocation on the asyncio loop thread for near-cap frames.

The current design (supersedes the char-count heuristic)
--------------------------------------------------------
``_safe_send`` (shared by the dispatch-response path and the writer
task) now:

    raw_bytes = await loop.run_in_executor(_get_ws_encode_pool(), _encode_ws_frame, event)
    if len(raw_bytes) > _MAX_FRAME_BYTES:   # EXACT byte count
        ... drop with ERROR log ...
    await websocket.send(raw_bytes)          # sends the same bytes — no re-encode

- The frame is encoded exactly ONCE (``_encode_ws_frame`` runs
  ``json.dumps`` + ``.encode`` together, off the event loop).
- The size check measures the exact UTF-8 byte count — this catches
  multi-byte-heavy frames (CJK / emoji dictation) that a char-count
  check missed (the char-count heuristic was a safe *lower bound*, but
  the byte count is the authoritative limit the Rust host's tungstenite
  ``max_size`` enforces on receive).
- The encoded bytes are reused for ``send`` — no double encode.

These tests verify the source contract and the size-check semantics.
"""

from __future__ import annotations

import inspect

import pytest
from voice_typer.server.sidecar_ws import (
    _MAX_FRAME_BYTES,
    _encode_ws_frame,
    _safe_send,
)


class TestWSFrameSizeCheckSource:
    """AB-38: source-level verification that the size check measures the
    exact byte count of a single-encoded frame."""

    def test_size_check_uses_encoded_byte_count(self):
        """The size check must compare ``len(raw_bytes)`` (the exact
        UTF-8 byte count of the encoded frame) against
        ``_MAX_FRAME_BYTES``."""
        src = inspect.getsource(_safe_send)
        assert "if len(raw_bytes) > _MAX_FRAME_BYTES:" in src, (
            "AB-38: _safe_send size check must use `len(raw_bytes)` "
            "(the exact byte count of the once-encoded frame), not a "
            "char count."
        )
        # The old double-encode pattern must NOT be present as a
        # statement (strip comments, which may quote the old pattern).
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "raw.encode(" not in code_only, (
            "AB-38: _safe_send must NOT encode the frame to measure its "
            "size — that is a wasted O(n) pass. It must encode once via "
            "_encode_ws_frame and measure the resulting bytes."
        )

    def test_single_encode_via_run_in_executor(self):
        """The frame must be encoded exactly once, off the event loop
        (``run_in_executor`` + ``_encode_ws_frame``), and the same bytes
        handed to ``websocket.send``."""
        src = inspect.getsource(_safe_send)
        assert "run_in_executor" in src, (
            "AB-38: the encode must be off-loaded via run_in_executor so "
            "near-cap frames don't block the asyncio loop thread."
        )
        assert "_encode_ws_frame" in src, (
            "AB-38: _safe_send must route through _encode_ws_frame (json.dumps + .encode in one pass)."
        )
        assert "websocket.send(raw_bytes)" in src, (
            "AB-38: _safe_send must send the SAME encoded bytes — not "
            "re-encode a str for send (that would double the encode cost)."
        )

    def test_safety_comment_present(self):
        """The size-check code must include a comment explaining why the
        byte count is authoritative (the Rust host's tungstenite reader
        enforces its own ``max_size`` on receive)."""
        src = inspect.getsource(_safe_send)
        assert "tungstenite" in src.lower(), (
            "AB-38: the size-check fix must reference the Rust host's "
            "tungstenite reader, which enforces its own `max_size` on "
            "receive (the authoritative limit)."
        )
        assert "max_size" in src.lower(), (
            "AB-38: the size-check fix must reference the `max_size` receive enforcement on the Rust side."
        )


class TestWSFrameSizeCheckSemantics:
    """AB-38: behavioral verification that the exact byte count is the
    authoritative size measure (a char-count check would miss
    multi-byte-heavy frames)."""

    def test_multibyte_frame_exceeds_byte_cap_but_not_char_cap(self):
        """A frame with 4-byte emoji chars can exceed ``_MAX_FRAME_BYTES``
        in bytes while its char count stays under — the exact byte-count
        check must drop it."""
        n_chars = _MAX_FRAME_BYTES // 3  # byte count = 4N > cap, char count = N < cap
        event = {"type": "test_multibyte", "data": "😀" * n_chars}
        raw_bytes = _encode_ws_frame(event)
        # Char count (of the JSON text) is under the cap…
        assert len(raw_bytes.decode("utf-8")) <= _MAX_FRAME_BYTES, "Test setup: char count must be under the cap"
        # …but the exact byte count exceeds it (the authoritative limit).
        assert len(raw_bytes) > _MAX_FRAME_BYTES, (
            "AB-38: a multi-byte frame whose char count is under the cap "
            "but whose byte count exceeds it MUST be caught by the exact "
            "byte-count check. The old char-count heuristic would have "
            "passed it to send (relying on the Rust host to close with "
            "1009) — the byte-count check drops it proactively."
        )

    def test_ascii_frame_byte_count_matches_char_count(self):
        """For pure-ASCII JSON, byte count == char count (1 byte/char)."""
        for payload in ["hello", '{"type":"test"}', "a" * 1000, ""]:
            raw_bytes = _encode_ws_frame({"data": payload})
            assert len(raw_bytes) == len(raw_bytes.decode("utf-8")), (
                f"ASCII payload {payload[:20]!r}: byte count must equal char count"
            )


class TestWSFrameSizeCheckBehavioral:
    """AB-38: behavioral verification that ``_safe_send`` drops frames
    whose encoded byte count exceeds the cap, without ever encoding
    twice."""

    async def _run_safe_send(self, event):
        """Call ``_safe_send`` against a fake websocket that records the
        payload handed to ``send``."""
        sent = []

        class _FakeWS:
            def __init__(self) -> None:
                self._closed = []

            async def send(self, payload) -> None:
                sent.append(payload)

            async def close(self, code=1000, reason=""):  # noqa: ARG002
                self._closed.append((code, reason))

        ws = _FakeWS()
        status = await _safe_send(ws, event)
        return status, ws, sent

    @pytest.mark.asyncio
    async def test_oversized_ascii_frame_is_dropped(self):
        """An ASCII frame whose encoded byte count exceeds the cap is
        dropped (never reaches ``send``)."""
        event = {"type": "test_oversized", "data": "x" * (_MAX_FRAME_BYTES + 100)}
        assert len(_encode_ws_frame(event)) > _MAX_FRAME_BYTES
        status, ws, sent = await self._run_safe_send(event)
        assert status == "dropped", f"expected dropped, got {status!r}"
        assert sent == [], "oversized frame must never reach websocket.send"

    @pytest.mark.asyncio
    async def test_multibyte_frame_over_byte_cap_is_dropped(self):
        """A multi-byte frame whose encoded byte count exceeds the cap
        (even though its char count is under) is dropped."""
        n_chars = _MAX_FRAME_BYTES // 3
        event = {"type": "test_oversized_emoji", "data": "😀" * n_chars}
        assert len(_encode_ws_frame(event)) > _MAX_FRAME_BYTES
        status, ws, sent = await self._run_safe_send(event)
        assert status == "dropped", f"expected dropped, got {status!r}"
        assert sent == [], "oversized multi-byte frame must never reach send"

    @pytest.mark.asyncio
    async def test_normal_frame_is_sent_with_encoded_bytes(self):
        """A small frame is sent — and the payload handed to
        ``websocket.send`` must be the ENCODED bytes (bytes, not str —
        proving no re-encode happens)."""
        event = {"type": "bubble_level", "level": 0.42}
        raw_bytes = _encode_ws_frame(event)
        assert len(raw_bytes) <= _MAX_FRAME_BYTES
        status, ws, sent = await self._run_safe_send(event)
        assert status == "sent", f"expected sent, got {status!r}"
        assert len(sent) == 1, "exactly one send expected"
        assert sent[0] == raw_bytes, (
            "AB-38: websocket.send must receive the exact encoded bytes "
            "produced by the single encode pass — no re-encode of a str."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
