"""Shared model-integrity constants — SEC-audit-005 / CRIT-5 / SEC-2.

Single source of truth for the file-pattern allow-lists used by both
``parakeet_engine.py`` (download + verify path) and ``asr_setup.py``
(parakeet weight downloader) and ``transcription.py`` (Whisper weight
downloader).  Keeping the allow-lists in one module prevents the
copies in ``parakeet_engine.py``, ``asr_setup.py`` and
``transcription.py`` from drifting out of sync.

CRIT-5 / SEC-2 root cause: the manifest in ``model_hashes.json`` pinned
hashes for files that this allow-list omits (``.gitattributes``,
``README.md``, ``plots/asr.png``, ``.eval_results/open_asr_leaderboard.yaml``,
``parakeet-tdt-0.6b-v3.nemo``, ``processor_config.json``).
``verify_model_integrity()`` hard-fails if any pinned file is missing
from the downloaded snapshot, so every Parakeet download failed
verification — which combined with CRIT-4 (load-on-warning) meant the
supply-chain gate was effectively disabled.

IMPORTANT: these allow-lists MUST stay in sync with the ``files`` dict
in ``model_hashes.json``.  When adding a new file pattern here, also
add its SHA-256 to ``model_hashes.json``; when removing a pattern,
remove the corresponding manifest entry.  The
``test_model_hashes_have_pinned_config_json`` regression test catches
the most common drift (config.json going missing); broader drift is
caught at runtime by ``verify_model_integrity()`` returning False.

(Session 7 — Group 4): the original monolithic
``ALLOW_PATTERNS`` list included ``*.bin`` (a pickle-serialised
PyTorch state-dict) which is a remote-code-execution vector.  Parakeet
ships ``model.safetensors`` only and never needs ``*.bin``; allowing
it created an injection surface where a compromised HF repo could ship
a malicious ``pytorch_model.bin`` that the user would pull into their
local cache (and that ``verify_model_integrity`` would then have to
either pin or ignore).  The list is now split per backend:

- ``ALLOW_PATTERNS_PARAKEET`` — safetensors + config/tokenizer JSONs.
  No ``*.bin``.  Used by ``parakeet_engine.py`` and the Parakeet path
  of ``asr_setup.download_parakeet_weights``.
- ``ALLOW_PATTERNS_WHISPER`` — keeps ``*.bin`` because CTranslate2
  (used by ``faster_whisper``) consumes the ``model.bin`` format
  natively.  Whisper weights are only ever loaded via CTranslate2
  and never via ``torch.load`` (the pickle-vector path), so the
  risk is bounded.  Used by ``transcription.py::_pre_download_model``.

"""

# SEC-audit-005: Allowlist of file patterns permitted in
# HuggingFace Parakeet model downloads.  ``*.bin`` is intentionally
# OMITTED — Parakeet ships ``model.safetensors`` only, and the
# pickle-serialised ``*.bin`` format is a remote-code-execution vector
# if a compromised HF repo were to ship a malicious
# ``pytorch_model.bin``.  ``verify_model_integrity()`` hard-fails if a
# pinned file is missing, so every pattern here must also have a
# corresponding entry in the ``files`` dict of ``model_hashes.json``
# (or the structural check in ``verify_model_integrity()`` will pass
# but the pinned-files check will fail).
#
# Patterns are matched by ``fnmatch`` (HuggingFace's ``allow_patterns``
# argument uses ``fnmatch.filter``).  ``*.safetensors`` matches any
# top-level ``.safetensors`` file (e.g. ``model.safetensors`` and the
# shard files ``model-00001-of-00003.safetensors``).
ALLOW_PATTERNS_PARAKEET: list[str] = [
    "*.safetensors",
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

# SEC-audit-005: Allowlist for HuggingFace Whisper-family
# downloads (``Systran/faster-whisper-*``).  CTranslate2 loads model
# weights from ``model.bin`` — this is the native on-disk format for
# ``faster_whisper.WhisperModel`` and is NOT loaded via
# ``torch.load`` (the pickle-vector path), so the ``*.bin`` risk is
# bounded to "wrong weights → bad transcription" rather than "arbitrary
# code execution".  ``model_hashes.json`` pins the SHA-256 of every
# ``model.bin`` so a tampered file would be detected by
# ``verify_model_integrity()`` before ``WhisperModel.__init__`` is
# called.
ALLOW_PATTERNS_WHISPER: list[str] = [
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
