"""autostart cleanup."""

from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TAURI_CONF_JSON = REPO_ROOT / "src-tauri" / "tauri.conf.json"
UNINSTALL_PERMISSIONS_PY = REPO_ROOT / "scripts" / "windows" / "uninstall_permissions.py"
UNINSTALL_BAT = REPO_ROOT / "scripts" / "windows" / "uninstall.bat"
UNINSTALLER_NSH = REPO_ROOT / "scripts" / "windows" / "uninstaller.nsh"

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly."""
    fake = types.ModuleType("winreg")
    fake.HKEY_CURRENT_USER = 0x80000001
    fake.KEY_SET_VALUE = 0x0002
    fake.KEY_READ = 0x20019
    fake.KEY_ALL_ACCESS = 0xF003F
    fake.REG_SZ = 1
    fake.OpenKey = MagicMock(return_value=MagicMock())
    fake.SetValueEx = MagicMock()
    fake.QueryValueEx = MagicMock(return_value=("cmd", 1))
    fake.DeleteValue = MagicMock()
    fake.CloseKey = MagicMock()
    # Default: no Run-key values to enumerate.
    fake.EnumValue = MagicMock(side_effect=OSError("no more values"))
    monkeypatch.setitem(sys.modules, "winreg", fake)
    return fake


@pytest.fixture
def win32_platform(monkeypatch, fake_winreg):
    """Pretend we're on Windows for the duration of the test."""
    monkeypatch.setattr(sys, "platform", "win32")
    from voice_typer.server import server_platform
    from voice_typer.server.server_platform import platform_flags

    monkeypatch.setattr(platform_flags, "SYSTEM", "win32")
    return server_platform


def _enum_value_side_effect(entries: list[tuple[str, str, int]]):
    """Build a side_effect for ``winreg.EnumValue`` that yields each entry"""
    iterator = iter(entries)

    def _side_effect(_key, _index):
        try:
            return next(iterator)
        except StopIteration:
            raise OSError("no more values") from None

    return _side_effect


