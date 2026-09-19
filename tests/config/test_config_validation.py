"""Tests for config load/save and field behavior."""

import json

import pytest
from voice_typer.server.config import Config, _default_hotkey_for_platform

EXPECTED_DEFAULT_HOTKEY = _default_hotkey_for_platform()


class TestNonNumericFieldValidation:
    """No type validation on loaded JSON config values."""

    def test_bool_field_coerces_truthy_string(self, tmp_path, tmp_config_dir):
        """Non-bool truthy value for bool field should be coerced."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"autostart": "true"}))
        c = Config.load()
        assert c.autostart is True

    def test_bool_field_coerces_zero(self, tmp_path, tmp_config_dir):
        """Zero for bool field should be coerced to False."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"paste_on_stop": 0}))
        c = Config.load()
        assert c.paste_on_stop is False

    def test_bool_field_resets_invalid_value(self, tmp_path, tmp_config_dir):
        """Invalid value for bool field should reset to default."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"autostart": [1, 2]}))
        c = Config.load()
        assert c.autostart is True  # default

    def test_str_field_resets_non_string(self, tmp_path, tmp_config_dir):
        """Non-string value for str field should reset to default."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"hotkey": 42}))
        c = Config.load()
        assert c.hotkey == EXPECTED_DEFAULT_HOTKEY  # default

    def test_str_field_keeps_valid_string(self, tmp_path, tmp_config_dir):
        """Valid string value for str field should be preserved."""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"language": "fr"}))
        c = Config.load()
        assert c.language == "fr"

    def test_silence_warning_seconds_default(self):
        """config fields should have correct defaults."""
        c = Config()
        assert c.silence_warning_seconds == 20.0
        assert c.stop_on_silence_seconds == 60.0
        assert c.max_recording_time_seconds == 900

    def test_startup6_int_field_not_treated_as_bool(self, tmp_path, tmp_config_dir, caplog):
        """STARTUP-6: volume_duck_smart_poll_interval_ms (int) must NOT be"""
        config_file = tmp_path / "config.json"
        # Write the default value explicitly, this is what Config.save() produces
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

    def test_startup6_int_field_preserves_user_value(self, tmp_path, tmp_config_dir):
        """STARTUP-6: a non-default but in-range int value should also"""
        config_file = tmp_path / "config.json"
        config_file.write_text(json.dumps({"volume_duck_smart_poll_interval_ms": 1500}))
        c = Config.load()
        assert c.volume_duck_smart_poll_interval_ms == 1500


class TestCfg5AccumulateAllErrors:
    """first invalid field (``break``), forcing the user to fix-and-resubmit"""

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
        """When some fields are valid and others are invalid, the valid"""
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
        """Backwards compat: a single invalid field still returns"""
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
        """Unknown keys are silently dropped (debug-logged), regardless"""
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
        """A type error (wrong type) and a range error (right type, bad"""
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


class TestCfg6ControlCharRejection:
    """under the length cap, including strings with embedded C0 control"""

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
        # Unicode is fine, only C0 control chars + DEL are rejected.
        assert v("héllo wörld 中文") is None

    def test_str_validator_accepts_high_codepoints(self):
        """must pass.  C0 (0x00-0x1f), DEL (0x7f), and C1 controls (0x80-0x9f)"""
        from voice_typer.server.config_validators import _make_str_validator

        v = _make_str_validator()
        # C1 control chars (0x80-0x9f) are rejected.
        assert v("café\x80") is not None
        # 0xA0 (NBSP), emoji, CJK, all OK (above C1 range).
        assert v("\xa0space") is None
        assert v("emoji 😀") is None

    def test_optional_str_validator_rejects_control_char(self):
        """_make_optional_str_validator (used for ``microphone``) also"""
        from voice_typer.server.config_validators import _make_optional_str_validator

        v = _make_optional_str_validator()
        assert v(None) is None  # None is always allowed
        result = v("mic\ntest")
        assert result is not None
        assert "control" in result.lower()

    def test_str_validator_via_ipc_rejects_control_char_in_api_key(self):
        """End-to-end: a ``cloud_api_key`` with a newline is rejected"""
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


class TestCfg7UrlCredentialsRejection:
    """credentials-leak vector: the renderer would otherwise persist them"""

    def test_rejects_user_password_url(self):
        """A URL with both username and password is rejected."""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        result = v("https://user:pass@api.example.com/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_rejects_user_only_url(self):
        """A URL with username but no password is also rejected —"""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        result = v("https://user@api.example.com/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_rejects_password_only_url(self):
        """A URL with password but no username is also rejected"""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        # ``:pass@host``, urlparse parses this as password-only.
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
        """Loopback HTTP (local dev server) is still accepted when no"""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        assert v("http://localhost:11434/v1/chat/completions") is None

    def test_rejects_credentials_on_loopback_too(self):
        """applies even to loopback URLs, credentials are"""
        from voice_typer.server.config_validators import _make_url_validator

        v = _make_url_validator(allow_empty=False)
        result = v("http://admin:secret@localhost:11434/v1/chat")
        assert result is not None
        assert "credential" in result.lower()

    def test_rejects_credentials_via_ipc_set_config(self):
        """End-to-end: ``set_config`` with a credential-bearing"""
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
        assert len(errors) >= 1
        assert "cloud_api_url" not in validated
        # If the credential check ran, the message mentions credentials.
        assert any("credential" in e.lower() or "HTTPS" in e or "loopback" in e.lower() for e in errors)
