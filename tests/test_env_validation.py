"""Unit tests for :mod:`voice_typer.server.env_validation`.

Covers the single public function ``_validate_env_vars`` defined in
``voice_typer/server/env_validation.py``.

Important: this validator checks **format** (not "required-ness") of the
environment variables consumed by the voice-typer server. Invalid values
are logged at WARNING level and **removed** from ``os.environ`` via
``os.environ.pop`` — the function never raises.

Vars validated (see ``env_validation.py`` for the authoritative list):

* 4 boolean vars — ``VOICE_TYPER_QUIET``, ``VOICE_TYPER_DEBUG``,
  ``VOICE_TYPER_NO_TRAY``, ``VOICE_TYPER_STREAMING``
  (pattern: ``^(1|0|true|false|yes|no)$``, case-insensitive).
* 2 token vars — ``VOICE_TYPER_RESTART``, ``VOICE_TYPER_IPC_TOKEN``
  (pattern: ``^[A-Za-z0-9._\\-]{1,128}$``).
* 2 path vars — ``VOICE_TYPER_CONFIG_DIR``, ``HF_HOME``
  (pattern: ``^[^\\0]+$`` with ``len <= 4096``).
* ``SystemRoot`` — delegated to
  :func:`voice_typer.server.config._validate_systemroot` (no-op on
  non-Windows).

The tests below cover: valid values preserved, invalid values removed,
empty-string handling, whitespace, unicode, length boundaries, the
``_validate_systemroot`` delegation, the ``None`` return value, and
log-warning emission.
"""

from __future__ import annotations

import logging
import os

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
_ALL_VARS = _BOOL_VARS + _TOKEN_VARS + _PATH_VARS

_VALID_BOOL_VALUES = ("1", "0", "true", "false", "yes", "no")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure none of the validated vars pre-exist before each test.

    Some CI shells leak ``HF_HOME`` or ``VOICE_TYPER_DEBUG`` into the
    pytest process; we want each test to start from a known-empty state.
    """
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


# ─── Top-level behaviour ───────────────────────────────────────────────


class TestValidateEnvVarsContract:
    """Return value, no-op behaviour, and SystemRoot delegation."""

    def test_returns_none_when_no_vars_set(self):
        """No env vars set — must succeed and return None."""
        assert _validate_env_vars() is None

    def test_no_vars_set_does_not_create_any(self):
        """No env vars set — must not add anything to os.environ."""
        before = {v: os.environ.get(v) for v in _ALL_VARS}
        _validate_env_vars()
        after = {v: os.environ.get(v) for v in _ALL_VARS}
        assert before == after == {v: None for v in _ALL_VARS}

    def test_calls_validate_systemroot(self, monkeypatch):
        """SEC-audit-011: must delegate SystemRoot checks to config module."""
        calls = []

        def _fake_validate_systemroot():
            calls.append("called")

        # env_validation.py imports _validate_systemroot lazily inside the
        # function body, so patching the attribute on config works.
        monkeypatch.setattr(
            "voice_typer.server.config._validate_systemroot",
            _fake_validate_systemroot,
        )
        _validate_env_vars()
        assert calls == ["called"]


# ─── Boolean vars ──────────────────────────────────────────────────────


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
        # regex compiled with re.IGNORECASE — mixed-case valid values pass.
        # "0No" is included as a negative control: it must be removed.
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
        # Empty string is "set but not None" — gets checked and fails.
        monkeypatch.setenv(var, "")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _BOOL_VARS)
    def test_whitespace_only_boolean_removed(self, monkeypatch, var):
        # Padded valid value — regex anchors ^ and $, so this fails.
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


# ─── Token vars ────────────────────────────────────────────────────────


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
        # {1,128} is inclusive at both ends — 128 chars passes.
        monkeypatch.setenv(var, "a" * 128)
        _validate_env_vars()
        assert os.environ.get(var) == "a" * 128

    @pytest.mark.parametrize("var", _TOKEN_VARS)
    def test_overlength_token_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "a" * 129)
        _validate_env_vars()
        assert var not in os.environ


# ─── Path vars ─────────────────────────────────────────────────────────


class TestPathVars:
    """``VOICE_TYPER_CONFIG_DIR`` and ``HF_HOME``."""

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_valid_path_preserved(self, monkeypatch, var):
        monkeypatch.setenv(var, "/home/user/.config/voice-typer")
        _validate_env_vars()
        assert os.environ.get(var) == "/home/user/.config/voice-typer"

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_empty_path_removed(self, monkeypatch, var):
        # Path pattern ^[^\0]+ requires at least 1 char — "" fails.
        monkeypatch.setenv(var, "")
        _validate_env_vars()
        assert var not in os.environ

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_whitespace_path_preserved(self, monkeypatch, var):
        # Whitespace (incl. spaces inside) is allowed — only NUL is
        # forbidden. Paths legitimately contain spaces.
        monkeypatch.setenv(var, "   /tmp/voice typer   ")
        _validate_env_vars()
        assert os.environ.get(var) == "   /tmp/voice typer   "

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_unicode_path_preserved(self, monkeypatch, var):
        # Non-ASCII chars are allowed (only NUL is forbidden).
        monkeypatch.setenv(var, "/home/user/配置/voice-typer")
        _validate_env_vars()
        assert os.environ.get(var) == "/home/user/配置/voice-typer"

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_path_at_max_length_preserved(self, monkeypatch, var):
        # Boundary: len == 4096 is allowed (length check is `> 4096`).
        monkeypatch.setenv(var, "a" * 4096)
        _validate_env_vars()
        assert os.environ.get(var) == "a" * 4096

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_overlength_path_removed(self, monkeypatch, var):
        monkeypatch.setenv(var, "/a" * 2500)  # 5000 chars > 4096
        _validate_env_vars()
        assert var not in os.environ


# ─── Integration: all vars set ─────────────────────────────────────────


class TestAllVarsSet:
    """End-to-end: every validated var present and valid — all preserved."""

    def test_all_valid_all_preserved(self, monkeypatch):
        for var in _BOOL_VARS:
            monkeypatch.setenv(var, "1")
        for var in _TOKEN_VARS:
            monkeypatch.setenv(var, "tok_123")
        for var in _PATH_VARS:
            monkeypatch.setenv(var, "/tmp/voice-typer")
        _validate_env_vars()
        for var in _BOOL_VARS:
            assert os.environ.get(var) == "1", f"{var} was modified"
        for var in _TOKEN_VARS:
            assert os.environ.get(var) == "tok_123", f"{var} was modified"
        for var in _PATH_VARS:
            assert os.environ.get(var) == "/tmp/voice-typer", f"{var} modified"

    def test_all_invalid_all_removed(self, monkeypatch):
        for var in _BOOL_VARS:
            monkeypatch.setenv(var, "maybe")
        for var in _TOKEN_VARS:
            monkeypatch.setenv(var, "'; rm -rf /")
        for var in _PATH_VARS:
            monkeypatch.setenv(var, "a" * 5000)
        _validate_env_vars()
        for var in _ALL_VARS:
            assert var not in os.environ, f"{var} should have been removed"
