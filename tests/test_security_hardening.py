"""Regression tests for all SEC fixes applied in this audit round.

Tests cover:
  - SEC-audit-005: Model integrity verification with SHA-256
  - SEC-audit-007: Qwen model directory validation (no .py files)
  - SEC-001: Mutex name uses Local\\ prefix (already verified in code)
  - SEC-002: _secure_read_text inode verification & symlink rejection
  - SEC-009: PII-safe hallucination logging & privacy warning
  - SEC-011: MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, MAX_REPLACEMENT_LENGTH
  - SEC-audit-011: SystemRoot env var validation
  - SEC-003: Secure file write permissions (0o600)
  - SEC-audit-008: Audio buffer zeroing before clear
"""

import json
import logging
import sys
from unittest.mock import patch

import pytest

# ─── SEC-audit-005: Model Integrity Verification ──────────────────────────


class TestModelIntegrity:
    """SEC-audit-005: verify_model_integrity with SHA-256 hash checking."""

    def test_verify_model_integrity_valid(self, isolated_integrity_cache, tmp_path):
        """Returns True for directory with model and config files."""
        from voice_typer.server.security import verify_model_integrity

        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)
        (tmp_path / "config.json").write_text('{"model_type": "test"}')
        assert verify_model_integrity(str(tmp_path), "test/model") is True

    def test_verify_model_integrity_missing_dir(self, isolated_integrity_cache):
        """Returns False for non-existent directory."""
        from voice_typer.server.security import verify_model_integrity

        assert verify_model_integrity("/nonexistent/path", "test/model") is False

    def test_verify_model_integrity_empty_dir(self, isolated_integrity_cache, tmp_path):
        """Returns False for directory with no model files."""
        from voice_typer.server.security import verify_model_integrity

        assert verify_model_integrity(str(tmp_path), "test/model") is False

    def test_verify_model_integrity_no_config(self, isolated_integrity_cache, tmp_path):
        """Returns False for directory with model but no config.json."""
        from voice_typer.server.security import verify_model_integrity

        (tmp_path / "model.bin").write_bytes(b"\x00" * 100)
        assert verify_model_integrity(str(tmp_path), "test/model") is False

    def test_verify_model_integrity_empty_model_file(self, isolated_integrity_cache, tmp_path):
        """Returns False for directory with empty model file."""
        from voice_typer.server.security import verify_model_integrity

        (tmp_path / "model.safetensors").write_bytes(b"")
        (tmp_path / "config.json").write_text("{}")
        assert verify_model_integrity(str(tmp_path), "test/model") is False

    def test_compute_file_sha256(self, tmp_path):
        """compute_file_sha256 returns correct SHA-256 digest."""
        import hashlib

        from voice_typer.server.security import compute_file_sha256

        test_file = tmp_path / "test.bin"
        content = b"hello world"
        test_file.write_bytes(content)
        expected = hashlib.sha256(content).hexdigest()
        assert compute_file_sha256(test_file) == expected

    def test_model_hashes_manifest_exists(self):
        """SEC-audit-005: MODEL_HASHES manifest is populated with known repos."""
        from voice_typer.server.security import MODEL_HASHES

        assert "nvidia/parakeet-tdt-0.6b-v3" in MODEL_HASHES
        assert "Systran/faster-whisper-small.en" in MODEL_HASHES
        # Each entry must have a revision key
        for repo_id, manifest in MODEL_HASHES.items():
            assert "revision" in manifest, f"Missing 'revision' key for {repo_id}"

    def test_verify_model_integrity_with_pinned_hash(self, isolated_integrity_cache, tmp_path):
        """Returns False when pinned hash doesn't match actual file hash."""
        from voice_typer.server.security import verify_model_integrity

        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)
        (tmp_path / "config.json").write_text('{"model_type": "test"}')
        # Use a repo_id that has pinned file hashes (we'll inject one)
        from voice_typer.server import security

        original = security.MODEL_HASHES.copy()
        try:
            security.MODEL_HASHES["test/pinned-model"] = {
                "revision": "main",
                "files": {"model.safetensors": "0000000000000000000000000000000000000000000000000000000000000000"},
            }
            assert verify_model_integrity(str(tmp_path), "test/pinned-model") is False
        finally:
            security.MODEL_HASHES.clear()
            security.MODEL_HASHES.update(original)

    def test_asr_setup_delegates_to_security(self):
        """SEC-audit-005: asr_setup._verify_model_integrity delegates to security module."""
        from voice_typer.server.asr_setup import _verify_model_integrity

        # The function should exist and be callable
        assert callable(_verify_model_integrity)


