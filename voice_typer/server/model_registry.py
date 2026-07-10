"""Metadata registry for all supported ASR models.

NEW-MODEL-001: This module is the single source of truth for *rich*
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

from dataclasses import dataclass, asdict
from typing import Optional


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
    supported_languages: Optional[list[str]]  # None = all languages
    description: str
    repo_id: str
    is_distilled: bool = False
    speed_rating: str = "medium"  # "fast", "medium", "slow"
    accuracy_rating: str = "high"  # "low", "medium", "high"

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
        repo_id="Systran/faster-whisper-large",
        speed_rating="slow",
        accuracy_rating="high",
    ),

    # ── Turbo (NEW-MODEL-001) ─────────────────────────────────────
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
        repo_id="Systran/faster-whisper-large-v3-turbo",
        speed_rating="fast",
        accuracy_rating="high",
    ),

    # ── Distil-Whisper variants (NEW-MODEL-001) ──────────────────
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
        repo_id="Systran/faster-distil-whisper-medium.en",
        is_distilled=True,
        speed_rating="fast",
        accuracy_rating="medium",
    ),

    # ── Parakeet (by NVIDIA) ──────────────────────────────────────
    # ARCH-007: added to registry so get_model_status() can resolve the
    # repo_id and check HF cache download status. Previously the model
    # was only hardcoded in service.py / tray_models.py, causing status
    # checks to fail silently (returning downloaded=false even when the
    # model was fully cached).
    "parakeet": ModelMetadata(
        name="parakeet",
        download_size_mb=2500,
        required_vram_mb=4096,
        backend="parakeet",
        multilingual=True,
        supported_languages=None,
        description="NVIDIA Parakeet TDT 0.6b — high-accuracy ASR model.",
        repo_id="nvidia/parakeet-tdt-0.6b-v3",
        speed_rating="fast",
        accuracy_rating="high",
    ),

    # ── Qwen (by Alibaba) ─────────────────────────────────────────
    # ARCH-007: added to registry for status consistency. Qwen uses a
    # different download mechanism (auto-downloads from HuggingFace on
    # first use), so repo_id is informational. The download status is
    # checked via config.qwen_model_path or _check_qwen_deps().
    "qwen": ModelMetadata(
        name="qwen",
        download_size_mb=0,  # auto-downloaded, size varies
        required_vram_mb=4096,
        backend="qwen",
        multilingual=True,
        supported_languages=None,
        description="Alibaba Qwen — multilingual ASR. Auto-downloaded on first use.",
        repo_id="Qwen/Qwen-Audio",
        speed_rating="medium",
        accuracy_rating="high",
    ),
}


def get_model_metadata(model_size: str) -> Optional[ModelMetadata]:
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


__all__ = [
    "ModelMetadata",
    "MODEL_REGISTRY",
    "get_model_metadata",
    "get_all_models",
    "get_models_by_backend",
]
