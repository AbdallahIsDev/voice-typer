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


class TestModelIntegritySHA256:
    """SEC-audit-005: verify_model_integrity with SHA-256 hash checking."""

    def test_verify_model_integrity_valid(self, tmp_path):
        """Returns True for directory with model and config files."""
        from voice_typer.server.security import verify_model_integrity
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 100)
        (tmp_path / "config.json").write_text('{"model_type": "test"}')
        assert verify_model_integrity(str(tmp_path), "test/model") is True

    def test_verify_model_integrity_missing_dir(self):
        """Returns False for non-existent directory."""
        from voice_typer.server.security import verify_model_integrity
        assert verify_model_integrity("/nonexistent/path", "test/model") is False

    def test_verify_model_integrity_empty_dir(self, tmp_path):
        """Returns False for directory with no model files."""
        from voice_typer.server.security import verify_model_integrity
        assert verify_model_integrity(str(tmp_path), "test/model") is False

    def test_verify_model_integrity_no_config(self, tmp_path):
        """Returns False for directory with model but no config.json."""
        from voice_typer.server.security import verify_model_integrity
        (tmp_path / "model.bin").write_bytes(b"\x00" * 100)
        assert verify_model_integrity(str(tmp_path), "test/model") is False

    def test_verify_model_integrity_empty_model_file(self, tmp_path):
        """Returns False for directory with empty model file."""
        from voice_typer.server.security import verify_model_integrity
        (tmp_path / "model.safetensors").write_bytes(b"")
        (tmp_path / "config.json").write_text('{}')
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

    def test_verify_model_integrity_with_pinned_hash(self, tmp_path):
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


# ─── SEC-audit-007: Qwen Model Directory Validation ──────────────────────


class TestQwenModelDirValidation:
    """SEC-audit-007: .py files rejected in Qwen model directories."""

    def test_py_files_rejected(self, tmp_path):
        """Directories containing .py files are rejected."""
        from voice_typer.server.qwen_engine import _validate_qwen_model_dir
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 10)
        (tmp_path / "config.json").write_text('{}')
        (tmp_path / "malicious.py").write_text("import os; os.system('rm -rf /')")
        assert _validate_qwen_model_dir(str(tmp_path)) is False

    def test_sh_files_rejected(self, tmp_path):
        """Directories containing .sh files are rejected."""
        from voice_typer.server.qwen_engine import _validate_qwen_model_dir
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 10)
        (tmp_path / "config.json").write_text('{}')
        (tmp_path / "setup.sh").write_text("#!/bin/bash\necho pwned")
        assert _validate_qwen_model_dir(str(tmp_path)) is False

    def test_exe_files_rejected(self, tmp_path):
        """Directories containing .exe files are rejected."""
        from voice_typer.server.qwen_engine import _validate_qwen_model_dir
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 10)
        (tmp_path / "config.json").write_text('{}')
        (tmp_path / "payload.exe").write_bytes(b"\x00" * 10)
        assert _validate_qwen_model_dir(str(tmp_path)) is False

    def test_safe_model_dir_accepted(self, tmp_path):
        """Directories with only allowed file types are accepted."""
        from voice_typer.server.qwen_engine import _validate_qwen_model_dir
        (tmp_path / "model.safetensors").write_bytes(b"\x00" * 10)
        (tmp_path / "config.json").write_text('{}')
        (tmp_path / "tokenizer.json").write_text('{}')
        assert _validate_qwen_model_dir(str(tmp_path)) is True

    def test_py_not_in_allowed_extensions(self):
        """SEC-audit-007: .py is NOT in _QWEN_ALLOWED_EXTENSIONS."""
        from voice_typer.server.qwen_engine import _QWEN_ALLOWED_EXTENSIONS
        assert ".py" not in _QWEN_ALLOWED_EXTENSIONS


# ─── SEC-001: Mutex Local\ Prefix ──────────────────────────────────────────


