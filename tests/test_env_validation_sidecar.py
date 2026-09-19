"""XZ-R3-09: regression tests for :func:`voice_typer.server.env_validation._validate_sidecar_env`."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest
from voice_typer.server.env_validation import _validate_env_vars

# All env vars touched by _validate_sidecar_env (kept in sync with the SUT).
_SIDECAR_VARS = (
    "TAURI_SIDECAR",
    "VOICE_TYPER_IPC_TOKEN",
    "VOICE_TYPER_NATIVE_DIR",
    "VOICE_TYPER_PREWARM_EXE",
)
# Vars from the rest of _validate_env_vars, also cleaned so they don't
_OTHER_VALIDATED_VARS = (
    "VOICE_TYPER_QUIET",
    "VOICE_TYPER_DEBUG",
    "VOICE_TYPER_NO_TRAY",
    "VOICE_TYPER_STREAMING",
    "VOICE_TYPER_RESTART",
    "VOICE_TYPER_CONFIG_DIR",
    "HF_HOME",
    "HF_ENDPOINT",
)
_ALL_VARS = _SIDECAR_VARS + _OTHER_VALIDATED_VARS


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Ensure none of the validated vars pre-exist before each test."""
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def _set_valid_sidecar_env(
    monkeypatch,
    *,
    native_dir: str | None = None,
    ipc_token: str = "tok_123ABC",
) -> None:
    """Set every sidecar env var to a valid value under ``Path.home()``."""
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", ipc_token)
    if native_dir is None:
        native_dir = str(Path.home() / ".voice-typer" / "native")
    monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", native_dir)


class TestNoOpWhenNotSidecar:
    """When ``TAURI_SIDECAR != \"1\"``, sidecar validation is skipped entirely."""

    def test_no_sidecar_flag_is_noop(self, monkeypatch):
        # TAURI_SIDECAR not set, must not log warnings or pop anything.
        _validate_env_vars()
        # Nothing was set in the first place, so nothing to assert —

    def test_sidecar_flag_wrong_value_is_noop(self, monkeypatch):
        monkeypatch.setenv("TAURI_SIDECAR", "0")
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "")  # would fail validation
        _validate_env_vars()
        # TAURI_SIDECAR != "1" → skip. Empty token must NOT be popped


class TestEmptyValuesPopped:
    """XZ-R3-09: empty token / path values must be popped (not just logged)."""

    def test_empty_ipc_token_popped(self, monkeypatch):
        _set_valid_sidecar_env(monkeypatch, ipc_token="")
        _validate_env_vars()
        assert "VOICE_TYPER_IPC_TOKEN" not in os.environ

    def test_empty_native_dir_popped(self, monkeypatch):
        _set_valid_sidecar_env(monkeypatch, native_dir="")
        _validate_env_vars()
        assert "VOICE_TYPER_NATIVE_DIR" not in os.environ


class TestPathSafetyValidation:
    """XZ-R3-09: NATIVE_DIR / PREWARM_EXE are run through _validate_path_safety."""

    def test_safe_path_under_home_preserved(self, monkeypatch):
        safe = str(Path.home() / ".voice-typer" / "native")
        _set_valid_sidecar_env(monkeypatch, native_dir=safe)
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_NATIVE_DIR") == safe

    def test_overlength_path_popped(self, monkeypatch):
        bad = "a" * 5000
        _set_valid_sidecar_env(monkeypatch, native_dir=bad)
        _validate_env_vars()
        assert "VOICE_TYPER_NATIVE_DIR" not in os.environ

    def test_path_outside_home_popped(self, monkeypatch):
        # /tmp is typically NOT under Path.home(), _validate_path_safety
        bad = "/tmp/voice-typer-native"
        # Skip this test if /tmp happens to be under home (extremely
        if Path(bad).resolve() == Path(Path.home(), "tmp", "voice-typer-native").resolve():
            pytest.skip("/tmp is under home on this host")
        _set_valid_sidecar_env(monkeypatch, native_dir=bad)
        _validate_env_vars()
        assert "VOICE_TYPER_NATIVE_DIR" not in os.environ

    def test_path_traversal_with_dots_popped(self, monkeypatch):
        # ``..`` traversal that escapes home, rejected by
        bad = str(Path.home() / ".." / ".." / "etc")
        _set_valid_sidecar_env(monkeypatch, native_dir=bad)
        _validate_env_vars()
        assert "VOICE_TYPER_NATIVE_DIR" not in os.environ


