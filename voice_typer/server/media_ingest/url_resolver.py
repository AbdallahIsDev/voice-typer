"""URL resolution for remote media sources (ADR-0023 Tier 0/1).

Resolve-only yt-dlp extraction returns a direct audio URL plus metadata.
Nothing is downloaded here. Streaming/download behavior remains in
``decoder.py``. This module intentionally has no network behavior of its
own, so unit tests inject fakes through ``YoutubeDLFactory``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from voice_typer.server.media_ingest.errors import (
    DRM_REFUSED,
    GEO_BLOCKED,
    LOGIN_REQUIRED,
    PLAYLIST_REJECTED,
    RESOLVE_FAILED,
    UNSUPPORTED_LIVE,
    MediaIngestError,
)

log = logging.getLogger(__name__)

RESOLVE_FORMAT = "bestaudio[protocol^=http]/bestaudio/best"
YOUTUBE_HOSTS = ("youtube.com", "youtu.be", "music.youtube.com")


@dataclass(frozen=True)
class ResolvedMedia:
    """Direct audio target selected from extractor metadata."""

    audio_url: str
    title: str = ""
    duration: float | None = None
    is_live: bool = False
    needs_js_runtime: bool = False
    webpage_url: str = ""
    subtitles_available: bool = False


YoutubeDLFactory = Callable[..., Any]


def _default_ytdlp_factory(options: dict[str, Any]) -> Any:
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise MediaIngestError(RESOLVE_FAILED, "media extractor unavailable") from exc
    return YoutubeDL(options)  # type: ignore[no-untyped-call]


def resolve_media_url(
    url: str,
    *,
    factory: YoutubeDLFactory | None = None,
    js_runtimes: dict[str, str] | None = None,
) -> ResolvedMedia:
    """Resolve ``url`` to a direct downloadable/streamable audio URL."""
    cleaned = (url or "").strip()
    if not cleaned:
        raise MediaIngestError(RESOLVE_FAILED, "empty media URL")
    if "://" not in cleaned:
        raise MediaIngestError(RESOLVE_FAILED, "unsupported media URL")

    options: dict[str, Any] = {
        "format": RESOLVE_FORMAT,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "skip_download": True,
        "retries": 2,
    }
    if js_runtimes:
        options["js_runtimes"] = js_runtimes

    maker = factory or _default_ytdlp_factory
    try:
        downloader = maker(options)
        if hasattr(downloader, "__enter__"):
            with downloader as active:
                info = active.extract_info(cleaned, download=False)
        else:
            info = downloader.extract_info(cleaned, download=False)
    except MediaIngestError:
        raise
    except Exception as exc:
        raise _classify_extract_error(exc) from exc

    if not isinstance(info, dict):
        raise MediaIngestError(RESOLVE_FAILED, "media extractor returned no data")
    return _resolved_from_info(info, source_url=cleaned)


def _resolved_from_info(info: dict[str, Any], *, source_url: str) -> ResolvedMedia:
    if info.get("_type") == "playlist" or isinstance(info.get("entries"), list):
        raise MediaIngestError(PLAYLIST_REJECTED, "paste one video link, not a playlist")
    if info.get("is_live") or info.get("live_status") == "is_live":
        raise MediaIngestError(UNSUPPORTED_LIVE, "live streams are not supported")

    formats = info.get("formats")
    if isinstance(formats, list) and any(
        isinstance(item, dict) and str(item.get("drm") or "").lower() in ("true", "widevine", "fairplay", "playready")
        for item in formats
    ):
        raise MediaIngestError(DRM_REFUSED, "this media is protected by DRM")
    has_subs = bool(info.get("subtitles")) or bool(info.get("automatic_captions"))
    if isinstance(formats, list) and formats:
        selected = _select_audio_format(formats)
        if selected is not None:
            direct = str(selected.get("url") or "").strip()
            if direct:
                return ResolvedMedia(
                    audio_url=direct,
                    title=str(info.get("title") or ""),
                    duration=_as_seconds(info.get("duration")),
                    webpage_url=str(info.get("webpage_url") or source_url),
                    needs_js_runtime=_is_youtube_url(source_url),
                    subtitles_available=has_subs,
                )

    direct = str(info.get("url") or "").strip()
    if direct:
        return ResolvedMedia(
            audio_url=direct,
            title=str(info.get("title") or ""),
            duration=_as_seconds(info.get("duration")),
            webpage_url=str(info.get("webpage_url") or source_url),
            needs_js_runtime=_is_youtube_url(source_url),
            subtitles_available=has_subs,
        )

    message = str(info.get("error") or "")
    lowered = message.lower()
    if any(token in lowered for token in ("sign in", "login", "private", "age")):
        raise MediaIngestError(LOGIN_REQUIRED, "this link needs login or is private")
    if "geo" in lowered or "blocked" in lowered:
        raise MediaIngestError(GEO_BLOCKED, "this video is blocked in this region")
    raise MediaIngestError(RESOLVE_FAILED, "no playable audio found at this link")


def _classify_extract_error(exc: Exception) -> MediaIngestError:
    """Map extractor exception messages to the ADR-0023 edge taxonomy."""
    lowered = str(exc).lower()
    if "drm" in lowered or "widevine" in lowered:
        return MediaIngestError(DRM_REFUSED, "this media is protected by DRM")
    if any(token in lowered for token in ("sign in", "login", "private", "age")):
        return MediaIngestError(LOGIN_REQUIRED, "this link needs login or is private")
    if "geo" in lowered or "blocked" in lowered:
        return MediaIngestError(GEO_BLOCKED, "this video is blocked in this region")
    if "is live" in lowered or "livestream" in lowered:
        return MediaIngestError(UNSUPPORTED_LIVE, "live streams are not supported")
    if "playlist" in lowered:
        return MediaIngestError(PLAYLIST_REJECTED, "paste one video link, not a playlist")
    return MediaIngestError(RESOLVE_FAILED, f"could not read media link: {exc}")


def _select_audio_format(formats: list[dict[str, Any]]) -> dict[str, Any] | None:
    audio_only = [
        item
        for item in formats
        if isinstance(item, dict)
        and item.get("url")
        and item.get("vcodec") in (None, "none")
        and item.get("acodec") not in (None, "none")
    ]
    candidates = audio_only or [item for item in formats if isinstance(item, dict) and item.get("url")]
    if not candidates:
        return None
    return max(candidates, key=_format_score)


def _format_score(item: dict[str, Any]) -> tuple[int, float]:
    protocol = str(item.get("protocol") or "")
    http = 1 if protocol.startswith("http") else 0
    try:
        rate = float(item.get("abr") or item.get("tbr") or 0.0)
    except (TypeError, ValueError):
        rate = 0.0
    return (http, rate)


def _as_seconds(value: Any) -> float | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds >= 0 else None


def _is_youtube_url(url: str) -> bool:
    lowered = url.lower()
    return any(host in lowered for host in YOUTUBE_HOSTS)
