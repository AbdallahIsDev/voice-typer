"""Tests for PROD-006 / SEC-audit-005: Model integrity verification.

These tests cover two concerns:
1. Structural checks (missing dir, empty dir, no config, empty model file) —
   the original PROD-006 suite.
2. SEC-audit-005 manifest enforcement — every HuggingFace repo must have a
   pinned 40-char commit SHA (NOT the mutable 'main' branch) and at least
   config.json pinned in the files dict, so verify_model_integrity() actually
   enforces file-level integrity rather than silently soft-passing.
"""

import hashlib
import re
import tempfile
from pathlib import Path

# A HuggingFace commit SHA is a 40-char lowercase hex string (Git SHA-1).
# We accept exactly this format and reject 'main' / 'master' / branch names.
_COMMIT_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
# A SHA-256 hex digest is 64 chars lowercase hex.
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _ok(result: tuple) -> bool:
    """``asr_setup._verify_model_integrity`` returns ``(ok, details)``."""
    return bool(result[0])


def test_verify_model_integrity_missing_dir(isolated_integrity_cache):
    """Returns False for non-existent directory."""
    from voice_typer.server.asr_setup import _verify_model_integrity

    assert _ok(_verify_model_integrity("test/model", "/nonexistent/path")) is False


def test_verify_model_integrity_empty_dir(isolated_integrity_cache):
    """Returns False for directory with no model files."""
    from voice_typer.server.asr_setup import _verify_model_integrity

    with tempfile.TemporaryDirectory() as tmp:
        assert _ok(_verify_model_integrity("test/model", tmp)) is False


def test_verify_model_integrity_valid(isolated_integrity_cache):
    """Returns True for directory with model and config files."""
    from voice_typer.server.asr_setup import _verify_model_integrity

    with tempfile.TemporaryDirectory() as tmp:
        # Create a model file
        (Path(tmp) / "model.safetensors").write_bytes(b"\x00" * 100)
        # Create a config file
        (Path(tmp) / "config.json").write_text('{"model_type": "test"}')
        assert _ok(_verify_model_integrity("test/model", tmp)) is True


def test_verify_model_integrity_no_config(isolated_integrity_cache):
    """Returns False for directory with model but no config."""
    from voice_typer.server.asr_setup import _verify_model_integrity

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "model.bin").write_bytes(b"\x00" * 100)
        assert _ok(_verify_model_integrity("test/model", tmp)) is False


def test_verify_model_integrity_empty_model_file(isolated_integrity_cache):
    """Returns False for directory with empty model file."""
    from voice_typer.server.asr_setup import _verify_model_integrity

    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "model.safetensors").write_bytes(b"")
        (Path(tmp) / "config.json").write_text("{}")
        assert _ok(_verify_model_integrity("test/model", tmp)) is False


# ── SEC-audit-005: Manifest enforcement regression tests ──────────────────


def test_model_hashes_revisions_are_pinned_commit_shas():
    """SEC-audit-005: Every HuggingFace repo entry in model_hashes.json must
    have a 40-char hex commit SHA as 'revision' — NOT 'main' or any other
    mutable branch reference.

    A mutable-branch revision defeats the supply-chain protection entirely:
    a compromised HuggingFace repo could push a malicious new commit to
    'main', and Voice Typer would silently download and load it. Pinning to
    an immutable commit SHA guarantees the downloaded model files are
    bit-identical to the audited version.
    """
    from voice_typer.server.security import MODEL_HASHES

    assert MODEL_HASHES, "MODEL_HASHES is empty — manifest failed to load"
    hf_repos = [r for r in MODEL_HASHES if r != "qwen"]
    assert len(hf_repos) >= 5, f"Expected at least 5 HuggingFace repos in MODEL_HASHES, got {len(hf_repos)}: {hf_repos}"
    for repo_id in hf_repos:
        entry = MODEL_HASHES[repo_id]
        revision = entry.get("revision", "")
        assert _COMMIT_SHA_RE.match(revision), (
            f"model_hashes.json entry '{repo_id}' has revision={revision!r} — "
            f"must be a 40-char hex commit SHA, not 'main' or another mutable branch. "
            f"A mutable-branch revision allows a compromised HuggingFace repo to push "
            f"a malicious new commit that Voice Typer would silently download and load."
        )


