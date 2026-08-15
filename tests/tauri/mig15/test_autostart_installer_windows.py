"""MIG-1.5 Phase 0-W Gate Check 10 (+1): Windows autostart + installer validation.

Tests the Windows autostart mechanism (Task Scheduler LogonTrigger + HKCU Run
key fallback) and the Tauri NSIS/MSI installer configuration. The autostart
logic lives in ``voice_typer/server/server_platform.py`` (cross-platform
facade) and ``voice_typer/server/task_scheduler.py`` (schtasks wrappers
reused by the autostart path). The installer config is in
``src-tauri/tauri.conf.json``.

AUTOSTART ARCHITECTURE (actual implementation)
----------------------------------------------
The app autostart (``server_platform._enable_autostart_windows``) tries the
HKCU Run key FIRST (no admin elevation needed) and falls back to Task
Scheduler (``_register_app_autostart_task`` which builds a LogonTrigger XML
and calls ``task_scheduler._schtasks /Create``) only if the Run key fails
(AUTOSTART-UAC-FIX). (The former prewarm scheduled task was deleted with
the prewarm binary it launched — master plan §6.2 P-1.)

Both paths:
  - Use ``LogonTrigger`` (fires at user logon, not boot — interactive session
    required for ``InteractiveToken`` + ``pythonw.exe``).
  - Run as the current user with ``LeastPrivilege`` (NO admin elevation).
  - Omit ``<UserId>`` so the task defaults to the registering (HKCU) user.
  - Have an admin-free HKCU Run key fallback for the locked-task scenario.

VALIDATE ON WINDOWS HOST:
1. Build the installer: cd src-tauri; cargo tauri build --target x86_64-pc-windows-msvc
2. Install target\\x86_64-pc-windows-msvc\\release\\bundle\\nsis\\*-setup.exe
3. Verify Start Menu shortcut: "Voice Typer" appears under Start Menu
4. Launch Voice Typer → enable autostart via Settings
5. Run: schtasks /query /tn "com.voicetyper.autostart*" /v /fo LIST
   Expected: Trigger=At logon; Action=voice-typer-tauri.exe
   (Note: the task name includes an 8-char install-path hash suffix,
   e.g. com.voicetyper.autostart_a1b2c3d4 — use the wildcard form above.)
6. Sign out + sign back in → verify Voice Typer auto-launches
7. Launch a second instance → verify it focuses the first (single-instance plugin)
8. Uninstall via "Add or remove programs" → verify schtasks entry + Start Menu
   shortcut are removed
Expected: autostart works; single-instance works; uninstall cleans up

TEST-HOST NOTES
---------------
On the Linux test host, ``winreg`` is unavailable and ``sys.platform !=
"win32"``, so the Windows-only code paths are exercised by installing a fake
``winreg`` module in ``sys.modules`` and monkeypatching ``sys.platform`` +
``server_platform.SYSTEM`` to ``"win32"``. The Tauri config / Cargo.toml /
main.rs source-inspection tests read the real files (no mocking).
"""

from __future__ import annotations

import json
import sys
import types
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Paths to real source files (source-inspection, NOT mocked) ──────────

# tests/tauri/mig15/test_autostart_installer_windows.py → repo root in 3 parents:
#   parents[0]=mig15, parents[1]=tauri, parents[2]=tests, parents[3]=voice-typer.
_REPO_ROOT = Path(__file__).resolve().parents[3]
TAURI_CONF = _REPO_ROOT / "src-tauri" / "tauri.conf.json"
SRC_TAURI_DIR = TAURI_CONF.parent
CARGO_TOML = SRC_TAURI_DIR / "Cargo.toml"
MAIN_RS = SRC_TAURI_DIR / "src" / "main.rs"

# XML namespace for Task Scheduler definitions.
_TASK_NS = {"ms": "http://schemas.microsoft.com/windows/2004/02/mit/task"}


# ─── fixtures: fake winreg + win32 platform ──────────────────────────────


