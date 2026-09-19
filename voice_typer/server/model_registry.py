"""Metadata registry for all supported ASR models."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ModelMetadata:
    """Rich metadata for a single ASR model variant."""

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
    # Published WER (%) on LibriSpeech test-clean, sourced per entry
    wer: float | None = None
    # declares what network activity the model requires, so the
    network_behavior: str = "local-only"
    # User-facing display name shown on the model card. ``None`` means
    display_name: str | None = None

    def to_dict(self) -> dict:
        """Serialize to a JSON-safe dict for IPC transport."""
        return asdict(self)


# Order matters for the Models page: entries appear in this order in

# Sentinel for "no model selected": the config's ``model_size`` can be
NO_MODEL_SIZE: str = ""

# The canonical DEFAULT model size. THIS IS DELIBERATELY "no model
DEFAULT_MODEL_SIZE: str = NO_MODEL_SIZE

MODEL_REGISTRY: dict[str, ModelMetadata] = {
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
        # WER 7.5% on LibriSpeech test-clean, self-reported in the
        wer=7.5,
    ),
    # ``large-v3``: highest-accuracy multilingual Whisper. Restored
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
        # WER 2.0% on LibriSpeech test-clean, published Whisper
        wer=2.0,
    ),
    # ``large-v3-turbo`` is OpenAI's 2024 fast multilingual model:
    "large-v3-turbo": ModelMetadata(
        name="large-v3-turbo",
        download_size_mb=809,
        required_vram_mb=2000,
        backend="whisper",
        multilingual=True,
        supported_languages=None,
        description="Turbo model, near-large-v3 accuracy at 8x speed. Recommended for most users.",
        network_behavior="downloads-on-first-use-consent-gated",
        # faster-whisper 1.2.1 _MODELS maps "large-v3-turbo"/"turbo" to
        repo_id="mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        speed_rating="fast",
        accuracy_rating="high",
        # WER 2.1% on LibriSpeech test-clean, published benchmark
        wer=2.1,
    ),
    # added to registry so get_model_status() can resolve the
    "parakeet": ModelMetadata(
        name="parakeet",
        display_name="Parakeet-TDT-0.6b-V3",
        download_size_mb=1275,
        required_vram_mb=3072,
        backend="parakeet",
        multilingual=True,
        supported_languages=None,
        description="NVIDIA Parakeet TDT 0.6b v3. ONNX fp16 export (grikdotnet), fast CPU/GPU ASR without PyTorch.",
        network_behavior="downloads-on-first-use-consent-gated",
        repo_id="grikdotnet/parakeet-tdt-0.6b-fp16",
        speed_rating="fast",
        accuracy_rating="high",
        # WER 1.93% on LibriSpeech test-clean, self-reported in the
        wer=1.93,
    ),
    # added to registry for status consistency. Qwen uses a
    "qwen": ModelMetadata(
        name="qwen",
        display_name="Qwen-3",
        download_size_mb=0,  # local-only, size depends on user-supplied snapshot
        required_vram_mb=4096,
        backend="qwen",
        multilingual=True,
        supported_languages=None,
        description="Alibaba Qwen3-ASR-1.7B (ONNX), multilingual ASR via "
        "onnxruntime. Requires manual model path setup in Settings "
        "(pre-exported ONNX dir, see PLAN_ONNX_INTEGRATION.md §4.3 C-2).",
        network_behavior="local-only",
        # The pre-exported ONNX repo (torch-free, 2026-08-15), the old
        repo_id="andrewleech/qwen3-asr-1.7b-onnx",
        speed_rating="medium",
        accuracy_rating="high",
        # WER 1.63% on LibriSpeech test-clean, from the official
        wer=1.63,
    ),
}


def get_model_metadata(model_size: str) -> ModelMetadata | None:
    """Return metadata for ``model_size`` or ``None`` if unknown."""
    return MODEL_REGISTRY.get(model_size)


def get_all_models() -> list[ModelMetadata]:
    """Return all registered models in registry order."""
    return list(MODEL_REGISTRY.values())


__all__ = [
    "ModelMetadata",
    "MODEL_REGISTRY",
    "DEFAULT_MODEL_SIZE",
    "NO_MODEL_SIZE",
    "get_model_metadata",
    "get_all_models",
]
