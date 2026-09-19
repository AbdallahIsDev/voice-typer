"""Cloud ASR/LLM engine configuration and dispatch helpers."""

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
