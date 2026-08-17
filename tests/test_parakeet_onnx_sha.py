"""SHA-256 manifest verification for the Parakeet ONNX model files.

Verifies:

1. ``ALLOW_PATTERNS_PARAKEET_ONNX`` exists in
   ``voice_typer.server.security.model_integrity`` and contains the
   patterns required for the ONNX backend (``*.onnx`` + tokenizer
   + config JSONs) — see PLAN_ONNX_INTEGRATION.md §3.5.4.
2. The ``model_hashes.json`` manifest entry for ``nvidia/parakeet-tdt-0.6b-v3``
   has the expected schema (``revision`` = 40-char SHA, ``files`` dict
   of ``{relative_path: sha256_hex}``).
3. Every pinned SHA is a valid 64-char lowercase hex string.
4. (When the model is downloaded) ``verify_model_integrity`` returns
   True for the cached snapshot.

These tests do NOT require ``onnx_asr`` to be installed — they test
the JSON manifest schema and the allowlist constant directly. The
optional ``pytest.importorskip("onnx_asr")`` guard is on the parity
download-verification class only.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

# NOTE: no module-level ``pytest.importorskip("onnx_asr")`` — these
# tests verify the JSON manifest schema + the allowlist constant, which
# are independent of whether onnx_asr is installed. The download-
# verification class uses importorskip (it needs the real model).
from voice_typer.server.security.model_integrity import (  # noqa: E402
    ALLOW_PATTERNS_PARAKEET,
    ALLOW_PATTERNS_PARAKEET_ONNX,
    ALLOW_PATTERNS_WHISPER,
    verify_model_integrity,
)

_MANIFEST_PATH = Path(__file__).resolve().parent.parent / "voice_typer" / "server" / "model_hashes.json"
_PARAKEET_REPO_ID = "nvidia/parakeet-tdt-0.6b-v3"
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_HF_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


# ─── ALLOW_PATTERNS_PARAKEET_ONNX ──────────────────────────────────────


class TestAllowPatternsParakeetOnnx:
    """``ALLOW_PATTERNS_PARAKEET_ONNX`` — the new ONNX allowlist (§3.5.4)."""

    def test_constant_exists_and_is_frozenset(self):
        """The constant must exist and be a ``frozenset`` (immutable,
        matches the convention documented in §3.5.4)."""
        assert ALLOW_PATTERNS_PARAKEET_ONNX is not None, (
            "ALLOW_PATTERNS_PARAKEET_ONNX must be defined per PLAN_ONNX_INTEGRATION.md §3.5.4."
        )
        assert isinstance(ALLOW_PATTERNS_PARAKEET_ONNX, frozenset), (
            f"ALLOW_PATTERNS_PARAKEET_ONNX must be a frozenset (got "
            f"{type(ALLOW_PATTERNS_PARAKEET_ONNX).__name__}). The plan §3.5.4 "
            f"specifies frozenset to document immutability + dedup intent."
        )

    def test_includes_onnx_glob(self):
        """``*.onnx`` must be in the allowlist — the ONNX encoder /
        decoder / joint graphs ship as ``.onnx`` files."""
        assert "*.onnx" in ALLOW_PATTERNS_PARAKEET_ONNX, (
            "*.onnx must be in ALLOW_PATTERNS_PARAKEET_ONNX — the ONNX "
            "encoder/decoder/joint graphs ship as .onnx files."
        )

    def test_includes_required_json_configs(self):
        """The TDT decoding loop needs tokenizer + config files. The
        minimum set per §3.5.4:

        - ``config.json`` (model architecture config)
        - ``tokenizer.json`` (token-ID → text mapping)
        - ``vocab.txt`` (vocabulary, used by some tokenizers)
        - ``special_tokens_map.json`` (special token IDs)
        - ``generation_config.json`` (generation parameters)
        """
        required = {
            "config.json",
            "tokenizer.json",
            "vocab.txt",
            "special_tokens_map.json",
            "generation_config.json",
        }
        missing = required - ALLOW_PATTERNS_PARAKEET_ONNX
        assert not missing, (
            f"ALLOW_PATTERNS_PARAKEET_ONNX is missing required patterns: "
            f"{sorted(missing)}. PLAN_ONNX_INTEGRATION.md §3.5.4 specifies "
            f"these 5 JSON files + *.onnx."
        )

    def test_does_not_include_bin_or_safetensors(self):
        """The ONNX allowlist must NOT include ``*.bin`` (pickle RCE
        vector) or ``*.safetensors`` (torch format, not used by the
        ONNX backend). The old ``ALLOW_PATTERNS_PARAKEET`` keeps those
        for the torch/safetensors cache layout — the ONNX constant is
        a separate, narrower allowlist."""
        assert "*.bin" not in ALLOW_PATTERNS_PARAKEET_ONNX, (
            "*.bin must NOT be in the ONNX allowlist — it's a pickle RCE "
            "vector and the ONNX backend doesn't use torch checkpoints."
        )
        assert "*.safetensors" not in ALLOW_PATTERNS_PARAKEET_ONNX, (
            "*.safetensors must NOT be in the ONNX allowlist — the ONNX "
            "backend uses .onnx files, not torch/safetensors weights."
        )

    def test_old_allow_patterns_parakeet_still_exists(self):
        """The pre-migration ``ALLOW_PATTERNS_PARAKEET`` (safetensors-
        based) must still exist — it covers the torch/safetensors cache
        layout that pre-ONNX-migration users have downloaded. The ONNX
        migration adds ``ALLOW_PATTERNS_PARAKEET_ONNX`` alongside, NOT
        as a replacement."""
        assert ALLOW_PATTERNS_PARAKEET is not None, (
            "ALLOW_PATTERNS_PARAKEET (the pre-migration safetensors-based "
            "allowlist) must still exist — it covers the torch/safetensors "
            "cache layout that pre-ONNX-migration users have downloaded."
        )
        assert "*.safetensors" in ALLOW_PATTERNS_PARAKEET, (
            "ALLOW_PATTERNS_PARAKEET must keep *.safetensors for the pre-ONNX-migration cache layout."
        )

    def test_whisper_allowlist_unchanged(self):
        """Sanity: ``ALLOW_PATTERNS_WHISPER`` is unchanged (the ONNX
        migration doesn't touch Whisper — it already uses ctranslate2)."""
        assert "*.bin" in ALLOW_PATTERNS_WHISPER, (
            "ALLOW_PATTERNS_WHISPER must keep *.bin — CTranslate2 loads model.bin (unchanged by the ONNX migration)."
        )


# ─── model_hashes.json manifest schema ─────────────────────────────────


class TestModelHashesManifest:
    """Schema verification for ``model_hashes.json`` (parakeet entry)."""

    @pytest.fixture(scope="class")
    @classmethod
    def manifest(cls):
        """Load the model_hashes.json manifest once per test class."""
        with _MANIFEST_PATH.open("r", encoding="utf-8") as f:
            return json.load(f)

    def test_manifest_file_exists(self):
        """The manifest file must exist at the canonical path."""
        assert _MANIFEST_PATH.is_file(), (
            f"model_hashes.json not found at {_MANIFEST_PATH}. "
            f"SEC-audit-005 requires the manifest for supply-chain integrity."
        )

    def test_parakeet_entry_exists(self, manifest):
        """The ``nvidia/parakeet-tdt-0.6b-v3`` entry must exist."""
        assert _PARAKEET_REPO_ID in manifest, (
            f"Manifest must have an entry for {_PARAKEET_REPO_ID}. Got top-level keys: {sorted(manifest.keys())}"
        )

    def test_parakeet_entry_has_valid_revision(self, manifest):
        """The ``revision`` field must be a 40-char HuggingFace commit SHA."""
        entry = manifest[_PARAKEET_REPO_ID]
        assert isinstance(entry, dict), f"Entry must be a dict, got {type(entry).__name__}"
        revision = entry.get("revision")
        assert isinstance(revision, str) and _HF_COMMIT_SHA_RE.match(revision), (
            f"revision must be a 40-char lowercase hex SHA (got {revision!r}). "
            f"This pins the immutable HuggingFace commit for supply-chain integrity."
        )

    def test_parakeet_entry_has_files_dict(self, manifest):
        """The ``files`` dict must exist and be non-empty."""
        entry = manifest[_PARAKEET_REPO_ID]
        files = entry.get("files")
        assert isinstance(files, dict), f"files must be a dict, got {type(files).__name__}"
        assert len(files) > 0, (
            "files dict must be non-empty — empty files dict triggers the "
            "soft-pass branch in verify_model_integrity (SEC-audit-005 warning)."
        )

    def test_parakeet_pinned_shas_are_valid_hex(self, manifest):
        """Every pinned SHA must be a 64-char lowercase hex string."""
        files = manifest[_PARAKEET_REPO_ID]["files"]
        invalid = []
        for path, sha in files.items():
            if not (isinstance(sha, str) and _SHA256_HEX_RE.match(sha)):
                invalid.append((path, sha))
        assert not invalid, f"Invalid SHA-256 entries in parakeet manifest (must be 64-char lowercase hex): {invalid}"

    def test_parakeet_manifest_pins_model_safetensors(self, manifest):
        """The manifest must pin ``model.safetensors`` — the primary
        model weight file. Without this pin, a tampered weight file
        would load unchecked."""
        files = manifest[_PARAKEET_REPO_ID]["files"]
        assert "model.safetensors" in files, (
            "model.safetensors must be pinned in the parakeet manifest — "
            "it's the primary model weight file. Without the pin, "
            "verify_model_integrity cannot detect a tampered weight file."
        )

    def test_parakeet_manifest_pins_config_json(self, manifest):
        """The manifest must pin ``config.json`` — the model architecture
        config. A tampered config could redirect the model to load
        different weights."""
        files = manifest[_PARAKEET_REPO_ID]["files"]
        assert "config.json" in files, "config.json must be pinned in the parakeet manifest."

    def test_parakeet_manifest_pins_tokenizer_json(self, manifest):
        """The manifest must pin ``tokenizer.json`` — a tampered
        tokenizer could leak transcribed text via malicious token mappings."""
        files = manifest[_PARAKEET_REPO_ID]["files"]
        assert "tokenizer.json" in files, "tokenizer.json must be pinned in the parakeet manifest."


# ─── Downloaded-model verification (skips if model not cached) ─────────


class TestParakeetOnnxDownloadedModelSha:
    """Verify a DOWNLOADED Parakeet model matches the manifest.

    Skips if the model is not in the local HF cache (these tests need
    a real downloaded model — they don't mock the file system).
    Requires ``onnx_asr`` to be installed (the engine's cache probe
    uses it).
    """

    def test_downloaded_model_matches_manifest(self):
        """If the Parakeet model is downloaded, ``verify_model_integrity``
        must return True for the cached snapshot (every pinned SHA
        matches the local file)."""
        pytest.importorskip("onnx_asr")
        pytest.importorskip("onnxruntime")

        from voice_typer.server.config import _config_dir
        from voice_typer.server.parakeet_engine import ParakeetEngine

        if not ParakeetEngine._is_cached():
            pytest.skip("Parakeet model not in HF cache — download via Models page first")

        cache_root = _config_dir() / "huggingface" / "hub"
        model_dir = cache_root / f"models--{_PARAKEET_REPO_ID.replace('/', '--')}"
        snapshots = model_dir / "snapshots"
        if not snapshots.is_dir():
            pytest.skip(f"No snapshots dir at {snapshots}")

        verified = False
        last_exc: Exception | None = None
        for snapshot in snapshots.iterdir():
            if not snapshot.is_dir():
                continue
            try:
                if verify_model_integrity(str(snapshot), _PARAKEET_REPO_ID):
                    verified = True
                    break
            except Exception as exc:
                last_exc = exc
        assert verified, (
            f"verify_model_integrity failed for all snapshots in {snapshots}. "
            f"Last exception: {last_exc}. The cached model may be tampered — "
            f"delete it and re-download from the Models page."
        )


# ─── SHA-256 helper tests ──────────────────────────────────────────────


class TestSha256HexRegex:
    """Sanity tests for the SHA-256 hex regex used above."""

    def test_valid_sha256_matches(self):
        valid = "e747b85e1bdfd300c8b8ac63bac8dd5221f8fe9bc275b48d06c735fcd6971b6e"
        assert _SHA256_HEX_RE.match(valid)

    def test_uppercase_hex_does_not_match(self):
        upper = "E747B85E1BDFD300C8B8AC63BAC8DD5221F8FE9BC275B48D06C735FCD6971B6E"
        assert not _SHA256_HEX_RE.match(upper)

    def test_short_hex_does_not_match(self):
        short = "e747b85e1bdfd300"
        assert not _SHA256_HEX_RE.match(short)

    def test_non_hex_does_not_match(self):
        non_hex = "g" * 64
        assert not _SHA256_HEX_RE.match(non_hex)

    def test_real_sha256_of_known_string(self):
        """Sanity: hashlib.sha256(b'hello').hexdigest() matches the regex."""
        digest = hashlib.sha256(b"hello").hexdigest()
        assert _SHA256_HEX_RE.match(digest), f"hashlib.sha256 output didn't match the regex: {digest}"
