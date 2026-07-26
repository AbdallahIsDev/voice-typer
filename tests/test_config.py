"""Tests for config load/save and field behavior."""

import contextlib
import json
import sys
from unittest.mock import patch

import pytest
from voice_typer.server.config import _CURRENT_SCHEMA_VERSION, Config, _default_hotkey_for_platform

# NATIVE-001: the default hotkey is now platform-aware
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
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert c.autostart is True

    def test_load_reads_existing_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
        # P1 fix: User values are now preserved (no longer overridden)
        assert c.paste_on_stop is False
        assert c.show_notifications is False

    def test_load_preserves_user_device_and_paste_settings(self, tmp_path, monkeypatch):
        """P1 fix: User's device, paste_on_stop, and streaming_transcription
        values in config.json must survive load() without being overridden."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_load_raises_streaming_overlap_and_guard_to_safer_minimums(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    @pytest.mark.parametrize("legacy_model", ["large-v3", "base.en", "unsupported"])
    def test_load_normalizes_legacy_or_unsupported_model_to_small_en(self, tmp_path, monkeypatch, legacy_model):
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

    def test_load_returns_defaults_on_corrupt_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")

        c = Config.load()
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY  # defaults

    def test_load_logs_error_on_corrupt_file(self, tmp_path, monkeypatch, caplog):
        """P1 fix: Config.load() must log instead of silently swallowing failures.

        RW-9: the level was lowered from ERROR to WARNING (recovery to
        defaults is a recoverable event, not a fatal error) and the
        message now includes the exception class name and file path so
        the user can see *why* their settings were reset.
        """
        import logging

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text("NOT VALID JSON {{{")

        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()

        # RW-9: warning must include the failure-mode name and file path.
        assert any("JSONDecodeError" in r.message for r in caplog.records)
        assert any(str(config_file) in r.message for r in caplog.records)

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

    def test_qwen_model_path_file_not_dir_resets_to_none(self, tmp_path, monkeypatch):
        """If qwen_model_path points to a file (not a directory), reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_qwen_model_path_valid_dir_preserved(self, tmp_path, monkeypatch):
        """If qwen_model_path points to an existing directory, preserve it."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_corrections_path_invalid_resets_to_none(self, tmp_path, monkeypatch):
        """If corrections_path points to a non-existent file, reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_corrections_path_dir_not_file_resets_to_none(self, tmp_path, monkeypatch):
        """If corrections_path points to a directory (not a file), reset to None."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_corrections_path_valid_file_preserved(self, tmp_path, monkeypatch):
        """If corrections_path points to an existing file, preserve it."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_none_paths_pass_validation(self, tmp_path, monkeypatch):
        """None values for qwen_model_path and corrections_path are valid."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
        # NEW-SEC-008: save() now delegates to _secure_atomic_write.
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
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY  # default

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
        assert c.stop_on_silence_seconds == 60.0
        # SIMPLIFY-001: single explicit field replaces the old 3-field split
        assert c.max_recording_time_seconds == 900

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
            "volume_duck_smart_poll_interval_ms" in rec.message and "invalid value" in rec.message
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
        assert c.schema_version == _CURRENT_SCHEMA_VERSION

    def test_config_save_load_preserves_schema_version(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        c.save()
        loaded = Config.load()
        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION

    def test_config_migration_from_version_0(self, tmp_path, monkeypatch):
        import json

        from voice_typer.server.config import Config

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": "<f3>", "model_size": "small.en"}))
        loaded = Config.load()
        assert loaded.schema_version == _CURRENT_SCHEMA_VERSION
        assert loaded.hotkey == "<f3>"


class TestM4SaveErrorHandling:
    """M4: save() has no error handling."""

    def test_save_returns_false_on_permission_error(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()

        # NEW-SEC-008: save() now uses json.dumps (string) not json.dump (file).
        # Mock json.dumps to raise OSError so the test verifies save()
        # returns False on error.
        def failing_dumps(*args, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr("json.dumps", failing_dumps)
        result = c.save()
        assert result is False

    def test_save_returns_true_on_success(self, tmp_path, monkeypatch):
        from voice_typer.server.config import Config

        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config()
        result = c.save()
        assert result is True


# ── SEC-007: config file permissions ─────────────────────────────────────


class TestConfigSaveEnforcesPosixFilePermissions:
    """SEC-007: on POSIX, the config file must be 0o600 and the
    config directory 0o700 so API keys and other settings are not
    world-readable.  On Windows these checks are skipped (NTFS ACLs
    are the relevant control, and the config dir is already under
    %APPDATA% which is per-user)."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only")
    def test_save_creates_config_file_with_0600_permissions(self, tmp_path, monkeypatch):
        import os
        import stat

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
    def test_save_preserves_0600_on_existing_file(self, tmp_path, monkeypatch):
        """A second save() must keep the 0o600 permissions, not drift
        back to default umask."""
        import os
        import stat

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