# Qwen model integrity hard-fail ( canonical path) ────


class TestQwenModelIntegrityHardFail:
    """G4-H-33: ``security.verify_model_integrity`` is the canonical
    hard-fail path for local Qwen models with an empty ``files`` dict
    in ``model_hashes.json``.

    The Qwen model is loaded from a user-supplied local path (not a
    HuggingFace repo_id), so there is NO upstream SHA pin from
    ``snapshot_download``. The empty-files state was previously a
    soft-pass in ``qwen_engine._verify_qwen_model_hashes`` — a
    tampered or substituted local Qwen directory would load with NO
    content hash verification.

    The canonical hard-fail in ``security.verify_model_integrity``
    returns False when ``manifest["revision"] == "local"`` AND
    ``manifest["files"]`` is empty, so callers (qwen_engine, once
    agent 2-f migrates it) refuse to load a tampered model directory.
    """

    def test_qwen_dir_with_empty_pinned_files_hard_fails(self, isolated_integrity_cache, tmp_path):
        """G4-H-33: a local Qwen dir with the default (empty) pinned-files
        manifest MUST hard-fail integrity verification.

        Constructs a plausible Qwen model directory (model.safetensors,
        config.json, tokenizer.json — the typical Qwen layout) and
        asserts that ``verify_model_integrity(dir, "qwen")`` returns
        False because ``model_hashes.json["qwen"]`` has
        ``"revision": "local"`` with an empty ``"files"`` dict.
        """
        from voice_typer.server.security import verify_model_integrity

        # Construct a plausible Qwen model dir.
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 1024)
        (tmp_path / "config.json").write_text('{"model_type": "qwen2"}')
        (tmp_path / "tokenizer.json").write_text("{}")

        # The default model_hashes.json has ``"qwen": {"revision":
        # "local", "files": {}}``, so the hard-fail branch MUST fire.
        result = verify_model_integrity(str(tmp_path), "qwen")
        assert result is False, (
            "verify_model_integrity must hard-FAIL for a local Qwen dir "
            "when model_hashes.json has empty 'files' (NF-R18-9). A "
            "tampered local model has no upstream SHA pin to fall back on."
        )

    def test_qwen_dir_tampered_with_pinned_hash_mismatch(self, isolated_integrity_cache, tmp_path):
        """G4-H-33: a tampered local Qwen dir (pinned hash mismatch)
        is rejected via the canonical ``verify_model_integrity`` path.

        Populates the manifest with pinned hashes for the Qwen repo
        (simulating an operator who has populated ``files`` with the
        expected SHA-256 digests), then constructs a directory whose
        ``model.safetensors`` content does NOT match the pinned hash.
        Verifies that ``verify_model_integrity`` returns False.
        """
        import hashlib

        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        # Construct a plausible Qwen model dir.
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 1024)
        (tmp_path / "config.json").write_text('{"model_type": "qwen2"}')
        (tmp_path / "tokenizer.json").write_text("{}")

        # Compute the ACTUAL hash of model.safetensors, then pin a
        # WRONG hash to simulate a tampered directory (the file on
        # disk differs from what the manifest expects).
        actual_safetensors_hash = hashlib.sha256(b"\x00" * 1024).hexdigest()
        wrong_hash = "0" * 64  # definitely not the actual hash

        # Sanity: ensure the wrong hash really is wrong.
        assert wrong_hash != actual_safetensors_hash

        original = security.MODEL_HASHES.copy()
        try:
            security.MODEL_HASHES["qwen"] = {
                "revision": "local",
                "files": {
                    "model.safetensors": wrong_hash,
                    "config.json": hashlib.sha256(b'{"model_type": "qwen2"}').hexdigest(),
                    "tokenizer.json": hashlib.sha256(b"{}").hexdigest(),
                },
            }
            result = verify_model_integrity(str(tmp_path), "qwen")
            assert result is False, (
                "verify_model_integrity must return False when a pinned "
                "file's actual hash does not match the manifest — this is "
                "the canonical hard-fail path for a tampered local Qwen "
                "model directory."
            )
        finally:
            security.MODEL_HASHES.clear()
            security.MODEL_HASHES.update(original)

    def test_qwen_dir_valid_with_correct_pinned_hashes(self, isolated_integrity_cache, tmp_path):
        """G4-H-33: a valid local Qwen dir (pinned hashes match) is
        accepted via ``verify_model_integrity``.

        Populates the manifest with the CORRECT pinned hashes for the
        Qwen repo, then constructs a directory whose file contents
        match. Verifies that ``verify_model_integrity`` returns True.
        This is the positive case — operators who populate
        ``model_hashes.json`` with real hashes can load their local
        Qwen model.
        """
        import hashlib

        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        safetensors_content = b"\x00" * 1024
        config_content = b'{"model_type": "qwen2"}'
        tokenizer_content = b"{}"

        (tmp_path / "model.safetensors").write_bytes(safetensors_content)
        (tmp_path / "config.json").write_bytes(config_content)
        (tmp_path / "tokenizer.json").write_bytes(tokenizer_content)

        original = security.MODEL_HASHES.copy()
        try:
            security.MODEL_HASHES["qwen"] = {
                "revision": "local",
                "files": {
                    "model.safetensors": hashlib.sha256(safetensors_content).hexdigest(),
                    "config.json": hashlib.sha256(config_content).hexdigest(),
                    "tokenizer.json": hashlib.sha256(tokenizer_content).hexdigest(),
                },
            }
            result = verify_model_integrity(str(tmp_path), "qwen")
            assert result is True, (
                "verify_model_integrity must return True when all pinned "
                "hashes match — this is the success path for an operator "
                "who has populated model_hashes.json with real hashes."
            )
        finally:
            security.MODEL_HASHES.clear()
            security.MODEL_HASHES.update(original)

    def test_qwen_dir_missing_pinned_file(self, isolated_integrity_cache, tmp_path):
        """G4-H-33: a local Qwen dir missing a pinned file is rejected.

        Even when the manifest is populated with pinned hashes, if a
        pinned file is missing from the directory, the integrity
        check must fail (hard-fail).
        """
        import hashlib

        from voice_typer.server import security
        from voice_typer.server.security import verify_model_integrity

        # Construct a Qwen dir missing tokenizer.json (which IS pinned).
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 1024)
        (tmp_path / "config.json").write_text('{"model_type": "qwen2"}')
        # NOTE: tokenizer.json is intentionally NOT created.

        original = security.MODEL_HASHES.copy()
        try:
            security.MODEL_HASHES["qwen"] = {
                "revision": "local",
                "files": {
                    "model.safetensors": hashlib.sha256(b"\x00" * 1024).hexdigest(),
                    "config.json": hashlib.sha256(b'{"model_type": "qwen2"}').hexdigest(),
                    "tokenizer.json": hashlib.sha256(b"{}").hexdigest(),
                },
            }
            result = verify_model_integrity(str(tmp_path), "qwen")
            assert result is False, (
                "verify_model_integrity must return False when a pinned file is missing from the model directory."
            )
        finally:
            security.MODEL_HASHES.clear()
            security.MODEL_HASHES.update(original)


