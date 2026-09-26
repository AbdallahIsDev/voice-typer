"""Universal media-to-text ingestion (ADR-0023).

Local-file + URL audio extraction, chunked transcription, History
persistence. One concern per module; see ADR-0023 for the tier ladder.
"""

from voice_typer.server.media_ingest.errors import MediaIngestError
from voice_typer.server.media_ingest.jobs import MediaJob, MediaJobManager, get_job_manager
from voice_typer.server.media_ingest.mini_update import (
    ExtractorRefreshState,
    check_refresh,
    installed_extractor_versions,
    load_state,
    save_state,
)
from voice_typer.server.media_ingest.runtime import JsRuntime, find_js_runtime, js_runtime_options
from voice_typer.server.media_ingest.sources import MediaSource, classify_source
from voice_typer.server.media_ingest.url_resolver import ResolvedMedia, resolve_media_url

__all__ = [
    "ExtractorRefreshState",
    "JsRuntime",
    "MediaIngestError",
    "MediaJob",
    "MediaJobManager",
    "MediaSource",
    "ResolvedMedia",
    "check_refresh",
    "classify_source",
    "find_js_runtime",
    "get_job_manager",
    "installed_extractor_versions",
    "js_runtime_options",
    "load_state",
    "resolve_media_url",
    "save_state",
]