@pytest.fixture
def fake_winreg(monkeypatch):
    """Install a fake ``winreg`` module so Windows code paths import cleanly.

    Returns the fake module; tests can configure its OpenKey / SetValueEx /
    QueryValueEx / DeleteValue / CloseKey / EnumValue behavior as needed
    (they start as MagicMocks so calls are no-ops by default).
    """
    fake = types.ModuleType("winreg")
    # Constants used by the production code (task_scheduler.py + server_platform.py).
    fake.HKEY_CURRENT_USER = 0x80000001
    fake.KEY_SET_VALUE = 0x0002
    fake.KEY_READ = 0x20019
    fake.KEY_ALL_ACCESS = 0xF003F
    fake.REG_SZ = 1
    # Methods — MagicMock so tests can assert call counts / configure returns.
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
    """Pretend we're on Windows for the duration of the test.

    Patches:
      - ``sys.platform`` → "win32" (used by platform_utils.is_windows)
      - ``voice_typer.server.server_platform.SYSTEM`` → "win32" (module-level
        constant read at function-call time by enable/disable/is_enabled)
      - installs ``fake_winreg`` so ``import winreg`` succeeds

    Returns the (already-imported) ``server_platform`` module.
    """
    monkeypatch.setattr(sys, "platform", "win32")
    from voice_typer.server import server_platform

    monkeypatch.setattr(server_platform, "SYSTEM", "win32")
    return server_platform


# ─── Test 1: app autostart task XML uses LogonTrigger + InteractiveToken ──


def test_task_scheduler_xml_uses_logon_trigger_no_elevation():
    """``server_platform._build_app_autostart_task_xml`` creates a Task
    Scheduler entry with a LogonTrigger for the current user,
    InteractiveToken + LeastPrivilege (no admin elevation). (The former
    prewarm task XML builder was deleted with the prewarm binary it
    launched — master plan §6.2 P-1 — leaving the app autostart task as
    the only Task Scheduler consumer.)
    """
    from voice_typer.server import server_platform

    xml_str = server_platform._build_app_autostart_task_xml()
    root = ET.fromstring(xml_str)

    # LogonTrigger (fires at user logon — NOT Boot/Event/Calendar triggers).
    triggers = root.find("ms:Triggers", _TASK_NS)
    assert triggers is not None, "XML must have <Triggers>"
    logon = triggers.find("ms:LogonTrigger", _TASK_NS)
    assert logon is not None, (
        "must use <LogonTrigger> (not BootTrigger/EventTrigger) — interactive "
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
    """``enable_autostart()`` on Windows dispatches to
    ``_enable_autostart_windows()`` (HKCU Run key + Task Scheduler fallback).
    It must NOT create a macOS LaunchAgent plist or a Linux .desktop file.

    NOTE: the actual implementation prefers the HKCU Run key FIRST (no admin
    elevation) and falls back to Task Scheduler — see ``AUTOSTART-UAC-FIX``
    in ``server_platform._enable_autostart_windows``. The Run key + Task
    Scheduler both launch ``autostart_launcher.py`` (not a plist or .desktop).
    """
    server_platform = win32_platform

    runkey_calls: list[int] = []
    task_calls: list[int] = []
    macos_calls: list[int] = []
    linux_calls: list[int] = []

    monkeypatch.setattr(
        server_platform,
        "_register_app_autostart_runkey",
        lambda: (runkey_calls.append(1), True)[1],
    )
    monkeypatch.setattr(
        server_platform,
        "_register_app_autostart_task",
        lambda: (task_calls.append(1), False)[1],
    )
    monkeypatch.setattr(
        server_platform,
        "_enable_autostart_macos",
        lambda: (macos_calls.append(1), False)[1],
    )
    monkeypatch.setattr(
        server_platform,
        "_enable_autostart_linux",
        lambda: (linux_calls.append(1), False)[1],
    )

    result = server_platform.enable_autostart()

    assert result is True
    assert len(runkey_calls) == 1, "must call _register_app_autostart_runkey on Windows (preferred path)"
    assert len(task_calls) == 0, "Task Scheduler is the FALLBACK — not called when Run key succeeds"
    assert len(macos_calls) == 0, "must NOT call _enable_autostart_macos (LaunchAgent plist) on Windows"
    assert len(linux_calls) == 0, "must NOT call _enable_autostart_linux (.desktop file) on Windows"


# ─── Test 4: enable_autostart falls back to Task Scheduler (LogonTrigger)


def test_enable_autostart_windows_falls_back_to_task_scheduler(monkeypatch, fake_winreg, win32_platform):
    """When the HKCU Run key fails, ``enable_autostart()`` falls back to the
    Task Scheduler path which builds a LogonTrigger XML and calls
    ``task_scheduler._schtasks /Create``. This is the
    "Task Scheduler LogonTrigger" half of the autostart mechanism.
    """
    server_platform = win32_platform
    from voice_typer.server import task_scheduler

    # Run key fails → Task Scheduler fallback must be tried.
    monkeypatch.setattr(server_platform, "_register_app_autostart_runkey", lambda: False)

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
        server_platform,
        "_build_app_autostart_task_xml",
        lambda: "<Task><Triggers><LogonTrigger/></Triggers></Task>",
    )

    result = server_platform.enable_autostart()

    assert result is True, "Task Scheduler fallback must succeed"
    create_calls = [c for c in schtasks_calls if "/Create" in c]
    assert len(create_calls) >= 1, "must call schtasks /Create as the Task Scheduler fallback"
    # The task name must use the com.voicetyper.autostart prefix (+ install hash).
    assert any("com.voicetyper.autostart" in " ".join(c) for c in create_calls), (
        "task name must include the com.voicetyper.autostart prefix"
    )


