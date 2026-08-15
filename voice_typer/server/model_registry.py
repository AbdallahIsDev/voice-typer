"""Metadata registry for all supported ASR models.

This module is the single source of truth for *rich*
model metadata — anything beyond the bare ``download_size_mb`` table
that lives in :mod:`voice_typer.server.transcription`.

Each entry in :data:`MODEL_REGISTRY` is a :class:`ModelMetadata`
instance capturing:

- ``download_size_mb`` — same value as ``_MODEL_SIZE_MB[name]``
- ``required_vram_mb`` — estimated VRAM for inference (GPU) or RAM (CPU)
- ``backend`` — ``"whisper"`` for the standard Systran faster-whisper
  repos, ``"distil-whisper"`` for the distilled variants.  Used by
  :func:`get_models_by_backend` to filter the catalog.
- ``multilingual`` — whether the model handles non-English audio
- ``supported_languages`` — ``None`` (all) or a list like ``["en"]``
- ``description`` — one-line, user-facing description shown in the
  Models page card.
- ``repo_id`` — HuggingFace repo ID.  Distilled variants use the
  ``Systran/faster-distil-whisper-*`` prefix; the rest use
  ``Systran/faster-whisper-*``.
- ``is_distilled`` — ``True`` for distil-* models
- ``speed_rating`` — ``"fast"`` / ``"medium"`` / ``"slow"``
- ``accuracy_rating`` — ``"low"`` / ``"medium"`` / ``"high"``
``network_behavior`` () — one of ``"local-only"``,
  ``"downloads-on-first-use-consent-gated"``,
  ``"downloads-on-first-use-no-consent"``, or ``"cloud-per-call"``.
  Declares what network activity the model requires so the UI / privacy
  surface can show "downloads on first use (consent gated)" vs.
  "local-only" vs. "cloud per call" honestly.  Whisper + distil variants
  are consent-gated HF downloads; parakeet downloads without explicit
consent (documented honestly per  — this is a known issue to
  fix in a follow-up); qwen is local-only (user supplies the model
  path); cloud providers (not in this registry) are per-call.

The registry is consulted by:

- :func:`voice_typer.server.handlers.model_handlers._handle_get_model_catalog`
  to populate the Models page with rich metadata.
- :meth:`voice_typer.server.service.VoiceTyperService.download_model`
  to resolve the repo_id for new variants (turbo, distil-*) without
  hard-coding a separate name→repo map.

Models are NEVER auto-downloaded at import time.  Importing this
module is side-effect-free; downloads only happen when the user
clicks "Download" on the Models page.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelMetadata:
    """Rich metadata for a single ASR model variant.

    ``frozen=True`` so registry entries can be safely shared between
    threads (the renderer reads them via IPC; the service layer reads
    them during downloads) without copying.
    """

    name: str
    download_size_mb: int
    required_vram_mb: int
    backend: str
    multilingual: bool
    supported_languages: list[str] | None  # None = all languages
    description: str
    repo_id: str
    is_distilled: bool = False
    speed_rating: str = "medium"  # "fast", "medium", "slow"
    accuracy_rating: str = "high"  # "low", "medium", "high"
    # declares what network activity the model requires, so the
    # UI/privacy surface can show "downloads on first use (consent gated)"
    # vs. "local-only" vs. "cloud per call" honestly.  Default
    # ``"local-only"`` is the safest assumption — entries that DO
    # download must override explicitly so the catalog cannot silently
    # misrepresent a download as offline.
    network_behavior: str = "local-only"

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict for IPC transport.

        ``asdict`` handles the dataclass fields; we additionally
        normalize ``supported_languages`` so the renderer can always
        treat ``None`` as "all languages" via the explicit ``null``.
        """
        return asdict(self)


# ── Registry ────────────────────────────────────────────────────────
#
# Order matters for the Models page: entries appear in this order in
# the catalog.  Group by size (tiny → large → turbo → distil) so the
# user sees the most relevant models first.

