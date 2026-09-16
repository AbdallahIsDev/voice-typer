"""Windows console-flash regression tests for hidden child spawns.

Every ``subprocess.run`` of a console-subsystem binary on Windows must
pass ``creationflags=CREATE_NO_WINDOW`` (``0x08000000``), otherwise a
conhost window pops open and closes in under a second. The flag value
is single-sourced via
``voice_typer.server.server_platform.autostart._windows_create_no_window_flags``.

Sites pinned here (all previously flashed):

- config ACL hardening (``icacls`` on every first ``Config.save`` of
  the process; later saves hit the verified-dir fast path and skip
  the subprocess, hence first-settings-change-only flashing),
- Task Scheduler wrapper (``schtasks`` Query at startup logon sync +
  Create/Delete on autostart toggle),
- Electron shutdown kill (``taskkill`` in ``terminate_electron``),
- desktop-shortcut PowerShell fallback + AppUserModelID stamp
  (``powershell`` at startup shortcut ensure).

Each site is asserted Windows-only: on POSIX no ``creationflags``
kwarg is passed at all (the kwarg is invalid there).
"""

from __future__ import annotations

import os
import signal
import subprocess
from unittest.mock import MagicMock

CREATE_NO_WINDOW = 0x08000000


def _fake_completed(**attrs):
    completed = MagicMock()
    completed.returncode = 0
    completed.stdout = ""
    completed.stderr = ""
    for key, value in attrs.items():
        setattr(completed, key, value)
    return completed


class TestConfigIcaclsHidden:
    def test_icacls_passes_create_no_window_on_windows(self, monkeypatch, tmp_path):
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: True)
        monkeypatch.setenv("USERNAME", "testuser")
        monkeypatch.delenv("USER", raising=False)

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed(stdout="Successfully processed 1 files")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        target = tmp_path / "config.json"
        target.write_text("{}")
        assert config_mod._enforce_windows_owner_only_acl(target) is True

        assert captured["cmd"][0] == "icacls"
        assert captured["kwargs"].get("creationflags") == CREATE_NO_WINDOW

    def test_icacls_never_runs_on_posix(self, monkeypatch, tmp_path):
        from voice_typer.server import config as config_mod

        monkeypatch.setattr(config_mod, "is_windows", lambda: False)

        calls = {"n": 0}

        def _fake_run(*args, **kwargs):
            calls["n"] += 1
            return _fake_completed()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        target = tmp_path / "config.json"
        target.write_text("{}")
        assert config_mod._enforce_windows_owner_only_acl(target) is True
        assert calls["n"] == 0


