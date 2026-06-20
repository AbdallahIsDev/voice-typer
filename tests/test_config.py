"""Tests for config load/save and field behavior."""

import json
import sys
import pytest
from pathlib import Path
from unittest.mock import patch

from voice_typer.server.config import Config, _config_dir


class TestConfigDefaults:
    def test_default_values(self):
        c = Config()
        assert c.hotkey == "<f2>"
        assert c.sample_rate == 16000
        assert c.microphone is None
        assert c.model_size == "small.en"
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
    def test_save_creates_config_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(hotkey="<f3>", autostart=True)
        c.save()

        config_file = tmp_path / "config.json"
        assert config_file.exists()

        data = json.loads(config_file.read_text())
        assert data["hotkey"] == "<f3>"
        assert data["autostart"] is True

    def test_load_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config.load()
        assert c.hotkey == "<f2>"
        assert c.autostart is True

    def test_load_reads_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "hotkey": "<f9>",
            "microphone": "WO Mic",
            "autostart": True,
            "paste_on_stop": False,
            "show_notifications": False,
        }))

        c = Config.load()
        assert c.hotkey == "<f9>"
        assert c.microphone == "WO Mic"
        assert c.autostart is True
        # P1 fix: User values are now preserved (no longer overridden)
        assert c.paste_on_stop is False
        assert c.show_notifications is False

    def test_load_preserves_user_device_and_paste_settings(self, tmp_path, monkeypatch):
        """P1 fix: User's device, paste_on_stop, and streaming_transcription
        values in config.json must survive load() without being overridden."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "streaming_transcription": False,
            "paste_on_stop": False,
            "device": "cpu",
        }))

        c = Config.load()
        # User values must be preserved — no more forced overrides
        assert c.streaming_transcription is False
        assert c.paste_on_stop is False
        assert c.device == "cpu"

    def test_load_raises_streaming_overlap_and_guard_to_safer_minimums(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "streaming_left_overlap_seconds": 2.0,
            "streaming_right_guard_seconds": 1.0,
        }))

        c = Config.load()

        assert c.streaming_left_overlap_seconds == 3.0
        assert c.streaming_right_guard_seconds == 1.5

    @pytest.mark.parametrize("legacy_model", ["large-v3", "base.en", "unsupported"])
    def test_load_normalizes_legacy_or_unsupported_model_to_small_en(
        self, tmp_path, monkeypatch, legacy_model
    ):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"model_size": legacy_model}))

        c = Config.load()
        assert c.model_size == "small.en"

    def test_load_keeps_medium_en_model(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"model_size": "medium.en"}))

        c = Config.load()
        assert c.model_size == "medium.en"

    def test_load_ignores_unknown_keys(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "hotkey": "<f5>",
            "bogus_key": "should be ignored",
        }))

        c = Config.load()
        assert c.hotkey == "<f5>"
        assert not hasattr(c, "bogus_key")

    def test_load_returns_defaults_on_corrupt_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")

        c = Config.load()
        assert c.hotkey == "<f2>"  # defaults

    def test_load_logs_error_on_corrupt_file(self, tmp_path, monkeypatch, caplog):
        """P1 fix: Config.load() must log errors instead of silently swallowing them."""
        import logging
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")

        with caplog.at_level(logging.ERROR, logger="voice_typer.server.config"):
            c = Config.load()

        assert any("corrupted" in r.message.lower() or "failed to load" in r.message.lower()
                    for r in caplog.records)

    def test_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c1 = Config(
            hotkey="<f7>",
            microphone="Blue Yeti",
            model_size="medium.en",
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
    """P5 fix: qwen_model_path and corrections_path are validated on load."""

    def test_qwen_model_path_invalid_resets_to_none(self, tmp_path, monkeypatch):
        """If qwen_model_path points to a non-existent directory, reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "asr_backend": "qwen",
            "qwen_model_path": "/nonexistent/path/to/model",
        }))

        c = Config.load()
        assert c.qwen_model_path is None

    def test_qwen_model_path_file_not_dir_resets_to_none(self, tmp_path, monkeypatch):
        """If qwen_model_path points to a file (not a directory), reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        # Create a file (not a directory) at the path
        fake_model = tmp_path / "model_file"
        fake_model.write_text("not a directory")
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "qwen_model_path": str(fake_model),
        }))

        c = Config.load()
        assert c.qwen_model_path is None

    def test_qwen_model_path_valid_dir_preserved(self, tmp_path, monkeypatch):
        """If qwen_model_path points to an existing directory, preserve it."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        model_dir = tmp_path / "qwen_model"
        model_dir.mkdir()
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "qwen_model_path": str(model_dir),
        }))

        c = Config.load()
        assert c.qwen_model_path == str(model_dir)

    def test_corrections_path_invalid_resets_to_none(self, tmp_path, monkeypatch):
        """If corrections_path points to a non-existent file, reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "corrections_path": "/nonexistent/corrections.json",
        }))

        c = Config.load()
        assert c.corrections_path is None

    def test_corrections_path_dir_not_file_resets_to_none(self, tmp_path, monkeypatch):
        """If corrections_path points to a directory (not a file), reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        corrections_dir = tmp_path / "corrections_dir"
        corrections_dir.mkdir()
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "corrections_path": str(corrections_dir),
        }))

        c = Config.load()
        assert c.corrections_path is None

    def test_corrections_path_valid_file_preserved(self, tmp_path, monkeypatch):
        """If corrections_path points to an existing file, preserve it."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        corrections_file = tmp_path / "corrections.json"
        corrections_file.write_text('{"misspellings": {}}')
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "corrections_path": str(corrections_file),
        }))

        c = Config.load()
        assert c.corrections_path == str(corrections_file)

    def test_none_paths_pass_validation(self, tmp_path, monkeypatch):
        """None values for qwen_model_path and corrections_path are valid."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({
            "qwen_model_path": None,
            "corrections_path": None,
        }))

        c = Config.load()
        assert c.qwen_model_path is None
        assert c.corrections_path is None