class TestUnregisterAllLausuRunkeys:
    """the uninstaller helper removes ALL Lausu_* entries"""

    def test_empty_run_key_no_deletions(self, fake_winreg, win32_platform):
        """An empty Run key (no Lausu entries) results in no"""
        from voice_typer.server.server_platform import autostart_windows

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([])

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        assert deleted == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_single_lausu_entry_deleted(self, fake_winreg, win32_platform):
        """A single Lausu_<hash> entry is deleted and its name"""
        from voice_typer.server.server_platform import autostart_windows

        name = "Lausu_a1b2c3d4"
        value = r'"C:\Program Files\Lausu\app.exe" --delay 15'
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(name, value, fake_winreg.REG_SZ)])

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        assert deleted == [name]
        fake_winreg.DeleteValue.assert_called_once()
        # DeleteValue(key, name), name is the second positional arg.
        assert fake_winreg.DeleteValue.call_args.args[1] == name

    def test_multiple_lausu_entries_all_deleted(self, fake_winreg, win32_platform):
        """Multiple Lausu entries (different hashes from previous"""
        from voice_typer.server.server_platform import autostart_windows

        entries = [
            ("Lausu_aaaaaaaa", r'"C:\path1\app.exe"', fake_winreg.REG_SZ),
            ("Lausu_bbbbbbbb", r'"C:\path2\app.exe"', fake_winreg.REG_SZ),
            ("Lausu_cccccccc", r'"C:\path3\app.exe"', fake_winreg.REG_SZ),
        ]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        # All three must be deleted (order preserved from enumeration).
        assert sorted(deleted) == sorted(["Lausu_aaaaaaaa", "Lausu_bbbbbbbb", "Lausu_cccccccc"])
        assert fake_winreg.DeleteValue.call_count == 3

    def test_mixed_entries_only_lausu_deleted(self, fake_winreg, win32_platform):
        """When the Run key contains BOTH Lausu entries AND non-"""
        from voice_typer.server.server_platform import autostart_windows

        entries = [
            ("OneDrive", r'"C:\Program Files\OneDrive\OneDrive.exe" /background', fake_winreg.REG_SZ),
            ("Lausu_aaaaaaaa", r'"C:\Lausu\app.exe"', fake_winreg.REG_SZ),
            ("Discord", r'"C:\AppData\Discord\Discord.exe"', fake_winreg.REG_SZ),
            ("Lausu_bbbbbbbb", r'"C:\LausuOld\app.exe"', fake_winreg.REG_SZ),
        ]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        # Only the Lausu_* entries should be in the deleted list.
        assert sorted(deleted) == ["Lausu_aaaaaaaa", "Lausu_bbbbbbbb"]
        # Exactly two DeleteValue calls (the two Lausu entries).
        assert fake_winreg.DeleteValue.call_count == 2
        deleted_names = {call.args[1] for call in fake_winreg.DeleteValue.call_args_list}
        assert deleted_names == {"Lausu_aaaaaaaa", "Lausu_bbbbbbbb"}

    def test_no_winreg_returns_empty_list(self, monkeypatch):
        """empty list, no exception raised. This is the production contract:"""
        import builtins

        from voice_typer.server.server_platform import autostart_windows

        real_import = builtins.__import__

        def _fake_import(name, *args, **kwargs):
            if name == "winreg":
                raise ImportError("winreg is Windows-only")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fake_import)

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        assert deleted == []

    def test_openkey_oserror_returns_empty_list(self, fake_winreg, win32_platform):
        """If OpenKey raises OSError (e.g. the Run key doesn't exist —"""
        from voice_typer.server.server_platform import autostart_windows

        fake_winreg.OpenKey.side_effect = OSError("key not found")

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        assert deleted == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_deletevalue_oserror_skips_entry_continues_sweep(self, fake_winreg, win32_platform):
        """
        If DeleteValue raises OSError on one entry (e.g. transient
        CONTINUES, the uninstaller must not abort on a single failure.
        """
        from voice_typer.server.server_platform import autostart_windows

        entries = [
            ("Lausu_aaaaaaaa", r'"C:\path1\app.exe"', fake_winreg.REG_SZ),
            ("Lausu_bbbbbbbb", r'"C:\path2\app.exe"', fake_winreg.REG_SZ),
            ("Lausu_cccccccc", r'"C:\path3\app.exe"', fake_winreg.REG_SZ),
        ]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)
        # Make DeleteValue fail ONLY for the second entry (by call count).
        call_count = {"n": 0}

        def _delete_side_effect(_key, _name):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("transient registry error")

        fake_winreg.DeleteValue.side_effect = _delete_side_effect

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        # The second entry should be MISSING from the deleted list
        assert "Lausu_aaaaaaaa" in deleted
        assert "Lausu_bbbbbbbb" not in deleted
        assert "Lausu_cccccccc" in deleted
        # All three DeleteValue calls were attempted.
        assert fake_winreg.DeleteValue.call_count == 3

    def test_loop_terminates_on_enum_oserror(self, fake_winreg, win32_platform):
        """The enumeration loop terminates cleanly when EnumValue raises"""
        from voice_typer.server.server_platform import autostart_windows

        # Simulate: one entry, then OSError to end enumeration.
        entries = [("Lausu_aaaaaaaa", r'"C:\path\app.exe"', fake_winreg.REG_SZ)]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)

        deleted = autostart_windows._unregister_all_lausu_runkeys()
        assert deleted == ["Lausu_aaaaaaaa"]
        # Verify the loop actually terminated (didn't infinite-loop) —
        assert fake_winreg.EnumValue.call_count <= 2

    def test_closekey_called_in_finally(self, fake_winreg, win32_platform):
        """CloseKey is called even if the sweep raises, the function"""
        from voice_typer.server.server_platform import autostart_windows

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([])

        autostart_windows._unregister_all_lausu_runkeys()
        fake_winreg.CloseKey.assert_called_once()


