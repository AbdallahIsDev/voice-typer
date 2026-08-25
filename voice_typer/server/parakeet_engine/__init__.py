"""Parakeet TDT v3 ASR engine package.

Facade preserving the historical ``voice_typer.server.parakeet_engine``
import path (also the dotted string in ``asr_registry._BACKEND_SPECS``)."""

from ._constants import (
    _CHUNK_OVERLAP_SECONDS,
    _CHUNK_SECONDS,
    _MAX_BOUNDARY_SKIP_WORDS,
    _NON_LATIN_RATIO_LIMIT,
    _OVERLAP_DEDUP_WINDOW,
    _PARAKERT_MODEL_ID,
    _PARAKERT_ONNX_CACHE_DIR,
    _PARAKERT_ONNX_MODEL_NAME,
    _PARAKERT_ONNX_REPO_ID,
    _PARAKERT_QUANTIZATION,
    _PARAKERT_WEIGHTS_MB,
)
from ._helpers import (
    _ASR_UTILS_HELPERS_AVAILABLE,
    _is_latin_char,
    _is_likely_english,
)
from ._shims import TranscriptionBackendError, _AbortStoppingCriteria
from .engine import ParakeetEngine

__all__ = [
    "ParakeetEngine",
    "TranscriptionBackendError",
    "_AbortStoppingCriteria",
    "_is_latin_char",
    "_is_likely_english",
    "_NON_LATIN_RATIO_LIMIT",
    "_PARAKERT_MODEL_ID",
    "_PARAKERT_ONNX_REPO_ID",
    "_PARAKERT_ONNX_CACHE_DIR",
    "_PARAKERT_ONNX_MODEL_NAME",
    "_PARAKERT_QUANTIZATION",
    "_PARAKERT_WEIGHTS_MB",
    "_CHUNK_SECONDS",
    "_CHUNK_OVERLAP_SECONDS",
    "_MAX_BOUNDARY_SKIP_WORDS",
    "_OVERLAP_DEDUP_WINDOW",
    "_ASR_UTILS_HELPERS_AVAILABLE",
]
