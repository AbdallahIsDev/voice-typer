"""LausuService model-mixin constants (verbatim)."""

from __future__ import annotations

# TTL (seconds) for the get_model_status cache.  The IPC
_MODEL_STATUS_CACHE_TTL_S = 5.0

# user-facing messages for each ``download_parakeet_weights``
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
        "files may be corrupt, the cache was cleared; please retry."
    ),
}