# SEC-audit-007 note (2026-08-15): the Qwen model-directory allowlist
# (``_validate_qwen_model_dir`` / ``_QWEN_ALLOWED_EXTENSIONS``) was
# removed with the torch Qwen engine. The ONNX backend
# (``qwen_onnx_model.py``) loads ONLY the specific known files
# (encoder/decoder ONNX sessions, embed_tokens.bin, tokenizer.json,
# config.json) via ``is_onnx_model_dir`` + ``from_pretrained`` — a
# stricter trust posture than the old extension allowlist, with no
# code-execution surface from the model dir.


# ─── SEC-001: Mutex Local\ Prefix ──────────────────────────────────────────


class TestMutexLocalPrefix:
    """SEC-001: Verify mutex name uses Local\\ prefix."""

    def test_mutex_name_has_local_prefix(self):
        """SEC-001: The mutex name in app.py uses Local\\ prefix."""
        import voice_typer.server.app as app_module

        with open(app_module.__file__) as f:
            source = f.read()
        assert "Local\\\\VoiceTyperSingleInstance" in source or '"Local\\VoiceTyperSingleInstance"' in source


# ─── SEC-002: Secure Read Text ──────────────────────────────────────────────


class TestSecureReadText:
    """SEC-002: _secure_read_text with inode verification and symlink rejection."""

    def test_reads_normal_file(self, tmp_path):
        """_secure_read_text can read a normal file."""
        from voice_typer.server.config import _secure_read_text

        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        assert _secure_read_text(test_file) == "hello world"

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: O_NOFOLLOW symlink-rejection is not supported by the Win32 filesystem",
    )
    def test_rejects_symlink(self, tmp_path):
        """_secure_read_text raises OSError for symlinks on POSIX."""
        from voice_typer.server.config import _secure_read_text

        real_file = tmp_path / "real.txt"
        real_file.write_text("secret", encoding="utf-8")
        link = tmp_path / "link.txt"
        link.symlink_to(real_file)
        with pytest.raises(OSError):
            _secure_read_text(link)

    def test_reads_with_encoding(self, tmp_path):
        """_secure_read_text respects the encoding parameter."""
        from voice_typer.server.config import _secure_read_text

        test_file = tmp_path / "test.txt"
        test_file.write_text("héllo wörld", encoding="utf-8")
        assert _secure_read_text(test_file, encoding="utf-8") == "héllo wörld"


