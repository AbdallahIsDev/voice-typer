"""Media-ingest error taxonomy (ADR-0023 edge catalog E1-E15)."""

from __future__ import annotations


class MediaIngestError(Exception):
    """User-safe ingest failure carrying a stable machine code."""

    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


NO_AUDIO = "no_audio"
UNSUPPORTED_LIVE = "unsupported_live"
LOGIN_REQUIRED = "login_required"
GEO_BLOCKED = "geo_blocked"
PLAYLIST_REJECTED = "playlist_rejected"
DRM_REFUSED = "drm_refused"
DECODE_FAILED = "decode_failed"
RESOLVE_FAILED = "resolve_failed"
STREAM_STALLED = "stream_stalled"
URL_EXPIRED = "url_expired"
DOWNLOAD_FAILED = "download_failed"
NO_ENGINE = "no_engine_loaded"
JOB_BUSY = "job_busy"
NO_JOB = "no_active_job"
CANCELLED = "cancelled"
