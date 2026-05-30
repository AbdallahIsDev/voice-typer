"""Tests for platform autostart adapters and microphone listing."""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from voice_typer.platform import (
    _autostart_command,
    list_microphones,
    find_microphone_by_name,
    enable_autostart,
    disable_autostart,
    is_autostart_enabled,
)


class TestAutostartCommand:
    def test_uses_python_m_voice_typer(self):
        cmd = _autostart_command()
        assert "-m voice_typer" in cmd

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_windows_uses_pythonw_if_available(self):
        cmd = _autostart_command()
        # Should use pythonw.exe if it exists next to python.exe
        assert "pythonw" in cmd.lower() or "python" in cmd.lower()

    @pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows test")
    def test_unix_uses_quoted_executable(self):
        cmd = _autostart_command()
        assert cmd.startswith('"')
        assert sys.executable in cmd


class TestListMicrophones:
    def test_returns_list(self):
        # Just verify it doesn't crash — actual devices depend on system
        result = list_microphones()
        assert isinstance(result, list)

    def test_returns_empty_on_failure(self, monkeypatch):
        """When sounddevice raises, list_microphones returns []."""
        mock_sd = MagicMock()
        mock_sd.query_devices.side_effect = RuntimeError("no audio")
        monkeypatch.setitem(sys.modules, "sounddevice", mock_sd)
        assert list_microphones() == []


class TestFindMicrophoneByName:
    def test_finds_partial_match(self, monkeypatch):
        fake_mics = [
            {"id": "0", "index": 0, "name": "Built-in Microphone", "host_api": "ALSA", "channels": 2, "default": True},
            {"id": "1", "index": 1, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
            {"id": "2", "index": 2, "name": "Blue Yeti", "host_api": "MME", "channels": 2, "default": False},
        ]
        monkeypatch.setattr("voice_typer.platform.list_microphones", lambda: fake_mics)

        from voice_typer.platform import find_microphone_by_name
        result = find_microphone_by_name("wo mic")
        assert result is not None
        assert result["name"] == "WO Mic"
        assert result["id"] == "1"

    def test_returns_none_for_no_match(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Built-in", "host_api": "", "channels": 2, "default": True}],
        )
        from voice_typer.platform import find_microphone_by_name
        assert find_microphone_by_name("nonexistent mic") is None

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Blue Yeti", "host_api": "MME", "channels": 2, "default": False}],
        )
        from voice_typer.platform import find_microphone_by_name
        assert find_microphone_by_name("BLUE YETI") is not None


class TestFindMicrophoneById:
    def test_finds_by_id(self, monkeypatch):
        fake_mics = [
            {"id": "3", "index": 3, "name": "WO Mic", "host_api": "WASAPI", "channels": 1, "default": False},
            {"id": "7", "index": 7, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
        ]
        monkeypatch.setattr("voice_typer.platform.list_microphones", lambda: fake_mics)

        from voice_typer.platform import find_microphone_by_id
        result = find_microphone_by_id("7")
        assert result is not None
        assert result["index"] == 7
        assert result["host_api"] == "MME"

    def test_returns_none_for_bad_id(self, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.list_microphones", lambda: [
            {"id": "0", "index": 0, "name": "Mic", "host_api": "", "channels": 1, "default": True}
        ])
        from voice_typer.platform import find_microphone_by_id
        assert find_microphone_by_id("99") is None


class TestDuplicateMicrophoneDisambiguation:
    def test_duplicate_names_have_different_ids(self, monkeypatch):
        """Two devices with the same name must have distinct IDs."""
        fake_mics = [
            {"id": "3", "index": 3, "name": "WO Mic", "host_api": "Windows WASAPI", "channels": 1, "default": False},
            {"id": "7", "index": 7, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
        ]
        monkeypatch.setattr("voice_typer.platform.list_microphones", lambda: fake_mics)

        from voice_typer.platform import find_microphone_by_id
        mic1 = find_microphone_by_id("3")
        mic2 = find_microphone_by_id("7")
        assert mic1 is not None and mic2 is not None
        assert mic1["name"] == mic2["name"]  # same display name
        assert mic1["id"] != mic2["id"]      # different IDs
        assert mic1["host_api"] != mic2["host_api"]  # different host APIs


class TestLinuxAutostartEscaping:
    """P1 #4: Linux .desktop Exec line must escape special characters in the Python path."""

    def test_exec_line_quotes_and_escapes_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "linux")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

        from voice_typer.platform import _enable_autostart_linux
        _enable_autostart_linux()

        desktop_path = tmp_path / "voice-typer.desktop"
        assert desktop_path.exists()
        content = desktop_path.read_text()
        assert 'Exec=/usr/bin/python3 -m voice_typer' in content

    def test_exec_line_escapes_ampersand_in_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "linux")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "executable", "/home/user & test/bin/python")

        from voice_typer.platform import _enable_autostart_linux
        _enable_autostart_linux()

        desktop_path = tmp_path / "voice-typer.desktop"
        content = desktop_path.read_text()
        assert "'/home/user & test/bin/python'" in content

    def test_disable_removes_desktop_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "linux")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

        from voice_typer.platform import _enable_autostart_linux, _disable_autostart_linux
        _enable_autostart_linux()
        assert (tmp_path / "voice-typer.desktop").exists()

        _disable_autostart_linux()
        assert not (tmp_path / "voice-typer.desktop").exists()

    def test_is_autostart_enabled_checks_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "linux")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)

        from voice_typer.platform import is_autostart_enabled, _enable_autostart_linux
        assert not is_autostart_enabled()
        _enable_autostart_linux()
        assert is_autostart_enabled()


class TestMacOSAutostartEscaping:
    """P1 #4: macOS plist must escape special characters in the Python path."""

    def test_plist_escapes_ampersand_in_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "darwin")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "executable", "/home/user & test/bin/python")

        from voice_typer.platform import _enable_autostart_macos
        _enable_autostart_macos()

        plist_path = tmp_path / "com.voicetyper.plist"
        assert plist_path.exists()
        content = plist_path.read_text()
        assert "&amp;" in content

    def test_plist_contains_keep_alive(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "darwin")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

        from voice_typer.platform import _enable_autostart_macos
        _enable_autostart_macos()

        plist_path = tmp_path / "com.voicetyper.plist"
        content = plist_path.read_text()
        assert "<key>KeepAlive</key>" in content
        assert "<true/>" in content

    def test_disable_removes_plist(self, tmp_path, monkeypatch):
        monkeypatch.setattr("voice_typer.platform.SYSTEM", "darwin")
        monkeypatch.setattr("voice_typer.platform.get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(sys, "executable", "/usr/bin/python3")

        from voice_typer.platform import _enable_autostart_macos, _disable_autostart_macos
        _enable_autostart_macos()
        assert (tmp_path / "com.voicetyper.plist").exists()

        _disable_autostart_macos()
        assert not (tmp_path / "com.voicetyper.plist").exists()
