"""test matrix for ``Config.load()`` corrupt-config scenarios."""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path

import pytest
from voice_typer.server.config import Config, _default_hotkey_for_platform

EXPECTED_DEFAULT_HOTKEY = _default_hotkey_for_platform()


def _write_config(tmp_path: Path, payload: str) -> Path:
    """Write ``payload`` to ``<tmp_path>/config.json`` and return the path."""
    config_file = tmp_path / "config.json"
    config_file.write_text(payload, encoding="utf-8")
    return config_file


def _warning_records(caplog) -> list[logging.LogRecord]:
    """Return the WARNING+ records captured from the config logger."""
    return [r for r in caplog.records if r.name == "voice_typer.server.config" and r.levelno >= logging.WARNING]


def _loading_warning_records(caplog) -> list[logging.LogRecord]:
    """'loading config ... Using defaults' warning records."""
    return [
        r
        for r in _warning_records(caplog)
        if ("loading config" in r.message and "Using defaults" in r.message) or ("resetting to default" in r.message)
    ]


class TestConfigLoadCaughtFailureModes:
    """Each expected failure mode must fall back to defaults and log."""

    def test_file_missing_returns_defaults_no_warning(self, tmp_path, tmp_config_dir, caplog):
        """No config file at all → defaults, no exception, no warning."""
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert cfg.autostart is True
        # No file → no warning.
        assert _warning_records(caplog) == []

    def test_file_empty_returns_defaults_and_logs_warning(self, tmp_path, tmp_config_dir, caplog):
        """An empty file is not valid JSON → JSONDecodeError → defaults."""
        config_file = _write_config(tmp_path, "")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "JSONDecodeError" in recs[0].message
        assert str(config_file) in recs[0].message

    def test_corrupt_json_returns_defaults_and_logs_warning(self, tmp_path, tmp_config_dir, caplog):
        """Truncated/garbage JSON → JSONDecodeError → defaults."""
        config_file = _write_config(tmp_path, "NOT VALID JSON {{{")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "JSONDecodeError" in recs[0].message
        assert str(config_file) in recs[0].message

    @pytest.mark.parametrize(
        "bad_root",
        ["null", "true", "42", "3.14", '"a string"', "[]", "[1, 2, 3]"],
        ids=["null", "true", "int", "float", "string", "empty_list", "list"],
    )
    def test_json_non_dict_returns_defaults_and_logs_warning(self, tmp_path, tmp_config_dir, caplog, bad_root):
        """Valid JSON but not a dict → TypeError → defaults."""
        config_file = _write_config(tmp_path, bad_root)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "TypeError" in recs[0].message
        assert str(config_file) in recs[0].message
        # The message should explain what shape was expected vs. found.
        assert "JSON object" in recs[0].message

    def test_field_with_uncoercible_string_returns_defaults_and_logs_warning(self, tmp_path, tmp_config_dir, caplog):
        """A float field set to a non-numeric string → per-field reset + warning."""
        _write_config(tmp_path, json.dumps({"streaming_chunk_seconds": "abc"}))
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "streaming_chunk_seconds" in recs[0].message
        assert "abc" in recs[0].message
        assert "resetting to default" in recs[0].message
        # The bad field should be reset to its default.
        assert cfg.streaming_chunk_seconds == 12.0

    def test_field_with_null_for_float_returns_defaults_and_logs_warning(self, tmp_path, tmp_config_dir, caplog):
        """A float field set to ``null`` → per-field reset + warning."""
        _write_config(tmp_path, json.dumps({"streaming_chunk_seconds": None}))
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "streaming_chunk_seconds" in recs[0].message
        assert "None" in recs[0].message
        assert "resetting to default" in recs[0].message
        assert cfg.streaming_chunk_seconds == 12.0

    def test_field_with_uncoercible_int_for_float_returns_defaults_and_logs_warning(
        self, tmp_path, tmp_config_dir, caplog
    ):
        """A float field set to a list → per-field reset + warning."""
        _write_config(tmp_path, json.dumps({"streaming_chunk_seconds": [1, 2, 3]}))
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "streaming_chunk_seconds" in recs[0].message
        assert "resetting to default" in recs[0].message
        assert cfg.streaming_chunk_seconds == 12.0

    def test_validate_non_numeric_fields_raises_returns_defaults_and_logs_warning(
        self, tmp_path, tmp_config_dir, monkeypatch, caplog
    ):
        """If the validator raises a caught exception, fall back to defaults."""
        config_file = _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def raising_validate(cls, data):
            raise ValueError("simulated validator failure")

        monkeypatch.setattr(
            Config,
            "_validate_non_numeric_fields",
            classmethod(raising_validate),
        )
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "ValueError" in recs[0].message
        assert str(config_file) in recs[0].message

    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="POSIX-only: chmod 0o000 to deny read access",
    )
    def test_permission_denied_returns_defaults_and_logs_warning(self, tmp_path, tmp_config_dir, caplog):
        """File exists but is unreadable → PermissionError (OSError) → defaults."""
        config_file = _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))
        # Strip all permissions from the file (note: this only denies
        config_file.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
                cfg = Config.load()
            assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
            recs = _loading_warning_records(caplog)
            assert len(recs) == 1
            # PermissionError is a subclass of OSError; the log records
            assert "PermissionError" in recs[0].message or "OSError" in recs[0].message
            assert str(config_file) in recs[0].message
        finally:
            # Restore permissions so pytest can clean up tmp_path.
            try:
                config_file.chmod(0o600)
            except FileNotFoundError:
                for corrupt_backup in tmp_path.glob("config.json.corrupt-*"):
                    with contextlib.suppress(OSError):
                        corrupt_backup.chmod(0o600)

    def test_disk_error_during_read_returns_defaults_and_logs_warning(
        self, tmp_path, tmp_config_dir, monkeypatch, caplog
    ):
        """OSError mid-read (e.g. disk failure) → defaults + warning."""
        config_file = _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise OSError("simulated disk read error")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "OSError" in recs[0].message
        assert str(config_file) in recs[0].message

    def test_oserror_subclass_permissionerror_is_caught(self, tmp_path, tmp_config_dir, monkeypatch, caplog):
        """``PermissionError`` (subclass of ``OSError``) must be caught."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise PermissionError("simulated permission denied")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "PermissionError" in recs[0].message


class TestConfigLoadPropagatedFailureModes:
    """Unexpected exceptions propagate so genuine bugs are visible."""

    def test_keyerror_propagates(self, tmp_path, tmp_config_dir, monkeypatch):
        """``KeyError`` indicates a bug (we use ``.get()`` everywhere)."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def raising_validate(cls, data):
            raise KeyError("simulated bug: missing data[...] access")

        monkeypatch.setattr(
            Config,
            "_validate_non_numeric_fields",
            classmethod(raising_validate),
        )
        with pytest.raises(KeyError, match="simulated bug"):
            Config.load()

    def test_attributeerror_propagates(self, tmp_path, tmp_config_dir, monkeypatch):
        """``AttributeError`` indicates a bug (unexpected ``None`` access)."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def raising_validate(cls, data):
            raise AttributeError("simulated bug: None.foo")

        monkeypatch.setattr(
            Config,
            "_validate_non_numeric_fields",
            classmethod(raising_validate),
        )
        with pytest.raises(AttributeError, match="simulated bug"):
            Config.load()

    def test_memoryerror_propagates(self, tmp_path, tmp_config_dir, monkeypatch):
        """``MemoryError`` is system-level, must not be silently swallowed."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise MemoryError("simulated OOM")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(MemoryError):
            Config.load()

    def test_keyboardinterrupt_propagates(self, tmp_path, tmp_config_dir, monkeypatch):
        """``KeyboardInterrupt`` must always propagate (user hit Ctrl-C)."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(KeyboardInterrupt):
            Config.load()

    def test_systemexit_propagates(self, tmp_path, tmp_config_dir, monkeypatch):
        """``SystemExit`` must always propagate (``sys.exit()`` was called)."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise SystemExit(42)

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(SystemExit) as exc_info:
            Config.load()
        assert exc_info.value.code == 42

    def test_runtimeerror_propagates(self, tmp_path, tmp_config_dir, monkeypatch):
        """``RuntimeError`` is not an expected config-corruption mode."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise RuntimeError("simulated downstream bug")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(RuntimeError, match="simulated downstream bug"):
            Config.load()


class TestConfigLoadLegitimateCasesPreserved:
    """must not break any legitimate config-loading path."""

    def test_valid_config_loads_normally(self, tmp_path, tmp_config_dir):
        """A well-formed config dict loads with the user's values."""
        _write_config(
            tmp_path,
            json.dumps({"hotkey": "<f9>", "autostart": False, "device": "cpu"}),
        )
        cfg = Config.load()
        assert cfg.hotkey == "<f9>"
        assert cfg.autostart is False
        assert cfg.device == "cpu"

    def test_unknown_keys_ignored(self, tmp_path, tmp_config_dir):
        """Unknown JSON keys must not crash load()."""
        _write_config(
            tmp_path,
            json.dumps({"hotkey": "<f5>", "bogus_key": "ignored"}),
        )
        cfg = Config.load()
        assert cfg.hotkey == "<f5>"
        assert not hasattr(cfg, "bogus_key")

    def test_round_trip_preserved(self, tmp_path, tmp_config_dir):
        """Save → load round-trip is unaffected."""
        c1 = Config(hotkey="<f7>", autostart=False, device="cpu")
        c1.save()
        c2 = Config.load()
        assert c2.hotkey == "<f7>"
        assert c2.autostart is False
        assert c2.device == "cpu"

    def test_schema_migration_still_runs(self, tmp_path, tmp_config_dir):
        """A config without ``schema_version`` is migrated to current."""
        _write_config(tmp_path, json.dumps({"hotkey": "<f3>"}))
        cfg = Config.load()
        from voice_typer.server.config import _CURRENT_SCHEMA_VERSION

        assert cfg.schema_version == _CURRENT_SCHEMA_VERSION
        assert cfg.hotkey == "<f3>"