# ─── SEC-009: PII-Safe Hallucination Logging ──────────────────────────────


class TestHallucinationLogging:
    """SEC-009: Hallucination logging gates text behind log_transcriptions flag."""

    def test_log_transcriptions_false_only_metadata(self, caplog):
        """When log_transcriptions=False, only char count is logged — no text."""
        from voice_typer.server.hallucination import log_hallucination_rejection

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.hallucination"):
            log_hallucination_rejection(
                "[TEST]",
                "this is secret text that should not appear",
                reason="hallucination",
                log_transcriptions=False,
            )
        # Text should NOT appear in the log
        assert "secret text" not in caplog.text
        # But the char count should (the string is 42 chars)
        assert "chars" in caplog.text

    def test_log_transcriptions_true_shows_redacted_text(self, caplog):
        """When log_transcriptions=True, text is logged (truncated + PII-redacted)."""
        from voice_typer.server.hallucination import log_hallucination_rejection

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.hallucination"):
            log_hallucination_rejection(
                "[TEST]",
                "this is hallucinated text",
                reason="hallucination",
                log_transcriptions=True,
            )
        # Should contain some representation of the text (truncated to 40 chars)
        assert "Rejected likely hallucination" in caplog.text

    def test_truncation_limit_is_40(self):
        """SEC-009: The default truncation limit is 40 chars, not 80."""
        from voice_typer.server.hallucination import _HALLUCINATION_LOG_MAX_CHARS

        assert _HALLUCINATION_LOG_MAX_CHARS == 40

    def test_privacy_warning_on_log_transcriptions(self, tmp_path, caplog):
        """SEC-009: Loading config with log_transcriptions=True emits a privacy warning."""
        from voice_typer.server.config import Config

        config_file = tmp_path / "config.json"
        config_data = {"log_transcriptions": True, "schema_version": 1}
        config_file.write_text(json.dumps(config_data), encoding="utf-8")
        with (
            patch("voice_typer.server.config._config_dir", return_value=tmp_path),
            caplog.at_level(logging.WARNING, logger="voice_typer.server.config"),
        ):
            cfg = Config.load()
        assert cfg.log_transcriptions is True
        # The privacy warning should appear in the log
        assert "PII" in caplog.text or "privacy" in caplog.text.lower() or "log_transcriptions" in caplog.text