class TestTokenPatternValidation:
    """XZ-R3-09: VOICE_TYPER_IPC_TOKEN must match the alphanumeric token pattern."""

    @pytest.mark.parametrize(
        "token",
        [
            "abcDEF123_.-",
            "a" * 128,  # max length
            "tok_123",
        ],
    )
    def test_valid_token_preserved(self, monkeypatch, token):
        _set_valid_sidecar_env(monkeypatch, ipc_token=token)
        _validate_env_vars()
        assert os.environ.get("VOICE_TYPER_IPC_TOKEN") == token

    @pytest.mark.parametrize(
        "token",
        [
            "'; rm -rf /",  # shell metacharacters
            "token with spaces",
            "café_123",  # non-ASCII
            "a" * 129,  # overlength
            "token/with/slashes",
            "token@with@ats",
        ],
    )
    def test_invalid_token_popped(self, monkeypatch, token):
        _set_valid_sidecar_env(monkeypatch, ipc_token=token)
        _validate_env_vars()
        assert "VOICE_TYPER_IPC_TOKEN" not in os.environ


class TestGt63Redaction:
    """all SIDECAR-ENV log records must pre-redact the value."""

    def test_unsafe_path_value_not_logged(self, monkeypatch, caplog):
        secret_path = "/tmp/some/secret/path/with/username"
        _set_valid_sidecar_env(monkeypatch, native_dir=secret_path)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret_path in m for m in rendered), (
            f"GT-63 regression: raw SIDECAR path leaked to log: {rendered!r}"
        )

    def test_unsafe_token_value_not_logged(self, monkeypatch, caplog):
        secret_token = "'; rm -rf /"
        _set_valid_sidecar_env(monkeypatch, ipc_token=secret_token)
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        rendered = [r.getMessage() for r in caplog.records]
        assert not any(secret_token in m for m in rendered), (
            f"GT-63 regression: raw SIDECAR token leaked to log: {rendered!r}"
        )


class TestEndToEndAllValid:
    """All sidecar env vars valid → all preserved."""

    def test_all_valid_sidecar_env_preserved(self, monkeypatch):
        native = str(Path.home() / ".voice-typer" / "native")
        _set_valid_sidecar_env(
            monkeypatch,
            native_dir=native,
            ipc_token="tok_123ABC",
        )
        _validate_env_vars()
        assert os.environ.get("TAURI_SIDECAR") == "1"
        assert os.environ.get("VOICE_TYPER_IPC_TOKEN") == "tok_123ABC"
        assert os.environ.get("VOICE_TYPER_NATIVE_DIR") == native


class TestUnsetVarsLogged:
    """Unset expected sidecar env vars must trigger a warning (back-compat)."""

    def test_unset_ipc_token_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        monkeypatch.setenv("VOICE_TYPER_NATIVE_DIR", str(Path.home() / "native"))
        monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(Path.home() / "prewarm"))
        # VOICE_TYPER_IPC_TOKEN intentionally not set
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [
            r for r in caplog.records if "VOICE_TYPER_IPC_TOKEN" in r.getMessage() and "unset" in r.getMessage()
        ]
        assert matching, (
            f"expected a WARNING about unset VOICE_TYPER_IPC_TOKEN; got {[r.getMessage() for r in caplog.records]!r}"
        )

    def test_unset_native_dir_logs_warning(self, monkeypatch, caplog):
        monkeypatch.setenv("TAURI_SIDECAR", "1")
        monkeypatch.setenv("VOICE_TYPER_IPC_TOKEN", "tok_123")
        monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(Path.home() / "prewarm"))
        with caplog.at_level(logging.WARNING):
            _validate_env_vars()
        matching = [
            r for r in caplog.records if "VOICE_TYPER_NATIVE_DIR" in r.getMessage() and "unset" in r.getMessage()
        ]
        assert matching, (
            f"expected a WARNING about unset VOICE_TYPER_NATIVE_DIR; got {[r.getMessage() for r in caplog.records]!r}"
        )
