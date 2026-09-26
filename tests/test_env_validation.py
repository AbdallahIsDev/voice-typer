"""Unit tests for :mod:`voice_typer.server.env_validation`."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

import pytest
from voice_typer.server.env_validation import _validate_env_vars

# All env vars touched by _validate_env_vars (kept in sync with the SUT).
_BOOL_VARS = (
    "VOICE_TYPER_QUIET",
    "VOICE_TYPER_DEBUG",
    "VOICE_TYPER_NO_TRAY",
    "VOICE_TYPER_STREAMING",
)
_TOKEN_VARS = ("VOICE_TYPER_RESTART", "VOICE_TYPER_IPC_TOKEN")
_PATH_VARS = ("VOICE_TYPER_CONFIG_DIR", "HF_HOME")
_ALL_VARS = _BOOL_VARS + _TOKEN_VARS + _PATH_VARS + ("HF_ENDPOINT",)

_VALID_BOOL_VALUES = ("1", "0", "true", "false", "yes", "no")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure none of the validated vars pre-exist before each test."""
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


class TestValidateEnvVarsContract:
    """Return value, no-op behaviour, and SystemRoot delegation."""

    def test_returns_none_when_no_vars_set(self):
        """No env vars set, must succeed and return None."""
        assert _validate_env_vars() is None

    def test_no_vars_set_does_not_create_any(self):
        """No env vars set, must not add anything to os.environ."""
        before = {v: os.environ.get(v) for v in _ALL_VARS}
        _validate_env_vars()
        after = {v: os.environ.get(v) for v in _ALL_VARS}
        assert before == after == {v: None for v in _ALL_VARS}

    def test_calls_validate_systemroot(self, monkeypatch):
        """SEC-audit-011: must delegate SystemRoot checks to config module."""
        calls = []

        def _fake_validate_systemroot():
            calls.append("called")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_systemroot",
            _fake_validate_systemroot,
        )
        _validate_env_vars()
        assert calls == ["called"]


class TestBooleanVars:
    """``VOICE_TYPER_QUIET`` / ``_DEBUG`` / ``_NO_TRAY`` / ``_STREAMING``."""

    @pytest.mark.parametrize("var", _BOOL_VARS)
    @pytest.mark.parametrize("value", _VALID_BOOL_VALUES)
    def test_valid_boolean_values_preserved(self, monkeypatch, var, value):
        monkeypatch.setenv(var, value)
        _validate_env_vars()
        assert os.environ.get(var) == value

    @pytest.mark.parametrize("value", ["TRUE", "False", "Yes", "NO", "tRuE", "0No"])
    def test_boolean_pattern_is_case_insensitive(self, monkeypatch, value):
        monkeypatch.setenv("VOICE_TYPER_QUIET", value)
        _validate_env_vars()
        if value.lower() in {"1", "0", "true", "false", "yes", "no"}:
            assert os.environ.get("VOICE_TYPER_QUIET") == value
        else:
            assert "VOICE_TYPER_QUIET" not in os.environ

    @pytest.mark.parametrize("var", _BOOL_VARS)
    def test_invalid_boolean_value_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "maybe")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _BOOL_VARS)
    def test_empty_string_boolean_removed(self, monkeypatch, var):
        # Empty string is "set but not None", gets checked and fails.
        monkeypatch.setenv(var, "")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _BOOL_VARS)
    def test_whitespace_only_boolean_removed(self, monkeypatch, var):
        # Padded valid value, regex anchors ^ and $, so this fails.
        monkeypatch.setenv(var, "  true  ")
        _validate_env_vars()
        assert var not in os.environ

    def test_invalid_boolean_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("VOICE_TYPER_QUIET", "maybe")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [r for r in caplog.records if "VOICE_TYPER_QUIET" in r.message and "expected boolean" in r.message]
        assert matching, (
            f"expected a WARNING mentioning VOICE_TYPER_QUIET; got records={[r.message for r in caplog.records]}"
        )


