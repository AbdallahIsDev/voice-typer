"""FR-18 (P4-A1): regression tests for the sensitive-env-var stripping
added to :func:`voice_typer.server.env_validation._validate_env_vars`.

Context
-------
``electron_launcher._strip_sensitive_env`` strips well-known
cloud-provider API keys / model-download tokens
(``HF_TOKEN``, ``HUGGING_FACE_HUB_TOKEN``, ``OPENAI_API_KEY``,
``ANTHROPIC_API_KEY``, ``GEMINI_API_KEY``, ``DEEPGRAM_API_KEY``,
``GROQ_API_KEY``) from the Electron FRONTEND child's environment —
but the Python sidecar process itself (in Electron mode) and the
standalone-mode process (``python -m voice_typer.server``) inherit
the parent shell env verbatim. The Tauri production path strips them
via ``env_clear()`` in ``src-tauri/src/sidecar/spawn.rs``.

A developer with ``HF_TOKEN`` exported in their shell would have
``huggingface_hub.snapshot_download()`` (called from
``asr_setup.py:417`` WITHOUT ``token=``) silently attach their
personal HF token to model-download requests — rate-limited / quota-
charged against their HF account with no UI indication.

Fix
---
``_validate_env_vars()`` now pops every name in
``env_validation._SENSITIVE_ENV_NAMES`` from ``os.environ`` and logs a
WARNING with the key NAME ONLY (never the value — GT-63 redaction
contract).

Tests
-----
* Each sensitive env var is popped when set.
* A WARNING is logged mentioning the var name.
* The var VALUE is never logged (GT-63 redaction contract).
* Vars NOT in the sensitive list are preserved.
* ``_SENSITIVE_ENV_NAMES`` in ``env_validation`` matches
  ``electron_launcher._SENSITIVE_ENV_NAMES`` exactly (drift detection
  — catches a future contributor adding a new provider to one list
  but not the other).
"""

from __future__ import annotations

import logging
import os

import pytest
from voice_typer.server.env_validation import _SENSITIVE_ENV_NAMES, _validate_env_vars

# Mirror of the SUT's sensitive-env-name set, kept as a local literal so
# a future contributor who deletes / renames the SUT constant gets a
# clear failure here (rather than the test silently importing nothing).
_EXPECTED_SENSITIVE_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "DEEPGRAM_API_KEY",
        "GROQ_API_KEY",
    }
)


@pytest.fixture(autouse=True)
def _clean_sensitive_env(monkeypatch):
    """Ensure none of the sensitive env vars pre-exist before each test.

    Some CI shells leak ``HF_TOKEN`` or ``OPENAI_API_KEY`` into the
    pytest process (e.g. a developer running tests locally with their
    own env exported). We want each test to start from a known-empty
    state AND we want monkeypatch to restore the original value on
    teardown (so the test doesn't pollute later tests in the session
    via the SUT's direct ``os.environ.pop`` — which bypasses
    monkeypatch's restoration mechanism).
    """
    for var in _EXPECTED_SENSITIVE_ENV_NAMES:
        monkeypatch.delenv(var, raising=False)
    yield


# ─── Sensitive env vars are popped ────────────────────────────────────


class TestSensitiveEnvVarsPopped:
    """FR-18: every name in ``_SENSITIVE_ENV_NAMES`` is popped from
    ``os.environ`` by ``_validate_env_vars()``."""

    @pytest.mark.parametrize("var", sorted(_EXPECTED_SENSITIVE_ENV_NAMES))
    def test_sensitive_var_popped_when_set(self, monkeypatch, var):
        monkeypatch.setenv(var, "secret-value-do-not-log")
        _validate_env_vars()
        assert var not in os.environ, (
            f"FR-18 regression: {var} was NOT popped by _validate_env_vars() — "
            "it should be stripped to prevent the Python sidecar from "
            "inheriting cloud-provider API keys / HF tokens from the parent "
            "shell in Electron / standalone mode."
        )

    def test_all_sensitive_vars_popped_when_all_set(self, monkeypatch):
        for var in _EXPECTED_SENSITIVE_ENV_NAMES:
            monkeypatch.setenv(var, f"secret-{var}-value")
        _validate_env_vars()
        for var in _EXPECTED_SENSITIVE_ENV_NAMES:
            assert var not in os.environ, f"{var} should have been popped"

    def test_unset_sensitive_vars_are_noop(self, monkeypatch):
        """If no sensitive vars are set, the validator must not raise
        or create them."""
        # Fixture already cleared them; just call the validator.
        _validate_env_vars()
        for var in _EXPECTED_SENSITIVE_ENV_NAMES:
            assert var not in os.environ


# ─── Non-sensitive vars preserved ────────────────────────────────────