class TestAtomicConfigSave:
    """P0 fix: Config.save() must be atomic to prevent data loss on crash."""

    def test_save_uses_tmp_file_then_replace(self, tmp_path, monkeypatch):
        """save() writes to .tmp first then atomically replaces config.json."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(hotkey="<f5>")
        c.save()

        config_file = tmp_path / "config.json"
        tmp_file = tmp_path / "config.tmp"

        assert config_file.exists()
        assert not tmp_file.exists()

        data = json.loads(config_file.read_text())
        assert data["hotkey"] == "<f5>"

    def test_save_preserves_existing_config_on_partial_write(self, tmp_path, monkeypatch):
        """If a write fails mid-stream, the existing config.json is preserved."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c1 = Config(hotkey="<f3>")
        c1.save()

        config_file = tmp_path / "config.json"
        original_data = config_file.read_text()

        c2 = Config(hotkey="<f9>")
        with patch("builtins.open", side_effect=OSError("disk full")):
            try:
                c2.save()
            except OSError:
                pass

        assert config_file.read_text() == original_data

    def test_no_stale_tmp_file_after_successful_save(self, tmp_path, monkeypatch):
        """After a successful save, no .tmp file should remain."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        c.save()

        assert not (tmp_path / "config.tmp").exists()
        assert (tmp_path / "config.json").exists()


class TestH1NonNumericFieldValidation:
    """H1: No type validation on loaded JSON config values."""

    def test_bool_field_coerces_truthy_string(self, tmp_path, monkeypatch):
        """Non-bool truthy value for bool field should be coerced."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"autostart": "true"}))
        c = Config.load()
        assert c.autostart is True

    def test_bool_field_coerces_zero(self, tmp_path, monkeypatch):
        """Zero for bool field should be coerced to False."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"paste_on_stop": 0}))
        c = Config.load()
        assert c.paste_on_stop is False

    def test_bool_field_resets_invalid_value(self, tmp_path, monkeypatch):
        """Invalid value for bool field should reset to default."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"autostart": [1, 2]}))
        c = Config.load()
        assert c.autostart is True  # default

    def test_str_field_resets_non_string(self, tmp_path, monkeypatch):
        """Non-string value for str field should reset to default."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": 42}))
        c = Config.load()
        assert c.hotkey == "<f2>"  # default

    def test_str_field_keeps_valid_string(self, tmp_path, monkeypatch):
        """Valid string value for str field should be preserved."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"language": "fr"}))
        c = Config.load()
        assert c.language == "fr"

    def test_silence_warning_seconds_default(self):
        """H12 config fields should have correct defaults."""
        c = Config()
        assert c.silence_warning_seconds == 20.0
        assert c.silence_auto_stop_seconds == 120.0
        assert c.max_recording_seconds_gpu == 1200
        assert c.max_recording_seconds_cpu == 600

    def test_startup6_int_field_not_treated_as_bool(self, tmp_path, monkeypatch, caplog):
        """STARTUP-6: volume_duck_smart_poll_interval_ms (int) must NOT be
        flagged as an invalid bool when loading its default value 500.

        Previously this field was misclassified in bool_fields, causing the
        bool validator to log a spurious
        "had invalid value 500, resetting to default 500" warning on every
        startup. The value 500 is the default and is in the valid 50-5000
        range; no warning should fire.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        # Write the default value explicitly — this is what Config.save() produces
        config_file.write_text(json.dumps({"volume_duck_smart_poll_interval_ms": 500}))
        import logging
        with caplog.at_level(logging.WARNING):
            c = Config.load()
        assert c.volume_duck_smart_poll_interval_ms == 500
        # No "invalid value" warning should be logged for this field
        assert not any(
            "volume_duck_smart_poll_interval_ms" in rec.message
            and "invalid value" in rec.message
            for rec in caplog.records
        ), f"Spurious validation warning logged: {[r.message for r in caplog.records]}"

    def test_startup6_int_field_preserves_user_value(self, tmp_path, monkeypatch):
        """STARTUP-6: a non-default but in-range int value should also
        be preserved without being coerced or warned about."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"volume_duck_smart_poll_interval_ms": 1500}))
        c = Config.load()
        assert c.volume_duck_smart_poll_interval_ms == 1500