MODEL_REGISTRY: dict[str, ModelMetadata] = {
    # ── Standard Whisper variants (Systran/faster-whisper-*) ──────
    "tiny.en": ModelMetadata(
        name="tiny.en",
        download_size_mb=75,
        required_vram_mb=512,
        backend="whisper",
        multilingual=False,
        supported_languages=["en"],
        description="Fastest English-only model. Low accuracy, good for testing.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-tiny.en",
        speed_rating="fast",
        accuracy_rating="low",
    ),
    "tiny": ModelMetadata(
        name="tiny",
        download_size_mb=75,
        required_vram_mb=512,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Fastest multilingual model. Low accuracy, good for testing.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-tiny",
        speed_rating="fast",
        accuracy_rating="low",
    ),
    "base.en": ModelMetadata(
        name="base.en",
        download_size_mb=150,
        required_vram_mb=512,
        backend="whisper",
        multilingual=False,
        supported_languages=["en"],
        description="Fast English-only model. Reasonable accuracy for short dictation.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-base.en",
        speed_rating="fast",
        accuracy_rating="medium",
    ),
    "base": ModelMetadata(
        name="base",
        download_size_mb=150,
        required_vram_mb=512,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Fast multilingual model. Reasonable accuracy for short dictation.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-base",
        speed_rating="fast",
        accuracy_rating="medium",
    ),
    "small.en": ModelMetadata(
        name="small.en",
        download_size_mb=500,
        required_vram_mb=1024,
        backend="whisper",
        multilingual=False,
        supported_languages=["en"],
        description="Balanced English-only model. Recommended default for English.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-small.en",
        speed_rating="medium",
        accuracy_rating="high",
    ),
    "small": ModelMetadata(
        name="small",
        download_size_mb=500,
        required_vram_mb=1024,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Balanced multilingual model. Good accuracy for most languages.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-small",
        speed_rating="medium",
        accuracy_rating="high",
    ),
    "medium.en": ModelMetadata(
        name="medium.en",
        download_size_mb=1500,
        required_vram_mb=2048,
        backend="whisper",
        multilingual=False,
        supported_languages=["en"],
        description="High-accuracy English-only model. Slower; benefits from GPU.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-medium.en",
        speed_rating="slow",
        accuracy_rating="high",
    ),
    "medium": ModelMetadata(
        name="medium",
        download_size_mb=1500,
        required_vram_mb=2048,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="High-accuracy multilingual model. Slower; benefits from GPU.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-medium",
        speed_rating="slow",
        accuracy_rating="high",
    ),
    "large-v1": ModelMetadata(
        name="large-v1",
        download_size_mb=3000,
        required_vram_mb=4096,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Original large-v1 model. Superseded by large-v3; kept for reproducibility.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v1",
        speed_rating="slow",
        accuracy_rating="high",
    ),
    "large-v2": ModelMetadata(
        name="large-v2",
        download_size_mb=3000,
        required_vram_mb=4096,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Large-v2 model. Superseded by large-v3; kept for reproducibility.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v2",
        speed_rating="slow",
        accuracy_rating="high",
    ),
    "large-v3": ModelMetadata(
        name="large-v3",
        download_size_mb=3000,
        required_vram_mb=4096,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Highest-accuracy Whisper model. Slow on CPU; GPU strongly recommended.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v3",
        speed_rating="slow",
        accuracy_rating="high",
    ),
    "large": ModelMetadata(
        name="large",
        download_size_mb=3000,
        required_vram_mb=4096,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Alias for the latest large model (currently large-v3).",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large",
        speed_rating="slow",
        accuracy_rating="high",
    ),
    # Turbo () ─────────────────────────────────────
    # ``large-v3-turbo`` is OpenAI's 2024 fast multilingual model:
    # near-large-v3 accuracy at ~8x speed.  ``turbo`` is an alias
    # that resolves to the same HuggingFace repo.
    "large-v3-turbo": ModelMetadata(
        name="large-v3-turbo",
        download_size_mb=809,
        required_vram_mb=2000,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Turbo model — near-large-v3 accuracy at 8x speed. Recommended for most users.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v3-turbo",
        speed_rating="fast",
        accuracy_rating="high",
    ),
    "turbo": ModelMetadata(
        name="turbo",
        download_size_mb=809,
        required_vram_mb=2000,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Alias for large-v3-turbo. Same model, friendlier name.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-whisper-large-v3-turbo",
        speed_rating="fast",
        accuracy_rating="high",
    ),
    # Distil-Whisper variants () ──────────────────
    # Distilled models from the Distil-Whisper project: 2-4x faster
    # inference, ~50% smaller, slightly lower accuracy.  Use when
    # speed matters more than edge-case accuracy.
    "distil-large-v3": ModelMetadata(
        name="distil-large-v3",
        download_size_mb=1500,
        required_vram_mb=3000,
        backend="distil-whisper",
        multilingual=True,
        supported_languages=None,
        description="Distilled large-v3. ~2x faster, ~50% smaller; minor accuracy loss.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-distil-whisper-large-v3",
        is_distilled=True,
        speed_rating="fast",
        accuracy_rating="high",
    ),
    "distil-medium.en": ModelMetadata(
        name="distil-medium.en",
        download_size_mb=780,
        required_vram_mb=2048,
        backend="distil-whisper",
        multilingual=False,
        supported_languages=["en"],
        description="Distilled English-only medium. Fast and compact; great for laptops.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="Systran/faster-distil-whisper-medium.en",
        is_distilled=True,
        speed_rating="fast",
        accuracy_rating="medium",
    ),
    # ── Parakeet (by NVIDIA) ──────────────────────────────────────
    # added to registry so get_model_status() can resolve the
    # repo_id and check HF cache download status. Previously the model
    # was only hardcoded in service.py / tray_models.py, causing status
    # checks to fail silently (returning downloaded=false even when the
    # model was fully cached).
    "parakeet": ModelMetadata(
        name="parakeet",
        download_size_mb=1275,
        required_vram_mb=3072,
        backend="parakeet",
        multilingual=True,
        supported_languages=None,
        description="NVIDIA Parakeet TDT 0.6b v3 — ONNX fp16 export (visuall), fast CPU/GPU ASR without PyTorch.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="visuall/parakeet-tdt-0.6b-v3-onnx-fp16",
        speed_rating="fast",
        accuracy_rating="high",
    ),
    # ── Qwen (by Alibaba) ─────────────────────────────────────────
    # added to registry for status consistency. Qwen uses a
    # different download mechanism — the user must manually configure
    # ``qwen_model_path`` in Settings (pointing at a local snapshot of
    # ``Qwen/Qwen3-``). The repo_id below is informational
    # only; it is NOT auto-fetched. ``_check_qwen_deps()`` verifies
    # the configured path exists locally before the engine loads.
    #
    # previously the description claimed
    # "Auto-downloaded on first use", which was inaccurate — the
    # engine does not auto-download. Corrected to "Requires manual
    # model path setup in Settings" so the user is not misled into
    # expecting a transparent first-use download.
    "qwen": ModelMetadata(
        name="qwen",
        download_size_mb=0,  # local-only — size depends on user-supplied snapshot
        required_vram_mb=4096,
        backend="qwen",
        multilingual=True,
        supported_languages=None,
        description="Alibaba Qwen3-ASR-1.7B (ONNX) — multilingual ASR via "
        "onnxruntime. Requires manual model path setup in Settings "
        "(pre-exported ONNX dir, see PLAN_ONNX_INTEGRATION.md §4.3 C-2).",
        network_behavior="local-only",
        # The pre-exported ONNX repo (torch-free, 2026-08-15) — the old
        # torch ``Qwen/Qwen-Audio`` repo_id was removed with the torch
        # engine.
        repo_id="andrewleech/qwen3-asr-1.7b-onnx",
        speed_rating="medium",
        accuracy_rating="high",
    ),
}


