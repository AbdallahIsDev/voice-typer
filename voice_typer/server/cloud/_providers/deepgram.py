"""Deepgram ``/v1/listen`` URL building.

Extracted from the ``cloud_engines.py`` monolith. Pure shaping — no
I/O; the caller sends the returned URL with the audio WAV bytes.

SEC-005: query parameters (model, language) are URL-encoded to prevent
parameter injection via crafted config values. Previously the URL was
built with f-string interpolation, which let an attacker inject extra
query parameters or path segments via ``config.cloud_model`` (e.g.
``"&punctuate=false&smart_format=true"``).
"""

from __future__ import annotations

import re
from urllib.parse import urlencode

# A model/language value may only contain URL-safe token characters.
# Anything else is rejected before it can reach the query string —
# urlencode alone would encode the payload, but the strict allowlist
# also stops semantically bogus config values early.
_SAFE_TOKEN_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def build_listen_url(api_url: str, model_name: str, language: str) -> str:
    """Build the Deepgram listen URL with encoded query parameters.

    Raises ``RuntimeError`` if ``model_name`` or ``language`` contains
    characters outside ``[A-Za-z0-9._-]`` (parameter-injection defense;
    messages match the historical ``cloud_engines`` errors verbatim).
    """
    if not _SAFE_TOKEN_RE.match(model_name or ""):
        raise RuntimeError(f"Deepgram model name {model_name!r} contains invalid characters")
    if not _SAFE_TOKEN_RE.match(language or ""):
        raise RuntimeError(f"Deepgram language {language!r} contains invalid characters")
    query = urlencode(
        {
            "model": model_name,
            "language": language,
            "punctuate": "true",
        }
    )
    return f"{api_url}?{query}"
