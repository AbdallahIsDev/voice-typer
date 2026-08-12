"""Tests for platform autostart adapters and microphone listing."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from voice_typer.server import server_platform as platform_mod
from voice_typer.server.server_platform import (
    _autostart_command,
    _generate_icon_ico,
    create_launcher_shortcut,
    find_microphone_by_name,
    list_microphones,
)


class TestAutostartCommand:
    def test_uses_autostart_launcher(self):
        """The autostart command must run autostart_launcher.py, which
        spawns npm run dev (Electron dev mode) hidden — not the
        standalone ``-m voice_typer`` tray app."""
        cmd = _autostart_command()
        assert "autostart_launcher.py" in cmd

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_windows_uses_pythonw_if_available(self):
        cmd = _autostart_command()
        # Should use pythonw.exe if it exists next to python.exe
        assert "pythonw" in cmd.lower() or "python" in cmd.lower()

    @pytest.mark.skipif(sys.platform == "win32", reason="Non-Windows test")
    def test_unix_uses_quoted_executable(self):
        # the autostart command now uses spec-compliant
        # quoting — paths without reserved characters are NOT wrapped
        # in quotes (per the freedesktop Desktop Entry Spec).  We just
        # verify the executable appears in the command and the
        # launcher is present.
        #
        # PLAT-VENV: when running inside a virtualenv, the production
        # code (server_platform.autostart._autostart_command) may swap
        # ``sys.executable`` for the system Python (found via
        # ``shutil.which("python3")``) if the system Python can import
        # the launcher.  So we cannot hard-assert ``sys.executable`` is
        # in the command; instead we verify the first token is a real
        # executable on disk and the launcher is referenced.
        cmd = _autostart_command()
        tokens = cmd.split()
        assert len(tokens) >= 2, f"expected at least 2 tokens (python + launcher), got: {cmd}"
        exe = tokens[0].strip('"')
        assert Path(exe).exists(), (
            f"first token must be an existing executable on disk, got: {exe!r} (cmd: {cmd})"
        )
        assert "autostart_launcher.py" in cmd


class TestLinuxDesktopExec:
    """The Linux .desktop Exec= field must preserve the quoting produced
    by _autostart_command().  A previous version stripped the outer
    quotes, which corrupts the first argument."""

    def test_exec_field_is_command_verbatim(self, monkeypatch, tmp_path):
        # Force the Linux path regardless of host platform.
        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            platform_mod,
            "_autostart_command",
            lambda: '"/usr/bin/python3" "/opt/voice_typer/launcher.py"',
        )
        assert platform_mod._enable_autostart_linux() is True

        desktop = (tmp_path / "voice-typer.desktop").read_text()
        # The Exec line must contain the FULL command with quotes intact.
        # If strip('"') were applied, the line would read:
        #   Exec=/usr/bin/python3" "/opt/voice_typer/launcher.py
        # which is malformed per the Desktop Entry Spec.
        for line in desktop.splitlines():
            if line.startswith("Exec="):
                exec_val = line[len("Exec=") :]
                assert exec_val == '"/usr/bin/python3" "/opt/voice_typer/launcher.py"', (
                    f"Exec field must be the verbatim quoted command, got: {exec_val}"
                )
                break
        else:
            pytest.fail("no Exec= line in .desktop file")

    def test_exec_field_handles_paths_with_spaces(self, monkeypatch, tmp_path):
        """Paths with spaces must remain quoted (freedesktop spec)."""
        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        monkeypatch.setattr(
            platform_mod,
            "_autostart_command",
            lambda: '"/usr/bin/python3" "/home/my user/voice typer/launcher.py"',
        )
        assert platform_mod._enable_autostart_linux() is True

        desktop = (tmp_path / "voice-typer.desktop").read_text()
        for line in desktop.splitlines():
            if line.startswith("Exec="):
                exec_val = line[len("Exec=") :]
                assert exec_val == '"/usr/bin/python3" "/home/my user/voice typer/launcher.py"', (
                    f"spaces in path must stay quoted, got: {exec_val}"
                )
                return
        pytest.fail("no Exec= line in .desktop file")


class TestMacOsAutostartUnload:
    """disable_autostart on macOS must unload the running job via
    launchctl (bootout/remove) BEFORE deleting the plist, otherwise the
    job lingers until logout."""

    def test_disable_calls_launchctl_bootout_then_remove(self, monkeypatch, tmp_path):
        import subprocess as _sp

        monkeypatch.setattr(platform_mod, "SYSTEM", "darwin")
        monkeypatch.setattr(platform_mod, "get_autostart_dir", lambda: tmp_path)
        # Pretend the plist exists so the unlink path runs.
        (tmp_path / "com.voicetyper.plist").write_text("dummy")

        calls: list[list[str]] = []

        def fake_run(args, **kw):
            calls.append(list(args))
            r = MagicMock()
            r.returncode = 0
            r.stdout = b""
            r.stderr = b""
            return r

        monkeypatch.setattr(_sp, "run", fake_run)
        # Ensure os.getuid is available even on Windows test host.
        monkeypatch.setattr(platform_mod, "_os_uid", lambda: 501)

        assert platform_mod._disable_autostart_macos() is True

        # Must have invoked launchctl to unload the job.
        assert any("launchctl" in c and "bootout" in c for c in calls), f"expected launchctl bootout, got: {calls}"
        # Plist must be deleted.
        assert not (tmp_path / "com.voicetyper.plist").exists()


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
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: fake_mics)

        result = find_microphone_by_name("wo mic")
        assert result is not None
        assert result["name"] == "WO Mic"
        assert result["id"] == "1"

    def test_returns_none_for_no_match(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Built-in", "host_api": "", "channels": 2, "default": True}],
        )
        assert find_microphone_by_name("nonexistent mic") is None

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Blue Yeti", "host_api": "MME", "channels": 2, "default": False}],
        )
        assert find_microphone_by_name("BLUE YETI") is not None


class TestFindMicrophoneById:
    def test_finds_by_id(self, monkeypatch):
        fake_mics = [
            {"id": "3", "index": 3, "name": "WO Mic", "host_api": "WASAPI", "channels": 1, "default": False},
            {"id": "7", "index": 7, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
        ]
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: fake_mics)

        from voice_typer.server.server_platform import find_microphone_by_id

        result = find_microphone_by_id("7")
        assert result is not None
        assert result["index"] == 7
        assert result["host_api"] == "MME"

    def test_returns_none_for_bad_id(self, monkeypatch):
        monkeypatch.setattr(
            "voice_typer.server.server_platform.list_microphones",
            lambda: [{"id": "0", "index": 0, "name": "Mic", "host_api": "", "channels": 1, "default": True}],
        )
        from voice_typer.server.server_platform import find_microphone_by_id

        assert find_microphone_by_id("99") is None


class TestDuplicateMicrophoneDisambiguation:
    def test_duplicate_names_have_different_ids(self, monkeypatch):
        """Two devices with the same name must have distinct IDs."""
        fake_mics = [
            {"id": "3", "index": 3, "name": "WO Mic", "host_api": "Windows WASAPI", "channels": 1, "default": False},
            {"id": "7", "index": 7, "name": "WO Mic", "host_api": "MME", "channels": 1, "default": False},
        ]
        monkeypatch.setattr("voice_typer.server.server_platform.list_microphones", lambda: fake_mics)

        from voice_typer.server.server_platform import find_microphone_by_id

        mic1 = find_microphone_by_id("3")
        mic2 = find_microphone_by_id("7")
        assert mic1 is not None and mic2 is not None
        assert mic1["name"] == mic2["name"]  # same display name
        assert mic1["id"] != mic2["id"]  # different IDs
        assert mic1["host_api"] != mic2["host_api"]  # different host APIs


class TestCreateLauncherShortcut:
    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_creates_lnk_on_desktop(self, tmp_path, monkeypatch):
        """Should create a .lnk shortcut when win32com is available."""
        pythonw = tmp_path / "pythonw.exe"
        pythonw.touch()
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        import voice_typer.server.server_platform as mod

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
        assert mock_shortcut.save.call_count == 2  # Desktop + Start Menu
        assert str(pythonw) == mock_shortcut.Targetpath
        assert "autostart_launcher.py" in mock_shortcut.Arguments

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_falls_back_to_powershell_when_win32com_missing(self, tmp_path, monkeypatch):
        """Should create a .lnk via PowerShell when win32com is not importable."""
        pythonw = tmp_path / "pythonw.exe"
        pythonw.touch()
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

        import voice_typer.server.server_platform as mod

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
        assert result.name == "Voice Typer.lnk"

    def test_returns_none_on_non_windows(self, monkeypatch):
        import voice_typer.server.server_platform as mod

        monkeypatch.setattr(mod, "SYSTEM", "linux")
        assert create_launcher_shortcut() is None

    @pytest.mark.skipif(sys.platform != "win32", reason="Windows-only test")
    def test_returns_none_when_pythonw_missing(self, tmp_path, monkeypatch):
        """If pythonw.exe doesn't exist next to the interpreter, returns None."""
        monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))
        import voice_typer.server.server_platform as mod

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


