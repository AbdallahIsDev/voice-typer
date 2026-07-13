"""Tests for SEC-001: Restart token verification."""
import os
import tempfile
from pathlib import Path
from unittest.mock import patch


def test_verify_restart_token_missing_file():
    """Token verification returns False when token file doesn't exist."""
    from voice_typer.server.app import _verify_restart_token
    with patch.dict(os.environ, {"VOICE_TYPER_RESTART": "sometoken"}), \
            patch("voice_typer.server.config._config_dir", return_value=Path("/nonexistent")):
        assert _verify_restart_token() is False


def test_verify_restart_token_invalid_token():
    """Token verification returns False when tokens don't match."""
    from voice_typer.server.app import _verify_restart_token
    with tempfile.TemporaryDirectory() as tmp:
        token_path = Path(tmp) / ".restart_token"
        token_path.write_text("correct_token", encoding="utf-8")
        with patch.dict(os.environ, {"VOICE_TYPER_RESTART": "wrong_token"}), \
                patch("voice_typer.server.config._config_dir", return_value=Path(tmp)):
            assert _verify_restart_token() is False


def test_verify_restart_token_valid():
    """Token verification returns True when tokens match."""
    from voice_typer.server.app import _verify_restart_token
    with tempfile.TemporaryDirectory() as tmp:
        token = "abc123def456"
        token_path = Path(tmp) / ".restart_token"
        token_path.write_text(token, encoding="utf-8")
        with patch.dict(os.environ, {"VOICE_TYPER_RESTART": token}), \
                patch("voice_typer.server.config._config_dir", return_value=Path(tmp)):
            assert _verify_restart_token() is True


def test_verify_restart_token_no_env_var():
    """Token verification returns False when env var is not set."""
    from voice_typer.server.app import _verify_restart_token
    with patch.dict(os.environ, {}, clear=True):
        # Remove VOICE_TYPER_RESTART if it exists
        os.environ.pop("VOICE_TYPER_RESTART", None)
        assert _verify_restart_token() is False


def test_generate_restart_token():
    """Token generation creates a file with a random token."""
    from voice_typer.server.app import _generate_restart_token
    with tempfile.TemporaryDirectory() as tmp, \
            patch("voice_typer.server.config._config_dir", return_value=Path(tmp)):
        token = _generate_restart_token()
        assert len(token) == 32  # hex string of 16 random bytes
        token_path = Path(tmp) / ".restart_token"
        assert token_path.exists()
        assert token_path.read_text(encoding="utf-8") == token