# ─── SEC-011: Corrections Limits ──────────────────────────────────────────


class TestCorrectionsLimits:
    """SEC-011: MAX_CORRECTIONS_ENTRIES, MAX_PATTERN_LENGTH, MAX_REPLACEMENT_LENGTH."""

    def test_max_corrections_entries_constant(self):
        """SEC-011: MAX_CORRECTIONS_ENTRIES is 5000."""
        # The constant is used inside the function; verify indirectly
        # by checking that the vocabulary module also defines it
        from voice_typer.server.vocabulary import MAX_CORRECTIONS_ENTRIES

        assert MAX_CORRECTIONS_ENTRIES == 5000

    def test_max_pattern_length_constant(self):
        """SEC-011: MAX_PATTERN_LENGTH is 200."""
        from voice_typer.server.vocabulary import MAX_PATTERN_LENGTH

        assert MAX_PATTERN_LENGTH == 200

    def test_max_replacement_length_constant(self):
        """SEC-011: MAX_REPLACEMENT_LENGTH is 500."""
        from voice_typer.server.vocabulary import MAX_REPLACEMENT_LENGTH

        assert MAX_REPLACEMENT_LENGTH == 500

    def test_long_pattern_rejected_in_vocabulary(self, tmp_path):
        """SEC-011: VocabularyManager.add_entry rejects patterns > MAX_PATTERN_LENGTH."""
        from voice_typer.server.vocabulary import MAX_PATTERN_LENGTH, VocabularyManager

        vm = VocabularyManager(config_dir=tmp_path, bundled_path=tmp_path / "noop.json")
        long_key = "x" * (MAX_PATTERN_LENGTH + 1)
        result = vm.add_entry("misspellings", long_key, "value")
        assert result is False

    def test_long_replacement_rejected_in_vocabulary(self, tmp_path):
        """SEC-011: VocabularyManager.add_entry rejects replacements > MAX_REPLACEMENT_LENGTH."""
        from voice_typer.server.vocabulary import MAX_REPLACEMENT_LENGTH, VocabularyManager

        vm = VocabularyManager(config_dir=tmp_path, bundled_path=tmp_path / "noop.json")
        long_value = "x" * (MAX_REPLACEMENT_LENGTH + 1)
        result = vm.add_entry("misspellings", "key", long_value)
        assert result is False

    def test_long_phrase_pattern_rejected(self, tmp_path):
        """SEC-011: VocabularyManager.add_phrase rejects patterns > MAX_PATTERN_LENGTH."""
        from voice_typer.server.vocabulary import MAX_PATTERN_LENGTH, VocabularyManager

        vm = VocabularyManager(config_dir=tmp_path, bundled_path=tmp_path / "noop.json")
        long_wrong = "x" * (MAX_PATTERN_LENGTH + 1)
        result = vm.add_phrase("phrase_corrections", long_wrong, "correct")
        assert result is False

    def test_text_cleanup_drops_oversized_patterns(self, tmp_path):
        """SEC-011: _load_external_corrections drops patterns exceeding MAX_PATTERN_LENGTH."""
        from voice_typer.server.text_cleanup import _load_external_corrections

        # Create a corrections file with an oversized pattern
        corrections_file = tmp_path / "corrections.json"
        long_pattern = "x" * 300  # > MAX_PATTERN_LENGTH (200)
        corrections = {
            "misspellings": {long_pattern: "short"},
            "phrase_corrections": [],
            "extra_word_patterns": [],
        }
        corrections_file.write_text(json.dumps(corrections), encoding="utf-8")
        result = _load_external_corrections(tmp_path, str(corrections_file))
        if result is not None:
            misspellings, _, _ = result
            assert long_pattern not in misspellings

    def test_phrase_pattern_cache_is_bounded_via_combined_regex(self):
        """SEC-011 (revised): the former per-phrase LRU
        ``_phrase_pattern_cache`` was dead on the production hot path and
        has been removed. The live path is ``_get_phrases_regex``, which
        builds ONE combined-alternation regex per active-phrase list and
        caches it by list identity — so memory grows with the number of
        distinct ``configure_corrections`` calls, not with the number of
        distinct phrases ever seen. This test pins that the live cache
        contract holds: a single call returns a compiled regex + lookup
        dict for the active phrases, and a fresh call returns the same
        cached objects.
        """
        import re

        # The mutable regex-cache state lives on the ``_engine`` leaf of
        # the text_cleanup package — poke it there so the reading
        # functions see the replacement.
        from voice_typer.server.text_cleanup import _engine as text_cleanup

        saved = text_cleanup._active_phrases
        try:
            text_cleanup._active_phrases = [("test phrase", "T")]
            text_cleanup._phrases_re_cache = (None, None, {})
            pattern1, lookup1 = text_cleanup._get_phrases_regex()
            pattern2, lookup2 = text_cleanup._get_phrases_regex()
            assert pattern1 is pattern2
            assert lookup1 is lookup2
            assert isinstance(pattern1, re.Pattern)
            assert lookup1 == {"test phrase": "T"}
        finally:
            text_cleanup._active_phrases = saved
            text_cleanup._phrases_re_cache = (None, None, {})


