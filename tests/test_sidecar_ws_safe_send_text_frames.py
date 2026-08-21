"""C-WS-2 regression guard: ``_safe_send`` must emit WS **TEXT** frames.

tauri-side wire contract (ADR-0020 §7 + AGENTS.md C-WS-2): every
sidecar→host JSON frame — dispatch responses AND server events — travels
as a UTF-8 TEXT frame carrying the JSON object. The Rust host's WS reader
parses ``Message::Text`` only and logs-and-drops ``Message::Binary``
frames.

Pre-fix, ``_safe_send`` passed the executor's UTF-8 **bytes** straight to
``websocket.send()``. The ``websockets`` library maps ``bytes`` → BINARY
opcode, so EVERY dispatch response left as a binary frame and was
silently dropped inside the host — all renderer commands timed out while
the inline heartbeat acks (sent as ``str`` → TEXT frames) kept flowing.
Symptom on the first Windows host run (2026-08-21): "Lost connection to
Python backend" with a perfectly healthy backend.

These tests pin the contract at the source: whatever ``_safe_send`` is
given, what lands on the wire must be ``str`` (TEXT opcode), never
``bytes`` (BINARY opcode).
"""

from __future__ import annotations

import json

import pytest
from voice_typer.server.sidecar_ws import _safe_send


class _RecordingWebsocket:
    """Minimal fake websocket recording every ``send`` payload."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def send(self, data: object) -> None:
        self.sent.append(data)


@pytest.mark.asyncio
async def test_safe_send_sends_text_frame_not_binary() -> None:
    """A dispatch-response-shaped dict must go out as ``str`` (TEXT frame)."""
    ws = _RecordingWebsocket()
    event = {"type": "result", "data": {"ok": True}, "id": 5}

    status = await _safe_send(ws, event)

    assert status == "sent", f"expected 'sent', got {status!r}"
    assert len(ws.sent) == 1, "exactly one frame must be sent"
    payload = ws.sent[0]
    assert isinstance(payload, str), (
        "_safe_send must send str (WS TEXT frame) — sending bytes produces a "
        "BINARY frame that the Tauri host's reader silently drops "
        "(AGENTS.md C-WS-2)"
    )
    # And it must be valid JSON carrying the id for pending-resolution.
    parsed = json.loads(payload)
    assert parsed["id"] == 5
    assert parsed["type"] == "result"


@pytest.mark.asyncio
async def test_safe_send_large_payload_still_text_frame() -> None:
    """The text-frame contract holds near the size cap (no bytes shortcut)."""
    ws = _RecordingWebsocket()
    big = {"type": "result", "data": {"blob": "x" * 200_000}, "id": 7}

    status = await _safe_send(ws, big)

    assert status == "sent", f"expected 'sent', got {status!r}"
    assert len(ws.sent) == 1
    assert isinstance(ws.sent[0], str), "large payloads must still be TEXT frames (AGENTS.md C-WS-2)"