def test_model_hashes_have_pinned_config_json():
    """SEC-audit-005: Every HuggingFace repo entry must pin config.json in
    the 'files' dict with a valid SHA-256 digest.

    config.json controls model architecture (encoder/decoder dimensions,
    layer count, vocabulary size). Tampering with config.json can change
    model behavior in subtle ways (e.g. reducing layer count to weaken
    transcription quality, or altering output projection to inject adversarial
    tokens). The pinned revision SHA alone doesn't verify post-download file
    integrity — the files dict is the belt-and-suspenders check that catches
    local filesystem tampering or a corrupted download.
    """
    from voice_typer.server.security import MODEL_HASHES

    for repo_id, entry in MODEL_HASHES.items():
        if repo_id == "qwen":
            continue  # local model, not fetched from HuggingFace
        files = entry.get("files", {})
        assert files, (
            f"model_hashes.json entry '{repo_id}' has empty 'files' dict — "
            f"verify_model_integrity() is a no-op for this repo. "
            f"Pin at least config.json."
        )
        assert "config.json" in files, (
            f"model_hashes.json entry '{repo_id}' does not pin config.json — add its SHA-256 to the 'files' dict."
        )
        config_hash = files["config.json"]
        assert _SHA256_RE.match(config_hash), (
            f"model_hashes.json entry '{repo_id}' config.json hash {config_hash!r} is not a 64-char hex SHA-256 digest."
        )


def test_model_hashes_fallback_matches_json(monkeypatch):
    """The hardcoded fallback in security._load_model_hashes() must stay in
    sync with model_hashes.json.

    The fallback only runs when the JSON file is missing or unreadable
    (e.g. broken install, isolated test env). If the fallback regresses to
    'main' while the JSON has pinned SHAs, a broken-install scenario would
    silently downgrade to the insecure state. This test forces the fallback
    path and verifies every HuggingFace repo has the same revision + files
    as the JSON-loaded manifest.
    """
    from voice_typer.server import security

    # Snapshot the JSON-loaded manifest (normal path).
    json_manifest = {k: dict(v) for k, v in security.MODEL_HASHES.items()}

    # Force the fallback by making the JSON path report as not existing.
    real_exists = security.Path.exists

    def _fake_exists(self):
        if self.name == "model_hashes.json":
            return False
        return real_exists(self)

    monkeypatch.setattr(security.Path, "exists", _fake_exists)
    fallback_manifest = security._load_model_hashes()

    for repo_id, json_entry in json_manifest.items():
        if repo_id == "qwen":
            continue  # local model, not in the fallback (by design)
        assert repo_id in fallback_manifest, (
            f"repo '{repo_id}' is in model_hashes.json but missing from the "
            f"hardcoded fallback in security.py — add it so a missing JSON "
            f"file doesn't silently drop supply-chain protection for this repo."
        )
        fb_entry = fallback_manifest[repo_id]
        assert fb_entry.get("revision") == json_entry.get("revision"), (
            f"repo '{repo_id}': fallback revision {fb_entry.get('revision')!r} "
            f"!= JSON revision {json_entry.get('revision')!r}. The fallback "
            f"must mirror model_hashes.json."
        )
        assert fb_entry.get("files") == json_entry.get("files"), (
            f"repo '{repo_id}': fallback 'files' dict != JSON 'files' dict. The fallback must mirror model_hashes.json."
        )


def test_verify_model_integrity_hard_fails_on_hash_mismatch(monkeypatch, tmp_path, isolated_integrity_cache):
    """SEC-audit-005: verify_model_integrity() must return False when a pinned
    file's SHA-256 doesn't match — preventing a tampered model from loading.

    Before the fix, an empty 'files' dict caused verify_model_integrity() to
    soft-pass (return True) with only a WARNING log. Now that 'files' is
    populated, a mismatch must hard-fail.
    """
    from voice_typer.server import security

    fake_manifest = {
        "test/tampered-repo": {
            "revision": "abc123def4567890abc123def4567890abc123de",
            "files": {
                # Intentionally wrong hash — all zeros.
                "config.json": "0" * 64,
            },
        }
    }
    monkeypatch.setattr(security, "MODEL_HASHES", fake_manifest)

    # Create a model dir with a model file + config.json (with content that
    # obviously doesn't hash to all-zeros).
    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)
    (tmp_path / "config.json").write_text('{"model_type": "tampered"}')

    result = security.verify_model_integrity(str(tmp_path), "test/tampered-repo")
    assert result is False, (
        "verify_model_integrity() must hard-fail (return False) when a pinned "
        "file's SHA-256 doesn't match. A True return would allow a tampered "
        "model to load — defeating the supply-chain protection."
    )