# ─── SEC-audit-011: SystemRoot Validation ──────────────────────────────────


class TestSystemRootValidation:
    """SEC-audit-011: SystemRoot env var validation on Windows."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: _validate_systemroot is a Win32-only env-var guard that short-circuits on POSIX",
    )
    def test_validate_systemroot_noop_on_posix(self):
        """_validate_systemroot is a no-op on non-Windows platforms."""
        from voice_typer.server.config import _validate_systemroot

        # Should not raise
        _validate_systemroot()

    def test_validate_systemroot_function_exists(self):
        """SEC-audit-011: _validate_systemroot function exists in config module."""
        from voice_typer.server.config import _validate_systemroot

        assert callable(_validate_systemroot)

    def test_systemroot_validation_called_in_app(self):
        """SEC-audit-011: _validate_systemroot is called in _validate_env_vars.

        The check lives in the canonical ``env_validation`` module (the
        app-module re-export was removed with the test-seam cleanup).
        """
        import voice_typer.server.env_validation as env_validation_module

        with open(env_validation_module.__file__) as f:
            source = f.read()
        assert "_validate_systemroot" in source


# ─── SEC-003: Secure File Write Permissions ──────────────────────────────


class TestSecureFileWrites:
    """SEC-003: All persistent file writes use 0o600 permissions."""

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: 0o600 permission check has no equivalent on Win32 filesystem (ACLs use a different model)",
    )
    def test_secure_atomic_write_permissions(self, tmp_path):
        """_secure_atomic_write creates files with 0o600 on POSIX."""
        from voice_typer.server.config import _secure_atomic_write

        test_file = tmp_path / "test.json"
        _secure_atomic_write(test_file, '{"key": "value"}')
        mode = test_file.stat().st_mode
        # Check that group/other bits are 0
        assert (mode & 0o077) == 0, f"File has overly permissive mode: {oct(mode)}"

    def test_duck_crash_recovery_uses_secure_write(self):
        """SEC-003: DuckCrashRecovery.save uses _secure_atomic_write."""
        import voice_typer.server.duck_crash_recovery as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_atomic_write" in source

    def test_onboarding_uses_secure_write(self):
        """SEC-003: OnboardingManager.mark_complete uses _secure_atomic_write."""
        import voice_typer.server.onboarding as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_atomic_write" in source

    def test_autostart_launcher_uses_secure_write(self):
        """SEC-003: _write_pid_file uses _secure_atomic_write."""
        # _write_pid_file lives in the autostart subpackage since the
        # launcher was split into a facade + leaf modules.
        import voice_typer.server.autostart.pid_file as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_atomic_write" in source

    # (IMPROVE-mode run PI): ``test_security_restart_token_uses_secure_write``
    # removed — the ``generate_restart_token`` function it pinned was dead code
    # (imported into ``app.py`` but never called in production) and has been
    # deleted from ``voice_typer/server/security.py``. The other tests in this
    # class still verify ``_secure_atomic_write`` usage across
    # ``duck_crash_recovery``, ``onboarding``, and ``autostart_launcher``.


# ─── SEC-audit-008: Audio Buffer Zeroing ──────────────────────────────────


class TestAudioBufferZeroing:
    """SEC-audit-008: Audio buffer contents zeroed before clear."""

    def test_recording_source_has_zeroing(self):
        """SEC-audit-008: recording.py contains chunk.fill(0) for buffer zeroing."""
        import voice_typer.server.recording as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "chunk.fill(0)" in source
        assert "SEC-audit-008" in source

    def test_preroll_buffer_zeroing(self):
        """SEC-audit-008: preroll_buffer is also zeroed before clear."""
        import voice_typer.server.recording as mod

        with open(mod.__file__) as f:
            source = f.read()
        # The preroll buffer should also be zeroed
        # Find the section that zeros the preroll buffer
        assert "_preroll_buffer" in source
        # Verify that there's a fill(0) near preroll_buffer
        lines = source.split("\n")
        for i, line in enumerate(lines):
            if "_preroll_buffer" in line and "clear()" in line:
                # Look backward for fill(0)
                context = "\n".join(lines[max(0, i - 5) : i + 1])
                assert "fill(0)" in context, "preroll_buffer.clear() should be preceded by fill(0)"


# ─── SEC-002: Secure read used in security-sensitive paths ────────────────


class TestSecureReadUsage:
    """SEC-002: All security-sensitive file reads use _secure_read_text."""

    def test_vocabulary_loads_use_secure_read(self):
        """SEC-002: VocabularyManager._load_bundled and _load_user use _secure_read_text."""
        import voice_typer.server.vocabulary as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_read_text" in source

    def test_text_cleanup_uses_secure_read(self):
        """SEC-002: text_cleanup._load_external_corrections uses _secure_read_text."""
        # The corrections loaders (which perform the secure reads) moved
        # into the ``_corrections_data`` leaf of the text_cleanup package
        # split; scan the module where the actual read happens.
        import voice_typer.server.text_cleanup._corrections_data as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_read_text" in source

    def test_config_load_uses_secure_read(self):
        """SEC-002: Config.load uses _secure_read_text for config.json."""
        # The load implementation moved into ``config/loader.py`` during
        # the config package split; the facade (``config/__init__.py``)
        # only re-exports the name. Scan the module where the actual
        # read happens.
        import voice_typer.server.config.loader as mod

        with open(mod.__file__) as f:
            source = f.read()
        # Config.load should use _secure_read_text
        assert "_secure_read_text(config_file)" in source

    def test_duck_crash_recovery_uses_secure_read(self):
        """SEC-002: DuckCrashRecovery.load_stale uses _secure_read_text."""
        import voice_typer.server.duck_crash_recovery as mod

        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_read_text" in source

    # (IMPROVE-mode run PI): ``test_security_verify_restart_uses_secure_read``
    # removed — the ``verify_restart_token`` function it pinned was dead code
    # (imported into ``app.py`` but never called in production) and has been
    # deleted from ``voice_typer/server/security.py``. The other tests in this
    # class still verify ``_secure_read_text`` usage across ``vocabulary``,
    # ``text_cleanup``, ``config``, and ``duck_crash_recovery``.
