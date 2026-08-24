"""Tests for config load/save and field behavior."""

import contextlib
import json
import sys
from unittest.mock import patch

import pytest
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config, _default_hotkey_for_platform
from voice_typer.server.model_registry import DEFAULT_MODEL_SIZE

# the default hotkey is now platform-aware
# (Fn on macOS, Caps Lock on Windows/Linux, F2 on unknown platforms).
# Tests that assert the default hotkey use this helper instead of
# hard-coding "<f2>".
EXPECTED_DEFAULT_HOTKEY = _default_hotkey_for_platform()


class TestConfigDefaults:
    def test_default_values(self):
        c = Config()
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert c.sample_rate == 16000
        assert c.microphone is None
        assert c.model_size == DEFAULT_MODEL_SIZE
        assert c.language == "en"
        assert c.device == "cuda"
        assert c.beam_size == 1
        assert c.best_of == 1
        assert c.condition_on_previous_text is False
        assert c.streaming_transcription is True
        assert c.streaming_chunk_seconds == 12.0
        assert c.streaming_step_seconds == 5.0
        assert c.streaming_left_overlap_seconds == 3.0
        assert c.streaming_right_guard_seconds == 1.5
        assert c.streaming_min_first_chunk_seconds == 6.0
        assert c.streaming_silence_threshold == 0.003
        assert c.autostart is True
        assert c.paste_on_stop is True
        assert c.show_notifications is True
        # New config keys
        assert c.asr_backend == "whisper"
        assert c.qwen_model_path is None
        assert c.text_cleanup_enabled is True
        assert c.corrections_path is None