class TestSchtasksHidden:
    def test_schtasks_passes_create_no_window_on_windows(self, monkeypatch):
        from voice_typer.server import task_scheduler

        monkeypatch.setattr(task_scheduler, "is_windows", lambda: True)

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed(stdout="SUCCESS", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        rc, _ = task_scheduler._schtasks(["/Query", "/TN", "dummy"])
        assert rc == 0
        assert captured["cmd"][0] == "schtasks"
        assert captured["kwargs"].get("creationflags") == CREATE_NO_WINDOW
        assert captured["kwargs"]["capture_output"] is True
        assert captured["kwargs"]["text"] is True
        assert captured["kwargs"]["timeout"] == 30

    def test_schtasks_omits_creationflags_on_posix(self, monkeypatch):
        from voice_typer.server import task_scheduler

        monkeypatch.setattr(task_scheduler, "is_windows", lambda: False)

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed(stdout="SUCCESS", stderr="")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        rc, _ = task_scheduler._schtasks(["/Query", "/TN", "dummy"])
        assert rc == 0
        assert "creationflags" not in captured["kwargs"]


class TestTaskkillHidden:
    def test_terminate_electron_passes_create_no_window_on_windows(self, monkeypatch):
        from voice_typer.server import electron_launcher

        monkeypatch.setattr(electron_launcher, "is_windows", lambda: True)

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        electron_launcher.terminate_electron(1234)

        assert captured["cmd"][:3] == ["taskkill", "/T", "/F"]
        assert captured["kwargs"].get("creationflags") == CREATE_NO_WINDOW

    def test_terminate_electron_swallows_taskkill_failure(self, monkeypatch):
        """A non-zero taskkill exit (unknown PID, access denied) is
        best-effort: logged at debug, never raised."""
        from voice_typer.server import electron_launcher

        monkeypatch.setattr(electron_launcher, "is_windows", lambda: True)

        def _fake_run(cmd, **kwargs):
            return _fake_completed(returncode=1, stderr=b"ERROR: The process 1234 not found.")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Must not raise.
        electron_launcher.terminate_electron(1234)

    def test_terminate_electron_swallows_missing_taskkill_binary(self, monkeypatch):
        """A missing taskkill.exe (FileNotFoundError) is best-effort too."""
        from voice_typer.server import electron_launcher

        monkeypatch.setattr(electron_launcher, "is_windows", lambda: True)

        def _fake_run(cmd, **kwargs):
            raise FileNotFoundError("taskkill not found")

        monkeypatch.setattr(subprocess, "run", _fake_run)

        # Must not raise.
        electron_launcher.terminate_electron(1234)

    def test_taskkill_timeout_falls_back_to_sigterm(self, monkeypatch):
        """When taskkill hangs, the launcher warns and falls back to a
        direct SIGTERM so the shutdown path still makes a best-effort kill."""
        from voice_typer.server import electron_launcher

        monkeypatch.setattr(electron_launcher, "is_windows", lambda: True)

        def _fake_run(cmd, **kwargs):
            raise subprocess.TimeoutExpired(cmd, 5)

        monkeypatch.setattr(subprocess, "run", _fake_run)

        kill_calls: list = []

        def _fake_kill(pid, sig):
            kill_calls.append((pid, sig))

        monkeypatch.setattr(os, "kill", _fake_kill)

        electron_launcher.terminate_electron(1234)

        assert (1234, signal.SIGTERM) in kill_calls


class TestDesktopShortcutPowershellHidden:
    def _patch_windows(self, monkeypatch):
        from voice_typer.server.server_platform import platform_flags

        monkeypatch.setattr(platform_flags, "SYSTEM", "win32")

    def test_lnk_powershell_fallback_passes_create_no_window(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import desktop_shortcut

        self._patch_windows(monkeypatch)
        # NOTE: do NOT patch ``win32com.client.Dispatch`` here. The
        # ``__import__`` hook below is what forces the PowerShell fallback
        # (it raises ImportError for ``win32com.client`` regardless of
        # whether pywin32 is installed), and patching the real module would
        # make this test REQUIRE pywin32 to be installed: ``monkeypatch.setattr``
        # resolves the module at patch time, and ``raising=False`` only
        # tolerates a missing attribute, not a missing module
        # (ModuleNotFoundError: No module named 'win32com').

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed()

        monkeypatch.setattr(subprocess, "run", _fake_run)
        # Force the win32com path to fail so the PowerShell fallback runs.
        import builtins

        real_import = builtins.__import__

        def _import_hook(name, *args, **kwargs):
            if name == "win32com.client":
                raise ImportError("no win32com in tests")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _import_hook)

        lnk = tmp_path / "app.lnk"
        ok = desktop_shortcut._create_lnk_shortcut(
            lnk_path=lnk,
            target=r"C:\app\voice-typer-tauri.exe",
            arguments="",
            icon_ico=None,
            description="test",
        )
        assert ok is True
        assert captured["cmd"][0] == "powershell"
        assert captured["kwargs"].get("creationflags") == CREATE_NO_WINDOW
        assert captured["kwargs"]["check"] is True

    def test_lnk_powershell_fallback_omits_creationflags_on_posix(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import desktop_shortcut, platform_flags

        monkeypatch.setattr(platform_flags, "SYSTEM", "linux")

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        import builtins

        real_import = builtins.__import__

        def _import_hook(name, *args, **kwargs):
            if name == "win32com.client":
                raise ImportError("no win32com in tests")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _import_hook)

        lnk = tmp_path / "app.lnk"
        ok = desktop_shortcut._create_lnk_shortcut(
            lnk_path=lnk,
            target=r"C:\app\voice-typer-tauri.exe",
            arguments="",
            icon_ico=None,
            description="test",
        )
        assert ok is True
        assert "creationflags" not in captured["kwargs"]

    def test_aumid_stamp_passes_create_no_window_on_windows(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import desktop_shortcut, platform_flags

        monkeypatch.setattr(platform_flags, "SYSTEM", "win32")
        lnk = tmp_path / "app.lnk"
        lnk.write_bytes(b"fake-lnk-bytes")

        captured: dict = {}

        def _fake_run(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return _fake_completed()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        assert desktop_shortcut._set_lnk_app_user_model_id(lnk) is True
        assert captured["cmd"][0] == "powershell"
        assert captured["kwargs"].get("creationflags") == CREATE_NO_WINDOW
        assert captured["kwargs"]["check"] is True

    def test_aumid_stamp_never_spawns_on_posix(self, monkeypatch, tmp_path):
        from voice_typer.server.server_platform import desktop_shortcut, platform_flags

        monkeypatch.setattr(platform_flags, "SYSTEM", "linux")
        lnk = tmp_path / "app.lnk"
        lnk.write_bytes(b"fake-lnk-bytes")

        calls = {"n": 0}

        def _fake_run(*args, **kwargs):
            calls["n"] += 1
            return _fake_completed()

        monkeypatch.setattr(subprocess, "run", _fake_run)

        assert desktop_shortcut._set_lnk_app_user_model_id(lnk) is False
        assert calls["n"] == 0
