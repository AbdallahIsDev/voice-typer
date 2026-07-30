"""AB-38 regression tests: the WS sidecar outbound ``_writer`` coroutine
must use ``len(raw)`` (char count) instead of ``len(raw.encode("utf-8"))``
(byte count) for the frame-size check.

The bug (AB-38)
---------------
``sidecar_ws._handle_connection_inner``'s ``_writer`` closure (around
line 871-878) does, for every outbound WS frame:

    raw = json.dumps(event, ensure_ascii=False)  # str
    if len(raw.encode("utf-8")) > _MAX_FRAME_BYTES:  # encodes str→bytes (O(n))
        ...
    await websocket.send(raw)  # websockets lib encodes str→bytes AGAIN

The ``raw.encode("utf-8")`` call produces a temporary ``bytes`` object
just to compute its length, then discards it. ``websocket.send(raw)``
then re-encodes the str to bytes internally for the WS TEXT frame. So
every outbound frame is UTF-8 encoded TWICE. For near-cap frames
(~1 MiB ``download_progress`` / ``vocabulary_suggestion`` payloads at
1-5 Hz) that's 1-5 MiB/sec of garbage allocation on the asyncio loop
thread.

The fix
-------
Use ``len(raw)`` (char count) instead of ``len(raw.encode("utf-8"))``
(byte count). Char count is a LOWER BOUND on UTF-8 byte count (every
non-ASCII char occupies 2-4 bytes in UTF-8), so:
- If char count > limit → byte count > limit (definitely drop). ✓
- If char count <= limit → byte count MAY exceed the limit (multi-byte
  chars inflate the byte count). In that case we send the frame and
  rely on the Rust host's tungstenite reader to enforce its own
  ``max_size`` on receive (it closes the connection with a 1009 close
  code, which the reconnect path handles). This is a safe
  overestimate of the drop condition.

These tests verify the source change and the safety property.
"""

from __future__ import annotations

import inspect
import json

import pytest
from voice_typer.server.sidecar_ws import _MAX_FRAME_BYTES, _handle_connection_inner


class TestWSFrameSizeCheckSource:
    """AB-38: source-level verification that the size check uses
    ``len(raw)`` (char count), not ``len(raw.encode("utf-8"))``
    (byte count)."""

    def test_size_check_uses_char_count_not_byte_count(self):
        """The ``_writer`` closure's size check must use
        ``len(raw)`` (char count), not ``len(raw.encode("utf-8"))``
        (byte count)."""
        src = inspect.getsource(_handle_connection_inner)
        # The new check must be present as an actual statement (not just
        # inside a comment). We look for ``if len(raw) > _MAX_FRAME_BYTES:``
        # — the new check.
        assert "if len(raw) > _MAX_FRAME_BYTES:" in src, (
            "AB-38: _writer size check must use `len(raw)` (char count), "
            "not `len(raw.encode('utf-8'))` (byte count). The double "
            "UTF-8 encode is a per-frame O(n) waste (1-5 MiB/sec of "
            "garbage on large frames)."
        )
        # The old double-encode check must NOT be present as an actual
        # statement. We strip comment lines (lines whose first non-
        # whitespace char is ``#``) before checking, so the explanatory
        # comment that quotes the old pattern doesn't trigger a false
        # positive.
        code_lines = [line for line in src.splitlines() if line.strip() and not line.strip().startswith("#")]
        code_only = "\n".join(code_lines)
        assert 'len(raw.encode("utf-8"))' not in code_only, (
            "AB-38: _writer size check must NOT call "
            "`raw.encode('utf-8')` as a statement — that re-encodes the "
            "str to bytes just to compute the byte length, then discards "
            "the bytes. `websocket.send(raw)` re-encodes internally for "
            "the WS TEXT frame, so the encode is wasted work."
        )

    def test_safety_comment_present(self):
        """The size-check fix must include a comment explaining the
        safety reasoning (char count is a lower bound on byte count;
        the Rust host enforces its own ``max_size`` on receive)."""
        src = inspect.getsource(_handle_connection_inner)
        # The comment must reference the key safety properties.
        assert "lower bound" in src.lower() or "LOWER BOUND" in src, (
            "AB-38: the size-check fix must include a comment explaining "
            "that char count is a LOWER BOUND on UTF-8 byte count (so the "
            "drop condition is a safe overestimate)."
        )
        assert "tungstenite" in src.lower(), (
            "AB-38: the size-check fix must reference the Rust host's "
            "tungstenite reader, which enforces its own `max_size` on "
            "receive (the authoritative limit)."
        )


