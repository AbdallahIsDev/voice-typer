"""VoiceTyperService model-mixin constants (verbatim)."""

from __future__ import annotations

# PERF-10 / SVC-9: TTL (seconds) for the get_model_status cache.  The IPC
# renderer polls ~every 2s; a 5s TTL cuts filesystem syscall rate ~60% with
# no user-visible staleness (cache is invalidated on download/delete).
_MODEL_STATUS_CACHE_TTL_S = 5.0

# user-facing messages for each ``download_parakeet_weights``
# reason code. The service layer unpacks the ``(success, reason, exc_info)``
# 3-tuple and maps the short reason code to a human-readable message so
# the renderer's error toast / tray notification tells the user WHAT
# went wrong ("not enough disk space") rather than the raw code
# ("disk_space_insufficient").
#
# Keys mirror the reason codes documented in
# ``asr_setup.download_parakeet_weights`` (see that function's docstring
# for the full list). Unknown reason codes fall back to a generic
# "Download failed: <reason>" message at the call site.
_PARAKEET_REASON_MESSAGES: dict[str, str] = {
    "huggingface_consent_false": (
        "HuggingFace consent not given. Enable HuggingFace downloads in Settings to download the Parakeet model."
    ),
    "huggingface_hub_missing": (
        "huggingface_hub is not installed. Install it with `pip install huggingface_hub` and try again."
    ),
    "disk_space_insufficient": (
        "Not enough disk space to download the Parakeet model (~2.5 GB). Free up space and try again."
    ),
    "download_retry_exhausted": (
        "Download failed after multiple retries. Check your network connection and try again."
    ),
    "integrity_check_failed": (
        "Downloaded model failed integrity verification. The cached "
        "files may be corrupt — the cache was cleared; please retry."
    ),
}
