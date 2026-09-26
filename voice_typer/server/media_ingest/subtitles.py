"""Tier-0 fast path: fetch official subtitles/captions instead of ASR (ADR-0023 E12).

When the source offers subtitles in the requested language (or English),
downloading them is seconds-fast and free, so it runs before any audio
pipeline starts. Parsing is minimal: pick a vtt/srv3/srt track, strip tags.
"""

from __future__ import annotations

import logging
import re
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_TIMESTAMP_RE = re.compile(r"^\d{2}:\d{2}:\d{2}[.,]\d{3}")


def fetch_subtitles(
    url: str,
    *,
    lang: str = "en",
    factory: Callable[..., Any] | None = None,
) -> str | None:
    """Return cleaned subtitle text for ``url`` in ``lang``, else None."""
    try:
        from yt_dlp import YoutubeDL
    except ImportError:
        return None
    maker = factory or (lambda options: YoutubeDL(options))
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitlesformat": "vtt/srv3/srt/best",
        "subtitleslangs": [lang, "en"],
    }
    try:
        downloader = maker(options)
        if hasattr(downloader, "__enter__"):
            with downloader as active:
                info = active.extract_info(url, download=False)
        else:
            info = downloader.extract_info(url, download=False)
    except Exception:  # noqa: BLE001, fast path only; caller falls back to ASR
        log.debug("[MEDIA] subtitle probe failed", exc_info=True)
        return None
    if not isinstance(info, dict):
        return None
    subs = info.get("subtitles") or {}
    entry = _pick_language(subs, lang)
    if entry is None:
        return None
    for fmt in entry:
        sub_url = str((fmt or {}).get("url") or "")
        if not sub_url:
            continue
        text = _download_and_clean(sub_url)
        if text:
            return text
    return None


def _pick_language(subs: Any, lang: str) -> list[dict[str, Any]] | None:
    """Prefer exact language, then English, from a subtitles dict."""
    if not isinstance(subs, dict) or not subs:
        return None
    entry = subs.get(lang)
    if isinstance(entry, list) and entry:
        return entry
    entry = subs.get("en")
    if isinstance(entry, list) and entry:
        return entry
    return None


def _download_and_clean(sub_url: str, *, timeout: float = 30.0) -> str | None:
    try:
        with urllib.request.urlopen(sub_url, timeout=timeout) as response:  # noqa: S310, yt-dlp-provided URL
            raw = response.read(4 * 1024 * 1024).decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001, try next format
        log.debug("[MEDIA] subtitle download failed", exc_info=True)
        return None
    return _clean_caption_text(raw)


def _clean_caption_text(raw: str) -> str | None:
    """Strip WebVTT/SRT scaffolding and tags into plain transcript text."""
    lines: list[str] = []
    for line in raw.splitlines():
        text = _TAG_RE.sub("", line).strip()
        if not text or text.upper() == "WEBVTT":
            continue
        if _TIMESTAMP_RE.match(text) or "-->" in text:
            continue
        if text.isdigit():
            continue
        if text.startswith(("NOTE", "Kind:", "Language:", "STYLE", "REGION")):
            continue
        lines.append(text)
    deduped = [line for i, line in enumerate(lines) if not i or line != lines[i - 1]]
    cleaned = " ".join(deduped).strip()
    return cleaned or None