class TestConfigLoadSave:
    def test_save_creates_config_file(self, tmp_path, tmp_config_dir):
        c = Config(hotkey="<f3>", autostart=True)
        c.save()

        config_file = tmp_path / "config.json"
        assert config_file.exists()

        data = json.loads(config_file.read_text())
        assert data["hotkey"] == "<f3>"
        assert data["autostart"] is True

    def test_load_returns_defaults_when_no_file(self, tmp_path, tmp_config_dir):
        c = Config.load()
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert c.autostart is True

    def test_load_reads_existing_file(self, tmp_path, tmp_config_dir):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "hotkey": "<f9>",
                    "microphone": "WO Mic",
                    "autostart": True,
                    "paste_on_stop": False,
                    "show_notifications": False,
                }
            )
        )

        c = Config.load()
        assert c.hotkey == "<f9>"
        assert c.microphone == "WO Mic"
        assert c.autostart is True
        # User values are now preserved (no longer overridden)
        assert c.paste_on_stop is False
        assert c.show_notifications is False

    def test_load_preserves_user_device_and_paste_settings(self, tmp_path, tmp_config_dir):
        """fix: User's device, paste_on_stop, and streaming_transcription
        values in config.json must survive load() without being overridden."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "streaming_transcription": False,
                    "paste_on_stop": False,
                    "device": "cpu",
                }
            )
        )

        c = Config.load()
        # User values must be preserved — no more forced overrides
        assert c.streaming_transcription is False
        assert c.paste_on_stop is False
        assert c.device == "cpu"

    def test_load_raises_streaming_overlap_and_guard_to_safer_minimums(self, tmp_path, tmp_config_dir):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "streaming_left_overlap_seconds": 2.0,
                    "streaming_right_guard_seconds": 1.0,
                }
            )
        )

        c = Config.load()

        assert c.streaming_left_overlap_seconds == 3.0
        assert c.streaming_right_guard_seconds == 1.5

    @pytest.mark.parametrize(
        "unsupported_model",
        ["large-v4", "mega.en", "nonexistent-model", "small.en", "base.en", "medium.en", "turbo", "distil-large-v3"],
    )
    def test_load_normalizes_legacy_or_unsupported_model_to_default(self, tmp_path, tmp_config_dir, unsupported_model):
        """Models not in the (pruned) allowlist — stale legacy entries
        AND the variants removed by the 2026-08-15 catalog prune — are
        reset to the canonical ``DEFAULT_MODEL_SIZE``."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"model_size": unsupported_model}))

        c = Config.load()
        assert c.model_size == DEFAULT_MODEL_SIZE

    @pytest.mark.parametrize("valid_model", ["tiny", "large-v3-turbo"])
    def test_load_keeps_supported_models_unchanged(self, tmp_path, tmp_config_dir, valid_model):
        """Models in MODEL_REGISTRY must round-trip unchanged instead of
        being normalized to the default."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"model_size": valid_model}))

        c = Config.load()
        assert c.model_size == valid_model

    def test_load_keeps_large_v3_turbo_model(self, tmp_path, tmp_config_dir):
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"model_size": "large-v3-turbo"}))

        c = Config.load()
        assert c.model_size == "large-v3-turbo"

    def test_load_ignores_unknown_keys(self, tmp_path, tmp_config_dir):
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "hotkey": "<f5>",
                    "bogus_key": "should be ignored",
                }
            )
        )

        c = Config.load()
        assert c.hotkey == "<f5>"
        assert not hasattr(c, "bogus_key")

    def test_load_returns_defaults_on_corrupt_file(self, tmp_path, tmp_config_dir):
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")

        c = Config.load()
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY  # defaults

    def test_load_logs_error_on_corrupt_file(self, tmp_path, tmp_config_dir, caplog):
        """fix: Config.load() must log instead of silently swallowing failures.

        the level was lowered from ERROR to WARNING (recovery to
        defaults is a recoverable event, not a fatal error) and the
        message now includes the exception class name and file path so
        the user can see *why* their settings were reset.
        """
        import logging

        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        # warning must include the failure-mode name and file path.
        assert any("JSONDecodeError" in r.message for r in caplog.records)
        assert any(str(config_file) in r.message for r in caplog.records)

    def test_round_trip(self, tmp_path, tmp_config_dir):
        c1 = Config(
            hotkey="<f7>",
            microphone="Blue Yeti",
            model_size="large-v3-turbo",
            device="cpu",
            beam_size=3,
            best_of=2,
            condition_on_previous_text=True,
            streaming_transcription=False,
            streaming_chunk_seconds=10.0,
            streaming_step_seconds=4.0,
            streaming_left_overlap_seconds=3.5,
            streaming_right_guard_seconds=1.75,
            streaming_min_first_chunk_seconds=5.0,
            streaming_silence_threshold=0.001,
            autostart=True,
            paste_on_stop=False,
            show_notifications=False,
        )
        c1.save()
        c2 = Config.load()
        assert c1.hotkey == c2.hotkey
        assert c1.microphone == c2.microphone
        assert c1.model_size == c2.model_size
        # P1 fix: device, paste_on_stop, streaming_transcription survive round-trip
        assert c2.device == "cpu"
        assert c2.paste_on_stop is False
        assert c2.streaming_transcription is False
        assert c1.beam_size == c2.beam_size
        assert c1.best_of == c2.best_of
        assert c1.condition_on_previous_text == c2.condition_on_previous_text
        assert c1.streaming_chunk_seconds == c2.streaming_chunk_seconds
        assert c1.streaming_step_seconds == c2.streaming_step_seconds
        assert c1.streaming_left_overlap_seconds == c2.streaming_left_overlap_seconds
        assert c1.streaming_right_guard_seconds == c2.streaming_right_guard_seconds
        assert c1.streaming_min_first_chunk_seconds == c2.streaming_min_first_chunk_seconds
        assert c1.streaming_silence_threshold == c2.streaming_silence_threshold
        assert c1.autostart == c2.autostart
        assert c1.show_notifications == c2.show_notifications


class TestConfigPathValidation:
    """qwen_model_path and corrections_path are validated on load."""

    def test_qwen_model_path_invalid_resets_to_none(self, tmp_path, tmp_config_dir):
        """If qwen_model_path points to a non-existent directory, reset to None."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "asr_backend": "qwen",
                    "qwen_model_path": "/nonexistent/path/to/model",
                }
            )
        )

        c = Config.load()
        assert c.qwen_model_path is None

    def test_qwen_model_path_file_not_dir_resets_to_none(self, tmp_path, tmp_config_dir):
        """If qwen_model_path points to a file (not a directory), reset to None."""
        # Create a file (not a directory) at the path
        fake_model = tmp_path / "model_file"
        fake_model.write_text("not a directory")
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "qwen_model_path": str(fake_model),
                }
            )
        )

        c = Config.load()
        assert c.qwen_model_path is None

    def test_qwen_model_path_valid_dir_preserved(self, tmp_path, tmp_config_dir):
        """If qwen_model_path points to an existing directory, preserve it."""
        model_dir = tmp_path / "qwen_model"
        model_dir.mkdir()
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "qwen_model_path": str(model_dir),
                }
            )
        )

        c = Config.load()
        assert c.qwen_model_path == str(model_dir)

    def test_corrections_path_invalid_resets_to_none(self, tmp_path, tmp_config_dir):
        """If corrections_path points to a non-existent file, reset to None."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "corrections_path": "/nonexistent/corrections.json",
                }
            )
        )

        c = Config.load()
        assert c.corrections_path is None

    def test_corrections_path_dir_not_file_resets_to_none(self, tmp_path, tmp_config_dir):
        """If corrections_path points to a directory (not a file), reset to None."""
        corrections_dir = tmp_path / "corrections_dir"
        corrections_dir.mkdir()
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "corrections_path": str(corrections_dir),
                }
            )
        )

        c = Config.load()
        assert c.corrections_path is None

    def test_corrections_path_valid_file_preserved(self, tmp_path, tmp_config_dir):
        """If corrections_path points to an existing file, preserve it."""
        corrections_file = tmp_path / "corrections.json"
        corrections_file.write_text('{"misspellings": {}}')
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "corrections_path": str(corrections_file),
                }
            )
        )

        c = Config.load()
        assert c.corrections_path == str(corrections_file)

    def test_none_paths_pass_validation(self, tmp_path, tmp_config_dir):
        """None values for qwen_model_path and corrections_path are valid."""
        config_file = tmp_path / "config.json"
        config_file.write_text(
            json.dumps(
                {
                    "qwen_model_path": None,
                    "corrections_path": None,
                }
            )
        )

        c = Config.load()
        assert c.qwen_model_path is None
        assert c.corrections_path is None


class TestAtomicConfigSave:
    """fix: Config.save() must be atomic to prevent data loss on crash."""

    def test_save_uses_tmp_file_then_replace(self, tmp_path, tmp_config_dir):
        """save() writes to .tmp first then atomically replaces config.json."""
        c = Config(hotkey="<f5>")
        c.save()

        config_file = tmp_path / "config.json"
        tmp_file = tmp_path / "config.tmp"

        assert config_file.exists()
        assert not tmp_file.exists()

        data = json.loads(config_file.read_text())
        assert data["hotkey"] == "<f5>"

    def test_save_preserves_existing_config_on_partial_write(self, tmp_path, tmp_config_dir):
        """If a write fails mid-stream, the existing config.json is preserved."""
        c1 = Config(hotkey="<f3>")
        c1.save()

        config_file = tmp_path / "config.json"
        original_data = config_file.read_text()

        c2 = Config(hotkey="<f9>")
        # save() now delegates to _secure_atomic_write.
        # Mock it to raise OSError so the test verifies the existing
        # config is preserved when the write fails.
        with (
            patch(
                "voice_typer.server.config._secure_atomic_write",
                side_effect=OSError("disk full"),
            ),
            contextlib.suppress(OSError),
        ):
            c2.save()

        assert config_file.read_text() == original_data

    def test_no_stale_tmp_file_after_successful_save(self, tmp_path, tmp_config_dir):
        """After a successful save, no .tmp file should remain."""
        c = Config()
        c.save()

        assert not (tmp_path / "config.tmp").exists()
        assert (tmp_path / "config.json").exists()


class TestSaveErrorHandling:
    """save() has no error handling."""

    def test_save_returns_false_on_permission_error(self, tmp_path, monkeypatch, tmp_config_dir):
        from voice_typer.server.config import Config

        c = Config()

        # save() now uses json.dumps (string) not json.dump (file).
        # Mock json.dumps to raise OSError so the test verifies save()
        # returns False on error.
        def failing_dumps(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("json.dumps", failing_dumps)
        result = c.save()
        assert result is False

    def test_save_returns_true_on_success(self, tmp_path, tmp_config_dir):
        from voice_typer.server.config import Config

        c = Config()
        result = c.save()
        assert result is True


# ── config file permissions ─────────────────────────────────────


class TestConfigSaveEnforcesPosixFilePermissions:
    """on POSIX, the config file must be 0o600 and the
    config directory 0o700 so API keys and other settings are not
    world-readable.  On Windows these checks are skipped (NTFS ACLs
    are the relevant control, and the config dir is already under
    %APPDATA% which is per-user)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_save_creates_config_file_with_0600_permissions(self, tmp_path, tmp_config_dir):
        import os
        import stat

        from voice_typer.server.config import Config

        cfg = Config()
        cfg.cloud_api_key = "sk-test-secret"
        cfg.save()
        config_file = tmp_path / "config.json"
        assert config_file.exists()
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_save_creates_config_dir_with_0700_permissions(self, tmp_path, monkeypatch):
        import os
        import stat

        from voice_typer.server.config import Config

        # Use a subdir that doesn't exist yet so save() creates it
        config_dir = tmp_path / "nested" / ".voice-typer"
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: config_dir)
        cfg = Config()
        cfg.save()
        assert config_dir.exists()
        mode = stat.S_IMODE(os.stat(config_dir).st_mode)
        assert mode == 0o700, f"expected 0o700, got 0o{mode:o}"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_save_preserves_0600_on_existing_file(self, tmp_path, tmp_config_dir):
        """A second save() must keep the 0o600 permissions, not drift
        back to default umask."""
        import os
        import stat

        from voice_typer.server.config import Config

        cfg = Config()
        cfg.cloud_api_key = "first"
        cfg.save()
        cfg.cloud_api_key = "second"
        cfg.save()
        config_file = tmp_path / "config.json"
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600


