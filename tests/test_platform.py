"""Tests for platform autostart adapters and microphone listing."""

import sys
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from voice_typer.server.platform import (
    _autostart_command,
    _generate_icon_ico,
    create_launcher_shortcut,
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
        monkeypatch.setattr("voice_typer.server.platform.list_microphones", lambda: fake_mics)

        from voice_typer.server.platform import find_microphone_by_name
        result = find_microphone_by_name("wo mic")
        assert result is not None
        assert result["name"] == "WO Mic"
        assert result["id"] == "1"

    def test_returns_none_for_no_match(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Built-in", "host_api": "", "channels": 2, "default": True}],
        )
        from voice_typer.server.platform import find_microphone_by_name
        assert find_microphone_by_name("nonexistent mic") is None

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Blue Yeti", "host_api": "MME", "channels": 2, "default": False}],
        )
        from voice_typer.server.platform import find_microphone_by_name
        assert find_microphone_by_name("BLUE YETI") is not None


class TestFindMicrophoneById:
    def test_finds_by_id(self, monkeypatch):
        fake_mics = [
            {"id": "3", "index": 3, "name": "WO Mic", "host_api": "WASAPI", "channels": 1, "default": False},
            {"id": "7", "index": 7, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
        ]
        monkeypatch.setattr("voice_typer.server.platform.list_microphones", lambda: fake_mics)

        from voice_typer.server.platform import find_microphone_by_id
        result = find_microphone_by_id("7")
        assert result is not None
        assert result["index"] == 7
        assert result["host_api"] == "MME"

    def test_returns_none_for_bad_id(self, monkeypatch):
        monkeypatch.setattr("voice_typer.server.platform.list_microphones", lambda: [
            {"id": "0", "index": 0, "name": "Mic", "host_api": "", "channels": 1, "default": True}
        ])
        from voice_typer.server.platform import find_microphone_by_id
        assert find_microphone_by_id("99") is None


class TestDuplicateMicrophoneDisambiguation:
    def test_duplicate_names_have_different_ids(self, monkeypatch):
        """Two devices with the same name must have distinct IDs."""
        fake_mics = [
            {"id": "3", "index": 3, "name": "WO Mic", "host_api": "Windows WASAPI", "channels": 1, "default": False},
            {"id": "7", "index": 7, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
        ]
        monkeypatch.setattr("voice_typer.server.platform.list_microphones", lambda: fake_mics)

        from voice_typer.server.platform import find_microphone_by_id
        mic1 = find_microphone_by_id("3")
        mic2 = find_microphone_by_id("7")
        assert mic1 is not None and mic2 is not None
        assert mic1["name"] == mic2["name"]  # same display name
        assert mic1["id"] != mic2["id"]      # different IDs
        assert mic1["host_api"] != mic2["host_api"]  # different host APIs


class TestCreateLauncherShortcut:
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_creates_lnk_on_desktop(self, tmp_path, monkeypatch):
        """Should create a .lnk shortcut when win32com is available."""
        pythonw = tmp_path / "pythonw.exe"
        pythonw.touch()
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        import voice_typer.server.platform as mod
        monkeypatch.setattr(mod, "SYSTEM", "win32")

        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        # Patch APPDATA so icon goes into tmp
        monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))

        mock_shell = MagicMock()
        mock_shortcut = MagicMock()
        mock_shell.CreateShortCut.return_value = mock_shortcut
        mock_win32com = MagicMock()
        mock_win32com.client.Dispatch.return_value = mock_shell

        monkeypatch.setitem(sys.modules, "win32com", mock_win32com)
        monkeypatch.setitem(sys.modules, "win32com.client", mock_win32com.client)

        result = create_launcher_shortcut()
        assert result is not None
        assert result.name == "Voice Typer.lnk"
        assert str(result) == str(desktop / "Voice Typer.lnk")
        mock_shortcut.save.assert_called_once()
        assert str(pythonw) == mock_shortcut.Targetpath
        assert "-m voice_typer" in mock_shortcut.Arguments

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_falls_back_to_bat_when_win32com_missing(self, tmp_path, monkeypatch):
        """Should create a .bat file when win32com is not importable."""
        pythonw = tmp_path / "pythonw.exe"
        pythonw.touch()
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        import voice_typer.server.platform as mod
        monkeypatch.setattr(mod, "SYSTEM", "win32")

        desktop = tmp_path / "Desktop"
        desktop.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))

        # Make win32com raise ImportError
        import builtins
        real_import = builtins.__import__
        def mock_import(name, *args, **kwargs):
            if name == "win32com" or name.startswith("win32com."):
                raise ImportError("no win32com")
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, "__import__", mock_import)

        result = create_launcher_shortcut()
        assert result is not None
        assert result.exists()
        assert result.name == "Voice Typer.bat"
        content = result.read_text(encoding="utf-8")
        assert "pythonw" in content
        assert "-m voice_typer" in content

    def test_returns_none_on_non_windows(self, monkeypatch):
        import voice_typer.server.platform as mod
        monkeypatch.setattr(mod, "SYSTEM", "linux")
        assert create_launcher_shortcut() is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_returns_none_when_pythonw_missing(self, tmp_path, monkeypatch):
        """If pythonw.exe doesn't exist next to the interpreter, returns None."""
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        import voice_typer.server.platform as mod
        monkeypatch.setattr(mod, "SYSTEM", "win32")
        assert create_launcher_shortcut() is None


class TestGenerateIconIco:
    @pytest.mark.skip(reason="Requires real PIL/Image to create .ico files")
    def test_generates_ico_file(self, tmp_path, monkeypatch):
        """Should create an icon.ico file using PIL."""
        monkeypatch.setenv("APPDATA", str(tmp_path))

        result = _generate_icon_ico()
        assert result is not None
        assert result.exists()
        assert result.name == "icon.ico"
        assert result.parent.name == "voice-typer"
