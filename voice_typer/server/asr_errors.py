"""Typed ASR/mic errors for safe UI mapping."""

from __future__ import annotations

from datetime import datetime, timezone


class ConsentRequiredError(RuntimeError):
    """Raised when an ASR engine is asked to transcribe"""

    # Class-level defaults so ``getattr(exc, "provider", "")``
    provider: str = ""
    scope: str = ""

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
            "provider": self.provider,
            "scope": self.scope,
        }


class HuggingFaceConsentRequiredError(ConsentRequiredError):
    """Typed subclass for HuggingFace *download* consent denial."""

    provider = "huggingface"
    scope = "download"


class CloudConsentRequiredError(ConsentRequiredError):
    """``# type: ignore[arg-type]``: type-checkers can verify the"""

    scope = "transcribe"
    # Explicit class-attribute declaration (mirrors the base
    provider: str = ""

    def __init__(
        self,
        message: str = "",
        *,
        provider: str = "",
        engine_name: str | None = None,
        consent_field: str | None = None,
        model_id: str | None = None,
    ) -> None:
        super().__init__(
            message,
            engine_name=engine_name,
            consent_field=consent_field,
            model_id=model_id,
        )
        self.provider = provider


# Pre-typed-hierarchy, every cloud/LLM failure (401 from the cloud

# The typed hierarchy lets the IPC layer ``isinstance``-check the

# Mapping (cloud_engines.py / llm_polish.py):


# IPC code mapping (handlers/_base.py):
class CloudEngineError(RuntimeError):
    """Base for cloud/LLM engine errors."""


class CloudAuthError(CloudEngineError):
    """401, 403: API key invalid or revoked."""


class CloudRateLimitError(CloudEngineError):
    """429: rate limited (after retry budget exhausted)."""


class CloudServerError(CloudEngineError):
    """5xx: cloud server error."""


class CloudNetworkError(CloudEngineError):
    """URLError: timeout, DNS failure, connection reset."""


class CloudConfigError(CloudEngineError):
    """Missing API key or URL, configuration incomplete."""


class CloudEmptyResponseError(CloudEngineError):
    """HTTP 200 with an empty/blank body or no transcript."""


class MicrophonePermissionDeniedError(RuntimeError):
    """Raised when the OS denies microphone access (or the user"""

    def __init__(
        self,
        message: str = "Microphone permission denied",
        *,
        state: str | None = None,
    ) -> None:
        super().__init__(message)
        self.state = state

    def __str__(self) -> str:
        base = super().__str__()
        if self.state:
            return f"{base} (state={self.state})"
        return base


class ModelNotDownloadedError(RuntimeError):
    """Raised when a local ASR engine is asked to load a model that has"""

    def __init__(
        self,
        message: str = "",
        *,
        model_size: str | None = None,
        backend: str | None = None,
        repo_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.model_size = model_size
        self.backend = backend
        self.repo_id = repo_id


class ModelIntegrityError(RuntimeError):
    """Raised when a cached local model fails integrity verification on"""

    def __init__(
        self,
        message: str = "",
        *,
        model_size: str | None = None,
        backend: str | None = None,
        repo_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.model_size = model_size
        self.backend = backend
        self.repo_id = repo_id