# ── TEST-032: Parametrized config tests ─────────────────────────────────


class TestConfigParametrized:
    """TEST-032: Use @pytest.mark.parametrize for multiple config values."""

    @pytest.mark.parametrize(
        "field,value",
        [
            ("hotkey", "<f5>"),
            ("hotkey", "<caps_lock>"),
            ("language", "fr"),
            ("language", "zh"),
            ("model_size", "medium.en"),
            ("device", "cpu"),
            ("device", "cuda"),
        ],
    )
    def test_config_field_roundtrip(self, tmp_path, monkeypatch, field, value):
        """Config field values should survive save/load roundtrip."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(**{field: value})
        c.save()
        loaded = Config.load()
        assert getattr(loaded, field) == value

    @pytest.mark.parametrize("sample_rate", [8000, 16000, 22050, 44100, 48000])
    def test_various_sample_rates(self, tmp_path, monkeypatch, sample_rate):
        """Different sample rates should be preserved in config."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(sample_rate=sample_rate)
        c.save()
        loaded = Config.load()
        assert loaded.sample_rate == sample_rate

    @pytest.mark.parametrize("beam_size", [1, 2, 3, 5])
    def test_various_beam_sizes(self, tmp_path, monkeypatch, beam_size):
        """Different beam sizes should be preserved in config."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(beam_size=beam_size)
        c.save()
        loaded = Config.load()
        assert loaded.beam_size == beam_size

    @pytest.mark.parametrize("autostart", [True, False])
    def test_autostart_roundtrip(self, tmp_path, monkeypatch, autostart):
        """Autostart flag should survive save/load roundtrip."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(autostart=autostart)
        c.save()
        loaded = Config.load()
        assert loaded.autostart == autostart

    @pytest.mark.parametrize("paste_on_stop", [True, False])
    def test_paste_on_stop_roundtrip(self, tmp_path, monkeypatch, paste_on_stop):
        """paste_on_stop flag should survive save/load roundtrip."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(paste_on_stop=paste_on_stop)
        c.save()
        loaded = Config.load()
        assert loaded.paste_on_stop == paste_on_stop

    @pytest.mark.parametrize("show_notifications", [True, False])
    def test_show_notifications_roundtrip(self, tmp_path, monkeypatch, show_notifications):
        """show_notifications flag should survive save/load roundtrip."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c = Config(show_notifications=show_notifications)
        c.save()
        loaded = Config.load()
        assert loaded.show_notifications == show_notifications

    @pytest.mark.parametrize("streaming_transcription", [True, False])
    def test_streaming_transcription_roundtrip(self, tmp_path, monkeypatch, streaming_transcription):
        """streaming_transcription flag should survive save/load roundtrip."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
    def test_various_corrupt_config_files(self, tmp_path, monkeypatch, corrupt_content):
        """Various types of corrupt config files should fall back to defaults."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        config_file.write_text(corrupt_content)
        c = Config.load()
        # Should return defaults, not crash
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert c.sample_rate == 16000


# ──────────────────────────────────────────────────────────────────────────
# CFG-5: validate_config_update accumulates ALL errors (was: break on first)
# ──────────────────────────────────────────────────────────────────────────


