"""RW-9: test matrix for ``Config.load()`` corrupt-config scenarios.

Prior to RW-9, ``Config.load()`` wrapped its body in a broad
``except Exception`` that silently returned defaults.  This file pins
the new contract:

* **Caught** (fall back to defaults + WARNING log containing the
  exception class name and the config file path):
  ``OSError``, ``json.JSONDecodeError``, ``TypeError``, ``ValueError``.
* **Propagated** (NOT caught — indicates a bug in our code or a
  system-level failure): ``KeyError``, ``AttributeError``,
  ``MemoryError``, ``KeyboardInterrupt``, ``SystemExit``.

The tests below cover each row of that decision matrix.
"""

from __future__ import annotations

import contextlib
import json
import logging
import sys
from pathlib import Path

import pytest
from voice_typer.server.config import Config, _default_hotkey_for_platform

EXPECTED_DEFAULT_HOTKEY = _default_hotkey_for_platform()


# ── Helpers ────────────────────────────────────────────────────────────────


def _write_config(tmp_path: Path, payload: str) -> Path:
    """Write ``payload`` to ``<tmp_path>/config.json`` and return the path."""
    config_file = tmp_path / "config.json"
    config_file.write_text(payload, encoding="utf-8")
    return config_file


def _warning_records(caplog) -> list[logging.LogRecord]:
    """Return the WARNING+ records captured from the config logger."""
    return [r for r in caplog.records if r.name == "voice_typer.server.config" and r.levelno >= logging.WARNING]


def _loading_warning_records(caplog) -> list[logging.LogRecord]:
    """Return only the per-field 'invalid ... resetting to default' or
    'loading config ... Using defaults' warning records.

    G4-H-10 adds a second warning ('moved corrupt config ... for forensic
    recovery') when a corrupt config is moved aside.  Tests that assert
    on the load-failure warning specifically should use this helper to
    filter out the move-aside warning.

    G4-M-11/M-13: per-field coercion warnings now use the format
    'invalid <field> value ... resetting to default' rather than the
    old generic 'loading config ... Using defaults' message.
    """
    return [
        r
        for r in _warning_records(caplog)
        if ("loading config" in r.message and "Using defaults" in r.message) or ("resetting to default" in r.message)
    ]


# ── Caught: fall back to defaults + WARNING ────────────────────────────────


