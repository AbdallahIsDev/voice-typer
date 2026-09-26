"""Tier-1/2 fallback: temp audio-only download via yt-dlp (ADR-0023 E9).

When direct streaming stalls (STREAM_STALLED) or the signed URL expires
(URL_EXPIRED) past one inline re-resolve, the audio is downloaded to a
temp file instead. Cleanup is guaranteed by the caller's context manager.
"""

from __future__ import annotations

import contextlib
import logging
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from voice_typer.server.media_ingest.errors import DOWNLOAD_FAILED, MediaIngestError

log = logging.getLogger(__name__)


@contextlib.contextmanager
def temp_audio_download(
    url: str,
    *,
    factory: Callable[..., Any] | None = None,
    on_progress: Callable[[float], None] | None = None,
    js_runtimes: dict[str, str] | None = None,
) -> Iterator[Path]:
    """Download audio-only to a temp file; delete it on exit.

    Yields the downloaded file path. ``on_progress`` receives the
    download fraction (0-1) so the job can publish a download phase.
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:
        raise MediaIngestError(DOWNLOAD_FAILED, "media downloader unavailable") from exc

    def _hook(status: dict[str, Any]) -> None:
        if on_progress is None or status.get("status") != "downloading":
            return
        total = status.get("total_bytes") or status.get("total_bytes_estimate") or 0
        done = status.get("downloaded_bytes") or 0
        if total and done:
            on_progress(min(1.0, float(done) / float(total)))

    options: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": "%(title).64s.%(ext)s",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "continuedl": True,
        "progress_hooks": [_hook],
    }
    if js_runtimes:
        options["js_runtimes"] = js_runtimes

    maker = factory or (lambda opts: YoutubeDL(opts))
    with tempfile.TemporaryDirectory(prefix="lausu_media_") as tmp:
        out_dir = Path(tmp)
        options["outtmpl"] = str(out_dir / "%(title).64s.%(ext)s")
        before = set(out_dir.iterdir())
        try:
            downloader = maker(options)
            if hasattr(downloader, "__enter__"):
                with downloader as active:
                    active.download([url])
            else:
                downloader.download([url])
        except MediaIngestError:
            raise
        except Exception as exc:
            raise MediaIngestError(DOWNLOAD_FAILED, f"audio download failed: {exc}") from exc
        produced = [p for p in set(out_dir.iterdir()) - before if p.is_file()]
        if not produced:
            raise MediaIngestError(DOWNLOAD_FAILED, "audio download produced no file")
        target = max(produced, key=lambda p: p.stat().st_size)
        yield target
