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
        # use a path under ``Path.home()`` because both
        # VOICE_TYPER_CONFIG_DIR and HF_HOME now run
        # ``_validate_path_safety(Path(val), Path.home())`` (mirroring
        # the SEC-HFHOME-001 pattern). A path like ``/home/user/...`` is
        # rejected on hosts where ``Path.home()`` is not ``/home/user``.
        safe_path = str(Path.home() / ".config" / "voice-typer")
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

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
        # Keep the value under home (like the other preserved tests): a
        # leading-space relative value like "   /tmp/voice typer   "
        # resolves against the process CWD, which on CI runners is NOT
        # under home, so _validate_path_safety would reject it and the
        # var would be discarded.
        safe_path = str(Path.home() / "voice typer   ")
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_unicode_path_preserved(self, monkeypatch, var):
        # Non-ASCII chars are allowed (only NUL is forbidden).
        # keep the path under ``Path.home()`` so the
        # ``_validate_path_safety`` check (run for both VOICE_TYPER_CONFIG_DIR
        # and HF_HOME) does not reject it as an out-of-home traversal.
        safe_path = str(Path.home() / "配置" / "voice-typer")
        monkeypatch.setenv(var, safe_path)
        _validate_env_vars()
        assert os.environ.get(var) == safe_path

    @pytest.mark.parametrize("var", _PATH_VARS)
    def test_path_at_max_length_preserved(self, monkeypatch, var):
        # Boundary: len == 4096 is allowed (length check is `> 4096`).
        # Build an ABSOLUTE path under home of exactly 4096 chars.
        # A relative "a"*4096 resolves against the process CWD, which on
        # CI runners is NOT under home (e.g. D:\\a\\_work on Windows
        # runners), so _validate_path_safety would reject it and the var
        # would be discarded — the test would fail even though the
        # length-boundary behavior under test is correct.
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


# ─── Integration: all vars set ─────────────────────────────────────────


class TestAllVarsSet:
    """End-to-end: every validated var present and valid — all preserved."""

    def test_all_valid_all_preserved(self, monkeypatch):
        # ``/tmp/voice-typer`` is outside ``Path.home()`` so it
        # is now rejected by the path-safety check for both
        # VOICE_TYPER_CONFIG_DIR and HF_HOME. Use a path under home.
        safe_path = str(Path.home() / ".voice-typer-test")
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
        # HF_ENDPOINT invalid value (HTTP scheme) — must be popped.
        monkeypatch.setenv("HF_ENDPOINT", "http://evil.example.com")
        _validate_env_vars()
        for var in _ALL_VARS:
            assert var not in os.environ, f"{var} should have been removed"


# HF_ENDPOINT () ─────────────────────────────────────────────


class TestHfEndpoint:
    """G4-M-58: ``HF_ENDPOINT`` is validated against an HTTPS+allowlist rule.

    HF_ENDPOINT is consumed by the ``huggingface_hub`` library as the
    base URL for model downloads. An attacker-controlled value could
    redirect downloads to a malicious server that serves tampered
    weights. The validator:

      1. Requires the ``https://`` scheme (rejects ``http://``).
      2. Validates the hostname is well-formed.
      3. Allowlists to ``huggingface.co`` and ``hf-mirror.com``.

    On failure, the env var is popped and a WARNING is logged (same
    pattern as the ``HF_HOME`` path-safety check).
    """

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
            # HTTP scheme — must be rejected even for allowlisted host.
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
        # Empty string is "set but not None" — basic pattern ^[^\0]+ fails.
        monkeypatch.setenv("HF_ENDPOINT", "")
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ

    def test_overlength_hf_endpoint_removed(self, monkeypatch):
        monkeypatch.setenv("HF_ENDPOINT", "https://huggingface.co/" + "a" * 5000)
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ

    def test_unset_hf_endpoint_is_noop(self, monkeypatch):
        # No HF_ENDPOINT set — validator must not raise or create it.
        monkeypatch.delenv("HF_ENDPOINT", raising=False)
        _validate_env_vars()
        assert "HF_ENDPOINT" not in os.environ


# env-var values pre-redacted in log records ─────────────────


class TestGt63EnvVarValuesRedacted:
    """GT-63: ALL env-var values logged by ``_validate_env_vars`` are
    pre-redacted at the call site (``<redacted>`` literal in the message
    body) — defense-in-depth so a handler that bypasses
    ``PIIRedactionFilter`` cannot leak the raw value.

    Booleans and log levels are on the explicit safe-list per the
    spec, but a *failed* boolean validation means the value is NOT a
    boolean — it's an opaque string the operator typed — so it must
    be redacted too.
    """

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
        secret_path = "/Users/jane.doe/.config/voice-typer" + "x" * 5000
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
        """All three HF_ENDPOINT rejection branches (scheme, hostname,
        allowlist) must redact the raw URL.
        """
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
        """The allowlist rejection path logs the hostname (which is
        allowlisted metadata, not PII) but must NOT log the raw URL.
        """
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


# path-safety validation failure includes exception type ──


class TestPathSafetyExceptionType:  # noqa: N801
    """GT-B1-14: when ``_validate_path_safety`` rejects ``HF_HOME``, the
    log message must include ``type(exc).__name__`` so the operator
    knows which validation predicate failed (``ValueError`` vs
    ``OSError`` vs ``RuntimeError``) without having to grep the source.

    The HF_HOME value itself is redacted per GT-63; only the exception
    *type name* and the exception *message* (which describes the rule,
    not the value) are logged.
    """

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
        """Same as above but with ``OSError`` to confirm the type name
        is dynamic, not hardcoded.
        """
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
    """The validation regexes are module-level compiled constants.

    ``_validate_env_vars`` runs once per process at the startup gate;
    recompiling the patterns inside the function body wasted work on
    every call (and made the compiled objects unreachable for
    inspection). They are now module constants with identical
    semantics.
    """

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
