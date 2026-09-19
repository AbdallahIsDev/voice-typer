"""Phase 0-W Gate Check 10 (+1): Windows autostart + installer validation."""

from __future__ import annotations

import json
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
SRC_TAURI_DIR = TAURI_CONF.parent
CARGO_TOML = SRC_TAURI_DIR / "Cargo.toml"
MAIN_RS = SRC_TAURI_DIR / "src" / "main.rs"

# XML namespace for Task Scheduler definitions.
_TASK_NS = {"ms": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly."""
    fake = types.ModuleType("winreg")
    # Constants used by the production code (task_scheduler.py + server_platform.py).
    fake.HKEY_CURRENT_USER = 0x80000001
    fake.KEY_SET_VALUE = 0x0002
    fake.KEY_READ = 0x20019
    fake.KEY_ALL_ACCESS = 0xF003F
    fake.REG_SZ = 1
    # Methods. MagicMock so tests can assert call counts / configure returns.
    fake.OpenKey = MagicMock(return_value=MagicMock())
    fake.SetValueEx = MagicMock()
    fake.QueryValueEx = MagicMock(return_value=("cmd", 1))
    fake.DeleteValue = MagicMock()
    fake.CloseKey = MagicMock()
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


def test_task_scheduler_xml_uses_logon_trigger_no_elevation():
    """``server_platform._build_app_autostart_task_xml`` creates a Task"""
    from voice_typer.server import server_platform

    xml_str = server_platform._build_app_autostart_task_xml()
    root = ET.fromstring(xml_str)

    # LogonTrigger (fires at user logon. NOT Boot/Event/Calendar triggers).
    triggers = root.find("ms:Triggers", _TASK_NS)
    assert triggers is not None, "XML must have <Triggers>"
    logon = triggers.find("ms:LogonTrigger", _TASK_NS)
    assert logon is not None, (
        "must use <LogonTrigger> (not BootTrigger/EventTrigger), interactive "
        "session required for InteractiveToken + pythonw.exe"
    )
    assert logon.find("ms:Enabled", _TASK_NS).text == "true"

    # Principal: InteractiveToken + LeastPrivilege (no admin elevation).
    principal = root.find("ms:Principals/ms:Principal", _TASK_NS)
    assert principal is not None, "must have a Principal"
    assert principal.find("ms:LogonType", _TASK_NS).text == "InteractiveToken", (
        "must use InteractiveToken (current user's interactive session)"
    )
    assert principal.find("ms:RunLevel", _TASK_NS).text == "LeastPrivilege", (
        "must use LeastPrivilege (NO admin elevation)"
    )
    # No <UserId> → defaults to the registering (current) user → HKCU scope.
    assert principal.find("ms:UserId", _TASK_NS) is None, (
        "must omit <UserId> so the task defaults to the current user (HKCU, "
        "no admin); an explicit SID extraction previously broke with "
        "'UserId incorrectly formatted'"
    )

    # Action: the app launcher directly (no cmd.exe wrapper).
    command = root.find("ms:Actions/ms:Exec/ms:Command", _TASK_NS)
    assert command is not None, "must have an Exec/Command action"
    assert command.text, "Command must be non-empty"


# ─── Test 3: enable_autostart on Windows uses Windows path (not plist/.desktop)


def test_enable_autostart_on_windows_uses_windows_path_not_plist_or_desktop(monkeypatch, fake_winreg, win32_platform):
    """
    ``_enable_autostart_windows()`` (Task Scheduler preferred, then
    Startup .bat, then HKCU Run key). It must NOT create a macOS
    """
    server_platform = win32_platform

    runkey_calls: list[int] = []
    task_calls: list[int] = []
    startup_calls: list[int] = []
    macos_calls: list[int] = []
    linux_calls: list[int] = []

    from voice_typer.server.server_platform import autostart_linux, autostart_macos, autostart_windows

    monkeypatch.setattr(
        autostart_windows,
        "_register_app_autostart_task",
        lambda: (task_calls.append(1), True)[1],
    )
    monkeypatch.setattr(
        autostart_windows,
        "_register_app_autostart_runkey",
        lambda: (runkey_calls.append(1), True)[1],
    )
    monkeypatch.setattr(
        autostart_windows,
        "_register_app_autostart_startup",
        lambda: (startup_calls.append(1), True)[1],
    )
    monkeypatch.setattr(
        autostart_macos,
        "_enable_autostart_macos",
        lambda: (macos_calls.append(1), False)[1],
    )
    monkeypatch.setattr(
        autostart_linux,
        "_enable_autostart_linux",
        lambda: (linux_calls.append(1), False)[1],
    )

    result = server_platform.enable_autostart()

    assert result is True
    assert len(task_calls) == 1, "must call _register_app_autostart_task first (AUTOSTART-ORDER-FIX)"
    assert len(runkey_calls) == 0, "HKCU Run key is LAST resort, not called when Task succeeds"
    assert len(startup_calls) == 0, "Startup .bat must NOT be registered when Task Scheduler succeeds"
    assert len(macos_calls) == 0, "must NOT call _enable_autostart_macos (LaunchAgent plist) on Windows"
    assert len(linux_calls) == 0, "must NOT call _enable_autostart_linux (.desktop file) on Windows"


# ─── Test 4: enable_autostart falls back to Task Scheduler (LogonTrigger)


def test_enable_autostart_windows_falls_back_to_task_scheduler(monkeypatch, fake_winreg, win32_platform):
    """Task Scheduler path which builds a LogonTrigger XML and calls"""
    server_platform = win32_platform
    from voice_typer.server import task_scheduler
    from voice_typer.server.server_platform import autostart_windows

    # Run key fails → Task Scheduler fallback must be tried.
    monkeypatch.setattr(autostart_windows, "_register_app_autostart_runkey", lambda: False)

    # Track schtasks invocations.
    schtasks_calls: list[list[str]] = []

    def fake_schtasks(args, *, capture=True):
        schtasks_calls.append(list(args))
        if "/Create" in args:
            return 0, "SUCCESS: Task created"
        if "/Delete" in args:
            return 0, "SUCCESS: Deleted"
        if "/Query" in args:
            return 1, "not found"
        return 0, ""

    monkeypatch.setattr(task_scheduler, "_schtasks", fake_schtasks)
    monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
    monkeypatch.setattr(
        task_scheduler,
        "_schtasks_elevated",
        lambda args, **kw: (0, "SUCCESS"),
    )
    # Stub XML builder so the test doesn't depend on sys.executable resolution.
    monkeypatch.setattr(
        autostart_windows,
        "_build_app_autostart_task_xml",
        lambda: "<Task><Triggers><LogonTrigger/></Triggers></Task>",
    )

    result = server_platform.enable_autostart()

    assert result is True, "Task Scheduler registration must succeed"
    create_calls = [c for c in schtasks_calls if "/Create" in c]
    assert len(create_calls) >= 1, "must call schtasks /Create for the Task Scheduler path"
    # The task name must use the com.voicetyper.autostart prefix (+ install hash).
    assert any("com.voicetyper.autostart" in " ".join(c) for c in create_calls), (
        "task name must include the com.voicetyper.autostart prefix"
    )


def test_disable_autostart_windows_removes_both(monkeypatch, fake_winreg, win32_platform):
    """``disable_autostart()`` on Windows removes BOTH the Task Scheduler"""
    server_platform = win32_platform
    from voice_typer.server.server_platform import autostart_windows

    task_removed: list[int] = []
    reg_removed: list[int] = []

    monkeypatch.setattr(
        autostart_windows,
        "_unregister_app_autostart_task",
        lambda: (task_removed.append(1), True)[1],
    )
    monkeypatch.setattr(
        autostart_windows,
        "_unregister_app_autostart_runkey",
        lambda: (reg_removed.append(1), True)[1],
    )

    result = server_platform.disable_autostart()

    assert result is True
    assert len(task_removed) == 1, "must remove the Task Scheduler entry"
    assert len(reg_removed) == 1, "must remove the HKCU Run key fallback"


@pytest.mark.parametrize(
    "task_registered, runkey_registered, expected",
    [
        (False, False, False),  # neither → False
        (True, False, True),  # task only → True
        (False, True, True),  # runkey only → True
        (True, True, True),  # both → True
    ],
    ids=["neither", "task-only", "runkey-only", "both"],
)
def test_is_autostart_enabled_windows_either_mechanism(
    monkeypatch,
    fake_winreg,
    win32_platform,
    task_registered,
    runkey_registered,
    expected,
):
    """``is_autostart_enabled()`` returns True if EITHER the Task Scheduler"""
    server_platform = win32_platform
    from voice_typer.server.server_platform import autostart_windows

    monkeypatch.setattr(
        autostart_windows,
        "_is_app_autostart_task_registered",
        lambda: task_registered,
    )
    monkeypatch.setattr(
        autostart_windows,
        "_is_app_autostart_runkey_registered",
        lambda: runkey_registered,
    )

    assert server_platform.is_autostart_enabled() is expected


def test_tauri_conf_has_bundle_windows_or_nsis_msi_defaults():
    """Windows per Tauri v2 defaults)."""
    assert TAURI_CONF.exists(), f"tauri.conf.json missing at {TAURI_CONF}"
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))

    assert "bundle" in conf, "must have a top-level bundle block"
    bundle = conf["bundle"]
    assert bundle.get("active") is True, "bundle.active must be true"

    # Either explicit bundle.windows OR targets includes nsis/msi.
    has_windows = "windows" in bundle
    targets = bundle.get("targets")
    targets_include_nsis_msi = targets == "all" or (
        isinstance(targets, list) and ("nsis" in targets or "msi" in targets)
    )
    assert has_windows or targets_include_nsis_msi, (
        "bundle.windows must exist OR bundle.targets must include nsis/msi "
        f"(got targets={targets!r}, has_windows={has_windows})"
    )


def test_installer_includes_sidecar_and_native_resources():
    """native key-listener exes (``resources``) so the Tauri app can spawn"""
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    bundle = conf["bundle"]

    external_bin = bundle.get("externalBin", [])
    assert "bin/python-sidecar" in external_bin, (
        "externalBin must include bin/python-sidecar (Tauri appends the target triple to find the per-platform binary)"
    )

    # Native key-listener for Windows (ADR-0020 §6.4).
    resources = bundle.get("resources", [])
    resources_blob = "\n".join(resources)
    assert "native/windows-key-listener.exe" in resources_blob, (
        "resources must include native/windows-key-listener.exe "
        "(ADR-0020 §6.4, compiled WH_KEYBOARD_LL hook for dictation toggle)"
    )


def test_installer_creates_start_menu_and_desktop_shortcuts():
    """The NSIS installer creates Start Menu + Desktop shortcuts."""
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    bundle = conf.get("bundle", {})

    # No explicit NSIS override that would disable shortcuts → defaults apply
    windows_cfg = bundle.get("windows", {})
    nsis_cfg = windows_cfg.get("nsis", {})
    # If installMode is set, it must be "currentUser" (per-user, no admin —
    if "installMode" in nsis_cfg:
        assert nsis_cfg["installMode"] == "currentUser", (
            "NSIS installMode must be currentUser (per-user, no admin, matches voice-typer.manifest asInvoker)"
        )
    # No explicit shortcut suppression (Tauri v2 has no such key, but guard
    assert "nsis" not in windows_cfg or not nsis_cfg.get("disableShortcuts", False), (
        "NSIS shortcuts must not be disabled"
    )

    # The runtime shortcut creator exists in server_platform (creates
    from voice_typer.server import server_platform

    assert hasattr(server_platform, "create_launcher_shortcut"), (
        "server_platform must have create_launcher_shortcut() for runtime .lnk creation (Desktop + Start Menu)"
    )
    assert hasattr(server_platform, "_start_menu_programs_dir"), (
        "server_platform must have _start_menu_programs_dir() helper "
        "(used by create_launcher_shortcut to place the Start Menu .lnk)"
    )


def test_single_instance_plugin_enforced():
    """Single-instance is enforced via ``tauri-plugin-single-instance``."""
    # 1. tauri.conf.json declares the plugin.
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    plugins = conf.get("plugins", {})
    assert "single-instance" in plugins, "plugins.single-instance must be declared in tauri.conf.json"

    # 2. Cargo.toml has the dependency.
    assert CARGO_TOML.exists(), f"Cargo.toml missing at {CARGO_TOML}"
    cargo = CARGO_TOML.read_text(encoding="utf-8")
    assert "tauri-plugin-single-instance" in cargo, "Cargo.toml must depend on tauri-plugin-single-instance"

    # 3. main.rs registers the plugin FIRST + focuses the main window.
    assert MAIN_RS.exists(), f"main.rs missing at {MAIN_RS}"
    main_rs = MAIN_RS.read_text(encoding="utf-8")
    assert "tauri_plugin_single_instance::init" in main_rs, (
        "main.rs must call tauri_plugin_single_instance::init() to register the single-instance plugin"
    )
    first_plugin_idx = main_rs.find(".plugin(")
    single_instance_idx = main_rs.find(".plugin(tauri_plugin_single_instance::init")
    assert first_plugin_idx != -1, "no .plugin() calls found in main.rs"
    assert single_instance_idx != -1, "single-instance plugin registration not found"
    assert single_instance_idx == first_plugin_idx, (
        "single-instance must be the FIRST .plugin() call in the Builder "
        "chain (ADR-0020 §12 ordering) so a second launch doesn't spawn a "
        "zombie sidecar before the duplicate check runs"
    )
    # The sidecar spawn happens inside the .setup() hook (which calls
    setup_idx = main_rs.find(".setup(")
    if setup_idx != -1:
        assert single_instance_idx < setup_idx, (
            "single-instance plugin must be registered BEFORE the .setup() "
            "hook (where the sidecar is spawned), ADR-0020 §12 ordering"
        )

    # wiring-only (C-ARCH-1) and just calls it, so this test now asserts
    assert "show_main_window" in main_rs, (
        "single-instance callback must raise the existing main window via "
        "the shared `host_events::show_main_window` routine (MO-109)"
    )
    host_events_rs = (_REPO_ROOT / "src-tauri" / "src" / "host_events.rs").read_text(encoding="utf-8")
    for token in ("webview_windows", "set_focus", '"main"'):
        assert token in host_events_rs, (
            f"shared raise routine must contain {token!r} (show + focus the "
            "'main' window label: second launch → focus first, no duplicate "
            "window)"
        )
