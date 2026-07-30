"""S2-CR-69 (sub-agent 8): regression tests for the Windows uninstaller
autostart cleanup.

The Windows uninstaller must remove the per-user autostart entries that
``voice_typer/server/server_platform/autostart_windows.py`` creates at
runtime — otherwise the OS will keep trying to launch the (now-deleted)
binary at every login, spamming the system log with "file not found"
errors.

Pre-fix (S2-CR-69 PARTIAL state): only Linux had the cleanup
(``scripts/linux/uninstall_permissions.py`` + prerm) and macOS had
``scripts/macos/uninstall.sh``. Windows had ``deleteAppDataOnUninstall:
true`` in electron-builder.yml (which only removes the AppData
directory — NOT the registry / Task Scheduler entries) and a
deferred-comment block in the ``nsis:`` section saying the fix
"needed a .nsh file that was NOT yet in this repo's file tree".

Post-fix (this commit):
  1. ``scripts/windows/uninstaller.nsh`` (existing — added by an
     earlier wave) is now WIRED via ``nsis.include`` in
     electron-builder.yml. The .nsh does the native NSIS registry
     + schtasks sweep.
  2. ``scripts/windows/uninstall_permissions.py`` (NEW) — Python
     equivalent, invoked by the .bat wrapper, which calls the
     production helpers in ``autostart_windows.py``:
       - ``_unregister_all_voicetyper_runkeys`` — enumerates ALL
         ``VoiceTyper_*`` values under HKCU\\...\\Run and deletes
         them (covers stale entries from previous installs at
         different paths).
       - ``_unregister_all_voicetyper_tasks`` — PowerShell
         ``Get-ScheduledTask`` sweep for ``VoiceTyperAutostart*``.
  3. ``scripts/windows/uninstall.bat`` (NEW) — wrapper that invokes
     the Python script (preferred) and falls back to a native
     PowerShell sweep if Python is unavailable at uninstall time.
  4. ``src-tauri/tauri.conf.json`` ``bundle.windows.nsis.preRemoveScript``
     points at the .bat (Tauri v2 installerHooks path — uses the
     ``preRemoveScript`` key WITH the ``Script`` suffix, NOT the v1
     ``preRemove`` short form that the Tauri v1 schema accepted).
  5. ``src-tauri/tauri.conf.json`` ``bundle.windows.webviewInstallMode``
     is set to ``downloadBootstrapper`` (Tauri v2 default — pinned
     explicitly so a future schema change can't silently flip it).

Tests use the ``fake_winreg`` fixture pattern (mirrors
``tests/test_autostart_windows_de67.py``) so the Windows-only ``winreg``
module is importable on the Linux test host. We mock ``EnumValue`` /
``DeleteValue`` / ``OpenKey`` and verify which entries the production
helpers delete.

Test matrix
-----------
- ``_unregister_all_voicetyper_runkeys``:
  - Empty Run key -> no deletions, empty list returned.
  - Single VoiceTyper entry -> deleted, returned in list.
  - Multiple VoiceTyper entries (mixed hashes) -> all deleted.
  - Mix of VoiceTyper + non-VoiceTyper entries -> only VoiceTyper deleted.
  - Non-Windows (no winreg) -> empty list, no exception.
  - OpenKey OSError -> empty list, no exception (best-effort).
  - DeleteValue OSError on one entry -> that entry skipped, others
    still deleted.
- ``uninstall_permissions.py`` script entry point:
  - main() with mocked helpers returns 0.
  - --purge flag triggers _purge_user_data.
  - VOICE_TYPER_PURGE=1 env var triggers _purge_user_data.
- electron-builder.yml + tauri.conf.json wiring:
  - electron-builder.yml has ``nsis.include`` pointing at an existing
    .nsh file.
  - tauri.conf.json has ``bundle.windows.webviewInstallMode`` set.
  - tauri.conf.json has ``bundle.windows.nsis.preRemoveScript`` set
    (NOT the v1 ``preRemove`` short form).
  - The preRemoveScript path resolves to an existing .bat file.
"""

from __future__ import annotations

import json
import pathlib
import sys
import types
from unittest.mock import MagicMock