class TestUnregisterAllLausuTasks:
    """the Task Scheduler sweep uses PowerShell because"""

    def test_no_task_scheduler_returns_empty_list(self, monkeypatch):
        """When task_scheduler.is_supported() returns False (non-Windows"""
        import voice_typer.server as server_pkg
        from voice_typer.server.server_platform import autostart_windows

        # Mock task_scheduler.is_supported to return False.
        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: False
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)
        # ALSO pin the package attribute: `from voice_typer.server import
        monkeypatch.setattr(server_pkg, "task_scheduler", fake_task_scheduler, raising=False)

        deleted = autostart_windows._unregister_all_lausu_tasks()
        assert deleted == []

    def test_powershell_success_returns_task_names(self, monkeypatch, fake_winreg, win32_platform):
        """When PowerShell succeeds and outputs task names, those names"""
        import voice_typer.server as server_pkg
        from voice_typer.server.server_platform import autostart_windows

        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)
        # ALSO pin the package attribute (see
        monkeypatch.setattr(server_pkg, "task_scheduler", fake_task_scheduler, raising=False)

        # Mock subprocess.run to simulate PowerShell success.
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = (
            "LausuAutostart_aaaaaaaa\nLausuAutostart_bbbbbbbb\ncom.Lausu.prewarm\ncom.Lausu.autostart_cccccccc\n"
        )
        fake_result.stderr = ""
        monkeypatch.setattr(
            "subprocess.run",
            MagicMock(return_value=fake_result),
        )

        deleted = autostart_windows._unregister_all_lausu_tasks()
        assert sorted(deleted) == [
            "LausuAutostart_aaaaaaaa",
            "LausuAutostart_bbbbbbbb",
            "com.Lausu.autostart_cccccccc",
            "com.Lausu.prewarm",
        ]

    def test_powershell_failure_returns_empty_list(self, monkeypatch, fake_winreg, win32_platform):
        """When PowerShell exits non-zero, the function returns an empty"""
        from voice_typer.server.server_platform import autostart_windows

        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)

        fake_result = MagicMock()
        fake_result.returncode = 1
        fake_result.stdout = ""
        fake_result.stderr = "PowerShell error"
        monkeypatch.setattr(
            "subprocess.run",
            MagicMock(return_value=fake_result),
        )

        deleted = autostart_windows._unregister_all_lausu_tasks()
        assert deleted == []

    def test_subprocess_oserror_returns_empty_list(self, monkeypatch, fake_winreg, win32_platform):
        """PATH on a non-Windows host with task_scheduler.is_supported()"""
        from voice_typer.server.server_platform import autostart_windows

        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)

        def _raise(*_a, **_kw):
            raise OSError("powershell.exe not found")

        monkeypatch.setattr("subprocess.run", _raise)

        deleted = autostart_windows._unregister_all_lausu_tasks()
        assert deleted == []