# ─── Test 5: disable_autostart removes both mechanisms ───────────────────


def test_disable_autostart_windows_removes_both(monkeypatch, fake_winreg, win32_platform):
    """``disable_autostart()`` on Windows removes BOTH the Task Scheduler
    entry AND the HKCU Run key fallback (so neither lingers to relaunch
    the app after the user disables autostart).
    """
    server_platform = win32_platform

    task_removed: list[int] = []
    reg_removed: list[int] = []

    monkeypatch.setattr(
        server_platform,
        "_unregister_app_autostart_task",
        lambda: (task_removed.append(1), True)[1],
    )
    monkeypatch.setattr(
        server_platform,
        "_unregister_app_autostart_runkey",
        lambda: (reg_removed.append(1), True)[1],
    )

    result = server_platform.disable_autostart()

    assert result is True
    assert len(task_removed) == 1, "must remove the Task Scheduler entry"
    assert len(reg_removed) == 1, "must remove the HKCU Run key fallback"


# ─── Test 6-9 (parametrized): is_autostart_enabled OR semantics ──────────


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
    """``is_autostart_enabled()`` returns True if EITHER the Task Scheduler
    entry OR the HKCU Run key exists. This is the OR-semantics that lets
    the Settings toggle reflect the actual state regardless of which
    mechanism succeeded at registration time.
    """
    server_platform = win32_platform
    monkeypatch.setattr(
        server_platform,
        "_is_app_autostart_task_registered",
        lambda: task_registered,
    )
    monkeypatch.setattr(
        server_platform,
        "_is_app_autostart_runkey_registered",
        lambda: runkey_registered,
    )

    assert server_platform.is_autostart_enabled() is expected


# ─── Test 10: tauri.conf.json bundle config (NSIS + MSI) ─────────────────


def test_tauri_conf_has_bundle_windows_or_nsis_msi_defaults():
    """``tauri.conf.json`` has a bundle config that produces NSIS + MSI on
    Windows. Either an explicit ``bundle.windows`` block exists, OR
    ``bundle.targets`` is ``"all"`` (which defaults to NSIS + MSI on
    Windows per Tauri v2 defaults).
    """
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


# ─── Test 11: installer bundles sidecar + prewarm + native listener ──────


def test_installer_includes_sidecar_and_native_resources():
    """The installer bundles the sidecar exe (``externalBin``) and the
    native key-listener exes (``resources``) so the Tauri app can spawn
    them at runtime without a separate Python install. (The prewarm
    binaries were removed from ``resources`` with the prewarm feature —
    master plan §6.2 P-1.)
    """
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    bundle = conf["bundle"]

    # Sidecar: externalBin (Tauri appends the target triple at runtime to
    # resolve bin/python-sidecar-x86_64-pc-windows-msvc.exe).
    external_bin = bundle.get("externalBin", [])
    assert "bin/python-sidecar" in external_bin, (
        "externalBin must include bin/python-sidecar (Tauri appends the target triple to find the per-platform binary)"
    )

    # Native key-listener for Windows (ADR-0020 §6.4).
    resources = bundle.get("resources", [])
    resources_blob = "\n".join(resources)
    assert "native/windows-key-listener.exe" in resources_blob, (
        "resources must include native/windows-key-listener.exe "
        "(ADR-0020 §6.4 — compiled WH_KEYBOARD_LL hook for dictation toggle)"
    )