class TestConfigLoadWarningMessageQuality:
    """the warning must include enough context to be actionable."""

    def test_warning_includes_exception_class_name(self, tmp_path, tmp_config_dir, caplog):
        """The exception class name (e.g. ``JSONDecodeError``) is the"""
        _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs, "expected at least one WARNING record"
        # The class name is in the message (formatted via %s).
        assert "JSONDecodeError" in recs[0].message

    def test_warning_includes_config_file_path(self, tmp_path, tmp_config_dir, caplog):
        """The config file path must be in the message so the user knows"""
        config_file = _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs
        assert str(config_file) in recs[0].message

    def test_warning_includes_exception_message(self, tmp_path, tmp_config_dir, caplog):
        """The underlying exception message (e.g. JSON parse error"""
        _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs
        assert "line" in recs[0].message
        assert "column" in recs[0].message

    def test_warning_level_is_warning_not_error(self, tmp_path, tmp_config_dir, caplog):
        """level is WARNING (recoverable), not ERROR (fatal)."""
        _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs
        assert recs[0].levelno == logging.WARNING


class TestCoercionHelpers:
    """``_warn_and_reset`` and ``_warn_and_coerce`` extract"""

    def test_warn_and_reset_returns_default_value(self, caplog):
        """``_warn_and_reset`` returns the default value for the field."""
        defaults = Config()
        warnings: list[str] = []
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = Config._warn_and_reset(
                "autostart",
                "yes",
                defaults,
                warnings,
                reason="had non-bool value",
            )
        # The default for ``autostart`` is True (per the Config dataclass).
        assert result == defaults.autostart
        assert result is True

    def test_warn_and_reset_appends_to_warnings_list(self, caplog):
        """The warnings list is appended in place."""
        defaults = Config()
        warnings: list[str] = []
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config._warn_and_reset(
                "streaming_chunk_seconds",
                "not a number",
                defaults,
                warnings,
                reason="had non-float value",
            )
        assert len(warnings) == 1
        msg = warnings[0]
        # The message must include enough context to be actionable:
        assert "streaming_chunk_seconds" in msg
        assert "'not a number'" in msg
        assert repr(defaults.streaming_chunk_seconds) in msg
        assert "had non-float value" in msg
        assert "resetting to default" in msg

    def test_warn_and_reset_logs_at_warning_level(self, caplog):
        """The warning is emitted via the config logger at WARNING level."""
        defaults = Config()
        warnings: list[str] = []
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config._warn_and_reset(
                "language",
                42,
                defaults,
                warnings,
                reason="had non-string value",
            )
        recs = _warning_records(caplog)
        assert len(recs) == 1
        assert recs[0].levelno == logging.WARNING
        assert "language" in recs[0].message

    def test_warn_and_coerce_returns_coerced_value(self, caplog):
        """``_warn_and_coerce`` returns the coerced value (not the original)."""
        warnings: list[str] = []
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            result = Config._warn_and_coerce(
                "streaming_chunk_seconds",
                "0.5",  # string from form input
                0.5,  # coerced float
                warnings,
                reason="had non-float value",
            )
        assert result == 0.5
        assert isinstance(result, float)

    def test_warn_and_coerce_appends_to_warnings_list(self, caplog):
        """The warnings list captures the coercion event."""
        warnings: list[str] = []
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config._warn_and_coerce(
                "sample_rate",
                "16000",  # string from JSON
                16000,  # coerced int
                warnings,
                reason="had non-int value",
            )
        assert len(warnings) == 1
        msg = warnings[0]
        # The message must include both the original and coerced values
        assert "sample_rate" in msg
        assert "'16000'" in msg  # original string form
        assert repr(16000) in msg  # coerced int form
        assert "had non-int value" in msg
        assert "coerced to" in msg

    def test_warn_and_coerce_logs_at_warning_level(self, caplog):
        """The coercion warning is logged at WARNING (recoverable)."""
        warnings: list[str] = []
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config._warn_and_coerce(
                "autostart",
                "yes",
                True,
                warnings,
                reason="had non-bool value",
            )
        recs = _warning_records(caplog)
        assert len(recs) == 1
        assert recs[0].levelno == logging.WARNING

    def test_validate_non_numeric_fields_uses_helpers_for_bool_coercion(self, caplog):
        """regression: bool coercion routes through"""
        # Force a non-bool value for a bool field.
        data = {"autostart": "yes"}
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            validated = Config._validate_non_numeric_fields(data)
        assert validated["autostart"] is True
        # The warning must match the helper's format (not the legacy
        recs = _warning_records(caplog)
        assert recs, "expected at least one WARNING record"
        assert "autostart" in recs[0].message
        assert "coerced to" in recs[0].message

    def test_validate_non_numeric_fields_uses_helpers_for_int_reset(self, caplog):
        """regression: int field reset routes through"""
        # An int field with a non-numeric string value → reset to default.
        data = {"sample_rate": "not a number"}
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            validated = Config._validate_non_numeric_fields(data)
        # Should be reset to the default sample_rate.
        assert validated["sample_rate"] == Config().sample_rate
        recs = _warning_records(caplog)
        assert recs
        assert "sample_rate" in recs[0].message
        assert "resetting to default" in recs[0].message