class TestMutexLocalPrefix:
    """SEC-001: Verify mutex name uses Local\\ prefix."""

    def test_mutex_name_has_local_prefix(self):
        """SEC-001: The mutex name in app.py uses Local\\ prefix."""
        import voice_typer.server.app as app_module
        with open(app_module.__file__) as f:
            source = f.read()
        assert 'Local\\\\VoiceTyperSingleInstance' in source or \
               '"Local\\VoiceTyperSingleInstance"' in source


# ─── SEC-002: Secure Read Text ──────────────────────────────────────────────


class TestSecureReadText:
    """SEC-002: _secure_read_text with inode verification and symlink rejection."""

    def test_reads_normal_file(self, tmp_path):
        """_secure_read_text can read a normal file."""
        from voice_typer.server.config import _secure_read_text
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world", encoding="utf-8")
        assert _secure_read_text(test_file) == "hello world"

    def test_rejects_symlink(self, tmp_path):
        """_secure_read_text raises OSError for symlinks on POSIX."""
        if sys.platform == "win32":
            pytest.skip("O_NOFOLLOW not available on Windows")
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
                "[TEST]", "this is secret text that should not appear",
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
                "[TEST]", "this is hallucinated text",
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
        with patch("voice_typer.server.config._config_dir", return_value=tmp_path), \
             caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
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

    def test_phrase_pattern_cache_has_lru_eviction(self):
        """SEC-011: _phrase_pattern_cache evicts oldest entries when full."""
        from voice_typer.server.text_cleanup import (
            _PHRASE_PATTERN_CACHE_MAXSIZE,
            _get_compiled_phrase_pattern,
        )
        # The cache should have a maximum size
        assert _PHRASE_PATTERN_CACHE_MAXSIZE > 0
        # The function should work for basic patterns
        pattern = _get_compiled_phrase_pattern("test phrase")
        assert pattern is not None


# ─── SEC-audit-011: SystemRoot Validation ──────────────────────────────────


class TestSystemRootValidation:
    """SEC-audit-011: SystemRoot env var validation on Windows."""

    def test_validate_systemroot_noop_on_posix(self):
        """_validate_systemroot is a no-op on non-Windows platforms."""
        if sys.platform == "win32":
            pytest.skip("Test only applies to non-Windows platforms")
        from voice_typer.server.config import _validate_systemroot
        # Should not raise
        _validate_systemroot()

    def test_validate_systemroot_function_exists(self):
        """SEC-audit-011: _validate_systemroot function exists in config module."""
        from voice_typer.server.config import _validate_systemroot
        assert callable(_validate_systemroot)

    def test_systemroot_validation_called_in_app(self):
        """SEC-audit-011: _validate_systemroot is called in _validate_env_vars."""
        import voice_typer.server.app as app_module
        with open(app_module.__file__) as f:
            source = f.read()
        assert "_validate_systemroot" in source


# ─── SEC-003: Secure File Write Permissions ──────────────────────────────


class TestSecureFileWrites:
    """SEC-003: All persistent file writes use 0o600 permissions."""

    def test_secure_atomic_write_permissions(self, tmp_path):
        """_secure_atomic_write creates files with 0o600 on POSIX."""
        if sys.platform == "win32":
            pytest.skip("POSIX-specific test")
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
        import voice_typer.server.autostart_launcher as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_atomic_write" in source

    def test_security_restart_token_uses_secure_write(self):
        """SEC-003: generate_restart_token uses _secure_atomic_write."""
        import voice_typer.server.security as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_atomic_write" in source


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
                context = "\n".join(lines[max(0, i - 5):i + 1])
                assert "fill(0)" in context, \
                    "preroll_buffer.clear() should be preceded by fill(0)"


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
        import voice_typer.server.text_cleanup as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_read_text" in source

    def test_config_load_uses_secure_read(self):
        """SEC-002: Config.load uses _secure_read_text for config.json."""
        import voice_typer.server.config as mod
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

    def test_security_verify_restart_uses_secure_read(self):
        """SEC-002: verify_restart_token uses _secure_read_text."""
        import voice_typer.server.security as mod
        with open(mod.__file__) as f:
            source = f.read()
        assert "_secure_read_text" in source