def test_verify_model_integrity_passes_when_all_hashes_match(monkeypatch, tmp_path, isolated_integrity_cache):
    """SEC-audit-005: verify_model_integrity() returns True when all pinned
    file hashes match — verifying the happy path of hash enforcement."""
    from voice_typer.server import security

    config_content = b'{"model_type": "verified"}'
    config_hash = hashlib.sha256(config_content).hexdigest()

    fake_manifest = {
        "test/verified-repo": {
            "revision": "abc123def4567890abc123def4567890abc123de",
            "files": {
                "config.json": config_hash,
            },
        }
    }
    monkeypatch.setattr(security, "MODEL_HASHES", fake_manifest)

    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)
    (tmp_path / "config.json").write_bytes(config_content)

    result = security.verify_model_integrity(str(tmp_path), "test/verified-repo")
    assert result is True


def test_verify_model_integrity_fails_when_pinned_file_missing(monkeypatch, tmp_path, isolated_integrity_cache):
    """SEC-audit-005: verify_model_integrity() returns False when a pinned
    file is missing from the model directory."""
    from voice_typer.server import security

    fake_manifest = {
        "test/missing-file-repo": {
            "revision": "abc123def4567890abc123def4567890abc123de",
            "files": {
                "config.json": "a" * 64,
            },
        }
    }
    monkeypatch.setattr(security, "MODEL_HASHES", fake_manifest)

    # Model file exists, but config.json is missing.
    (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)

    result = security.verify_model_integrity(str(tmp_path), "test/missing-file-repo")
    assert result is False


# ALLOW_PATTERNS aliases regression ──────────────────────────


def test_allow_patterns_parakeet_omits_bin():
    """SEC-audit-005 / G4-M-39: the Parakeet allowlist must NOT include
    ``*.bin`` — the pickle-serialised format is a remote-code-execution
    vector and Parakeet ships ``model.safetensors`` only.
    """
    from voice_typer.server._model_integrity import ALLOW_PATTERNS_PARAKEET

    assert "*.safetensors" in ALLOW_PATTERNS_PARAKEET
    assert "config.json" in ALLOW_PATTERNS_PARAKEET
    assert "*.bin" not in ALLOW_PATTERNS_PARAKEET, (
        "GT-E1-3 / SEC-audit-005: ALLOW_PATTERNS_PARAKEET must not include "
        "*.bin — Parakeet ships safetensors only and *.bin is an RCE vector."
    )


def test_allow_patterns_whisper_includes_bin():
    """SEC-audit-005 / G4-M-39: the Whisper allowlist keeps ``*.bin``
    because CTranslate2 (``faster_whisper``) consumes ``model.bin``
    natively and never via ``torch.load`` (the pickle-vector path).
    """
    from voice_typer.server._model_integrity import ALLOW_PATTERNS_WHISPER

    assert "*.bin" in ALLOW_PATTERNS_WHISPER
    assert "*.safetensors" in ALLOW_PATTERNS_WHISPER


def test_allow_patterns_backward_compat_alias_removed():
    """GT-E1-3: the bare ``ALLOW_PATTERNS`` backward-compat alias was
    removed because no production caller imports it (all callers use
    the backend-specific ``ALLOW_PATTERNS_PARAKEET`` / ``ALLOW_PATTERNS_WHISPER``
    explicitly). If a future contributor re-introduces the alias, this
    test fails so they are forced to either delete it again or wire a
    real caller.
    """
    import voice_typer.server._model_integrity as mi

    assert not hasattr(mi, "ALLOW_PATTERNS"), (
        "GT-E1-3: bare ALLOW_PATTERNS alias was deleted as dead code "
        "(zero production callers). Re-introducing it without a real "
        "caller violates the cleanup. Use ALLOW_PATTERNS_PARAKEET or "
        "ALLOW_PATTERNS_WHISPER explicitly instead."
    )