class TestNonSensitiveVarsPreserved:
    """FR-18: env vars NOT in ``_SENSITIVE_ENV_NAMES`` are preserved
    by the new stripping block (defense-in-depth: the strip must be
    surgical, not blanket)."""

    @pytest.mark.parametrize(
        "var",
        [
            # Vars that LOOK sensitive but are legitimately needed by
            # the Python sidecar or by Voice Typer's own operation.
            "PATH",
            "HOME",
            "LANG",
            "VOICE_TYPER_IPC_TOKEN",
            "VT_IPC_TOKEN",
            "VT_PYTHON_PORT",
            "TAURI_SIDECAR",
            "HF_HOME",  # consumed by config._validate_import_path
            "HF_ENDPOINT",  # consumed by huggingface_hub (validated separately)
        ],
    )
    def test_non_sensitive_var_preserved(self, monkeypatch, var):
        # HF_ENDPOINT and HF_HOME have their own validation logic in
        # _validate_env_vars() — set them to a valid value so they
        # survive the full validator (not just the  strip block).
        if var == "HF_HOME":
            from pathlib import Path

            monkeypatch.setenv(var, str(Path.home() / ".cache" / "huggingface"))
        elif var == "HF_ENDPOINT":
            monkeypatch.setenv(var, "https://huggingface.co")
        elif var == "VOICE_TYPER_IPC_TOKEN":
            monkeypatch.setenv(var, "valid_token_123")
        elif var == "VT_PYTHON_PORT":
            monkeypatch.setenv(var, "9876")
        elif var == "TAURI_SIDECAR":
            # Setting TAURI_SIDECAR=1 would trigger _validate_sidecar_env
            # which warns about missing sidecar vars. Set to "0" to skip
            # the sidecar path (the SUT checks `!= "1"`).
            monkeypatch.setenv(var, "0")
        else:
            monkeypatch.setenv(var, "some-non-secret-value")
        original = os.environ.get(var)
        _validate_env_vars()
        assert os.environ.get(var) == original, (
            f"FR-18 regression: non-sensitive var {var} was modified by the "
            "sensitive-env strip block. The strip must be surgical — only "
            "the names in _SENSITIVE_ENV_NAMES should be popped."
        )


# ─── WARNING logged (key name only, never the value) ─────────────────


class TestSensitiveEnvWarningLogged:
    """FR-18: a WARNING is logged for each stripped var, mentioning the
    var NAME ONLY (never the value — GT-63 redaction contract)."""

    def test_warning_logged_for_each_sensitive_var(self, monkeypatch, caplog):
        monkeypatch.setenv("HF_TOKEN", "hf_secret_value_12345")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [r for r in caplog.records if "HF_TOKEN" in r.getMessage()]
        assert matching, (
            "FR-18 regression: expected a WARNING mentioning HF_TOKEN; "
            f"got {[r.getMessage() for r in caplog.records]!r}"
        )

    def test_warning_value_never_logged(self, monkeypatch, caplog):
        """GT-63: the secret VALUE must never appear in the log — only
        the key name. The warning must use the ``<redacted>`` style or
        simply omit the value entirely."""
        secret = "hf_super_secret_value_DO_NOT_LEAK_abc123"
        monkeypatch.setenv("HF_TOKEN", secret)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret in m for m in rendered), (
            f"GT-63 regression: raw HF_TOKEN value leaked to log: {rendered!r}"
        )

    @pytest.mark.parametrize(
        "var,value",
        [
            ("OPENAI_API_KEY", "sk-leak-me-12345"),
            ("ANTHROPIC_API_KEY", "sk-ant-leak-me-67890"),
            ("GEMINI_API_KEY", "AIza-leak-me-abcdef"),
            ("HF_TOKEN", "hf_leak_me_ghijkl"),
            ("HUGGING_FACE_HUB_TOKEN", "hf_hub_leak_mnopqr"),
            ("DEEPGRAM_API_KEY", "deepgram-leak-stuvwx"),
            ("GROQ_API_KEY", "gsk-leak-yz0123"),
        ],
    )
    def test_no_secret_value_leaked_for_any_var(self, monkeypatch, caplog, var, value):
        """GT-63: the secret value MUST NEVER appear in any log record,
        for every var in the sensitive list."""
        monkeypatch.setenv(var, value)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(value in m for m in rendered), (
            f"GT-63 regression: raw value of {var} leaked to log: {rendered!r}"
        )
        # The var NAME should appear (so the operator can diagnose).
        assert any(var in m for m in rendered), (
            f"FR-18 regression: expected a WARNING mentioning {var}; got {rendered!r}"
        )

    def test_warning_level_is_warning_not_error(self, monkeypatch, caplog):
        """The log record MUST be at WARNING level (not ERROR — the
        operator may have set the env var legitimately for another
        tool; we just don't want Voice Typer to inherit it)."""
        monkeypatch.setenv("HF_TOKEN", "hf_test_value")
        with caplog.at_level(logging.DEBUG):
            _validate_env_vars()
        matching = [r for r in caplog.records if "HF_TOKEN" in r.getMessage()]
        assert matching, "expected at least one record mentioning HF_TOKEN"
        assert all(r.levelno == logging.WARNING for r in matching), (
            f"expected all HF_TOKEN records at WARNING level; got levels {[r.levelno for r in matching]!r}"
        )