class TestTokenVars:
    """``VOICE_TYPER_RESTART`` and ``VOICE_TYPER_IPC_TOKEN``."""

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_valid_token_preserved(self, monkeypatch, var):
        monkeypatch.setenv(var, "abcDEF123_.-")
        _validate_env_vars()
        assert os.environ.get(var) == "abcDEF123_.-"

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_invalid_token_removed(self, monkeypatch, var):
        # Spaces and shell metacharacters are not in [A-Za-z0-9._-].
        monkeypatch.setenv(var, "'; rm -rf /")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_empty_token_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_whitespace_token_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "   ")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_unicode_token_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "café_123")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_token_at_max_length_preserved(self, monkeypatch, var):
        # {1,128} is inclusive at both ends, 128 chars passes.
        monkeypatch.setenv(var, "a" * 128)
        _validate_env_vars()
        assert os.environ.get(var) == "a" * 128

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_overlength_token_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "a" * 129)
        _validate_env_vars()
        assert var not in os.environ


class TestPathVars:
    """``VOICE_TYPER_CONFIG_DIR`` and ``HF_HOME``."""

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_valid_path_preserved(self, monkeypatch, var):
        # the SEC-HFHOME-001 pattern). A path like ``/home/user/...`` is
        safe_path = str(Path.home() / ".config" / "lausu")
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_empty_path_removed(self, monkeypatch, var):
        # Path pattern ^[^\0]+ requires at least 1 char: "" fails.
        monkeypatch.setenv(var, "")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_whitespace_path_preserved(self, monkeypatch, var):
        safe_path = str(Path.home() / "Lausu   ")
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_unicode_path_preserved(self, monkeypatch, var):
        # Non-ASCII chars are allowed (only NUL is forbidden).
        safe_path = str(Path.home() / "配置" / "lausu")
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_path_at_max_length_preserved(self, monkeypatch, var):
        # Boundary: len == 4096 is allowed (length check is `> 4096`).
        home = str(Path.home())
        pad = 4096 - len(home) - 1  # -1 for the path separator
        assert pad > 0
        safe_path = home + os.sep + ("a" * pad)
        assert len(safe_path) == 4096
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_overlength_path_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "/a" * 2500)  # 5000 chars > 4096
        _validate_env_vars()
        assert var not in os.environ


class TestAllVarsSet:
    """End-to-end: every validated var present and valid, all preserved."""

    def test_all_valid_all_preserved(self, monkeypatch):
        # ``/tmp/lausu`` is outside ``Path.home()`` so it
        safe_path = str(Path.home() / ".lausu-test")
        for var in _BOOL_VARS:
            monkeypatch.setenv(var, "1")
        for var in _TOKEN_VARS:
            monkeypatch.setenv(var, "tok_123")
        for var in _PATH_VARS:
            monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        for var in _BOOL_VARS:
            assert os.environ.get(var) == "1", f"{var} was modified"
        for var in _TOKEN_VARS:
            assert os.environ.get(var) == "tok_123", f"{var} was modified"
        for var in _PATH_VARS:
            assert os.environ.get(var) == safe_path, f"{var} modified"

    def test_all_invalid_all_removed(self, monkeypatch):
        for var in _BOOL_VARS:
            monkeypatch.setenv(var, "maybe")
        for var in _TOKEN_VARS:
            monkeypatch.setenv(var, "'; rm -rf /")
        for var in _PATH_VARS:
            monkeypatch.setenv(var, "a" * 5000)
        # HF_ENDPOINT invalid value (HTTP scheme), must be popped.
        monkeypatch.setenv("HF_ENDPOINT", "http://evil.example.com")
        _validate_env_vars()
        for var in _ALL_VARS:
            assert var not in os.environ, f"{var} should have been removed"


# HF_ENDPOINT () ─────────────────────────────────────────────


