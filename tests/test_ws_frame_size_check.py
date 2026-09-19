"""AB-38 regression tests: the WS sidecar outbound send path must apply"""

from __future__ import annotations

import inspect

import pytest
from voice_typer.server.sidecar_ws import (
    _MAX_FRAME_BYTES,
    _encode_ws_frame,
    _safe_send,
)


class TestWSFrameSizeCheckSource:
    """AB-38: source-level verification that the size check measures the"""

    def test_size_check_uses_encoded_byte_count(self):
        """The size check must compare ``len(raw_bytes)`` (the exact"""
        src = inspect.getsource(_safe_send)
        assert "if len(raw_bytes) > _MAX_FRAME_BYTES:" in src, (
            "AB-38: _safe_send size check must use `len(raw_bytes)` "
            "(the exact byte count of the once-encoded frame), not a "
            "char count."
        )
        # The old double-encode pattern must NOT be present as a
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert "raw.encode(" not in code_only, (
            "AB-38: _safe_send must NOT encode the frame to measure its "
            "size: that is a wasted O(n) pass. It must encode once via "
            "_encode_ws_frame and measure the resulting bytes."
        )

    def test_single_encode_via_run_in_executor(self):
        """The frame must be encoded exactly once, off the event loop"""
        src = inspect.getsource(_safe_send)
        assert "run_in_executor" in src, (
            "AB-38: the encode must be off-loaded via run_in_executor so "
            "near-cap frames don't block the asyncio loop thread."
        )
        assert "_encode_ws_frame" in src, (
            "AB-38: _safe_send must route through _encode_ws_frame (json.dumps + .encode in one pass)."
        )
        assert 'websocket.send(raw_bytes.decode("utf-8"))' in src, (
            "AB-38 + C-WS-2: _safe_send must send the SAME single-encoded "
            "frame, decoded to str for a TEXT frame, not re-encoded and "
            "not sent as raw bytes (BINARY frames are dropped by the host)."
        )

    def test_safety_comment_present(self):
        """byte count is authoritative (the Rust host's tungstenite reader"""
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
    """authoritative size measure (a char-count check would miss"""

    def test_multibyte_frame_exceeds_byte_cap_but_not_char_cap(self):
        """A frame with 4-byte emoji chars can exceed ``_MAX_FRAME_BYTES``"""
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
            "1009), the byte-count check drops it proactively."
        )

    def test_ascii_frame_byte_count_matches_char_count(self):
        """For pure-ASCII JSON, byte count == char count (1 byte/char)."""
        for payload in ["hello", '{"type":"test"}', "a" * 1000, ""]:
            raw_bytes = _encode_ws_frame({"data": payload})
            assert len(raw_bytes) == len(raw_bytes.decode("utf-8")), (
                f"ASCII payload {payload[:20]!r}: byte count must equal char count"
            )


class TestWSFrameSizeCheckBehavioral:
    """AB-38: behavioral verification that ``_safe_send`` drops frames"""

    async def _run_safe_send(self, event):
        """Call ``_safe_send`` against a fake websocket that records the"""
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
        """An ASCII frame whose encoded byte count exceeds the cap is"""
        event = {"type": "test_oversized", "data": "x" * (_MAX_FRAME_BYTES + 100)}
        assert len(_encode_ws_frame(event)) > _MAX_FRAME_BYTES
        status, ws, sent = await self._run_safe_send(event)
        assert status == "dropped", f"expected dropped, got {status!r}"
        assert sent == [], "oversized frame must never reach websocket.send"

    @pytest.mark.asyncio
    async def test_multibyte_frame_over_byte_cap_is_dropped(self):
        """A multi-byte frame whose encoded byte count exceeds the cap"""
        n_chars = _MAX_FRAME_BYTES // 3
        event = {"type": "test_oversized_emoji", "data": "😀" * n_chars}
        assert len(_encode_ws_frame(event)) > _MAX_FRAME_BYTES
        status, ws, sent = await self._run_safe_send(event)
        assert status == "dropped", f"expected dropped, got {status!r}"
        assert sent == [], "oversized multi-byte frame must never reach send"

    @pytest.mark.asyncio
    async def test_normal_frame_is_sent_as_text_str(self):
        """``websocket.send`` must be the once-encoded frame decoded back"""
        event = {"type": "bubble_level", "level": 0.42}
        raw_bytes = _encode_ws_frame(event)
        assert len(raw_bytes) <= _MAX_FRAME_BYTES
        status, ws, sent = await self._run_safe_send(event)
        assert status == "sent", f"expected sent, got {status!r}"
        assert len(sent) == 1, "exactly one send expected"
        assert isinstance(sent[0], str), (
            f"C-WS-2: websocket.send must receive a str (TEXT frame), got {type(sent[0]).__name__}"
        )
        assert sent[0] == raw_bytes.decode("utf-8"), (
            "C-WS-2: the TEXT payload must be exactly the single-encoded "
            "frame decoded back to str, no re-serialization, no bytes."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