# Parametrized config tests ─────────────────────────────────


class TestConfigParametrized:
    """Use @pytest.mark.parametrize for multiple config values."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("hotkey", "<f5>"),
            ("hotkey", "<caps_lock>"),
            ("language", "fr"),
            ("language", "zh"),
            ("model_size", "large-v3-turbo"),
            ("device", "cpu"),
            ("device", "cuda"),
        ],
    )
    def test_config_field_roundtrip(self, tmp_path, tmp_config_dir, field, value):
        """Config field values should survive save/load roundtrip."""
        c = Config(**{field: value})
        c.save()
        loaded = Config.load()
        assert getattr(loaded, field) == value

    @pytest.mark.parametrize("sample_rate", [8000, 16000, 22050, 44100, 48000])
    def test_various_sample_rates(self, tmp_path, tmp_config_dir, sample_rate):
        """Different sample rates should be preserved in config."""
        c = Config(sample_rate=sample_rate)
        c.save()
        loaded = Config.load()
        assert loaded.sample_rate == sample_rate

    @pytest.mark.parametrize("beam_size", [1, 2, 3, 5])
    def test_various_beam_sizes(self, tmp_path, tmp_config_dir, beam_size):
        """Different beam sizes should be preserved in config."""
        c = Config(beam_size=beam_size)
        c.save()
        loaded = Config.load()
        assert loaded.beam_size == beam_size

    @pytest.mark.parametrize("autostart", [True, False])
    def test_autostart_roundtrip(self, tmp_path, tmp_config_dir, autostart):
        """Autostart flag should survive save/load roundtrip."""
        c = Config(autostart=autostart)
        c.save()
        loaded = Config.load()
        assert loaded.autostart == autostart

    @pytest.mark.parametrize("paste_on_stop", [True, False])
    def test_paste_on_stop_roundtrip(self, tmp_path, tmp_config_dir, paste_on_stop):
        """paste_on_stop flag should survive save/load roundtrip."""
        c = Config(paste_on_stop=paste_on_stop)
        c.save()
        loaded = Config.load()
        assert loaded.paste_on_stop == paste_on_stop

    @pytest.mark.parametrize("show_notifications", [True, False])
    def test_show_notifications_roundtrip(self, tmp_path, tmp_config_dir, show_notifications):
        """show_notifications flag should survive save/load roundtrip."""
        c = Config(show_notifications=show_notifications)
        c.save()
        loaded = Config.load()
        assert loaded.show_notifications == show_notifications

    @pytest.mark.parametrize("streaming_transcription", [True, False])
    def test_streaming_transcription_roundtrip(self, tmp_path, tmp_config_dir, streaming_transcription):
        """streaming_transcription flag should survive save/load roundtrip."""
        c = Config(streaming_transcription=streaming_transcription)
        c.save()
        loaded = Config.load()
        assert loaded.streaming_transcription == streaming_transcription

    @pytest.mark.parametrize(
        "corrupt_content",
        [
            "NOT VALID JSON {{{",
            "",
            "{",
            "null",
            "[]",
            "42",
            '"string"',
        ],
    )
    def test_various_corrupt_config_files(self, tmp_path, tmp_config_dir, corrupt_content):
        """Various types of corrupt config files should fall back to defaults."""
        config_file = tmp_path / "config.json"
        config_file.write_text(corrupt_content)
        c = Config.load()
        # Should return defaults, not crash
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert c.sample_rate == 16000


# ──────────────────────────────────────────────────────────────────────────
# validate_config_update accumulates ALL errors (was: break on first)
# ──────────────────────────────────────────────────────────────────────────


class TestDeprecatedFieldsScrubbedOnLoad:
    """existing ``config.json`` files written by older app versions
    that still carry the 7 now-removed deprecated fields MUST load without
    raising. The unknown-key filter in ``Config.load()`` (``data = {k: v
    for k, v in parsed.items() if k in cls.__dataclass_fields__}``) silently
    drops them before ``cls(**data)`` constructs the Config instance, and
    the v3 schema migration (``_migrate_to_v3``) scrubs them as a
    defense-in-depth backstop for any code path that bypasses the filter.
    """

    REMOVED_FIELDS = [
        "silence_rms_threshold",
        "silence_peak_threshold",
        "normalize_audio",
        "normalize_target_peak",
        "volume_duck_per_session",
        "volume_duck_smart",
        "noise_filter_gate_threshold",
    ]

    def test_config_with_deprecated_fields_loads_without_error(self, tmp_path, tmp_config_dir):
        """A ``config.json`` carrying all 7 removed deprecated fields loads
        without raising ``TypeError`` and the resulting Config instance
        does NOT expose the removed fields as attributes."""
        config_file = tmp_path / "config.json"
        stale_config = {"schema_version": 2, "hotkey": "<f9>"}
        for field in self.REMOVED_FIELDS:
            stale_config[field] = 0.5 if "threshold" in field or "peak" in field else True
        config_file.write_text(json.dumps(stale_config))

        c = Config.load()

        # Sanity: the non-deprecated field survived.
        assert c.hotkey == "<f9>"
        # Schema was bumped to v3 by the migration.
        assert c.schema_version == _CURRENT_SCHEMA_VERSION
        # The removed fields are NOT attributes on the Config instance.
        for field in self.REMOVED_FIELDS:
            assert not hasattr(c, field), f"Removed field {field!r} should NOT be on the Config instance"

    def test_config_with_deprecated_fields_at_schema_v3_loads(self, tmp_path, tmp_config_dir):
        """A ``config.json`` at schema_version=3 with the deprecated fields
        still present (e.g. written by a buggy migrator that didn't pop
        them) is handled gracefully by the unknown-key filter — the keys
        are silently dropped (with a WARNING log) and the remaining
        fields load normally. No fallback to defaults occurs."""
        config_file = tmp_path / "config.json"
        stale_config = {"schema_version": 3, "hotkey": "<f9>", "silence_rms_threshold": 0.5}
        config_file.write_text(json.dumps(stale_config))

        c = Config.load()
        # The non-deprecated field survived — no fallback to defaults.
        assert c.hotkey == "<f9>"
        # The deprecated field is NOT on the instance.
        assert not hasattr(c, "silence_rms_threshold")


# ──────────────────────────────────────────────────────────────────────────
# validator and migration function return types
# ──────────────────────────────────────────────────────────────────────────