import pytest
import yaml

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
ELECTRON_BUILDER_YML = REPO_ROOT / "voice_typer" / "client" / "electron-builder.yml"
TAURI_CONF_JSON = REPO_ROOT / "src-tauri" / "tauri.conf.json"
UNINSTALL_PERMISSIONS_PY = REPO_ROOT / "scripts" / "windows" / "uninstall_permissions.py"
UNINSTALL_BAT = REPO_ROOT / "scripts" / "windows" / "uninstall.bat"
UNINSTALLER_NSH = REPO_ROOT / "scripts" / "windows" / "uninstaller.nsh"

RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"


# ---------------------------------------------------------------------------
# Fixtures: fake winreg + win32 platform (mirrors test_autostart_windows_de67.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly.

    Mirrors the fixture in ``tests/test_autostart_windows_de67.py``. Returns
    the fake module; tests can configure its ``EnumValue`` / ``DeleteValue``
    behavior as needed.
    """
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

    monkeypatch.setattr(server_platform, "SYSTEM", "win32")
    return server_platform


def _enum_value_side_effect(entries: list[tuple[str, str, int]]):
    """Build a side_effect for ``winreg.EnumValue`` that yields each entry
    in order, then raises ``OSError`` to signal end-of-enumeration."""
    iterator = iter(entries)

    def _side_effect(_key, _index):
        try:
            return next(iterator)
        except StopIteration:
            raise OSError("no more values") from None

    return _side_effect


# ---------------------------------------------------------------------------
# _unregister_all_voicetyper_runkeys
# ---------------------------------------------------------------------------


class TestUnregisterAllVoiceTyperRunkeys:
    """S2-CR-69: the uninstaller helper removes ALL VoiceTyper_* entries
    (not just the current install's hash), so stale entries from previous
    installs are cleaned up too."""

    def test_empty_run_key_no_deletions(self, fake_winreg, win32_platform):
        """An empty Run key (no VoiceTyper entries) results in no
        DeleteValue calls and an empty list returned."""
        from voice_typer.server.server_platform import autostart_windows

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([])

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        assert deleted == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_single_voicetyper_entry_deleted(self, fake_winreg, win32_platform):
        """A single VoiceTyper_<hash> entry is deleted and its name
        returned in the list."""
        from voice_typer.server.server_platform import autostart_windows

        name = "VoiceTyper_a1b2c3d4"
        value = r'"C:\Program Files\VoiceTyper\app.exe" --delay 15'
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([(name, value, fake_winreg.REG_SZ)])

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        assert deleted == [name]
        fake_winreg.DeleteValue.assert_called_once()
        # DeleteValue(key, name) — name is the second positional arg.
        assert fake_winreg.DeleteValue.call_args.args[1] == name

    def test_multiple_voicetyper_entries_all_deleted(self, fake_winreg, win32_platform):
        """Multiple VoiceTyper entries (different hashes from previous
        installs) are ALL deleted — the uninstaller does NOT scope to
        the current install's hash (unlike _unregister_app_autostart_runkey).
        """
        from voice_typer.server.server_platform import autostart_windows

        entries = [
            ("VoiceTyper_aaaaaaaa", r'"C:\path1\app.exe"', fake_winreg.REG_SZ),
            ("VoiceTyper_bbbbbbbb", r'"C:\path2\app.exe"', fake_winreg.REG_SZ),
            ("VoiceTyper_cccccccc", r'"C:\path3\app.exe"', fake_winreg.REG_SZ),
        ]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        # All three must be deleted (order preserved from enumeration).
        assert sorted(deleted) == sorted(["VoiceTyper_aaaaaaaa", "VoiceTyper_bbbbbbbb", "VoiceTyper_cccccccc"])
        assert fake_winreg.DeleteValue.call_count == 3

    def test_mixed_entries_only_voicetyper_deleted(self, fake_winreg, win32_platform):
        """When the Run key contains BOTH VoiceTyper entries AND non-
        VoiceTyper entries (OneDrive, Discord, etc.), ONLY the VoiceTyper
        entries are deleted — non-VoiceTyper entries are left alone.
        """
        from voice_typer.server.server_platform import autostart_windows

        entries = [
            ("OneDrive", r'"C:\Program Files\OneDrive\OneDrive.exe" /background', fake_winreg.REG_SZ),
            ("VoiceTyper_aaaaaaaa", r'"C:\VoiceTyper\app.exe"', fake_winreg.REG_SZ),
            ("Discord", r'"C:\AppData\Discord\Discord.exe"', fake_winreg.REG_SZ),
            ("VoiceTyper_bbbbbbbb", r'"C:\VoiceTyperOld\app.exe"', fake_winreg.REG_SZ),
        ]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        # Only the VoiceTyper_* entries should be in the deleted list.
        assert sorted(deleted) == ["VoiceTyper_aaaaaaaa", "VoiceTyper_bbbbbbbb"]
        # Exactly two DeleteValue calls (the two VoiceTyper entries).
        assert fake_winreg.DeleteValue.call_count == 2
        deleted_names = {call.args[1] for call in fake_winreg.DeleteValue.call_args_list}
        assert deleted_names == {"VoiceTyper_aaaaaaaa", "VoiceTyper_bbbbbbbb"}

    def test_no_winreg_returns_empty_list(self, monkeypatch):
        """On non-Windows (winreg import fails), the function returns an
        empty list — no exception raised. This is the production contract:
        the uninstaller script calls this unconditionally and relies on
        the empty-list return to signal "nothing to do on this platform".
        """
        # Ensure winreg is NOT in sys.modules (simulates non-Windows host).
        monkeypatch.setitem(sys.modules, "winreg", None)
        from voice_typer.server.server_platform import autostart_windows

        # The function does `import winreg` — with winreg set to None in
        # sys.modules, the import "succeeds" but the bound name is None,
        # so the `except ImportError` doesn't trigger. We need to actually
        # raise ImportError. Use a side-effect via sys.modules manipulation:
        # Remove the entry so `import winreg` raises ImportError.
        if "winreg" in sys.modules:
            monkeypatch.delitem(sys.modules, "winreg")

        # Also ensure builtins.__import__ doesn't somehow find a real
        # winreg on the Linux host (it shouldn't — winreg is Windows-only).
        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        assert deleted == []

    def test_openkey_oserror_returns_empty_list(self, fake_winreg, win32_platform):
        """If OpenKey raises OSError (e.g. the Run key doesn't exist —
        shouldn't happen on a real Windows install but defensive), the
        function returns an empty list and logs a warning."""
        from voice_typer.server.server_platform import autostart_windows

        fake_winreg.OpenKey.side_effect = OSError("key not found")

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        assert deleted == []
        fake_winreg.DeleteValue.assert_not_called()

    def test_deletevalue_oserror_skips_entry_continues_sweep(self, fake_winreg, win32_platform):
        """If DeleteValue raises OSError on one entry (e.g. transient
        registry permission issue), that entry is skipped but the sweep
        CONTINUES — the uninstaller must not abort on a single failure.
        """
        from voice_typer.server.server_platform import autostart_windows

        entries = [
            ("VoiceTyper_aaaaaaaa", r'"C:\path1\app.exe"', fake_winreg.REG_SZ),
            ("VoiceTyper_bbbbbbbb", r'"C:\path2\app.exe"', fake_winreg.REG_SZ),
            ("VoiceTyper_cccccccc", r'"C:\path3\app.exe"', fake_winreg.REG_SZ),
        ]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)
        # Make DeleteValue fail ONLY for the second entry (by call count).
        call_count = {"n": 0}

        def _delete_side_effect(_key, _name):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise OSError("transient registry error")

        fake_winreg.DeleteValue.side_effect = _delete_side_effect

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        # The second entry should be MISSING from the deleted list
        # (DeleteValue raised); the first and third should be present.
        assert "VoiceTyper_aaaaaaaa" in deleted
        assert "VoiceTyper_bbbbbbbb" not in deleted
        assert "VoiceTyper_cccccccc" in deleted
        # All three DeleteValue calls were attempted.
        assert fake_winreg.DeleteValue.call_count == 3

    def test_loop_terminates_on_enum_oserror(self, fake_winreg, win32_platform):
        """The enumeration loop terminates cleanly when EnumValue raises
        OSError (Windows signals end-of-enumeration via OSError, NOT
        StopIteration). This is a regression guard against an accidental
        `except StopIteration` that would infinite-loop."""
        from voice_typer.server.server_platform import autostart_windows

        # Simulate: one entry, then OSError to end enumeration.
        entries = [("VoiceTyper_aaaaaaaa", r'"C:\path\app.exe"', fake_winreg.REG_SZ)]
        fake_winreg.EnumValue.side_effect = _enum_value_side_effect(entries)

        deleted = autostart_windows._unregister_all_voicetyper_runkeys()
        assert deleted == ["VoiceTyper_aaaaaaaa"]
        # Verify the loop actually terminated (didn't infinite-loop) —
        # EnumValue was called at most twice (once for the entry, once
        # for the end-signal).
        assert fake_winreg.EnumValue.call_count <= 2

    def test_closekey_called_in_finally(self, fake_winreg, win32_platform):
        """CloseKey is called even if the sweep raises — the function
        uses try/finally to release the registry handle. (Defensive
        against a future refactor that drops the finally block.)"""
        from voice_typer.server.server_platform import autostart_windows

        fake_winreg.EnumValue.side_effect = _enum_value_side_effect([])

        autostart_windows._unregister_all_voicetyper_runkeys()
        fake_winreg.CloseKey.assert_called_once()


# ---------------------------------------------------------------------------
# _unregister_all_voicetyper_tasks
# ---------------------------------------------------------------------------


class TestUnregisterAllVoiceTyperTasks:
    """S2-CR-69: the Task Scheduler sweep uses PowerShell because
    ``schtasks`` does NOT accept wildcards in ``/TN``."""

    def test_no_task_scheduler_returns_empty_list(self, monkeypatch):
        """When task_scheduler.is_supported() returns False (non-Windows
        host), the function returns an empty list — no PowerShell call.
        """
        from voice_typer.server.server_platform import autostart_windows

        # Mock task_scheduler.is_supported to return False.
        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: False
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)

        deleted = autostart_windows._unregister_all_voicetyper_tasks()
        assert deleted == []

    def test_powershell_success_returns_task_names(self, monkeypatch, fake_winreg, win32_platform):
        """When PowerShell succeeds and outputs task names, those names
        are returned in the deleted list."""
        from voice_typer.server.server_platform import autostart_windows

        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)

        # Mock subprocess.run to simulate PowerShell success.
        fake_result = MagicMock()
        fake_result.returncode = 0
        fake_result.stdout = "VoiceTyperAutostart_aaaaaaaa\nVoiceTyperAutostart_bbbbbbbb\n"
        fake_result.stderr = ""
        monkeypatch.setattr(
            "subprocess.run",
            MagicMock(return_value=fake_result),
        )

        deleted = autostart_windows._unregister_all_voicetyper_tasks()
        assert sorted(deleted) == ["VoiceTyperAutostart_aaaaaaaa", "VoiceTyperAutostart_bbbbbbbb"]

    def test_powershell_failure_returns_empty_list(self, monkeypatch, fake_winreg, win32_platform):
        """When PowerShell exits non-zero, the function returns an empty
        list (best-effort — a single failure should not abort the
        uninstaller)."""
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

        deleted = autostart_windows._unregister_all_voicetyper_tasks()
        assert deleted == []

    def test_subprocess_oserror_returns_empty_list(self, monkeypatch, fake_winreg, win32_platform):
        """If subprocess.run raises OSError (e.g. powershell.exe not on
        PATH on a non-Windows host with task_scheduler.is_supported()
        stubbed True), the function returns an empty list — no exception
        propagates."""
        from voice_typer.server.server_platform import autostart_windows

        fake_task_scheduler = types.ModuleType("task_scheduler")
        fake_task_scheduler.is_supported = lambda: True
        monkeypatch.setitem(sys.modules, "voice_typer.server.task_scheduler", fake_task_scheduler)

        def _raise(*_a, **_kw):
            raise OSError("powershell.exe not found")

        monkeypatch.setattr("subprocess.run", _raise)

        deleted = autostart_windows._unregister_all_voicetyper_tasks()
        assert deleted == []


# ---------------------------------------------------------------------------
# uninstall_permissions.py — script entry point
# ---------------------------------------------------------------------------


class TestUninstallPermissionsScript:
    """S2-CR-69: the uninstall_permissions.py script wires the production
    helpers + the --purge flag (mirrors scripts/linux/uninstall_permissions.py).
    """

    def test_script_main_returns_zero_with_empty_registry(self, monkeypatch):
        """main() returns 0 when there are no entries to remove (already
        clean — not an error)."""
        # Force the voice_typer package path to succeed with empty lists.
        from voice_typer.server.server_platform import autostart_windows

        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_voicetyper_runkeys",
            lambda: [],
        )
        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_voicetyper_tasks",
            lambda: [],
        )

        # Load the script as a module (so we can call main() directly
        # without invoking __main__).
        import importlib.util

        spec = importlib.util.spec_from_file_location("uninstall_permissions_windows", UNINSTALL_PERMISSIONS_PY)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        # Ensure --purge is NOT requested (no argv / env).
        monkeypatch.delenv("VOICE_TYPER_PURGE", raising=False)
        monkeypatch.setattr(sys, "argv", ["uninstall_permissions.py"])

        # Re-import after argv reset (the module reads argv at import
        # time for _purge_requested — so we need to re-exec).
        mod2 = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod2)

        rc = mod2.main()
        assert rc == 0

    def test_purge_flag_triggers_purge_user_data(self, monkeypatch, tmp_path):
        """The --purge flag triggers _purge_user_data (mocked here to
        verify it's called)."""
        import importlib.util

        spec = importlib.util.spec_from_file_location("uninstall_permissions_windows_purge", UNINSTALL_PERMISSIONS_PY)
        assert spec is not None and spec.loader is not None

        # Set argv with --purge BEFORE exec_module (the module reads
        # argv at import time).
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
            "_unregister_all_voicetyper_runkeys",
            lambda: [],
        )
        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_voicetyper_tasks",
            lambda: [],
        )

        rc = mod.main()
        assert rc == 0
        assert purge_called["yes"] is True, "_purge_user_data was not called when --purge passed"

    def test_purge_env_var_triggers_purge_user_data(self, monkeypatch):
        """The VOICE_TYPER_PURGE=1 env var triggers _purge_user_data
        (mirrors the Linux pattern — useful when invoked by NSIS/Tauri
        which can't pass argv)."""
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
            "_unregister_all_voicetyper_runkeys",
            lambda: [],
        )
        monkeypatch.setattr(
            autostart_windows,
            "_unregister_all_voicetyper_tasks",
            lambda: [],
        )

        rc = mod.main()
        assert rc == 0
        assert purge_called["yes"] is True, "_purge_user_data was not called when VOICE_TYPER_PURGE=1"

    def test_purge_user_data_no_appdata_skips_silently(self, monkeypatch):
        """_purge_user_data logs a warning and returns when APPDATA is
        unset (non-Windows CI host)."""
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