class TestCfg5AccumulateAllErrors:
    """CFG-5 (Low): ``validate_config_update`` previously stopped at the
    first invalid field (``break``), forcing the user to fix-and-resubmit
    N times to discover N problems.  The fix accumulates ALL errors so
    the renderer can surface every problem in a single round-trip.

    Atomicity is preserved: the dispatcher still rejects the entire
    payload when ANY error is present (no partial apply), but the error
    list now carries every invalid field, not just the first.
    """

    def test_three_invalid_fields_return_three_errors(self):
        """Three distinct validation failures produce three error strings."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "model_size": "not-a-real-model",  # enum violation
                "beam_size": 999,  # range violation
                "autostart": "yes",  # type violation
            }
        )
        assert len(errors) == 3, f"Expected 3 errors (one per invalid field); got {len(errors)}: {errors}"
        # Each error should reference the field it's about.
        joined = " ".join(errors)
        assert "model_size" in joined
        assert "beam_size" in joined
        assert "autostart" in joined

    def test_valid_fields_are_still_in_validated_when_errors_present(self):
        """When some fields are valid and others are invalid, the valid
        ones appear in ``validated`` (the dispatcher still ignores
        ``validated`` when ``errors`` is non-empty, but it's preserved
        for introspection/testing)."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "hotkey": "<f4>",  # valid
                "autostart": "yes",  # invalid
                "language": "fr",  # valid
            }
        )
        assert errors  # at least one error
        # The valid fields are in validated; the invalid one is NOT.
        assert validated.get("hotkey") == "<f4>"
        assert validated.get("language") == "fr"
        assert "autostart" not in validated

    def test_single_invalid_field_still_returns_single_error(self):
        """Backwards compat: a single invalid field still returns
        exactly one error (no regression for the common case)."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "model_size": "not-a-real-model",
            }
        )
        assert len(errors) == 1
        assert "model_size" in errors[0]
        assert validated == {}

    def test_no_errors_when_all_fields_valid(self):
        """All-valid payload returns zero errors and all fields validated."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "hotkey": "<f4>",
                "autostart": False,
                "language": "fr",
            }
        )
        assert errors == []
        assert validated == {"hotkey": "<f4>", "autostart": False, "language": "fr"}

    def test_unknown_keys_silently_dropped_even_when_errors_present(self):
        """Unknown keys are silently dropped (debug-logged), regardless
        of whether other fields produced errors.  They never appear in
        the error list."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "totally_unknown_field": "x",
                "another_unknown": 42,
                "model_size": "not-a-real-model",  # invalid
            }
        )
        # Only the known-but-invalid field produced an error.
        assert len(errors) == 1
        assert "model_size" in errors[0]
        # None of the unknown keys appear in errors OR validated.
        assert "totally_unknown_field" not in validated
        assert "another_unknown" not in validated
        for err in errors:
            assert "totally_unknown_field" not in err
            assert "another_unknown" not in err

    def test_type_error_and_range_error_both_returned(self):
        """A type error (wrong type) and a range error (right type, bad
        value) on different fields both surface — the function doesn't
        abort after the first kind."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "beam_size": "not-an-int",  # type error
                "best_of": 999,  # range error (must be in [1, 10])
            }
        )
        assert len(errors) == 2
        assert any("beam_size" in e for e in errors)
        assert any("best_of" in e for e in errors)


# ──────────────────────────────────────────────────────────────────────────
# CFG-6: string validators reject control characters
# ──────────────────────────────────────────────────────────────────────────


class TestCfg6ControlCharRejection:
    """CFG-6 (Low): ``_make_str_validator`` and
    ``_make_optional_str_validator`` previously accepted any string
    under the length cap, including strings with embedded C0 control
    characters (newline, tab, NUL, etc.).  These are never part of a
    legitimate config value (hotkey, language code, API key, URL, model
    name) and are a classic log-poisoning / header-injection vector
    when echoed into logs or HTTP headers.
    """

    @pytest.mark.parametrize(
        "char",
        ["\x00", "\x01", "\x05", "\n", "\r", "\t", "\x1f", "\x7f"],
        ids=["NUL", "SOH", "ENQ", "LF", "CR", "TAB", "US", "DEL"],
    )
    def test_str_validator_rejects_control_char(self, char):
        """Each C0 control char and DEL is rejected by _make_str_validator."""
        from voice_typer.server.config_validators import _make_str_validator

        v = _make_str_validator()
        result = v(f"hello{char}world")
        assert result is not None, f"Control char {char!r} should be rejected (CFG-6)"
        assert "control" in result.lower()

    def test_str_validator_accepts_clean_string(self):
        """A string without control characters is accepted."""
        from voice_typer.server.config_validators import _make_str_validator

        v = _make_str_validator()
        assert v("hello world") is None
        assert v("api-key_sk-abc123") is None
        assert v("https://api.example.com/v1") is None
        # Unicode is fine — only C0 control chars + DEL are rejected.
        assert v("héllo wörld 中文") is None

    def test_str_validator_accepts_high_codepoints(self):
        """High Unicode codepoints (>= 0x80) are NOT control chars and
        must pass.  Only C0 (0x00-0x1f) and DEL (0x7f) are rejected."""
        from voice_typer.server.config_validators import _make_str_validator

        v = _make_str_validator()
        # 0x80 (PAD), 0xA0 (NBSP), emoji, CJK — all OK.
        assert v("café\x80") is None
        assert v("\xa0space") is None
        assert v("emoji 😀") is None

    def test_optional_str_validator_rejects_control_char(self):
        """_make_optional_str_validator (used for ``microphone``) also
        rejects control characters in non-None values."""
        from voice_typer.server.config_validators import _make_optional_str_validator

        v = _make_optional_str_validator()
        assert v(None) is None  # None is always allowed
        result = v("mic\ntest")
        assert result is not None
        assert "control" in result.lower()

    def test_str_validator_via_ipc_rejects_control_char_in_api_key(self):
        """End-to-end: a ``cloud_api_key`` with a newline is rejected
        by ``validate_config_update`` (which uses _make_str_validator)."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "cloud_api_key": "sk-test\nX-Injected-Header: evil",
            }
        )
        assert len(errors) == 1
        assert "control" in errors[0].lower()
        assert "cloud_api_key" not in validated

    def test_str_validator_via_ipc_rejects_nul_in_language(self):
        """End-to-end: a ``language`` code with a NUL byte is rejected."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "language": "en\x00fr",
            }
        )
        assert len(errors) == 1
        assert "control" in errors[0].lower()


