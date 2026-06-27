"""Tests for SEC-005: Path traversal validation."""
from pathlib import Path
import pytest


def test_validate_path_safety_normal_path():
    """Normal paths within parent are accepted."""
    from voice_typer.server.config import _validate_path_safety
    parent = Path("/home/user/.voice-typer")
    child = Path("/home/user/.voice-typer/config.json")
    # This may not work on all systems, so use tmp paths
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / "config.json"
        assert _validate_path_safety(child, parent) == child.resolve()


def test_validate_path_safety_traversal():
    """Path traversal attempts are rejected."""
    from voice_typer.server.config import _validate_path_safety
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / ".." / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path_safety(child, parent)