# ---------------------------------------------------------------------------
# Wiring: electron-builder.yml + tauri.conf.json + file existence
# ---------------------------------------------------------------------------


class TestWiring:
    """S2-CR-69: verify the .nsh / .bat / Python script are wired into
    the build configs and reference existing files."""

    def test_electron_builder_yml_has_nsis_include(self):
        """electron-builder.yml nsis.include must point at a real .nsh file."""
        with ELECTRON_BUILDER_YML.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        nsis = cfg.get("nsis") or {}
        assert "include" in nsis, (
            "electron-builder.yml nsis.include is missing — the NSIS "
            "uninstaller hook (.nsh) is not wired. See S2-CR-69."
        )
        include_path = nsis["include"]
        assert isinstance(include_path, str), f"nsis.include must be a string, got {type(include_path)}"
        # electron-builder resolves `include:` relative to its cwd
        # (voice_typer/client/).
        resolved = (ELECTRON_BUILDER_YML.parent / include_path).resolve()
        assert resolved.is_file(), (
            f"nsis.include points at {include_path} (resolved: {resolved}) "
            f"but the file does NOT exist — the NSIS build would fail."
        )

    def test_electron_builder_yml_keeps_delete_app_data_on_uninstall(self):
        """S2-CR-70: deleteAppDataOnUninstall must remain true (the
        .nsh removes the registry; this removes the AppData dir)."""
        with ELECTRON_BUILDER_YML.open("r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        nsis = cfg.get("nsis") or {}
        assert nsis.get("deleteAppDataOnUninstall") is True, (
            "nsis.deleteAppDataOnUninstall must be true (S2-CR-70 — "
            "removes the %APPDATA%\\voice-typer directory on uninstall)."
        )

    def test_tauri_conf_has_windows_webview_install_mode(self):
        """tauri.conf.json bundle.windows.webviewInstallMode must be set
        (Tauri v2 key — pinned explicitly so a future schema change
        can't silently flip it)."""
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

    def test_tauri_conf_has_pre_remove_script_v2_key(self):
        """tauri.conf.json bundle.windows.nsis.preRemoveScript must be
        set WITH the 'Script' suffix (v2 key). The v1 short form
        'preRemove' (no suffix) is FORBIDDEN — see constraint #7."""
        # Verify the file does NOT contain the v1 short form.
        text = TAURI_CONF_JSON.read_text(encoding="utf-8")
        # Match `"preRemove":` or `"postInstall":` (v1 short form).
        import re

        v1_matches = re.findall(r'"(postInstall|preRemove)"\s*:', text)
        assert not v1_matches, (
            f"tauri.conf.json contains forbidden v1 short-form keys: {v1_matches}. "
            "Use the v2 keys WITH the 'Script' suffix: 'postInstallScript' / "
            "'preRemoveScript'."
        )
        # Verify the v2 key IS present for Windows.
        with TAURI_CONF_JSON.open("r", encoding="utf-8") as f:
            cfg = json.load(f)
        windows = cfg.get("bundle", {}).get("windows", {})
        nsis = windows.get("nsis", {})
        assert "preRemoveScript" in nsis, (
            "tauri.conf.json bundle.windows.nsis.preRemoveScript is missing — "
            "the Tauri NSIS bundler won't run the uninstall .bat. See S2-CR-69."
        )
        pre_remove = nsis["preRemoveScript"]
        assert isinstance(pre_remove, str), f"preRemoveScript must be a string, got {type(pre_remove)}"
        # tauri.conf.json is at src-tauri/tauri.conf.json; preRemoveScript
        # resolves relative to src-tauri/.
        resolved = (TAURI_CONF_JSON.parent / pre_remove).resolve()
        assert resolved.is_file(), (
            f"bundle.windows.nsis.preRemoveScript points at {pre_remove} "
            f"(resolved: {resolved}) but the .bat file does NOT exist."
        )

    def test_uninstall_permissions_py_exists(self):
        """scripts/windows/uninstall_permissions.py must exist (the
        Python cleanup script wired by the .bat)."""
        assert UNINSTALL_PERMISSIONS_PY.is_file(), (
            f"{UNINSTALL_PERMISSIONS_PY} does not exist — the .bat wrapper has nothing to invoke. See S2-CR-69."
        )

    def test_uninstall_bat_exists(self):
        """scripts/windows/uninstall.bat must exist (the .bat wrapper
        wired by tauri.conf.json preRemoveScript)."""
        assert UNINSTALL_BAT.is_file(), (
            f"{UNINSTALL_BAT} does not exist — tauri.conf.json's "
            "preRemoveScript points at a non-existent file. See S2-CR-69."
        )

    def test_uninstaller_nsh_exists(self):
        """scripts/windows/uninstaller.nsh must exist (the NSIS custom
        uninstaller macro wired by electron-builder.yml nsis.include)."""
        assert UNINSTALLER_NSH.is_file(), (
            f"{UNINSTALLER_NSH} does not exist — electron-builder.yml's "
            "nsis.include points at a non-existent file. See S2-CR-69."
        )

    def test_uninstall_permissions_py_py_compile_clean(self):
        """uninstall_permissions.py must pass py_compile (no syntax
        errors). Runs on Linux (sandbox) — the script uses deferred
        imports for winreg so it imports cleanly on non-Windows."""
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
