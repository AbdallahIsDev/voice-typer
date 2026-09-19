"""regression tests: ``_validate_path_safety`` prefix-match bug fix."""

import sys
from pathlib import Path

import pytest


class TestValidatePathSafetyCr17:
    """Pin the fix: prefix-match bug must not regress."""

    def test_sibling_prefix_is_rejected(self):
        """``/home/userX/secret`` is NOT within ``/home/user``."""
        from voice_typer.server.config import _validate_path_safety

        parent = Path("/home/user")
        sibling = Path("/home/userX/secret")
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path_safety(sibling, parent)

    def test_legitimate_child_is_accepted(self):
        """``/home/user/file.txt`` IS within ``/home/user``."""
        from voice_typer.server.config import _validate_path_safety

        parent = Path("/home/user")
        child = Path("/home/user/file.txt")
        # Should not raise; returns the resolved child path.
        result = _validate_path_safety(child, parent)
        assert result == child.resolve()

    def test_deep_descendant_is_accepted(self):
        """Deeply nested descendants are within the parent."""
        from voice_typer.server.config import _validate_path_safety

        parent = Path("/home/user")
        child = Path("/home/user/.voice-typer/models/qwen/base")
        result = _validate_path_safety(child, parent)
        assert result == child.resolve()

    def test_parent_itself_is_accepted(self):
        """The parent directory itself is \"within\" the parent."""
        from voice_typer.server.config import _validate_path_safety

        parent = Path("/home/user")
        result = _validate_path_safety(parent, parent)
        assert result == parent.resolve()

    def test_traversal_with_dotdot_is_rejected(self):
        """``/home/user/../etc/passwd`` escapes ``/home/user``."""
        from voice_typer.server.config import _validate_path_safety

        parent = Path("/home/user")
        escaped = Path("/home/user/../etc/passwd")
        with pytest.raises(ValueError, match="Path traversal"):
            _validate_path_safety(escaped, parent)

    def test_tmp_path_round_trip(self, tmp_path):
        """Real filesystem: child within tmp_path is accepted."""
        from voice_typer.server.config import _validate_path_safety

        child = tmp_path / "config.json"
        child.touch()
        result = _validate_path_safety(child, tmp_path)
        assert result == child.resolve()

    def test_tmp_path_sibling_rejected(self, tmp_path):
        """Real filesystem: sibling directory outside tmp_path is rejected."""
        from voice_typer.server.config import _validate_path_safety

        # Create a sibling directory whose name is a prefix of tmp_path.
        sibling_dir_name = tmp_path.name[:-1] if len(tmp_path.name) > 1 else tmp_path.name + "X"
        sibling_dir = tmp_path.parent / sibling_dir_name
        # The sibling may or may not exist; _validate_path_safety should
        try:
            from voice_typer.server.config import _validate_path_safety

            with pytest.raises(ValueError, match="Path traversal"):
                _validate_path_safety(sibling_dir, tmp_path)
        finally:
            # Best-effort cleanup in case the test created anything.
            pass


class TestIsPathWithinCrossDrive:
    """``_is_path_within`` (the helper delegates to) must"""

    def test_cross_drive_windows_returns_false(self, monkeypatch):
        """``C:\\foo`` is NOT within ``D:\\foo`` (different drives)."""
        from voice_typer.server import config

        # "platform", "win32")``, but ``config`` does NOT import
        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("D:/voice-typer")
        child = Path("C:/voice-typer/data")
        assert config._is_path_within(child, root, case_sensitive=False) is False

    def test_same_drive_windows_accepted(self, monkeypatch):
        """``C:\\Users\\X\\AppData`` IS within ``C:\\Users\\X``."""
        from voice_typer.server import config

        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("C:/Users/X")
        child = Path("C:/Users/X/AppData/Roaming")
        # ``case_sensitive=False`` for the Windows branch.
        assert config._is_path_within(child, root, case_sensitive=False) is True

    def test_case_insensitive_windows(self, monkeypatch):
        """On Windows the comparison is case-insensitive."""
        from voice_typer.server import config

        monkeypatch.setattr(sys, "platform", "win32")
        root = Path("C:/Users/X")
        child = Path("c:/users/x/appdata")
        # ``case_sensitive=False`` exercises the case-
        assert config._is_path_within(child, root, case_sensitive=False) is True


class TestReExportResolvesToOwningModule:
    """The path-safety helpers re-exported by ``voice_typer.server.config``"""

    def test_config_names_are_the_owning_module_objects(self):
        from voice_typer.server import config
        from voice_typer.server.config_internals import paths

        assert config._validate_path_safety is paths._validate_path_safety
        assert config._is_path_within is paths._is_path_within
        assert config._validate_import_path is paths._validate_import_path

    def test_shim_module_is_gone(self):
        """``voice_typer.server.config_path_safety`` must no longer be"""
        import importlib

        with pytest.raises(ModuleNotFoundError):
            importlib.import_module("voice_typer.server.config_path_safety")
