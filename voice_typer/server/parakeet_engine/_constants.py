"""Parakeet engine constants (verbatim from the original module)."""

from __future__ import annotations

# ─── Constants ──────────────────────────────────────────────────────────

# Maximum allowed ratio of non-Latin-script characters before we reject
# a transcription segment as a language-hallucination. Re-exported from
# asr_utils for backward-compat with tests that import the constant from
# parakeet_engine. See ``asr_utils.NON_LATIN_RATIO_LIMIT``.
_NON_LATIN_RATIO_LIMIT = 0.30

# HuggingFace repo ID of the *original* torch/safetensors Parakeet
# model. Kept as a module-level constant because ``prewarm/cache_probe``
# imports it to locate the cached ``model.safetensors`` for OS page-cache
# warming. The ONNX migration does NOT change this — prewarm still warms
# the same HF cache directory (the user may have either the torch or
# ONNX weights cached; both live under the same repo-id key).
_PARAKERT_MODEL_ID = "nvidia/parakeet-tdt-0.6b-v3"

# ONNX Runtime FP16 export of Parakeet TDT v3 (USER-selected repo,
# 2026-08-15, switched to the upstream original 2026-08-20).
# ``grikdotnet/parakeet-tdt-0.6b-fp16`` is the original half-precision
# conversion of the fp32 ONNX export published by
# ``istupakov/parakeet-tdt-0.6b-v3-onnx`` (the earlier
# ``visuall/parakeet-tdt-0.6b-v3-onnx-fp16`` was a copy of the same
# files minus config.json); identical WER to fp32 at ~1.28 GB instead
# of ~2.5 GB (see the repo's README). The repo ships a real
# ``config.json``, but onnx-asr reads ``model_type`` from it only when
# resolving a repo BY NAME — the engine still loads by TYPE name + a
# verified local snapshot dir (see ``load()``).
_PARAKERT_ONNX_REPO_ID = "grikdotnet/parakeet-tdt-0.6b-fp16"
_PARAKERT_ONNX_CACHE_DIR = f"models--{_PARAKERT_ONNX_REPO_ID.replace('/', '--')}"

# onnx-asr TYPE name (NOT a repo name). ``nemo-conformer-tdt`` selects
# the TDT decoder class directly, which is what lets us load the
# verified local snapshot dir (the integrity gate has already pinned
# every file). Do NOT pass the grikdotnet repo_id as the model name —
# onnx-asr would try to download from the repo instead of loading the
# verified local dir.
_PARAKERT_ONNX_MODEL_NAME = "nemo-conformer-tdt"

# Selects the ``.fp16.`` variant files inside the repo (onnx-asr 0.12.0
# globs ``encoder-model?fp16.onnx`` — matches ``encoder-model.fp16.onnx``).
_PARAKERT_QUANTIZATION = "fp16"

# Approximate ONNX weight size in MB for MB/s read-speed logging.
# grikdotnet fp16 export: encoder-model.fp16.onnx 1,239 MB +
# decoder_joint-model.fp16.onnx 36 MB + nemo128.onnx + vocab.txt
# ≈ 1,275 MB on disk.
_PARAKERT_WEIGHTS_MB = 1275

# Parakeet's Conformer encoder has a practical limit of ~30s of audio.
# Longer recordings are split into overlapping chunks via
# ``asr_utils.split_audio``. 3s overlap gives the model audio context at
# boundaries so it doesn't hallucinate repeated text at chunk starts.
_CHUNK_SECONDS = 25
_CHUNK_OVERLAP_SECONDS = 3

# Backward-compat re-exports of the merge-chunk constants. The canonical
# values now live in ``asr_utils`` (``MAX_BOUNDARY_SKIP_WORDS``,
# ``OVERLAP_DEDUP_WINDOW``). Kept here so existing tests / importers
# (``tests/test_parakeet_engine.py``, ``tests/regressions/test_parakeet_merge.py``)
# keep working.
_MAX_BOUNDARY_SKIP_WORDS = 2
_OVERLAP_DEDUP_WINDOW = 3
