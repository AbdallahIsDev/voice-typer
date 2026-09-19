"""Parakeet engine constants (verbatim from the original module)."""

from __future__ import annotations

from voice_typer.server.asr_utils import (
    MAX_BOUNDARY_SKIP_WORDS as _MAX_BOUNDARY_SKIP_WORDS,  # noqa: F401, re-exported alias
    NON_LATIN_RATIO_LIMIT as _NON_LATIN_RATIO_LIMIT,  # noqa: F401, re-exported alias
    OVERLAP_DEDUP_WINDOW as _OVERLAP_DEDUP_WINDOW,  # noqa: F401, re-exported alias
)

# The three imports above are the backward-compat re-export surface of


# (``_NON_LATIN_RATIO_LIMIT``: the maximum allowed ratio of non-Latin-

# HuggingFace repo ID of the *original* torch/safetensors Parakeet
_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# ONNX Runtime FP16 export of Parakeet TDT v3 (USER-selected repo,
_PARAKERT_ONNX_REPO_ID = "grikdotnet/parakeet-tdt-0.6b-fp16"
_PARAKERT_ONNX_CACHE_DIR = f"models--{_PARAKERT_ONNX_REPO_ID.replace('/', '--')}"

# onnx-asr TYPE name (NOT a repo name). ``nemo-conformer-tdt`` selects
_PARAKERT_ONNX_MODEL_NAME = "nemo-conformer-tdt"

# Selects the ``.fp16.`` variant files inside the repo (onnx-asr 0.12.0
_PARAKERT_QUANTIZATION = "fp16"

# Approximate ONNX weight size in MB for MB/s read-speed logging.
_PARAKERT_WEIGHTS_MB = 1275

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 3

# Backward-compat re-exports of the merge-chunk constants. The canonical