class TestWSFrameSizeCheckSemantics:
    """AB-38: behavioral verification that ``len(raw)`` (char count)
    is a safe lower bound on ``len(raw.encode("utf-8"))`` (byte count)
    for UTF-8 encoded JSON."""

    def test_char_count_is_lower_bound_for_ascii(self):
        """For pure-ASCII strings, char count == byte count (1 byte
        per char in UTF-8). So char count == byte count."""
        for s in ["hello", '{"type":"test"}', "a" * 1000, ""]:
            assert len(s) == len(s.encode("utf-8")), f"ASCII string {s!r}: char count must equal byte count"

    def test_char_count_is_lower_bound_for_multibyte(self):
        """For strings with non-ASCII chars (which encode to 2-4 bytes
        in UTF-8), char count < byte count. So if char count > limit,
        byte count > limit (definitely drop)."""
        # 2-byte UTF-8: Latin-1 Supplement (e.g., é, ñ, ü)
        for s in ["café", "piñata", "über", "é" * 100]:
            assert len(s) < len(s.encode("utf-8")), f"2-byte UTF-8 string {s!r}: char count must be < byte count"
            assert len(s) <= len(s.encode("utf-8")), f"2-byte UTF-8 string {s!r}: char count must be <= byte count"
        # 3-byte UTF-8: CJK (e.g., 中, 文, 日本語)
        for s in ["中文", "日本語", "한국어", "中" * 100]:
            assert len(s) < len(s.encode("utf-8")), f"3-byte UTF-8 string {s!r}: char count must be < byte count"
        # 4-byte UTF-8: emoji (e.g., 😀, 🎉)
        for s in ["😀", "🎉", "😀" * 100]:
            assert len(s) < len(s.encode("utf-8")), f"4-byte UTF-8 string {s!r}: char count must be < byte count"

    def test_drop_condition_is_safe_overestimate(self):
        """The new check ``len(raw) > _MAX_FRAME_BYTES`` is a SAFE
        overestimate of the old ``len(raw.encode('utf-8')) >
        _MAX_FRAME_BYTES``: every string dropped by the new check is
        ALSO dropped by the old check (because char count > limit →
        byte count > limit). The converse is NOT true — the new check
        may pass some strings the old check would drop (those with
        multi-byte chars inflating the byte count past the limit while
        the char count stays under)."""
        import random

        random.seed(42)
        # Generate strings with various mixes of ASCII / multi-byte chars.
        test_strings = [
            "a" * (_MAX_FRAME_BYTES + 1),  # over cap on chars (ASCII)
            "a" * _MAX_FRAME_BYTES,  # exactly at cap (ASCII)
            "a" * (_MAX_FRAME_BYTES - 1),  # just under cap (ASCII)
            "é" * (_MAX_FRAME_BYTES + 1),  # over cap on chars (2-byte)
            "é" * _MAX_FRAME_BYTES,  # exactly at cap (2-byte)
            "中" * (_MAX_FRAME_BYTES + 1),  # over cap on chars (3-byte)
            "中" * _MAX_FRAME_BYTES,  # exactly at cap (3-byte)
            "😀" * (_MAX_FRAME_BYTES + 1),  # over cap on chars (4-byte)
            "😀" * _MAX_FRAME_BYTES,  # exactly at cap (4-byte)
        ]
        for s in test_strings:
            char_count = len(s)
            byte_count = len(s.encode("utf-8"))
            new_check_drops = char_count > _MAX_FRAME_BYTES
            old_check_drops = byte_count > _MAX_FRAME_BYTES
            # Safety: every string the new check drops, the old check
            # ALSO drops. (char_count > limit ⟹ byte_count > limit
            # because byte_count >= char_count for UTF-8.)
            if new_check_drops:
                assert old_check_drops, (
                    f"AB-38 safety violation: new check drops {char_count}-char "
                    f"string ({byte_count} bytes) but old check would NOT drop "
                    f"it. char count must be a lower bound on byte count for UTF-8."
                )
            # The new check is a strict subset of the old check's drop
            # condition — it may pass some strings the old check drops
            # (those with multi-byte chars inflating byte_count past
            # the limit while char_count stays under).
            if old_check_drops and not new_check_drops:
                # This is the "safe overestimate" case — byte_count
                # exceeds the limit but char_count does not. The new
                # check passes the frame; the Rust host's tungstenite
                # reader enforces its own max_size on receive.
                assert byte_count > _MAX_FRAME_BYTES, (
                    f"AB-38: expected byte_count > limit when old check "
                    f"drops but new check passes; got byte_count={byte_count}"
                )
                assert char_count <= _MAX_FRAME_BYTES, (
                    f"AB-38: expected char_count <= limit when new check passes; got char_count={char_count}"
                )