def get_model_metadata(model_size: str) -> ModelMetadata | None:
    """Return metadata for ``model_size`` or ``None`` if unknown.

    Safe to call with any string — never raises.  Used by
    :meth:`VoiceTyperService.download_model` to resolve the HF repo_id
    without hard-coding a separate name→repo map.
    """
    return MODEL_REGISTRY.get(model_size)


def get_all_models() -> list[ModelMetadata]:
    """Return all registered models in registry order.

    The Models page renders the catalog in this order (tiny → large →
    turbo → distil).
    """
    return list(MODEL_REGISTRY.values())


def get_models_by_backend(backend: str) -> list[ModelMetadata]:
    """Return only models whose ``backend`` matches ``backend``.

    Used by the Models page to filter by backend (e.g. show only
    distilled variants).  Returns an empty list if no models match.
    """
    return [m for m in MODEL_REGISTRY.values() if m.backend == backend]


# the canonical allowlist of model names a user is
# permitted to select (onboarding picker + Config.load() validation +
# Settings page model selector). Exposed as a function (not a module
# constant) so callers always see the current registry state — if a
# model is added to ``MODEL_REGISTRY`` in a future PR, this allowlist
# updates automatically without needing a parallel hardcoded set.
#
# This is the durable fix for : the pre-fix ``ALLOWED_USER_MODELS``
# hardcoded set in ``config_validators.py`` drifted out of sync with
# ``OnboardingController.MODEL_OPTIONS`` (the onboarding picker offered
# ``"tiny"``, ``"small"``, ``"medium"`` multilingual variants, but the
# allowlist only contained the ``.en`` English-only variants — so
# Config.load() silently reset non-English users to ``"small.en"`` on
# every restart). The allowlist now lives here next to the registry;
# ``config_validators.ALLOWED_USER_MODELS`` can be populated from it,
# and the ``tests/test_allowed_user_models.py`` regression test
# asserts every name in ``OnboardingController.MODEL_OPTIONS`` is in
# this set.
#
# The function returns a ``frozenset`` so callers can cheaply do
# ``name in get_user_selectable_model_names()`` membership checks
# without re-traversing the registry on each call site. The set is
# rebuilt on every call (cheap — ~20 entries) so it always reflects
# the current registry state.
def get_user_selectable_model_names() -> frozenset[str]:
    """Return the set of model names a user is allowed to select.

    This is the single source of truth for ``ALLOWED_USER_MODELS`` —
    onboarding, config validation, and the Settings model selector all
    derive from this set so they can never drift out of sync.

    Currently returns every name in ``MODEL_REGISTRY`` (every entry is
    user-selectable). If a future PR adds internal-only models (e.g. a
    test fixture or an experimental backend not yet exposed in the UI),
    add a ``user_selectable: bool = True`` field to ``ModelMetadata``
    and filter here — but for now all registry entries are user-facing.
    """
    return frozenset(MODEL_REGISTRY.keys())


__all__ = [
    "ModelMetadata",
    "MODEL_REGISTRY",
    "get_model_metadata",
    "get_all_models",
    "get_models_by_backend",
    "get_user_selectable_model_names",
]