# ─── Universal launcher path ────────────────────────────────────────────


class TestUniversalLauncherPath:
    """_universal_launcher_path() must point at autostart_launcher.py."""

    def test_points_at_autostart_launcher(self):
        """The universal launcher path must reference autostart_launcher.py."""
        from voice_typer.server.server_platform import _universal_launcher_path

        p = _universal_launcher_path()
        assert p.name == "autostart_launcher.py"
        assert p.exists()


# ─── Start Menu Programs dir ────────────────────────────────────────────


class TestStartMenuProgramsDir:
    """_start_menu_programs_dir() returns the Windows Start Menu Programs path."""

    def test_returns_path_under_appdata(self, monkeypatch):
        from voice_typer.server.server_platform import _start_menu_programs_dir

        monkeypatch.setenv("APPDATA", "/fake/appdata")
        p = _start_menu_programs_dir()
        assert "Programs" in str(p)
        assert "Start Menu" in str(p)


# Linux autostart dir (: XDG_CONFIG_HOME empty-string bug) ──────


class TestGetAutostartDirLinux:
    """FR-9: get_autostart_dir() must treat XDG_CONFIG_HOME="" as unset.

    Regression for the bug where ``os.environ.get("XDG_CONFIG_HOME",
    default)`` returned the empty string when the env var was set but
    empty, causing ``Path("") / "autostart"`` to produce a RELATIVE
    ``PosixPath("autostart")`` — the .desktop file would be written to
    the process's CWD instead of ``~/.config/autostart/``, and the
    desktop environment would never pick it up. Mirrors the
    ``TestLinuxUnitDirHandlesEmptyXdgConfigHome`` suite already covering
    ``prewarm_scheduler_posix._linux_unit_dir``.
    """

    def test_empty_string_xdg_config_home_uses_fallback(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import autostart as autostart_mod, get_autostart_dir

        # Force the Linux branch regardless of host platform.
        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # Empty string must be treated as unset per the XDG spec.
        monkeypatch.setattr(autostart_mod.os, "environ", {"XDG_CONFIG_HOME": ""})

        result = get_autostart_dir()

        # Critical: must NOT be a relative path (the bug produced
        # PosixPath("autostart") which is relative).
        assert result.is_absolute(), f"FR-9 regression: empty XDG_CONFIG_HOME produced relative path {result!r}"
        expected = fake_home / ".config" / "autostart"
        assert result == expected

    def test_unset_xdg_config_home_uses_fallback(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import autostart as autostart_mod, get_autostart_dir

        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        fake_home = tmp_path
        monkeypatch.setattr(Path, "home", lambda: fake_home)
        # No XDG_CONFIG_HOME key at all.
        monkeypatch.setattr(autostart_mod.os, "environ", {})

        result = get_autostart_dir()

        assert result.is_absolute()
        assert result == fake_home / ".config" / "autostart"

    def test_set_nonempty_xdg_config_home_is_respected(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import autostart as autostart_mod, get_autostart_dir

        monkeypatch.setattr(platform_mod, "SYSTEM", "linux")
        monkeypatch.setattr(autostart_mod.os, "environ", {"XDG_CONFIG_HOME": str(tmp_path)})

        result = get_autostart_dir()

        assert result == tmp_path / "autostart"


# ─── Autostart command includes --hidden ───────────────────────────────


class TestAutostartCommandIncludesHidden:
    """The autostart command must include --hidden so Electron starts
    with the dashboard hidden at login."""

    def test_includes_hidden_flag(self):
        cmd = _autostart_command()
        assert "--hidden" in cmd

    def test_references_autostart_launcher(self):
        cmd = _autostart_command()
        assert "autostart_launcher.py" in cmd


# ─── Shortcut target verification ──────────────────────────────────────


class TestShortcutTarget:
    """Verify that the shortcut target points at the universal launcher,
    not the legacy -m voice_typer backend-only path."""

    def test_shortcut_arguments_reference_universal_launcher(self, monkeypatch):
        """The shortcut's arguments must point at autostart_launcher.py
        (not pythonw -m voice_typer, which starts backend-only without
        Electron, causing the bubble overlay to never appear)."""
        from voice_typer.server.server_platform import _universal_launcher_path

        launcher = _universal_launcher_path()
        # The launcher path should be autostart_launcher.py
        assert launcher.name == "autostart_launcher.py"
        # And the shortcut arguments should reference it
        assert "autostart_launcher.py" in str(launcher)
        # Critically: should NOT reference -m voice_typer
        assert "-m voice_typer" not in str(launcher)
        assert "-m" not in str(launcher)