class TestUninstallPermissionsScript:
    """the uninstall_permissions.py script wires the production"""

    def test_script_main_returns_zero_with_empty_registry(self, monkeypatch):
        """main() returns 0 when there are no entries to remove (already"""
        # Force the voice_typer package path to succeed with empty lists.
        from voice_typer.server.server_platform import autostart_windows

        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_lausu_runkeys",
            lambda: [],
        )
        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_lausu_tasks",
            lambda: [],
        )

        # Load the script as a module (so we can call main() directly
        import importlib.util

        spec = importlib.util.spec_from_file_location("uninstall_permissions_windows", UNINSTALL_PERMISSIONS_PY)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Ensure --purge is NOT requested (no argv / env).
        monkeypatch.delenv("VOICE_TYPER_PURGE", raising=False)
        monkeypatch.setattr(sys, "argv", ["uninstall_permissions.py"])

        # Re-import after argv reset (the module reads argv at import
        mod2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod2)

        rc = mod2.main()
        assert rc == 0

    def test_purge_flag_triggers_purge_user_data(self, monkeypatch, tmp_path):
        """The --purge flag triggers _purge_user_data (mocked here to"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("uninstall_permissions_windows_purge", UNINSTALL_PERMISSIONS_PY)
        assert spec is not None and spec.loader is not None

        # Set argv with --purge BEFORE exec_module (the module reads
        monkeypatch.setattr(sys, "argv", ["uninstall_permissions.py", "--purge"])
        monkeypatch.delenv("VOICE_TYPER_PURGE", raising=False)

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Mock _purge_user_data to verify it's called.
        purge_called = {"yes": False}

        def _fake_purge():
            purge_called["yes"] = True

        monkeypatch.setattr(mod, "_purge_user_data", _fake_purge)

        # Mock the autostart cleanup helpers.
        from voice_typer.server.server_platform import autostart_windows

        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_lausu_runkeys",
            lambda: [],
        )
        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_lausu_tasks",
            lambda: [],
        )

        rc = mod.main()
        assert rc == 0
        assert purge_called["yes"] is True, "_purge_user_data was not called when --purge passed"

    def test_purge_env_var_triggers_purge_user_data(self, monkeypatch):
        """The VOICE_TYPER_PURGE=1 env var triggers _purge_user_data"""
        import importlib.util

        spec = importlib.util.spec_from_file_location("uninstall_permissions_windows_env", UNINSTALL_PERMISSIONS_PY)
        assert spec is not None and spec.loader is not None

        # Set env var BEFORE exec_module.
        monkeypatch.setenv("VOICE_TYPER_PURGE", "1")
        monkeypatch.setattr(sys, "argv", ["uninstall_permissions.py"])

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        purge_called = {"yes": False}
        monkeypatch.setattr(mod, "_purge_user_data", lambda: purge_called.__setitem__("yes", True))

        from voice_typer.server.server_platform import autostart_windows

        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_lausu_runkeys",
            lambda: [],
        )
        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_lausu_tasks",
            lambda: [],
        )

        rc = mod.main()
        assert rc == 0
        assert purge_called["yes"] is True, "_purge_user_data was not called when VOICE_TYPER_PURGE=1"

    def test_purge_user_data_no_appdata_skips_silently(self, monkeypatch):
        """_purge_user_data logs a warning and returns when APPDATA is"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "uninstall_permissions_windows_noappdata", UNINSTALL_PERMISSIONS_PY
        )
        assert spec is not None and spec.loader is not None
        monkeypatch.setattr(sys, "argv", ["uninstall_permissions.py"])
        monkeypatch.delenv("VOICE_TYPER_PURGE", raising=False)
        monkeypatch.delenv("APPDATA", raising=False)

        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Should not raise.
        mod._purge_user_data()