class TestHfEndpoint:
    """G4-M-58: ``HF_ENDPOINT`` is validated against an HTTPS+allowlist rule."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://huggingface.co",
            "https://huggingface.co/",
            "https://hf-mirror.com",
            "https://hf-mirror.com/",
            "https://cdn.huggingface.co/some/path",
            "https://hf-mirror.com/hub/models",
        ],
    )
    def test_valid_hf_endpoint_preserved(self, monkeypatch, url):
        monkeypatch.setenv("HF_ENDPOINT", url)
        _validate_env_vars()
        assert os.environ.get("HF_ENDPOINT") == url

    @pytest.mark.parametrize(
        "url",
        [
            # HTTP scheme, must be rejected even for allowlisted host.
            "http://huggingface.co",
            "http://hf-mirror.com",
            "http://localhost:8080",
            # Non-allowlisted host.
            "https://evil.example.com",
            "https://huggingface.co.evil.com",
            "https://attacker.com/huggingface.co",
            # Missing scheme.
            "huggingface.co",
            "//huggingface.co",
            # Empty / malformed hostname.
            "https://",
            "https:///path-only",
        ],
    )
    def test_invalid_hf_endpoint_removed(self, monkeypatch, url):
        monkeypatch.setenv("HF_ENDPOINT", url)
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ, f"HF_ENDPOINT={url!r} should have been removed by the validator"

    def test_invalid_hf_endpoint_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("HF_ENDPOINT", "http://evil.example.com")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [r for r in caplog.records if "HF_ENDPOINT" in r.message and "rejected" in r.message]
        assert matching, (
            f"expected a WARNING about HF_ENDPOINT rejection; got records={[r.message for r in caplog.records]}"
        )

    def test_empty_hf_endpoint_removed(self, monkeypatch):
        # Empty string is "set but not None", basic pattern ^[^\0]+ fails.
        monkeypatch.setenv("HF_ENDPOINT", "")
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ

    def test_overlength_hf_endpoint_removed(self, monkeypatch):
        monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co/" + "a" * 5000)
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ

    def test_unset_hf_endpoint_is_noop(self, monkeypatch):
        # No HF_ENDPOINT set, validator must not raise or create it.
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ


class TestGt63EnvVarValuesRedacted:
    """ALL env-var values logged by ``_validate_env_vars`` are"""

    def test_invalid_boolean_value_redacted(self, monkeypatch, caplog):
        secret_value = "maybe-with-username-jane.doe"
        monkeypatch.setenv("VOICE_TYPER_QUIET", secret_value)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret_value in m for m in rendered), (
            f"GT-63 regression: raw boolean value leaked to log: {rendered!r}"
        )
        assert any("<redacted>" in m and "VOICE_TYPER_QUIET" in m for m in rendered), (
            f"expected a redacted warning mentioning VOICE_TYPER_QUIET; got {rendered!r}"
        )

    def test_invalid_config_dir_value_redacted(self, monkeypatch, caplog):
        secret_path = "/Users/jane.doe/.config/lausu" + "x" * 5000
        monkeypatch.setenv("VOICE_TYPER_CONFIG_DIR", secret_path)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret_path in m for m in rendered), (
            f"GT-63 regression: raw CONFIG_DIR path leaked to log: {rendered!r}"
        )
        assert any("<redacted>" in m and "VOICE_TYPER_CONFIG_DIR" in m for m in rendered), (
            f"expected a redacted warning mentioning VOICE_TYPER_CONFIG_DIR; got {rendered!r}"
        )

    def test_invalid_hf_home_value_redacted(self, monkeypatch, caplog):
        secret_path = "/Users/jane.doe/.cache/huggingface" + "x" * 5000
        monkeypatch.setenv("HF_HOME", secret_path)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret_path in m for m in rendered), (
            f"GT-63 regression: raw HF_HOME path leaked to log: {rendered!r}"
        )
        assert any("<redacted>" in m and "HF_HOME" in m for m in rendered), (
            f"expected a redacted warning mentioning HF_HOME; got {rendered!r}"
        )

    def test_invalid_hf_endpoint_value_redacted(self, monkeypatch, caplog):
        secret_url = "http://jane.doe:secret@google.com/" + "a" * 5000
        monkeypatch.setenv("HF_ENDPOINT", secret_url)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret_url in m for m in rendered), (
            f"GT-63 regression: raw HF_ENDPOINT URL leaked to log: {rendered!r}"
        )

    def test_hf_endpoint_rejection_paths_redacted(self, monkeypatch, caplog):
        """All three HF_ENDPOINT rejection branches (scheme, hostname,"""
        monkeypatch.setenv("HF_ENDPOINT", "http://huggingface.co")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        scheme_records = [r.getMessage() for r in caplog.records if "rejected" in r.getMessage()]
        assert scheme_records, "expected a rejection record for http:// scheme"
        assert all("http://huggingface.co" not in m for m in scheme_records), (
            f"GT-63 regression: raw HF_ENDPOINT leaked in scheme rejection: {scheme_records!r}"
        )
        assert any("<redacted>" in m for m in scheme_records)

    def test_hf_endpoint_allowlist_rejection_redacted(self, monkeypatch, caplog):
        """The allowlist rejection path logs the hostname (which is"""
        monkeypatch.setenv("HF_ENDPOINT", "https://evil.example.com/secret/path/with/key=abc")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rejected = [r.getMessage() for r in caplog.records if "rejected" in r.getMessage()]
        assert rejected, "expected a rejection record for non-allowlisted host"
        assert all("https://evil.example.com/secret/path/with/key=abc" not in m for m in rejected), (
            f"GT-63 regression: raw HF_ENDPOINT URL leaked in allowlist rejection: {rejected!r}"
        )
        # Hostname is OK to log (allowlist metadata, not PII).
        assert any("evil.example.com" in m for m in rejected)


class TestPathSafetyExceptionType:  # noqa: N801
    """log message must include ``type(exc).__name__`` so the operator"""

    def test_path_safety_failure_includes_exception_type_name(self, monkeypatch, caplog):
        secret_path = "/tmp/some/path/that/escapes/home"
        monkeypatch.setenv("HF_HOME", secret_path)

        def _raise_value_error(_path, _home):
            raise ValueError("path escapes home directory")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _raise_value_error,
        )

        with caplog.at_level(logging.WARNING):
            _validate_env_vars()

        matching = [r for r in caplog.records if "HF_HOME" in r.getMessage() and "path-safety" in r.getMessage()]
        assert matching, f"expected a path-safety failure record; got {[r.getMessage() for r in caplog.records]!r}"
        msg = matching[0].getMessage()
        assert "ValueError" in msg, f"GT-B1-14 regression: exception type name missing from log; got {msg!r}"
        assert secret_path not in msg, f"GT-63 regression: raw HF_HOME path leaked in path-safety failure log: {msg!r}"
        assert "path escapes home directory" in msg

    def test_path_safety_failure_with_oserror_includes_type(self, monkeypatch, caplog):
        """Same as above but with ``OSError`` to confirm the type name"""
        monkeypatch.setenv("HF_HOME", "/tmp/escapes/home")

        def _raise_oserror(_path, _home):
            raise OSError("permission denied")

        monkeypatch.setattr(
            "voice_typer.server.config._validate_path_safety",
            _raise_oserror,
        )

        with caplog.at_level(logging.WARNING):
            _validate_env_vars()

        matching = [r for r in caplog.records if "path-safety" in r.getMessage()]
        assert matching
        msg = matching[0].getMessage()
        assert "OSError" in msg
        assert "permission denied" in msg


class TestPrecompiledPatterns:
    """The validation regexes are module-level compiled constants."""

    def test_patterns_are_module_level_constants(self):
        from voice_typer.server import env_validation as ev

        assert isinstance(ev._BOOL_VALUE_PATTERN, re.Pattern)
        assert isinstance(ev._TOKEN_VALUE_PATTERN, re.Pattern)
        assert isinstance(ev._PATH_VALUE_PATTERN, re.Pattern)

    def test_bool_pattern_semantics_unchanged(self):
        from voice_typer.server import env_validation as ev

        for good in ("1", "0", "true", "FALSE", "Yes", "NO"):
            assert ev._BOOL_VALUE_PATTERN.match(good), good
        for bad in ("yes ", " 1", "on", "2", ""):
            assert ev._BOOL_VALUE_PATTERN.match(bad) is None, bad

    def test_token_pattern_semantics_unchanged(self):
        from voice_typer.server import env_validation as ev

        assert ev._TOKEN_VALUE_PATTERN.match("abc-123_XYZ.09")
        assert ev._TOKEN_VALUE_PATTERN.match("a" * 128)
        assert ev._TOKEN_VALUE_PATTERN.match("bad value") is None
        assert ev._TOKEN_VALUE_PATTERN.match("a" * 129) is None

    def test_path_pattern_semantics_unchanged(self):
        from voice_typer.server import env_validation as ev

        assert ev._PATH_VALUE_PATTERN.match("/home/user/.config")
        assert ev._PATH_VALUE_PATTERN.match("a\0b") is None