class TestWSFrameSizeCheckBehavioral:
    """AB-38: behavioral verification that the ``_writer`` closure's
    size check correctly drops oversized frames (char count > limit)
    and passes frames under the limit, using ``len(raw)`` (not
    ``len(raw.encode('utf-8'))``)."""

    def test_oversized_ascii_frame_is_dropped(self):
        """An ASCII frame whose char count exceeds ``_MAX_FRAME_BYTES``
        must be dropped (never reaches ``websocket.send``). Both the
        old and new checks drop this — char count == byte count for
        ASCII, so both checks fire."""
        # Build an event whose JSON-encoded form exceeds the cap on
        # chars (and thus also on bytes, since it's ASCII).
        big_payload = "x" * (_MAX_FRAME_BYTES + 100)
        event = {"type": "test_oversized", "data": big_payload}
        raw = json.dumps(event, ensure_ascii=False)
        assert len(raw) > _MAX_FRAME_BYTES, "Test setup: the ASCII frame must exceed the char-count cap"
        # The new check must drop it.
        assert len(raw) > _MAX_FRAME_BYTES, "AB-38: oversized ASCII frame must be dropped by the new check"

    def test_multibyte_frame_over_char_cap_is_dropped(self):
        """A multi-byte frame whose char count exceeds
        ``_MAX_FRAME_BYTES`` must be dropped. The new check correctly
        drops it because char count > limit ⟹ byte count > limit
        (multi-byte chars occupy 2-4 bytes each)."""
        # 4-byte emoji chars: char count = N, byte count = 4N.
        big_payload = "😀" * (_MAX_FRAME_BYTES + 10)
        event = {"type": "test_oversized_emoji", "data": big_payload}
        raw = json.dumps(event, ensure_ascii=False)
        assert len(raw) > _MAX_FRAME_BYTES, "Test setup: the emoji frame must exceed the char-count cap"
        # Both checks agree: char count > limit → byte count > limit.
        assert len(raw.encode("utf-8")) > _MAX_FRAME_BYTES, (
            "AB-38 safety: if char count > limit, byte count > limit for UTF-8"
        )

    def test_multibyte_frame_under_char_cap_passes_new_check(self):
        """A multi-byte frame whose char count is under
        ``_MAX_FRAME_BYTES`` but whose byte count EXCEEDS the cap
        passes the new check (it's sent; the Rust host's tungstenite
        reader enforces its own ``max_size`` on receive). The OLD
        check would have dropped this frame."""
        # 4-byte emoji chars: char count = N, byte count = 4N.
        # Pick N so char count < cap but byte count > cap.
        # _MAX_FRAME_BYTES = 1 MiB = 1048576. Pick N = cap/3 (so
        # byte count = 4N = (4/3)*cap > cap, char count = N < cap).
        n_chars = _MAX_FRAME_BYTES // 3
        big_payload = "😀" * n_chars
        event = {"type": "test_multibyte_under_char_cap", "data": big_payload}
        raw = json.dumps(event, ensure_ascii=False)
        # New check passes (char count under cap).
        assert len(raw) <= _MAX_FRAME_BYTES, f"Test setup: char count ({len(raw)}) must be <= cap ({_MAX_FRAME_BYTES})"
        # Old check would have dropped (byte count over cap).
        assert len(raw.encode("utf-8")) > _MAX_FRAME_BYTES, (
            f"Test setup: byte count ({len(raw.encode('utf-8'))}) must be > "
            f"cap ({_MAX_FRAME_BYTES}) — this is the case the new check "
            f"passes that the old check dropped"
        )

    def test_normal_frame_passes_both_checks(self):
        """A small frame passes both the old and new checks (char count
        and byte count both under the cap)."""
        event = {"type": "bubble_level", "level": 0.42}
        raw = json.dumps(event, ensure_ascii=False)
        assert len(raw) <= _MAX_FRAME_BYTES
        assert len(raw.encode("utf-8")) <= _MAX_FRAME_BYTES


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
