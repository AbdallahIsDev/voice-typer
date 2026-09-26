"""Tests: Path traversal validation."""

import sys
from pathlib import Path

import pytest


def test_validate_path_safety_normal_path():
    """Normal paths within parent are accepted."""
    from voice_typer.server.config import _validate_path_safety

    parent = Path("/home/user/.lausu")
    child = Path("/home/user/.lausu/config.json")
    # This may not work on all systems, so use tmp paths
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / "config.json"
        assert _validate_path_safety(child, parent) == child.resolve()


def test_validate_path_safety_traversal():
    """Path traversal attempts are rejected."""
    import tempfile

    from voice_typer.server.config import _validate_path_safety

    with tempfile.TemporaryDirectory() as tmp:
        parent = Path(tmp)
        child = parent / ".." / ".." / "etc" / "passwd"
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path_safety(child, parent)


class TestIsPathWithin:
    """Tests for ``_is_path_within``, the robust"""

    def test_same_path_is_within_itself(self):
        from voice_typer.server.config import _is_path_within

        p = Path("/home/user/.lausu")
        assert _is_path_within(p, p) is True

    def test_direct_child_is_within(self):
        from voice_typer.server.config import _is_path_within

        root = Path("/home/user/.lausu")
        child = root / "config.json"
        assert _is_path_within(child, root) is True

    def test_sibling_prefix_is_not_within(self):
        # The classic /home/userX vs /home/user trap that a naive
        from voice_typer.server.config import _is_path_within

        root = Path("/home/user")
        sibling = Path("/home/userX")
        assert _is_path_within(sibling, root) is False

    def test_traversal_after_resolve_is_rejected(self):
        # A ".." segment that escapes root must resolve to a path
        from voice_typer.server.config import _is_path_within

        root = Path("/home/user/.lausu")
        escaped = root / ".." / ".." / "etc"
        assert _is_path_within(escaped, root) is False

    def test_root_directory_contains_its_descendants(self):
        # /etc IS within / (commonpath edge case).
        from voice_typer.server.config import _is_path_within

        assert _is_path_within(Path("/etc"), Path("/")) is True

    def test_case_insensitive_on_windows_and_macos(self, monkeypatch):
        # On win32/darwin the comparison is lower-cased, so C:\Users\X
        from voice_typer.server import config

        # Belt-and-braces: patch the global sys.platform too so any
        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("C:/users/X")
        child = Path("C:/Users/X/AppData")
        assert config._is_path_within(child, root, case_sensitive=False) is True

    def test_case_sensitive_on_linux(self, monkeypatch):
        from voice_typer.server import config

        monkeypatch.setattr(sys, "platform", "linux")
        root = Path("/home/X")
        child = Path("/Home/X")
        assert config._is_path_within(child, root, case_sensitive=True) is False

    def test_cross_drive_windows_returns_false(self, monkeypatch):
        from voice_typer.server import config

        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("C:/lausu")
        child = Path("D:/lausu/data")
        assert config._is_path_within(child, root, case_sensitive=False) is False
