"""Shared ASR error types.

Extracted from ``cloud_engines.py`` to break the layering
violation where local ASR engines (``parakeet_engine``,
``transcription``) imported ``ConsentRequiredError`` from the cloud-
engines module ( finding #12 / ).

This module is intentionally dependency-free (no imports from
``voice_typer.server.*``) so any engine can import it without risk of
circular imports.
"""

from __future__ import annotations

from datetime import datetime, timezone


class ConsentRequiredError(RuntimeError):
    """Raised when an ASR engine is asked to transcribe
    or download model files but the user hasn't granted consent for
    that provider.

    Subclass of ``RuntimeError`` so existing ``except RuntimeError``
    catch clauses still work — but the IPC layer can
    ``isinstance``-check for this type to surface a consent dialog
    instead of an error toast.

    Structured fields are captured on the exception instance
    so the IPC layer, telemetry sinks, and the renderer's consent
    dialog can drive their behavior off typed fields instead of regex-
    matching the message string. The ``message`` positional argument
    is preserved for backward compatibility with all existing
    ``raise ConsentRequiredError("...")`` call sites; the structured
    fields are keyword-only so existing callers continue to work
    unchanged.

    ``provider`` and ``scope`` are class attributes (defaulting
    to empty string) so the IPC layer can read them off any instance
    via ``getattr(exc, "provider", "")`` without ``isinstance``
    branching. The typed subclasses (``HuggingFaceConsentRequiredError``
    / ``CloudConsentRequiredError``) override them so the renderer can
    distinguish "HuggingFace download consent missing" from "OpenAI
    cloud-transcription consent missing".

    Structured fields:

    - ``provider``: the consent provider (``"huggingface"`` /
      ``"openai"`` / ``"groq"`` / ``"deepgram"``). Empty string on the
      base class for backward compat with legacy raise sites.
    - ``scope``: the consent scope (``"download"`` / ``"transcribe"``).
      Empty string on the base class for backward compat.
    - ``engine_name``: the backend that needed consent
      (``"whisper"`` / ``"qwen"`` / ``"parakeet"`` / ``"openai"`` /
      ``"groq"`` / ``"deepgram"``).
    - ``consent_field``: the Config attribute name whose ``False``
      value triggered the error (e.g. ``"huggingface_consent"`` /
      ``"openai_consent"``). Used by the renderer to deep-link to the
      exact toggle in Settings.
    - ``model_id``: optional model identifier (e.g.
      ``"nvidia/parakeet-tdt-0.6b-v3"`` or ``"tiny"``) when the
      consent gate fired on a specific model. ``None`` for cloud
      engines that gate on provider rather than model.
    - ``timestamp``: ISO-8601 UTC timestamp when the error was raised
      (set in ``__init__``). Used by telemetry sinks to deduplicate
      repeat events within a session.
    """

    # Class-level defaults so ``getattr(exc, "provider", "")``
    # on ANY instance (base or subclass) always returns a string.
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
    """Typed subclass for HuggingFace *download* consent denial.

    ``provider`` / ``scope`` are class attributes (not per-instance) —
    every HuggingFace consent denial is a download-scope denial for
    the ``"huggingface"`` provider, so the values are fixed at the
    class level. The IPC layer reads them via ``getattr(exc,
    "provider", "")`` without ``isinstance`` branching.
    """

    provider = "huggingface"
    scope = "download"


class CloudConsentRequiredError(ConsentRequiredError):
    """Typed subclass for cloud-provider *transcribe* consent
    denial.

    ``scope`` is a class attribute (always ``"transcribe"`` — every
    cloud consent denial is a transcribe-scope denial). ``provider``
    is also a class attribute (inherited from
    :class:`ConsentRequiredError`, defaults to ``""``) so any
    ``getattr(exc, "provider", "")`` read on a bare instance returns
    a string without ``isinstance`` branching. Subclasses (or
    instances) should override ``provider`` to surface the specific
    cloud vendor — the ``__init__`` ``provider`` kwarg sets the
    instance attribute, shadowing the empty-string class default so
    each cloud engine (openai / groq / deepgram) can carry its own
    provider value without a separate subclass per provider.

    ``__init__`` now accepts the parent's structured fields
    (``engine_name`` / ``consent_field`` / ``model_id``) as explicit
    keyword arguments instead of a generic ``**kwargs: object`` +
    ``# type: ignore[arg-type]`` — type-checkers can verify the
    forwarding and the ignore comment is no longer needed.
    """

    scope = "transcribe"
    # Explicit class-attribute declaration (mirrors the base
    # class default of ``""``) documenting that subclasses or instances
    # should override this to surface the specific cloud vendor.
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


# ── Typed cloud/LLM exception hierarchy ─────────────────────────────────

# Pre-typed-hierarchy, every cloud/LLM failure (401 from the cloud
# provider, 429 rate limit, 5xx server error, network timeout, missing
# API key) collapsed to a generic ``RuntimeError``. The IPC handler
# catch-all then mapped that generic ``RuntimeError`` to the generic
# ``server.internal_error`` envelope — so the renderer could not
# distinguish "API key invalid" (user must re-enter) from "transient
# network" (auto-retry) from "rate limited" (backoff) from "missing
# config" (open Settings).

