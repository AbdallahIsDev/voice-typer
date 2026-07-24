"""Shared ASR error types.

EC-FIX-8: extracted from ``cloud_engines.py`` to break the layering
violation where local ASR engines (``parakeet_engine``,
``transcription``) imported ``ConsentRequiredError`` from the cloud-
engines module (EC-30 finding #12 / EC-B4).

This module is intentionally dependency-free (no imports from
``voice_typer.server.*``) so any engine can import it without risk of
circular imports.
"""

from __future__ import annotations

from datetime import datetime, timezone


class ConsentRequiredError(RuntimeError):
    """NEW-PRIV-006: raised when an ASR engine is asked to transcribe
    or download model files but the user hasn't granted consent for
    that provider.

    Subclass of ``RuntimeError`` so existing ``except RuntimeError``
    catch clauses still work — but the IPC layer can
    ``isinstance``-check for this type to surface a consent dialog
    instead of an error toast.

    GT-B2-9: structured fields are captured on the exception instance
    so the IPC layer, telemetry sinks, and the renderer's consent
    dialog can drive their behavior off typed fields instead of regex-
    matching the message string. The ``message`` positional argument
    is preserved for backward compatibility with all existing
    ``raise ConsentRequiredError("...")`` call sites; the structured
    fields are keyword-only so existing callers continue to work
    unchanged.

    Structured fields:

    - ``engine_name``: the backend that needed consent
      (``"whisper"`` / ``"qwen"`` / ``"parakeet"`` / ``"openai"`` /
      ``"groq"`` / ``"deepgram"``).
    - ``consent_field``: the Config attribute name whose ``False``
      value triggered the error (e.g. ``"huggingface_consent"`` /
      ``"openai_consent"``). Used by the renderer to deep-link to the
      exact toggle in Settings.
    - ``model_id``: optional model identifier (e.g.
      ``"nvidia/parakeet-tdt-0.6b-v3"`` or ``"tiny.en"``) when the
      consent gate fired on a specific model. ``None`` for cloud
      engines that gate on provider rather than model.
    - ``timestamp``: ISO-8601 UTC timestamp when the error was raised
      (set in ``__init__``). Used by telemetry sinks to deduplicate
      repeat events within a session.
    """

    def __init__(
        self,
        message: str = "",
        *,
        engine_name: str | None = None,
        consent_field: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.engine_name = engine_name
        self.consent_field = consent_field
        self.model_id = model_id
        self.timestamp: str = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> dict[str, str | None]:
        """Return the structured fields as a JSON-serializable dict."""
        return {
            "engine_name": self.engine_name,
            "consent_field": self.consent_field,
            "model_id": self.model_id,
            "timestamp": self.timestamp,
            "message": str(self.args[0]) if self.args else "",
        }
