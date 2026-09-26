"""Result persistence: History row + optional file export (ADR-0023)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

EXPORT_EXTENSIONS = frozenset({"txt", "srt", "vtt", "json"})


def persist_result(
    app: Any,
    text: str,
    *,
    duration: float,
    model: str = "",
    device: str = "",
    partial: bool = False,
) -> int:
    """Save transcript to History; publish ``history_changed``; return row id."""
    row_id = int(app.history_db.add_transcription(text, duration=duration, model=model, device=device))
    if row_id <= 0:
        raise RuntimeError("history writer unavailable")
    try:
        from voice_typer.server import event_bus

        event_bus.publish({"type": "history_changed", "data": {"reason": "media_ingest", "partial": partial}})
    except Exception:  # noqa: BLE001, best-effort push
        log.debug("[MEDIA] history_changed publish failed", exc_info=True)
    return row_id


def export_text(text: str, path: str, *, fmt: str = "txt") -> str:
    """Write ``text`` to ``path`` as txt/srt/vtt/json; return the path."""
    fmt = (fmt or "txt").lower()
    if fmt not in EXPORT_EXTENSIONS:
        raise ValueError(f"unsupported export format: {fmt}")
    out = Path(path)
    if fmt == "json":
        import json

        out.write_text(json.dumps({"text": text}, ensure_ascii=False, indent=2), encoding="utf-8")
    elif fmt in ("srt", "vtt"):
        body = "WEBVTT\n\n" if fmt == "vtt" else ""
        body += f"1\n00:00:00,000 --> 99:59:59,999\n{text}\n"
        out.write_text(body, encoding="utf-8")
    else:
        out.write_text(text, encoding="utf-8")
    return str(out)