# The typed hierarchy lets the IPC layer ``isinstance``-check the
# exception and emit a distinct IPC error code (registered in
# ``ERROR_CODES`` at ``voice_typer/server/ipc/validation.py``). The
# hierarchy subclasses ``RuntimeError`` so existing ``except
# RuntimeError`` clauses still catch them — but the new typed
# branches in ``HandlerBase._respond_with_error`` (see
# ``voice_typer/server/handlers/_base.py``) take precedence for the
# cloud/LLM codes.

# Mapping (cloud_engines.py / llm_polish.py):
#   401, 403                  → CloudAuthError
#   429 (after retry budget)  → CloudRateLimitError
#   5xx                       → CloudServerError
#   URLError (timeout/DNS)    → CloudNetworkError
#   missing api_key / url     → CloudConfigError


# IPC code mapping (handlers/_base.py):
#   CloudAuthError            → server.cloud_auth_failed
#   CloudRateLimitError       → server.cloud_rate_limited
#   CloudServerError          → server.cloud_server_error
#   CloudNetworkError         → server.cloud_network_error
#   CloudConfigError          → server.cloud_config_error
class CloudEngineError(RuntimeError):
    """Base for cloud/LLM engine errors.

    All cloud/LLM-specific exceptions subclass this so the IPC layer
    can ``isinstance``-narrow to "something went wrong with the cloud
    provider" without matching the message string. Subclasses below
    carry the semantic category (auth / rate-limit / server / network
    / config).
    """


class CloudAuthError(CloudEngineError):
    """401, 403 — API key invalid or revoked.

    The renderer surfaces "Cloud API key invalid — open Settings to
    re-enter" instead of a generic "internal error" toast.
    """


class CloudRateLimitError(CloudEngineError):
    """429 — rate limited (after retry budget exhausted).

    The renderer surfaces "Cloud provider rate limited — please retry
    shortly" and may schedule an automatic backoff retry.
    """


class CloudServerError(CloudEngineError):
    """5xx — cloud server error.

    The renderer surfaces "Cloud provider server error" and may retry
    with exponential backoff.
    """


class CloudNetworkError(CloudEngineError):
    """URLError — timeout, DNS failure, connection reset.

    The renderer surfaces "Network error contacting cloud provider"
    and may retry (the cloud engine itself already retries 3× with
    exponential backoff before raising, so by the time this reaches
    the IPC layer the retry budget is exhausted).
    """


class CloudConfigError(CloudEngineError):
    """Missing API key or URL — configuration incomplete.

    The renderer surfaces "Cloud provider not configured — open
    Settings to enter API key". The cross-field validator at
    ``config_validators._check_cross_field_cloud_config`` catches the
    common case at save time; this runtime check stays as
    defense-in-depth for the case where the key was revoked between
    save and transcribe.
    """


class CloudEmptyResponseError(CloudEngineError):
    """HTTP 200 with an empty/blank body or no transcript.

    A provider returning 200 with an empty body (or ``{}`` / a missing
    transcript field) is an anomaly — the pipeline must not ship an
    empty transcript as if it were valid. The renderer surfaces a
    cloud-provider error instead of a silent empty transcription.
    """


class MicrophonePermissionDeniedError(RuntimeError):
    """Raised when the OS denies microphone access (or the user
    declined the consent prompt).

    Subclass of ``RuntimeError`` so existing ``except RuntimeError``
    catch clauses still work — but the IPC layer can
    ``isinstance``-check for this type to surface the permission
    onboarding UI instead of a generic error toast.

    The optional ``state`` kwarg captures the OS-reported permission
    state (``"denied"``, ``"prompt"``, ``"restricted"``, etc.) so
    telemetry sinks and the renderer can drive their behavior off the
    typed field instead of regex-matching the message string.
    """

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
    """Raised when a local ASR engine is asked to load a model that has
    not been downloaded yet.

    The app NEVER downloads models automatically — the user must
    explicitly download a model (via the Models page Download button or
    the onboarding wizard) before it can be loaded. This error is raised
    by the engine ``load()`` paths when the selected model is absent
    from the local HuggingFace cache, so the IPC / tray layer can
    surface an actionable "open the Models page and download" message
    instead of a generic load failure.

    Subclass of ``RuntimeError`` so existing ``except RuntimeError``
    catch clauses still work. Structured fields (``model_size`` /
    ``backend`` / ``repo_id``) let callers surface a targeted message
    without regex-matching the text.
    """

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
    """Raised when a cached local model fails integrity verification on
    the engine ``load()`` path.

    The tampered cache directory is intentionally NOT deleted
    automatically — deleting a user's model files is an explicit user
    action (the Models page Delete button). Instead the load is refused
    and the user is told to delete + re-download the model (via the
    Models page) to recover. Only the explicit, user-initiated download
    path may replace tampered files.
    """

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
