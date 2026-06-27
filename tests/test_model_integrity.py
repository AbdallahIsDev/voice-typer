"""Tests for PROD-006: Model integrity verification."""
import tempfile
from pathlib import Path


def test_verify_model_integrity_missing_dir():
    """Returns False for non-existent directory."""
    from voice_typer.server.asr_setup import _verify_model_integrity
    assert _verify_model_integrity("test/model", "/nonexistent/path") is False


def test_verify_model_integrity_empty_dir():
    """Returns False for directory with no model files."""
    from voice_typer.server.asr_setup import _verify_model_integrity
    with tempfile.TemporaryDirectory() as tmp:
        assert _verify_model_integrity("test/model", tmp) is False


def test_verify_model_integrity_valid():
    """Returns True for directory with model and config files."""
    from voice_typer.server.asr_setup import _verify_model_integrity
    with tempfile.TemporaryDirectory() as tmp:
        # Create a model file
        (Path(tmp) / "model.safetensors").write_bytes(b"\x00" * 100)
        # Create a config file
        (Path(tmp) / "config.json").write_text('{"model_type": "test"}')
        assert _verify_model_integrity("test/model", tmp) is True


def test_verify_model_integrity_no_config():
    """Returns False for directory with model but no config."""
    from voice_typer.server.asr_setup import _verify_model_integrity
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "model.bin").write_bytes(b"\x00" * 100)
        assert _verify_model_integrity("test/model", tmp) is False


def test_verify_model_integrity_empty_model_file():
    """Returns False for directory with empty model file."""
    from voice_typer.server.asr_setup import _verify_model_integrity
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "model.safetensors").write_bytes(b"")
        (Path(tmp) / "config.json").write_text('{}')
        assert _verify_model_integrity("test/model", tmp) is False