class TestM3ConfigSchemaVersion:
    """M3: No config schema versioning."""

    def test_config_has_schema_version_field(self):
        from voice_typer.server.config import Config
        c = Config()
        assert hasattr(c, "schema_version")
        assert c.schema_version == 1

    def test_config_save_load_preserves_schema_version(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        c.save()
        loaded = Config.load()
        assert loaded.schema_version == 1

    def test_config_migration_from_version_0(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config
        import json
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f3>", "model_size": "small.en"}))
        loaded = Config.load()
        assert loaded.schema_version == 1
        assert loaded.hotkey == "<f3>"


class TestM4SaveErrorHandling:
    """M4: save() has no error handling."""

    def test_save_returns_false_on_permission_error(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        import json
        original_dump = json.dump
        def failing_dump(*args, **kwargs):
            raise OSError("disk full")
        monkeypatch.setattr("json.dump", failing_dump)
        result = c.save()
        assert result is False

    def test_save_returns_true_on_success(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        result = c.save()
        assert result is True


# ── SEC-007: config file permissions ─────────────────────────────────────


class TestSec007ConfigFilePermissions:
    """SEC-007: on POSIX, the config file must be 0o600 and the
    config directory 0o700 so API keys and other settings are not
    world-readable.  On Windows these checks are skipped (NTFS ACLs
    are the relevant control, and the config dir is already under
    %APPDATA% which is per-user)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_save_creates_config_file_with_0600_permissions(self, tmp_path, monkeypatch):
        import os, stat
        from voice_typer.server.config import Config
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        cfg = Config()
        cfg.cloud_api_key = "sk-test-secret"
        cfg.save()
        config_file = tmp_path / "config.json"
        assert config_file.exists()
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600, f"expected 0o600, got 0o{mode:o}"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_save_creates_config_dir_with_0700_permissions(self, tmp_path, monkeypatch):
        import os, stat
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
    def test_save_preserves_0600_on_existing_file(self, tmp_path, monkeypatch):
        """A second save() must keep the 0o600 permissions, not drift
        back to default umask."""
        import os, stat
        from voice_typer.server.config import Config
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        cfg = Config()
        cfg.cloud_api_key = "first"
        cfg.save()
        cfg.cloud_api_key = "second"
        cfg.save()
        config_file = tmp_path / "config.json"
        mode = stat.S_IMODE(os.stat(config_file).st_mode)
        assert mode == 0o600
