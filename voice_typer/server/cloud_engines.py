"""Cloud ASR backends: OpenAI, Groq, Deepgram.

Each engine implements the TranscriberProtocol so the app can swap
backends transparently. CloudEngine lifecycle is **per-transcription**
(see the lifecycle block below and
``tests/test_cloud_engines_dead_cache_removed.py``); the engine class
itself lives in :mod:`voice_typer.server.cloud._engine` and the
stateless plumbing (transport, retry policy, provider defaults,
request shaping) in the sibling leaf modules — every moved name is
re-exported here so all historical import sites keep resolving.

Configuration:
    asr_backend: "openai" | "groq" | "deepgram"
    cloud_api_key: str
    cloud_api_url: str (optional, for custom/self-hosted endpoints)
    cloud_model: str (optional, provider-specific default)
"""

import io  # noqa: F401  # facade re-export
import time  # noqa: F401  # facade re-export
import wave  # noqa: F401  # facade re-export
from datetime import datetime, timezone  # noqa: F401  # facade re-export

from voice_typer.server._audio_constants import WHISPER_SAMPLE_RATE  # noqa: F401  # facade re-export
from voice_typer.server._http_safety import build_secure_opener  # noqa: F401  # facade re-export
from voice_typer.server._secrets import (  # noqa: F401  # facade re-export
    assert_url_allowed,
    redact_secret,
    redact_url,
)
from voice_typer.server.asr_errors import (  # noqa: F401  # facade re-export
    CloudAuthError,
    CloudConfigError,
    CloudConsentRequiredError,
    CloudEmptyResponseError,
    CloudEngineError,
    CloudNetworkError,
    CloudRateLimitError,
    CloudServerError,
    ConsentRequiredError,
)
from voice_typer.server.cloud import (
    _PROVIDER_DEFAULTS,  # noqa: F401  # facade re-export
    CloudEngine,  # noqa: F401  # facade re-export
    _audio_to_wav_bytes,  # noqa: F401  # facade re-export
    _cloud_http_error_class,  # noqa: F401  # facade re-export
    _opener,  # noqa: F401  # facade re-export
    _parse_retry_after,  # noqa: F401  # facade re-export
    _read_capped,  # noqa: F401  # facade re-export
    _StreamingMultipartBody,  # noqa: F401  # facade re-export
    build_listen_url,  # noqa: F401  # facade re-export
    build_multipart_body,  # noqa: F401  # facade re-export
    build_multipart_parts,  # noqa: F401  # facade re-export
)

# CloudEngine lifecycle is **per-transcription**.
#
# Historically this module hosted an 80-line module-level cached-engine
# infrastructure (``_CACHED_ENGINES``, ``register_cached_cloud_engine``,
# ``get_cached_cloud_engine``, ``clear_cached_engine``,
# ``clear_all_cached_engines``) intended to support long-lived
# CloudEngine instances that could be invalidated on credential /
# consent revocation. Verified by repo-wide grep (2026-07-28): ZERO
# production callers — the only consumers were the unit tests in
# ``tests/test_cloud_engines.py::TestCloudEngineCacheInvalidation`` and
# ``app.py`` lazily sets ``self._cloud_engine = None`` but never
# assigns a real CloudEngine. The cache was dead code in a "worst of
# both worlds" state: maintenance burden + a docstring claim about
# GDPR-delete invalidation that the runtime never actually performed.
#
# The infrastructure has been deleted. When a future PR wires
# CloudEngine into production (per-transcription → long-lived), the
# invalidation logic MUST be added at that time AND the
# ``config_applier`` set_config path MUST invalidate the cached engine
# when ``openai_api_key`` / ``groq_api_key`` / ``deepgram_api_key`` /
# ``cloud_api_key`` changes (today only ``llm_*`` field changes
# invalidate ``_llm_polisher``).
#
# Until then, each transcription constructs a fresh CloudEngine with
# the current API key + consent flag from the Config dataclass, so
# stale-credential reuse is structurally impossible.
#
# NOTE on the split layout: the ``CloudEngine`` class body now lives in
# ``voice_typer.server.cloud._engine`` (engine lifecycle, shared retry
# loop, provider send orchestration, connection probe). The stateless
# leaf names (``_opener``, ``_read_capped``, ``_parse_retry_after``,
# ``_cloud_http_error_class``, ``_PROVIDER_DEFAULTS``,
# ``_audio_to_wav_bytes``, ``_StreamingMultipartBody``) are DEFINED in
# their owning ``voice_typer.server.cloud`` leaves and re-exported
# here. The engine resolves ``_opener`` and ``assert_url_allowed``
# from THIS module's namespace at call time (via
# ``cloud._engine._facade()``) on purpose: tests and production patch
# engine-adjacent singletons through this namespace
# (``setattr(cloud_engines, "_opener", mock)``,
# ``patch("voice_typer.server.cloud_engines.assert_url_allowed")``),
# so rebinding the facade attributes still steers the engine.
