"""Tests for platform_utils module.

CQ-029: Verify that centralized platform detection functions work correctly.
"""

from __future__ import annotations

from voice_typer.server.platform_utils import is_linux, is_macos, is_windows, platform_name


class TestPlatformDetection:
    """Test centralized platform detection functions."""

    def test_exactly_one_platform_is_true(self):
        """Exactly one platform function should return True."""
        results = [is_windows(), is_macos(), is_linux()]
        assert sum(results) == 1, f"Expected exactly 1 True, got {results}"

    def test_windows_detection(self, monkeypatch):
        """is_windows() should return True when sys.platform is 'win32'."""
        monkeypatch.setattr("voice_typer.server.platform_utils.sys.platform", "win32")
        assert is_windows() is True
        assert is_macos() is False
        assert is_linux() is False

    def test_macos_detection(self, monkeypatch):
        """is_macos() should return True when sys.platform is 'darwin'."""
        monkeypatch.setattr("voice_typer.server.platform_utils.sys.platform", "darwin")
        assert is_windows() is False
        assert is_macos() is True
        assert is_linux() is False

    def test_linux_detection(self, monkeypatch):
        """is_linux() should return True when sys.platform starts with 'linux'."""
        monkeypatch.setattr("voice_typer.server.platform_utils.sys.platform", "linux")
        assert is_windows() is False
        assert is_macos() is False
        assert is_linux() is True

    def test_linux_variant_detection(self, monkeypatch):
        """is_linux() should return True for linux2 (Python 3.x compat)."""
        monkeypatch.setattr("voice_typer.server.platform_utils.sys.platform", "linux2")
        assert is_linux() is True

    def test_platform_name_returns_string(self):
        """platform_name() should return a non-empty string."""
        name = platform_name()
        assert isinstance(name, str)
        assert len(name) > 0

    def test_platform_name_matches_detection(self):
        """platform_name() should match the detected platform."""
        name = platform_name()
        if is_windows():
            assert name == "windows"
        elif is_macos():
            assert name == "macos"
        elif is_linux():
            assert name == "linux"
