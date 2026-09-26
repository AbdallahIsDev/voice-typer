"""Source classification: local file path vs URL (ADR-0023)."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class MediaSource:
    """Classified ingest source."""

    kind: str  # "file" | "url"
    raw: str


def classify_source(raw: str) -> MediaSource:
    """Classify ``raw`` as a local file path or a remote URL."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("empty media source")
    scheme = urlparse(text).scheme.lower()
    if scheme in ("http", "https"):
        return MediaSource(kind="url", raw=text)
    return MediaSource(kind="file", raw=text)
