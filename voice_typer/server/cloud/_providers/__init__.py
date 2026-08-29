"""Provider-specific request shaping for the cloud ASR engines.

Each submodule owns ONE provider's wire format:

- :mod:`.openai`   — OpenAI-compatible multipart/form-data shaping
  (used by both the OpenAI and Groq endpoints).
- :mod:`.deepgram` — Deepgram ``/v1/listen`` URL building (model /
  language token validation + query encoding).

Transport and retry policy live in the parent package
(:mod:`voice_typer.server.cloud._transport`,
:mod:`voice_typer.server.cloud._retry`); these modules are pure
request/response shaping with no I/O.
"""