# ─── Test 12: installer creates Start Menu + Desktop shortcuts ───────────


def test_installer_creates_start_menu_and_desktop_shortcuts():
    """The NSIS installer creates Start Menu + Desktop shortcuts.

    Tauri v2's NSIS bundler creates both by default. The config has no
    explicit ``bundle.windows.nsis`` override that disables them, so the
    defaults apply. Additionally, ``server_platform.create_launcher_shortcut()``
    creates runtime .lnk shortcuts (Desktop + Start Menu) pointing at the
    universal launcher (``autostart_launcher.py``) — these are separate
    from the installer shortcuts and exist so the legacy Electron path
    also has Start Menu discoverability.
    """
    conf = json.loads(TAURI_CONF.read_text(encoding="utf-8"))
    bundle = conf.get("bundle", {})

    # No explicit NSIS override that would disable shortcuts → defaults apply
    # (Start Menu + Desktop shortcuts created by the Tauri NSIS bundler).
    windows_cfg = bundle.get("windows", {})
    nsis_cfg = windows_cfg.get("nsis", {})
    # If installMode is set, it must be "currentUser" (per-user, no admin —
    # matches the runbook §5 "no admin required" expectation).
    if "installMode" in nsis_cfg:
        assert nsis_cfg["installMode"] == "currentUser", (
            "NSIS installMode must be currentUser (per-user, no admin — matches voice-typer.manifest asInvoker)"
        )
    # No explicit shortcut suppression (Tauri v2 has no such key, but guard
    # against future configs that might add one).
    assert "nsis" not in windows_cfg or not nsis_cfg.get("disableShortcuts", False), (
        "NSIS shortcuts must not be disabled"
    )

    # The runtime shortcut creator exists in server_platform (creates
    # Desktop + Start Menu .lnk files at app startup).
    from voice_typer.server import server_platform

    assert hasattr(server_platform, "create_launcher_shortcut"), (
        "server_platform must have create_launcher_shortcut() for runtime .lnk creation (Desktop + Start Menu)"
    )
    assert hasattr(server_platform, "_start_menu_programs_dir"), (
        "server_platform must have _start_menu_programs_dir() helper "
        "(used by create_launcher_shortcut to place the Start Menu .lnk)"
    )


# ─── Test 13: single-instance plugin enforced ────────────────────────────


def test_single_instance_plugin_enforced():
    """Single-instance is enforced via ``tauri-plugin-single-instance``.

    Verifies (source-inspection, no mocking):
      1. ``tauri.conf.json`` declares ``plugins.single-instance``.
      2. ``Cargo.toml`` depends on ``tauri-plugin-single-instance``.
      3. ``main.rs`` registers the plugin FIRST (before sidecar spawn —
         ADR-0020 §12 ordering requirement so a second launch doesn't
         leave a zombie sidecar).
      4. The plugin's callback focuses the existing main window (show +
         set_focus) so the second launch routes to the first instance.
    """
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
    # ADR-0020 §12: single-instance MUST be the FIRST plugin in the
    # tauri::Builder chain so its duplicate-instance check runs before any
    # sidecar spawn (which would otherwise leave a zombie python process on
    # a double-launch). Verify it's the first .plugin( call in the file.
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
    # spawn_sidecar_and_get_port). The plugin registration (in the Builder
    # chain) must precede .setup().
    setup_idx = main_rs.find(".setup(")
    if setup_idx != -1:
        assert single_instance_idx < setup_idx, (
            "single-instance plugin must be registered BEFORE the .setup() "
            "hook (where the sidecar is spawned) — ADR-0020 §12 ordering"
        )

    # 4. The callback focuses the existing main window.
    assert "get_webview_window" in main_rs and "set_focus" in main_rs, (
        "single-instance callback must show + focus the existing main "
        "window (second launch → focus first, no duplicate window)"
    )
    # The callback should reference the "main" window label (the dashboard).
    # Find the single-instance init block and check it references "main".
    init_block_end = main_rs.find("}))", single_instance_idx)
    if init_block_end != -1:
        init_block = main_rs[single_instance_idx:init_block_end]
        assert '"main"' in init_block or "'main'" in init_block, (
            "single-instance callback must target the 'main' window label"
        )