# ──────────────────────────────────────────────────────────────────────────
# CFG-7: URL validator rejects embedded credentials
# ──────────────────────────────────────────────────────────────────────────


class TestCfg7UrlCredentialsRejection:
    """CFG-7 (Low): ``_make_url_validator`` previously accepted URLs
    with embedded credentials (``user:pass@host``).  Such URLs are a
    credentials-leak vector: the renderer would otherwise persist them
    to config.json on disk, echo them into logs, and potentially leak
    them to a proxy.  Legitimate API endpoints (OpenAI, Groq,
    Deepgram, Ollama) never use embedded credentials — auth is via
    the ``X-Api-Key`` / ``Authorization`` header, supplied separately.
    """

    def test_rejects_user_password_url(self):
        """A URL with both username and password is rejected."""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        result = v("https://user:pass@api.example.com/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_rejects_user_only_url(self):
        """A URL with username but no password is also rejected —
        the username alone is a credential leak."""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        result = v("https://user@api.example.com/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_rejects_password_only_url(self):
        """A URL with password but no username is also rejected
        (urlparse exposes this as .password only)."""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        # ``:pass@host`` — urlparse parses this as password-only.
        result = v("https://:pass@api.example.com/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_accepts_url_without_credentials(self):
        """A plain HTTPS URL is accepted (no credentials)."""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        assert v("https://api.openai.com/v1/chat/completions") is None
        assert v("https://api.groq.com/openai/v1/chat/completions") is None

    def test_accepts_loopback_http_without_credentials(self):
        """Loopback HTTP (local dev server) is still accepted when no
        credentials are present."""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        assert v("http://localhost:11434/v1/chat/completions") is None

    def test_rejects_credentials_on_loopback_too(self):
        """CFG-7 applies even to loopback URLs — credentials are
        rejected regardless of host.  (A local dev server shouldn't
        need embedded credentials either; use a separate header.)"""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        result = v("http://admin:secret@localhost:11434/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_rejects_credentials_via_ipc_set_config(self):
        """End-to-end: ``set_config`` with a credential-bearing
        ``llm_api_url`` is rejected by ``validate_config_update``."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "llm_api_url": "https://sk-leaked-key@api.openai.com/v1/chat",
            }
        )
        assert len(errors) == 1
        assert "credential" in errors[0].lower()
        assert "llm_api_url" not in validated

    def test_rejects_credentials_via_ipc_cloud_api_url(self):
        """Same rejection for ``cloud_api_url``."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "cloud_api_url": "https://user:pass@attacker.example.net/audio",
            }
        )
        # The URL is also HTTP non-loopback, so it might trip the
        # HTTPS-required check first.  Either way, it's rejected.
        assert len(errors) >= 1
        assert "cloud_api_url" not in validated
        # If the credential check ran, the message mentions credentials.
        # If the HTTPS check ran first, the message mentions HTTPS.
        # Both are acceptable rejections — we just need the URL rejected.
        assert any("credential" in e.lower() or "HTTPS" in e or "loopback" in e.lower() for e in errors)


# ──────────────────────────────────────────────────────────────────────────
# CFG-8: deprecated fields removed from IPC_CONFIG_ALLOWLIST
# ──────────────────────────────────────────────────────────────────────────


class TestCfg8DeprecatedFieldsRemoved:
    """CFG-8 (Low): the deprecated noise-filter and volume-duck fields
    were still in ``IPC_CONFIG_ALLOWLIST``, letting a malicious IPC
    client mutate dead Config fields.  The renderer's Settings UI
    never sends them via ``set_config`` (they were superseded by the
    new ADR 0007 §5.1 filter-chain fields), so removing them from the
    allowlist is safe — they're silently dropped, just like any other
    unknown key.

    The Config dataclass still carries the deprecated fields for
    backward-compat with old config.json files on disk.
    """

    DEPRECATED_REMOVED = [
        "noise_filter_enabled",
        "noise_filter_gate_threshold",
        "noise_filter_rnnoise",
        "noise_filter_post_capture",
        "volume_duck_per_session",
        "volume_duck_smart",
        # GT-58: also removed from IPC_CONFIG_ALLOWLIST — these were
        # declared, validated, and persisted but never read at runtime
        # (ADR 0007 §4.3 / §5.2). The Config dataclass fields themselves
        # were also removed; existing config.json values are silently
        # scrubbed by the v3 schema migration.
        "silence_rms_threshold",
        "silence_peak_threshold",
        "normalize_audio",
        "normalize_target_peak",
        # GT-F2-8: removed from IPC allowlist to match the TS-side
        # contract (config.ts documents this as a write-only back-compat
        # field the renderer MUST NOT write). The Config dataclass field
        # is retained — only the IPC write path is closed.
        "push_to_talk_hotkey",
    ]

    NON_DEPRECATED_KEPT = [
        # Non-deprecated noise-filter fields still present.
        "noise_filter_highpass",
        "noise_filter_highpass_cutoff_hz",
        "noise_filter_gate",
        "noise_filter_gate_hold_ms",
        "noise_suppression_method",
        "noise_filter_gate_open_threshold_db",
        "noise_filter_eq",
        "noise_filter_compressor",
        "noise_filter_limiter",
        "noise_filter_notch",
        # Non-deprecated volume-duck fields still present.
        "volume_duck_enabled",
        "volume_duck_level",
        "volume_duck_fade_ms",
        "volume_duck_smart_poll_interval_ms",
    ]

    def test_deprecated_fields_absent_from_allowlist(self):
        """Each deprecated field is NO LONGER in IPC_CONFIG_ALLOWLIST."""
        from voice_typer.server.config import IPC_CONFIG_ALLOWLIST

        for field in self.DEPRECATED_REMOVED:
            assert field not in IPC_CONFIG_ALLOWLIST, (
                f"Deprecated field {field!r} should be removed from IPC_CONFIG_ALLOWLIST (CFG-8)"
            )

    def test_non_deprecated_fields_still_present(self):
        """Sanity: the non-deprecated filter/duck fields ARE still in
        the allowlist (the fix doesn't over-prune)."""
        from voice_typer.server.config import IPC_CONFIG_ALLOWLIST

        for field in self.NON_DEPRECATED_KEPT:
            assert field in IPC_CONFIG_ALLOWLIST, (
                f"Non-deprecated field {field!r} should still be in IPC_CONFIG_ALLOWLIST (CFG-8 doesn't over-prune)"
            )

    def test_deprecated_fields_silently_dropped_by_validate_config_update(self):
        """Setting a deprecated field via ``set_config`` is silently
        dropped (no error, no apply) — same contract as any other
        unknown key.  This preserves the existing
        ``test_ignores_unknown_fields_without_crashing`` contract."""
        from voice_typer.server.config import validate_config_update

        payload = {field: True for field in self.DEPRECATED_REMOVED}
        # Use float for the threshold field (the others are bool).
        payload["noise_filter_gate_threshold"] = 0.05
        validated, errors = validate_config_update(payload)
        # No errors — deprecated fields are silently dropped, not rejected.
        assert errors == [], f"Deprecated fields should be silently dropped, not raise errors; got: {errors}"
        # None of the deprecated fields appear in validated.
        for field in self.DEPRECATED_REMOVED:
            assert field not in validated

    def test_deprecated_and_valid_fields_in_same_payload(self):
        """A payload with both deprecated and valid fields: valid fields
        are applied, deprecated fields are silently dropped, no errors."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update(
            {
                "noise_filter_enabled": True,  # deprecated, dropped
                "volume_duck_smart": False,  # deprecated, dropped
                "noise_filter_highpass": False,  # valid, applied
                "volume_duck_enabled": True,  # valid, applied
                "hotkey": "<f4>",  # valid, applied
            }
        )
        assert errors == []
        assert validated == {
            "noise_filter_highpass": False,
            "volume_duck_enabled": True,
            "hotkey": "<f4>",
        }


# ──────────────────────────────────────────────────────────────────────────
# GT-58: deprecated fields silently scrubbed on load (backward compat)
# ──────────────────────────────────────────────────────────────────────────


class TestGT58DeprecatedFieldsScrubbedOnLoad:
    """GT-58: existing ``config.json`` files written by older app versions
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

    def test_config_with_deprecated_fields_loads_without_error(self, tmp_path, monkeypatch):
        """A ``config.json`` carrying all 7 removed deprecated fields loads
        without raising ``TypeError`` and the resulting Config instance
        does NOT expose the removed fields as attributes."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_config_with_deprecated_fields_at_schema_v3_loads(self, tmp_path, monkeypatch):
        """A ``config.json`` at schema_version=3 with the deprecated fields
        still present (e.g. written by a buggy migrator that didn't pop
        them) is handled gracefully by the unknown-key filter — the keys
        are silently dropped (with a WARNING log) and the remaining
        fields load normally. No fallback to defaults occurs."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = tmp_path / "config.json"
        stale_config = {"schema_version": 3, "hotkey": "<f9>", "silence_rms_threshold": 0.5}
        config_file.write_text(json.dumps(stale_config))

        c = Config.load()
        # The non-deprecated field survived — no fallback to defaults.
        assert c.hotkey == "<f9>"
        # The deprecated field is NOT on the instance.
        assert not hasattr(c, "silence_rms_threshold")


# ──────────────────────────────────────────────────────────────────────────
# GT-D1-6 / GT-D1-7: validator and migration function return types
# ──────────────────────────────────────────────────────────────────────────


class TestGTD1ValidatorAndMigrationTypes:
    """GT-D1-6 / GT-D1-7: the validator entry points and migration
    functions are now typed with parameterised generics instead of bare
    ``dict``/``list``. These tests pin the new type contracts.
    """

    def test_validate_config_update_returns_tuple_of_correct_types(self):
        """``validate_config_update`` must return a 2-tuple whose first
        element is a ``dict`` and whose second is a ``list`` of ``str``."""
        from voice_typer.server.config import validate_config_update

        validated, errors = validate_config_update({"hotkey": "<f4>"})
        assert isinstance(validated, dict)
        assert isinstance(errors, list)
        for e in errors:
            assert isinstance(e, str)

    def test_validate_config_returns_list_of_str(self):
        """``validate_config`` must return a ``list[str]``."""
        from voice_typer.server.config import Config, validate_config

        errors = validate_config(Config())
        assert isinstance(errors, list)
        for e in errors:
            assert isinstance(e, str)

    def test_ipc_config_allowlist_is_dict_of_fieldspec(self):
        """``IPC_CONFIG_ALLOWLIST`` is now typed as
        ``dict[str, FieldSpec]`` (parameterised), not a bare ``dict``."""
        import typing

        import voice_typer.server.config_validators as cv

        hints = typing.get_type_hints(cv, include_extras=False)
        allowlist_hint = hints.get("IPC_CONFIG_ALLOWLIST")
        assert allowlist_hint is not None, "IPC_CONFIG_ALLOWLIST must have a type hint"
        origin = typing.get_origin(allowlist_hint)
        assert origin is dict, f"IPC_CONFIG_ALLOWLIST hint origin must be dict, got {origin!r}"
        args = typing.get_args(allowlist_hint)
        assert len(args) == 2, f"IPC_CONFIG_ALLOWLIST must be parameterised [str, FieldSpec], got args={args!r}"

    def test_migrate_to_v3_returns_dict(self):
        """``_migrate_to_v3`` returns a ``dict`` (migration contract)."""
        from voice_typer.server.config import _migrate_to_v3

        result = _migrate_to_v3({"silence_rms_threshold": 0.5, "_load_warnings": []})
        assert isinstance(result, dict)
        # The deprecated key was scrubbed.
        assert "silence_rms_threshold" not in result
