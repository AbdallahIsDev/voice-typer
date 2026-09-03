"""Cloud ASR support package: transport, retry policy, provider shaping.

Stateless plumbing split out of the former single-module
``cloud_engines.py`` monolith by concern. ``cloud_engines.py`` remains
the compatibility facade AND re-exports the ``CloudEngine``
orchestration, whose class body now lives in :mod:`._engine`
(engine class, shared retry loop, provider send orchestration,
connection probing), because tests and production patch
engine-adjacent singletons through the facade module's
namespace (``_opener`` rebinding, ``assert_url_allowed`` patching) —
see the facade docstring for the full contract.

Leaf modules:

- :mod:`._engine` — the ``CloudEngine`` orchestration class (lifecycle,
  consent gate, shared retry skeleton, provider send paths,
  connection probe). Resolves the facade-owned singletons
  (``_opener``, ``assert_url_allowed``) at call time so facade-namespace
  patches keep steering the engine.
- :mod:`._transport` — shared HTTP transport: the pooled secure
  ``_opener`` (no-redirect), response-body cap (``_read_capped``),
  float32→WAV encoding (``_audio_to_wav_bytes``), and the streaming
  multipart body (``_StreamingMultipartBody``).
- :mod:`._retry`    — retry-policy primitives: ``_parse_retry_after``
  (RFC 7231 §7.1.3, 60 s sleep cap) and ``_cloud_http_error_class``
  (HTTP status → typed ``CloudEngineError`` subclass).
- :mod:`._defaults` — per-provider endpoint/model defaults
  (``_PROVIDER_DEFAULTS``).
- :mod:`._providers.openai`   — OpenAI-compatible multipart shaping
  (OpenAI + Groq endpoints).
- :mod:`._providers.deepgram` — Deepgram listen-URL building (model /
  language token validation + query encoding).

Every leaf name is re-exported here so the package namespace mirrors
the leaves and ``from voice_typer.server.cloud import X`` works for
each of them.
"""

from __future__ import annotations

from ._defaults import (  # noqa: F401  # package re-export
    _PROVIDER_DEFAULTS,
)
from ._engine import (  # noqa: F401  # package re-export
    CloudEngine,
)
from ._providers.deepgram import (  # noqa: F401  # package re-export
    build_listen_url,
)
from ._providers.openai import (  # noqa: F401  # package re-export
    build_multipart_body,
    build_multipart_parts,
)
from ._retry import (  # noqa: F401  # package re-export
    _cloud_http_error_class,
    _parse_retry_after,
)
from ._transport import (  # noqa: F401  # package re-export
    _audio_to_wav_bytes,
    _opener,
    _read_capped,
    _StreamingMultipartBody,
)