class TestConfigLoadCaughtFailureModes:
    """Each expected failure mode must fall back to defaults and log."""

    def test_file_missing_returns_defaults_no_warning(self, tmp_path, monkeypatch, caplog):
        """No config file at all → defaults, no exception, no warning.

        This is the legitimate "first run" case and must NOT log a
        warning (there's nothing wrong — the user just hasn't saved a
        config yet).
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        assert cfg.autostart is True
        # No file → no warning.
        assert _warning_records(caplog) == []

    def test_file_empty_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """An empty file is not valid JSON → JSONDecodeError → defaults."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = _write_config(tmp_path, "")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "JSONDecodeError" in recs[0].message
        assert str(config_file) in recs[0].message

    def test_corrupt_json_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """Truncated/garbage JSON → JSONDecodeError → defaults."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
    def test_json_non_dict_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog, bad_root):
        """Valid JSON but not a dict → TypeError → defaults.

        RW-9: ``load()`` now explicitly checks ``isinstance(parsed, dict)``
        and raises ``TypeError`` with a clear message.  Previously this
        case raised ``AttributeError`` from ``parsed.items()``, which was
        caught by the broad ``except Exception``.  The new behavior
        surfaces the failure mode (``TypeError``) in the log.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_field_with_uncoercible_string_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """A float field set to a non-numeric string → per-field reset + warning.

        MED-K / VALID-1: ``float("abc")`` raises ``ValueError`` — the
        field cannot be coerced.  Previously this reset the ENTIRE config
        to defaults; now only the bad field is reset and a warning is
        logged so the user knows which field was bad.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"streaming_chunk_seconds": "abc"}))
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        # MED-K / VALID-1: new message format names the field and value.
        assert "streaming_chunk_seconds" in recs[0].message
        assert "abc" in recs[0].message
        assert "resetting to default" in recs[0].message
        # The bad field should be reset to its default.
        assert cfg.streaming_chunk_seconds == 12.0

    def test_field_with_null_for_float_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """A float field set to ``null`` → per-field reset + warning.

        MED-K / VALID-1: ``float(None)`` raises ``TypeError``.  Previously
        this reset the ENTIRE config; now only the bad field is reset.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
        self, tmp_path, monkeypatch, caplog
    ):
        """A float field set to a list → per-field reset + warning.

        MED-K / VALID-1: ``float([1, 2])`` raises ``TypeError``.  Previously
        this reset the ENTIRE config; now only the bad field is reset.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"streaming_chunk_seconds": [1, 2, 3]}))
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            cfg = Config.load()
        assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
        recs = _loading_warning_records(caplog)
        assert len(recs) == 1
        assert "streaming_chunk_seconds" in recs[0].message
        assert "resetting to default" in recs[0].message
        assert cfg.streaming_chunk_seconds == 12.0

    def test_validate_non_numeric_fields_raises_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """If the validator raises a caught exception, fall back to defaults.

        Simulates a future regression where ``_validate_non_numeric_fields``
        raises ``ValueError`` on a field it can't reason about.  The
        outer ``except`` must catch it and fall back to defaults.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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
    def test_permission_denied_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """File exists but is unreadable → PermissionError (OSError) → defaults.

        We never want a permission error to crash the app on startup;
        falling back to defaults (and warning) is the correct UX.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))
        # Strip all permissions from the file (note: this only denies
        # non-root users; root can still read).  Tests run as the user
        # that owns the file, so 0o000 denies read access.
        config_file.chmod(0o000)
        try:
            with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
                cfg = Config.load()
            assert cfg.hotkey == EXPECTED_DEFAULT_HOTKEY
            recs = _loading_warning_records(caplog)
            assert len(recs) == 1
            # PermissionError is a subclass of OSError; the log records
            # the actual subclass name so the user can see "permission
            # denied" vs. "disk error".
            assert "PermissionError" in recs[0].message or "OSError" in recs[0].message
            assert str(config_file) in recs[0].message
        finally:
            # Restore permissions so pytest can clean up tmp_path.
            # the corrupt config may have been moved aside to
            # config.json.corrupt-<timestamp>; chmod that if the original
            # is gone (best-effort cleanup).
            try:
                config_file.chmod(0o600)
            except FileNotFoundError:
                for corrupt_backup in tmp_path.glob("config.json.corrupt-*"):
                    with contextlib.suppress(OSError):
                        corrupt_backup.chmod(0o600)

    def test_disk_error_during_read_returns_defaults_and_logs_warning(self, tmp_path, monkeypatch, caplog):
        """OSError mid-read (e.g. disk failure) → defaults + warning.

        Simulated by mocking ``_secure_read_text`` to raise ``OSError``.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_oserror_subclass_permissionerror_is_caught(self, tmp_path, monkeypatch, caplog):
        """``PermissionError`` (subclass of ``OSError``) must be caught.

        This is a regression guard: if someone refactors the except
        tuple to ``except OSError`` only, ``PermissionError`` still
        works.  But if they narrow it to specific OSError subclasses,
        we want to know.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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


# ── Propagated: NOT caught ─────────────────────────────────────────────────


class TestConfigLoadPropagatedFailureModes:
    """Unexpected exceptions propagate so genuine bugs are visible."""

    def test_keyerror_propagates(self, tmp_path, monkeypatch):
        """``KeyError`` indicates a bug (we use ``.get()`` everywhere)."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_attributeerror_propagates(self, tmp_path, monkeypatch):
        """``AttributeError`` indicates a bug (unexpected ``None`` access)."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
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

    def test_memoryerror_propagates(self, tmp_path, monkeypatch):
        """``MemoryError`` is system-level — must not be silently swallowed.

        Pre-RW-9 the broad ``except Exception`` caught ``MemoryError``
        (it's a subclass of ``Exception``) and silently returned
        defaults, masking an OOM condition.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise MemoryError("simulated OOM")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(MemoryError):
            Config.load()

    def test_keyboardinterrupt_propagates(self, tmp_path, monkeypatch):
        """``KeyboardInterrupt`` must always propagate (user hit Ctrl-C).

        Pre-RW-9 ``except Exception`` did NOT catch ``KeyboardInterrupt``
        (it's a ``BaseException``, not ``Exception``), but we add this
        test to pin that behavior — if someone later widens the catch
        to ``except BaseException`` it would break Ctrl-C handling.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise KeyboardInterrupt()

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(KeyboardInterrupt):
            Config.load()

    def test_systemexit_propagates(self, tmp_path, monkeypatch):
        """``SystemExit`` must always propagate (``sys.exit()`` was called)."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise SystemExit(42)

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(SystemExit) as exc_info:
            Config.load()
        assert exc_info.value.code == 42

    def test_runtimeerror_propagates(self, tmp_path, monkeypatch):
        """``RuntimeError`` is not an expected config-corruption mode.

        If a ``RuntimeError`` bubbles up from inside ``load()``, it's
        likely a bug in our migration code or a downstream import
        failure — we want it surfaced, not silently swallowed.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"hotkey": "<f5>"}))

        def boom(path, **kwargs):
            raise RuntimeError("simulated downstream bug")

        monkeypatch.setattr("voice_typer.server.config._secure_read_text", boom)
        with pytest.raises(RuntimeError, match="simulated downstream bug"):
            Config.load()


# ── Backward-compat: existing legitimate cases still work ──────────────────


class TestConfigLoadLegitimateCasesPreserved:
    """RW-9 must not break any legitimate config-loading path."""

    def test_valid_config_loads_normally(self, tmp_path, monkeypatch):
        """A well-formed config dict loads with the user's values."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(
            tmp_path,
            json.dumps({"hotkey": "<f9>", "autostart": False, "device": "cpu"}),
        )
        cfg = Config.load()
        assert cfg.hotkey == "<f9>"
        assert cfg.autostart is False
        assert cfg.device == "cpu"

    def test_unknown_keys_ignored(self, tmp_path, monkeypatch):
        """Unknown JSON keys must not crash load()."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(
            tmp_path,
            json.dumps({"hotkey": "<f5>", "bogus_key": "ignored"}),
        )
        cfg = Config.load()
        assert cfg.hotkey == "<f5>"
        assert not hasattr(cfg, "bogus_key")

    def test_round_trip_preserved(self, tmp_path, monkeypatch):
        """Save → load round-trip is unaffected."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        c1 = Config(hotkey="<f7>", autostart=False, device="cpu")
        c1.save()
        c2 = Config.load()
        assert c2.hotkey == "<f7>"
        assert c2.autostart is False
        assert c2.device == "cpu"

    def test_schema_migration_still_runs(self, tmp_path, monkeypatch):
        """A config without ``schema_version`` is migrated to current."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, json.dumps({"hotkey": "<f3>"}))
        cfg = Config.load()
        from voice_typer.server.config import _CURRENT_SCHEMA_VERSION

        assert cfg.schema_version == _CURRENT_SCHEMA_VERSION
        assert cfg.hotkey == "<f3>"


# ── Log-message quality ────────────────────────────────────────────────────


class TestConfigLoadWarningMessageQuality:
    """RW-9: the warning must include enough context to be actionable."""

    def test_warning_includes_exception_class_name(self, tmp_path, monkeypatch, caplog):
        """The exception class name (e.g. ``JSONDecodeError``) is the
        failure-mode indicator — it must be in the log message."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs, "expected at least one WARNING record"
        # The class name is in the message (formatted via %s).
        assert "JSONDecodeError" in recs[0].message

    def test_warning_includes_config_file_path(self, tmp_path, monkeypatch, caplog):
        """The config file path must be in the message so the user knows
        which file is corrupt (in case there are multiple, e.g. legacy
        + new)."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        config_file = _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs
        assert str(config_file) in recs[0].message

    def test_warning_includes_exception_message(self, tmp_path, monkeypatch, caplog):
        """The underlying exception message (e.g. JSON parse error
        detail) must be in the log so the user can see *where* in the
        file the corruption is."""
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs
        # json.JSONDecodeError messages always include "line" and "column".
        assert "line" in recs[0].message
        assert "column" in recs[0].message

    def test_warning_level_is_warning_not_error(self, tmp_path, monkeypatch, caplog):
        """RW-9: level is WARNING (recoverable), not ERROR (fatal).

        Recovering to defaults is a normal, recoverable event — using
        ERROR would flood monitoring dashboards with false positives.
        """
        monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
        _write_config(tmp_path, "garbage")
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            Config.load()
        recs = _warning_records(caplog)
        assert recs
        assert recs[0].levelno == logging.WARNING


# extracted coercion helpers ─────────────────────────────────


class TestCoercionHelpers:
    """S2-CR-44: ``_warn_and_reset`` and ``_warn_and_coerce`` extract
    the duplicated 5-line "build msg → log → append → reset" pattern
    that appeared 6 times in the original
    ``_validate_non_numeric_fields``.

    These tests verify the helpers behave correctly in isolation so
    future refactors of the per-type branches can rely on them.
    """

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
        # field name, original value, default value, and the reason.
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
        # so the user can see what was changed.
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
        """S2-CR-44 regression: bool coercion routes through
        ``_warn_and_coerce`` so the warning message format stays
        consistent with int/float coercion.
        """
        # Force a non-bool value for a bool field.
        data = {"autostart": "yes"}
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.config"):
            validated = Config._validate_non_numeric_fields(data)
        assert validated["autostart"] is True
        # The warning must match the helper's format (not the legacy
        # inline format).
        recs = _warning_records(caplog)
        assert recs, "expected at least one WARNING record"
        assert "autostart" in recs[0].message
        assert "coerced to" in recs[0].message

    def test_validate_non_numeric_fields_uses_helpers_for_int_reset(self, caplog):
        """S2-CR-44 regression: int field reset routes through
        ``_warn_and_reset``.
        """
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
