"""Per-provider endpoint/model defaults for the cloud ASR engines.

Extracted from the ``cloud_engines.py`` monolith so provider onboarding
(new endpoint, new default model) is a one-map edit.
``voice_typer/server/cloud_engines.py`` re-exports the map so existing
importers keep resolving.
"""

from __future__ import annotations

_PROVIDER_DEFAULTS = {
    "openai": {
        "url": "https://api.openai.com/v1/audio/transcriptions",
        "model": "whisper-1",
    },
    "groq": {
        "url": "https://api.groq.com/openai/v1/audio/transcriptions",
        "model": "whisper-large-v3",
    },
    "deepgram": {
        "url": "https://api.deepgram.com/v1/listen",
        "model": "nova-2",
    },
}
