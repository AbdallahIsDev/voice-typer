"""Runtime pack downloader service. Phase 2b (plan-runtime-pack-split.md §4.5–4.9, §8).

A SEPARATE consent-gated downloader for the ML **runtime pack**
(worker exe + onnxruntime + ctranslate2 + engines).

Package layout (each module owns one lifecycle; this ``__init__`` is a
pure re-export surface so every historical import path keeps working):

* :mod:`.core` — schema, constants, paths, manifest validation,
  integrity verification, macOS signing.
* :mod:`.gates` — disk space / consent / SSRF / proxy pre-download gates.
* :mod:`.lock` — dual-instance pack lock (§8.13).
* :mod:`.install` — extract → verify → atomic swap (§8.3).
* :mod:`.download` — HTTP download with resume (§8.1, §8.7, §8.9).
* :mod:`.checksum` — background checksum (§8.16).
* :mod:`.events` — IPC event family + best-effort publish.

See the original module docstring history in git for the full §8 edge-case
catalogue and the platform path table.
"""

from __future__ import annotations

import time

from voice_typer.server.branding import APP_NAME

from .checksum import BackgroundChecksum
from .core import (
    OFFLINE_PACK_COMPRESSED_MB,
    OFFLINE_PACK_LOCK_POLL_S,
    OFFLINE_PACK_LOCK_TIMEOUT_S,
    OFFLINE_PACK_MAX_PER_FILE_BYTES,
    OFFLINE_PACK_RATE_LIMIT_BACKOFF_S,
    OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS,
    OFFLINE_PACK_REQUIRED_MB,
    OFFLINE_PACK_UNPACKED_MB,
    OfflinePackConsentRequiredError,
    OfflinePackDiskFullError,
    OfflinePackFileEntry,
    OfflinePackManifest,
    OfflinePackRateLimitError,
    _default_offline_pack_root,
    fallback_offline_pack_root,
    load_offline_pack_manifest,
    offline_pack_dir_for_version,
    offline_pack_exists,
    offline_pack_lock_path,
    offline_pack_manifest_path,
    offline_pack_partial_path,
    validate_offline_pack_manifest_dict,
    verify_offline_pack_or_skip,
    verify_offline_pack_signature_macos,
)
from .download import (
    _http_get_streaming,
    _RateLimitedError,
    _stream_response_to_disk,
    download_offline_pack_with_resume,
)
from .events import OFFLINE_PACK_EVENT_TYPES, _publish_event
from .gates import (
    assert_offline_pack_url_allowed,
    check_offline_pack_disk_space,
    proxy_env,
    require_offline_pack_consent,
)
from .install import (
    atomic_swap_offline_pack,
    install_offline_pack,
)
from .lock import OfflinePackLock

__all__ = [
    "APP_NAME",
    "OFFLINE_PACK_COMPRESSED_MB",
    "OFFLINE_PACK_EVENT_TYPES",
    "OFFLINE_PACK_LOCK_POLL_S",
    "OFFLINE_PACK_LOCK_TIMEOUT_S",
    "OFFLINE_PACK_MAX_PER_FILE_BYTES",
    "OFFLINE_PACK_RATE_LIMIT_BACKOFF_S",
    "OFFLINE_PACK_RATE_LIMIT_MAX_ATTEMPTS",
    "OFFLINE_PACK_REQUIRED_MB",
    "OFFLINE_PACK_UNPACKED_MB",
    "BackgroundChecksum",
    "OfflinePackConsentRequiredError",
    "OfflinePackDiskFullError",
    "OfflinePackFileEntry",
    "OfflinePackLock",
    "OfflinePackManifest",
    "OfflinePackRateLimitError",
    "_RateLimitedError",
    "_default_offline_pack_root",
    "_http_get_streaming",
    "_publish_event",
    "_stream_response_to_disk",
    "assert_offline_pack_url_allowed",
    "atomic_swap_offline_pack",
    "check_offline_pack_disk_space",
    "download_offline_pack_with_resume",
    "fallback_offline_pack_root",
    "install_offline_pack",
    "time",
    "validate_offline_pack_manifest_dict",
    "load_offline_pack_manifest",
    "offline_pack_dir_for_version",
    "offline_pack_exists",
    "offline_pack_lock_path",
    "offline_pack_manifest_path",
    "offline_pack_partial_path",
    "proxy_env",
    "require_offline_pack_consent",
    "verify_offline_pack_or_skip",
    "verify_offline_pack_signature_macos",
]