class TestWiring:
    """verify the .nsh / .bat / Python script are wired into"""

    def test_tauri_conf_has_windows_webview_install_mode(self):
        """tauri.conf.json bundle.windows.webviewInstallMode must be set"""
        with TAURI_CONF_JSON.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        windows = cfg.get("bundle", {}).get("windows", {})
        assert "webviewInstallMode" in windows, (
            "tauri.conf.json bundle.windows.webviewInstallMode is missing. "
            "Pin it explicitly so a future Tauri schema change can't "
            "silently flip the install mode."
        )
        wim = windows["webviewInstallMode"]
        assert isinstance(wim, dict), f"webviewInstallMode must be a dict, got {type(wim)}"
        assert "type" in wim, "webviewInstallMode.type is required."
        assert wim["type"] in {
            "downloadBootstrapper",
            "embedBootstrapper",
            "offlineInstaller",
            "skip",
        }, f"webviewInstallMode.type has unexpected value: {wim['type']}"

    def test_tauri_conf_has_nsis_installer_hooks_v2_key(self):
        """tauri.conf.json bundle.windows.nsis.installerHooks must be"""
        # Verify the file does NOT contain the v1 short form.
        text = TAURI_CONF_JSON.read_text(encoding="utf-8")
        # Match `"preRemove":` or `"postInstall":` (v1 short form).
        import re

        v1_matches = re.findall(r'"(postInstall|preRemove)"\s*:', text)
        assert not v1_matches, (
            f"tauri.conf.json contains forbidden v1 short-form keys: {v1_matches}. "
            "Use the v2 keys WITH the 'Script' suffix: 'postInstallScript' / "
            "'preRemoveScript' (and for NSIS, 'installerHooks')."
        )
        # Verify the v2 NSIS hooks key IS present for Windows.
        with TAURI_CONF_JSON.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        windows = cfg.get("bundle", {}).get("windows", {})
        nsis = windows.get("nsis", {})
        assert "installerHooks" in nsis, (
            "tauri.conf.json bundle.windows.nsis.installerHooks is missing, "
            "the Tauri NSIS bundler won't run the uninstall .nsh. See S2-CR-69."
        )
        pre_remove = nsis["installerHooks"]
        # Tauri v2 accepts string OR list of strings (schema: string | string[]).
        if isinstance(pre_remove, str):
            hook_paths = [pre_remove]
        elif isinstance(pre_remove, list) and all(isinstance(h, str) for h in pre_remove):
            hook_paths = pre_remove
        else:
            raise AssertionError(
                f"installerHooks must be a string or list of strings, got {type(pre_remove).__name__}: {pre_remove!r}"
            )
        assert hook_paths, "installerHooks must not be empty."
        for hook in hook_paths:
            resolved = (TAURI_CONF_JSON.parent / hook).resolve()
            assert resolved.is_file(), (
                f"bundle.windows.nsis.installerHooks entry {hook!r} "
                f"(resolved: {resolved}) does NOT exist, Tauri !includes "
                "each entry into installer.nsi; a missing file aborts makensis."
            )
            assert hook.endswith(".nsh"), (
                f"installerHooks entry {hook!r} must be an NSIS script (.nsh), "
                "Tauri !includes it into installer.nsi; a non-NSIS file (e.g. a "
                ".bat) aborts makensis with 'Invalid command: @echo'."
            )

    def test_uninstall_permissions_py_exists(self):
        """scripts/windows/uninstall_permissions.py must exist (the"""
        assert UNINSTALL_PERMISSIONS_PY.is_file(), (
            f"{UNINSTALL_PERMISSIONS_PY} does not exist, the .bat wrapper has nothing to invoke. See S2-CR-69."
        )

    def test_uninstall_bat_exists(self):
        """scripts/windows/uninstall.bat must exist (the .bat wrapper"""
        assert UNINSTALL_BAT.is_file(), (
            f"{UNINSTALL_BAT} does not exist, tauri.conf.json's "
            "nsis.installerHooks points at a non-existent file. See S2-CR-69."
        )

    def test_uninstaller_nsh_exists(self):
        """scripts/windows/uninstaller.nsh must exist (the NSIS custom"""
        assert UNINSTALLER_NSH.is_file(), (
            f"{UNINSTALLER_NSH} does not exist, the NSIS installer hook's "
            "nsis.include points at a non-existent file. See S2-CR-69."
        )

    def test_uninstall_permissions_py_py_compile_clean(self):
        """uninstall_permissions.py must pass py_compile (no syntax"""
        import py_compile

        py_compile.compile(str(UNINSTALL_PERMISSIONS_PY), doraise=True)

    def test_uninstall_permissions_py_has_no_type_ignore(self):
        """Constraint #5: no `# type: ignore` comments allowed."""
        text = UNINSTALL_PERMISSIONS_PY.read_text(encoding="utf-8")
        assert "# type: ignore" not in text, (
            "Constraint #5 violation: scripts/windows/uninstall_permissions.py "
            "contains `# type: ignore`. Remove it and fix the underlying type "
            "issue."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