# ─── Drift detection: env_validation ↔ electron_launcher ─────────────


class TestSensitiveEnvNamesDriftDetection:
    """FR-18: the ``_SENSITIVE_ENV_NAMES`` frozenset in
    ``env_validation`` MUST stay in sync with the
    ``_SENSITIVE_ENV_NAMES`` frozenset in ``electron_launcher``.

    The duplication is deliberate (env_validation is a low-level
    startup module; importing electron_launcher would pull in
    ``_electron_build`` and ``platform_utils`` at startup time, which
    is intentionally avoided — see ``shutdown_controller.py:917`` and
    ``ipc_server.py:2039`` which both lazy-import electron_launcher
    for the same reason). This drift-detection test catches a future
    contributor who adds a new cloud provider (e.g.
    ``MISTRAL_API_KEY``) to one list but not the other.
    """

    def test_env_validation_sensitive_names_match_electron_launcher(self):
        """The two ``_SENSITIVE_ENV_NAMES`` frozensets MUST be equal."""
        from voice_typer.server.electron_launcher import (
            _SENSITIVE_ENV_NAMES as electron_sensitive_names,  # noqa: N811
        )

        assert electron_sensitive_names == _SENSITIVE_ENV_NAMES, (
            "FR-18 drift: env_validation._SENSITIVE_ENV_NAMES and "
            "electron_launcher._SENSITIVE_ENV_NAMES have diverged. "
            f"env_validation={sorted(_SENSITIVE_ENV_NAMES)!r}; "
            f"electron_launcher={sorted(electron_sensitive_names)!r}. "
            "Both lists MUST stay in sync — a new cloud provider added "
            "to one MUST be added to the other (the lists are duplicated "
            "to avoid env_validation importing electron_launcher at "
            "startup time, but the drift is caught by this test)."
        )

    def test_expected_sensitive_names_match_sut(self):
        """The SUT's ``_SENSITIVE_ENV_NAMES`` MUST match the expected
        list (catches accidental deletion / rename of the constant)."""
        assert _SENSITIVE_ENV_NAMES == _EXPECTED_SENSITIVE_ENV_NAMES, (
            f"FR-18 regression: _SENSITIVE_ENV_NAMES drifted from the "
            f"expected list. SUT={sorted(_SENSITIVE_ENV_NAMES)!r}; "
            f"expected={sorted(_EXPECTED_SENSITIVE_ENV_NAMES)!r}."
        )

    def test_sensitive_names_contains_known_providers(self):
        """Sanity check: the well-known cloud-provider API key names
        MUST be present in the SUT's sensitive list."""
        required = {
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "HF_TOKEN",
            "GROQ_API_KEY",
            "DEEPGRAM_API_KEY",
        }
        missing = required - _SENSITIVE_ENV_NAMES
        assert not missing, (
            f"FR-18 regression: required sensitive env names missing from _SENSITIVE_ENV_NAMES: {sorted(missing)!r}"
        )

    def test_sensitive_names_is_frozenset(self):
        """The SUT's ``_SENSITIVE_ENV_NAMES`` MUST be a ``frozenset``
        (immutable — prevents accidental in-place mutation at runtime)."""
        assert isinstance(_SENSITIVE_ENV_NAMES, frozenset), (
            f"FR-18 regression: _SENSITIVE_ENV_NAMES must be a frozenset "
            f"(immutable); got {type(_SENSITIVE_ENV_NAMES).__name__}"
        )


# ─── Integration: stripping is idempotent ────────────────────────────


class TestStrippingIdempotent:
    """FR-18: calling ``_validate_env_vars()`` twice MUST NOT raise or
    log a second warning (the var is already gone after the first call)."""

    def test_second_call_no_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("HF_TOKEN", "hf_test_value")
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        first_call_warnings = [r for r in caplog.records if "HF_TOKEN" in r.getMessage()]
        assert first_call_warnings, "first call should log a warning"

        caplog.clear()
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        second_call_warnings = [r for r in caplog.records if "HF_TOKEN" in r.getMessage()]
        assert not second_call_warnings, (
            "FR-18 regression: second call should NOT log a warning for "
            "HF_TOKEN (it was already popped on the first call). "
            f"Got: {[r.getMessage() for r in second_call_warnings]!r}"
        )
