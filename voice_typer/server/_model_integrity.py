"""Shared model-integrity constants — SEC-audit-005 / CRIT-5 / SEC-2.

Single source of truth for the file-pattern allow-list used by both
``parakeet_engine.py`` (download + verify path) and ``asr_setup.py``
(parakeet weight downloader).  Keeping the allow-list in one module
prevents the two old copies (``_PARAKEET_ALLOW_PATTERNS`` in
``parakeet_engine.py`` and ``_HF_ALLOW_PATTERNS`` in ``asr_setup.py``)
from drifting out of sync.

CRIT-5 / SEC-2 root cause: the manifest in ``model_hashes.json`` pinned
hashes for files that this allow-list omits (``.gitattributes``,
``README.md``, ``plots/asr.png``, ``.eval_results/open_asr_leaderboard.yaml``,
``parakeet-tdt-0.6b-v3.nemo``, ``processor_config.json``).
``verify_model_integrity()`` hard-fails if any pinned file is missing
from the downloaded snapshot, so every Parakeet download failed
verification — which combined with CRIT-4 (load-on-warning) meant the
supply-chain gate was effectively disabled.

IMPORTANT: this allow-list MUST stay in sync with the ``files`` dict
in ``model_hashes.json``.  When adding a new file pattern here, also
add its SHA-256 to ``model_hashes.json``; when removing a pattern,
remove the corresponding manifest entry.  The
``test_model_hashes_have_pinned_config_json`` regression test catches
the most common drift (config.json going missing); broader drift is
caught at runtime by ``verify_model_integrity()`` returning False.
"""

# SEC-audit-005: Allowlist of file patterns permitted in HuggingFace
# model downloads.  Prevents supply-chain attacks where a compromised
# HF repo could include executables, scripts, or other unexpected files
# that ``snapshot_download`` would otherwise pull into the local cache
# (and that ``verify_model_integrity`` would then either pin or have to
# ignore).
#
# Patterns are matched by ``fnmatch`` (HuggingFace's ``allow_patterns``
# argument uses ``fnmatch.filter``).  ``*.safetensors`` matches any
# top-level ``.safetensors`` file (e.g. ``model.safetensors`` and the
# shard files ``model-00001-of-00003.safetensors``).
ALLOW_PATTERNS: list[str] = [
    "*.safetensors",
    "*.bin",
    "config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "preprocessor_config.json",
    "feature_extractor_config.json",
    "generation_config.json",
    "model.safetensors.index.json",
    "*.model",
]
