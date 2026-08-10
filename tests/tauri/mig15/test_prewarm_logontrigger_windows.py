"""MIG-1.5 Phase 0-W Gate Check 8 — prewarm LogonTrigger validation (Windows).

This test file validates the *structure* of the Windows prewarm scheduling
path from the Linux sandbox. The actual ``**VALIDATE ON WINDOWS HOST**``
commands documented below MUST be run on a real Windows host — these
tests only verify the code paths they reference behave as ADR-0020 §5
and the windows-validation-runbook §6.7 require.

Coverage map (8 tests, one per gate-check criterion):

1. ``test_resolve_prewarm_exe_finds_frozen_exe_on_windows`` —
   ``prewarm_resolver.resolve_prewarm_exe()`` finds the frozen
   ``prewarm-x86_64-pc-windows-msvc.exe`` on Windows.
2. ``test_resolver_priority_env_var_then_bundle_resource_then_dev_fallback`` —
   resolver priority order: ``VOICE_TYPER_PREWARM_EXE`` env var →
   Tauri bundle resource path → dev fallback (Python module invocation).
3. ``test_task_xml_uses_logon_trigger_not_boot_or_daily`` —
   Task Scheduler XML uses ``LogonTrigger`` (NOT ``BootTrigger``,
   ``DailyTrigger``, or ``EventTrigger``).
4. ``test_register_prewarm_task_uses_frozen_exe_not_pythonw`` —
   when the frozen exe is available, the schtasks /Create XML action
   runs ``prewarm-*.exe`` directly (NOT
   ``pythonw.exe -m voice_typer.server.prewarm``). Mocks
   ``subprocess.run`` for the ``schtasks /Create`` call.
5. ``test_task_xml_principal_uses_hkcu_no_elevation`` —
   Task Scheduler XML Principal uses ``InteractiveToken`` +
   ``LeastPrivilege`` with no ``<UserId>`` (defaults to current user =
   HKCU-equivalent, no admin).
6. ``test_hkcu_run_key_fallback_writes_to_hkey_current_user`` —
   the HKCU Run-key fallback writes to ``HKEY_CURRENT_USER\\...\\Run``
   (per-user, no elevation required).
7. ``test_prewarm_warms_ct2_cache_via_file_reads_not_imports`` —
   the warming pipeline reads CT2 model files via ``_warm_file``
   (sequential file reads), NOT by importing ``ctranslate2``
   (ADR-0020 §5: "frozen the same Nuitka way… warms the OS file cache").
8. ``test_prewarm_run_writes_pid_file_and_signals_completion_event`` —
   ``run()`` writes a PID file + signals a named completion event so
   the app's ``wait_for_prewarm()`` can wait without polling.

Platform checks are monkeypatched to ``win32`` so the Windows code paths
are exercised on the Linux test host. ``subprocess.run`` is mocked so no
real ``schtasks`` call is made.

VALIDATE ON WINDOWS HOST:
1. Launch Voice Typer (creates the LogonTrigger task)
2. Run: schtasks /query /tn "com.voicetyper.prewarm" /v /fo LIST
   Expected: TaskName=com.voicetyper.prewarm; Trigger=At logon; Action=prewarm-x86_64-pc-windows-msvc.exe
3. Sign out + sign back in (OR run: schtasks /run /tn "com.voicetyper.prewarm")
4. Check %APPDATA%\\voice-typer\\logs\\prewarm.log for:
   - "[PREWARM] starting (frozen exe)"
   - "[PREWARM] warming CT2 cache for small.en"
   - "[PREWARM] completed in X.Xs"
5. Verify CT2 model files are warm in %APPDATA%\\voice-typer\\models\\
Expected: prewarm completes within 30s of logon; model cache warm

References:
- ADR-0020 §5 (Prewarm packaging — frozen same Nuitka way as sidecar)
- docs/migration/windows-validation-runbook.md §6.7 (Prewarm LogonTrigger)
- voice_typer/server/prewarm_resolver.py
- voice_typer/server/task_scheduler.py
- voice_typer/server/prewarm.py
- scripts/build/build_prewarm_windows.sh
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ─── Windows platform mocking fixture ────────────────────────────────────


@pytest.fixture(autouse=True)
def _force_windows(monkeypatch):
    """Pretend we're on Windows for every test in this module.

    The Windows prewarm scheduling path (``task_scheduler.py``) is guarded
    by ``is_windows()``. On the Linux test host, we monkeypatch
    ``sys.platform`` + the ``is_windows`` / ``is_macos`` / ``is_linux``
    helpers (both in ``platform_utils`` and the bound names imported into
    ``task_scheduler`` / ``prewarm_resolver`` / ``prewarm``) to exercise
    the Windows code paths.

    ``task_scheduler.is_supported()`` is also mocked to ``True`` so the
    ``is_supported()``-guarded code paths execute (on Linux, the real
    ``is_supported()`` would return False because
    ``C:\\Windows\\System32\\schtasks.exe`` doesn't exist).
    """
    monkeypatch.setattr(sys, "platform", "win32")
    from voice_typer.server import (
        platform_utils,
        prewarm,
        prewarm_resolver,
        task_scheduler,
    )

    # Patch the source module.
    monkeypatch.setattr(platform_utils, "is_windows", lambda: True)
    monkeypatch.setattr(platform_utils, "is_macos", lambda: False)
    monkeypatch.setattr(platform_utils, "is_linux", lambda: False)
    # Patch the bound names imported into each consumer module.
    for mod in (task_scheduler, prewarm_resolver, prewarm):
        monkeypatch.setattr(mod, "is_windows", lambda: True)
        monkeypatch.setattr(mod, "is_macos", lambda: False)
        monkeypatch.setattr(mod, "is_linux", lambda: False)
    # is_supported() checks for schtasks.exe — mock to True.
    monkeypatch.setattr(task_scheduler, "is_supported", lambda: True)
    # Remove _MEIPASS so the resolver doesn't append a _MEIPASS candidate
    # (which could shadow the paths we set up in tmp_path).
    monkeypatch.delattr(sys, "_MEIPASS", raising=False)
    # Clear the prewarm env override so tests start from a clean slate.
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE", raising=False)
    monkeypatch.delenv("TAURI_SIDECAR", raising=False)


# ─── §1. prewarm_resolver.resolve_prewarm_exe() ──────────────────────────


def test_resolve_prewarm_exe_finds_frozen_exe_on_windows(monkeypatch, tmp_path):
    """resolve_prewarm_exe() finds prewarm-x86_64-pc-windows-msvc.exe on Windows.

    ADR-0020 §5: the prewarm binary is frozen the same Nuitka way as the
    sidecar, into ``prewarm-<target-triple>[.exe]``. On Windows x86_64
    the triple is ``x86_64-pc-windows-msvc`` so the frozen exe name is
    ``prewarm-x86_64-pc-windows-msvc.exe``. The resolver must find it at
    the bundle resource path (``%LOCALAPPDATA%\\Programs\\VoiceTyper\\
    resources\\prewarm-<triple>.exe``) when no env override is set.
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver

    # Force platform.machine() to return 'AMD64' (Windows x86_64).
    monkeypatch.setattr(_platform, "machine", lambda: "AMD64")

    # Create a real frozen exe at the bundle resource path
    # (%LOCALAPPDATA%\Programs\VoiceTyper\resources\prewarm-<triple>.exe).
    resources_dir = tmp_path / "Programs" / "VoiceTyper" / "resources"
    resources_dir.mkdir(parents=True)
    frozen_exe = resources_dir / "prewarm-x86_64-pc-windows-msvc.exe"
    frozen_exe.write_bytes(b"\x4d\x5a")  # MZ header (PE signature)

    # Point LOCALAPPDATA at tmp_path so _candidate_paths finds the file.
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    # Mock sys.executable to a non-existent tmp_path so the
    # exe_dir/name candidate doesn't shadow our frozen exe.
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is not None
    assert result == str(frozen_exe), f"resolve_prewarm_exe() should find the frozen exe at {frozen_exe}, got {result}"
    # Verify the filename matches the expected Windows triple.
    assert "prewarm-x86_64-pc-windows-msvc.exe" in result, (
        "Frozen exe name must be 'prewarm-x86_64-pc-windows-msvc.exe' "
        "(matches the Rust target triple for Windows x86_64 per ADR-0020 §4.1)"
    )


def test_resolver_priority_env_var_then_bundle_resource_then_dev_fallback(monkeypatch, tmp_path):
    """Resolver priority: VOICE_TYPER_PREWARM_EXE → bundle resource → dev fallback.

    ADR-0020 §5 resolution order:
      1. ``VOICE_TYPER_PREWARM_EXE`` env var (preferred — set by the Tauri
         host at startup to ``resourceDir/prewarm-<triple>``).
      2. Tauri resource dir, heuristically:
         - Windows: ``%LOCALAPPDATA%\\Programs\\VoiceTyper\\resources\\prewarm-<triple>.exe``
      3. Dev fallback: ``python -m voice_typer.server.prewarm`` (source-tree dev).
    """
    import platform as _platform

    from voice_typer.server import prewarm_resolver

    monkeypatch.setattr(_platform, "machine", lambda: "AMD64")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python.exe"))

    # ── Priority 1: VOICE_TYPER_PREWARM_EXE env var ────────────────────
    env_exe = tmp_path / "env-override-prewarm.exe"
    env_exe.write_bytes(b"\x4d\x5a")
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(env_exe))

    # Also create the bundle resource path (priority 2) to verify it's NOT used.
    resources_dir = tmp_path / "Programs" / "VoiceTyper" / "resources"
    resources_dir.mkdir(parents=True)
    bundle_exe = resources_dir / "prewarm-x86_64-pc-windows-msvc.exe"
    bundle_exe.write_bytes(b"\x4d\x5a")

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result == str(env_exe), (
        f"Priority 1: VOICE_TYPER_PREWARM_EXE should win over the bundle resource path, got {result}"
    )

    # ── Priority 2: bundle resource path (env var unset) ──────────────
    monkeypatch.delenv("VOICE_TYPER_PREWARM_EXE")
    result = prewarm_resolver.resolve_prewarm_exe()
    assert result == str(bundle_exe), (
        f"Priority 2: bundle resource path should be used when env var is unset, got {result}"
    )

    # ── Priority 3: dev fallback (no frozen exe found) ────────────────
    # Remove the bundle exe and mock _candidate_paths to return only a
    # nonexistent path (avoids accidentally finding a real prewarm exe
    # next to sys.executable in the test env).
    bundle_exe.unlink()
    monkeypatch.setattr(
        prewarm_resolver,
        "_candidate_paths",
        lambda: [tmp_path / "nonexistent"],
    )

    result = prewarm_resolver.resolve_prewarm_exe()
    assert result is not None
    assert " -m voice_typer.server.prewarm" in result, (
        f"Priority 3: dev fallback should be a Python module invocation, got {result}"
    )


# ─── §2. Task Scheduler XML — LogonTrigger ───────────────────────────────


def test_task_xml_uses_logon_trigger_not_boot_or_daily():
    """_build_task_xml emits a single LogonTrigger, no BootTrigger/DailyTrigger/EventTrigger.

    PREWARM-FIX (task_scheduler.py): a single LogonTrigger fires when the
    user logs on — an interactive session now exists, so the InteractiveToken
    principal + pythonw/frozen-exe can launch. The earlier design used
    BootTrigger + EventTrigger (fire pre-logon), which is fundamentally
    incompatible with InteractiveToken (the task sat at Last Result
    0x41303 "never run"). A LogonTrigger is the only reliable trigger
    for this task configuration.

    DailyTrigger would fire every day at a fixed time (not at logon),
    which doesn't match the "prewarm at login" contract.
    """
    from voice_typer.server import task_scheduler

    xml = task_scheduler._build_task_xml("C:\\frozen\\prewarm-x86_64-pc-windows-msvc.exe", "")

    # LogonTrigger present (fires at user logon).
    assert "LogonTrigger" in xml, (
        "PREWARM-FIX regression: LogonTrigger is missing — prewarm won't run at logon (its only reliable trigger)"
    )
    # BootTrigger / EventTrigger / DailyTrigger MUST be absent.
    assert "BootTrigger" not in xml, (
        "BootTrigger is present — it fires pre-logon where the InteractiveToken "
        "task cannot start (Last Result 0x41303 'never run')"
    )
    assert "EventTrigger" not in xml, (
        "EventTrigger is present — it fires pre-logon where the InteractiveToken task cannot start"
    )
    assert "DailyTrigger" not in xml, (
        "DailyTrigger is present — daily-fire doesn't match the 'prewarm at "
        "login' contract (LogonTrigger is the correct trigger)"
    )
    # LogonTrigger must carry a <Delay> element (STARTUP-2: _LOGON_DELAY,
    # PT0S — fire at logon+0 so prewarm gets a head start).
    assert "<Delay>" in xml, "LogonTrigger <Delay> is missing"


def test_register_prewarm_task_uses_frozen_exe_not_pythonw(monkeypatch, tmp_path):
    """When the frozen exe is available, register_prewarm_task() creates a
    Task Scheduler entry whose action runs prewarm-*.exe directly (NOT
    pythonw.exe -m voice_typer.server.prewarm).

    ADR-0020 §5: when the resolver returns a frozen exe path (no
    ``-m voice_typer.server.prewarm`` module args), the task action is
    just the exe path itself — NO ``<Arguments>`` element is emitted
    (the frozen exe IS the module). This test mocks ``subprocess.run``
    so no real ``schtasks /Create`` call is made; it captures the temp
    XML file content written by ``register_prewarm_task`` and asserts
    against it.
    """
    from voice_typer.server import prewarm_resolver, task_scheduler

    # Create a real frozen exe file (so resolve_prewarm_exe returns a path).
    frozen_exe = tmp_path / "prewarm-x86_64-pc-windows-msvc.exe"
    frozen_exe.write_bytes(b"\x4d\x5a")  # MZ header (PE signature)

    # Set the env vars that trigger the Tauri sidecar path.
    monkeypatch.setenv("TAURI_SIDECAR", "1")
    monkeypatch.setenv("VOICE_TYPER_PREWARM_EXE", str(frozen_exe))

    # Mock the resolver to return our frozen exe path (avoids the real
    # _candidate_paths lookup which might not find the file under the
    # mocked Windows platform).
    monkeypatch.setattr(prewarm_resolver, "resolve_prewarm_exe", lambda: str(frozen_exe))

    # Capture subprocess.run calls. For /Create, read the temp XML file
    # so we can assert against its contents.
    captured_xml: list[str] = []
    captured_calls: list[list[str]] = []

    def fake_run(cmd, **kw):
        captured_calls.append(list(cmd))
        # _schtasks wraps subprocess.run with ["schtasks"] + args.
        if "/Create" in cmd:
            xml_idx = cmd.index("/XML")
            temp_xml_path = cmd[xml_idx + 1]
            try:
                with open(temp_xml_path, encoding="utf-8") as f:
                    captured_xml.append(f.read())
            except OSError:
                pass
        r = MagicMock()
        r.returncode = 0
        r.stdout = "SUCCESS: Scheduled task created."
        r.stderr = ""
        return r

    monkeypatch.setattr(subprocess, "run", fake_run)
    # Prevent the HKCU Run-key cleanup from being touched on the real host.
    monkeypatch.setattr(task_scheduler, "_is_prewarm_registered_registry", lambda: False)
    monkeypatch.setattr(task_scheduler, "_unregister_prewarm_registry", lambda: True)

    result = task_scheduler.register_prewarm_task()
    assert result is True, "register_prewarm_task() should return True on schtasks /Create success"

    # ── Verify the schtasks /Create call shape ────────────────────────
    create_calls = [c for c in captured_calls if "/Create" in c]
    assert len(create_calls) == 1, (
        f"Expected exactly 1 schtasks /Create call, got {len(create_calls)}: {captured_calls}"
    )
    create_cmd = create_calls[0]
    assert create_cmd[0] == "schtasks", f"First token must be 'schtasks', got {create_cmd[0]}"
    assert "/Create" in create_cmd
    assert "/TN" in create_cmd
    assert task_scheduler.TASK_NAME in create_cmd, (
        f"Task name {task_scheduler.TASK_NAME!r} not in /Create cmd: {create_cmd}"
    )
    assert "/XML" in create_cmd
    assert "/F" in create_cmd, "/F (force overwrite) flag is missing from /Create cmd"

    # ── Verify the XML action runs the frozen exe, NOT pythonw ────────
    assert len(captured_xml) == 1, f"Expected 1 captured XML, got {len(captured_xml)}"
    xml = captured_xml[0]

    # The frozen exe path must be the <Command> element value.
    assert str(frozen_exe) in xml, (
        f"Frozen exe path {frozen_exe} not found in task XML — the task action must run the frozen exe directly"
    )
    # The XML must NOT contain pythonw.exe or the Python module invocation.
    assert "pythonw.exe" not in xml, (
        "Task XML contains 'pythonw.exe' — when the frozen exe is available, "
        "the task action should run prewarm-*.exe directly, NOT pythonw.exe"
    )
    assert "-m voice_typer.server.prewarm" not in xml, (
        "Task XML contains '-m voice_typer.server.prewarm' — when the frozen "
        "exe is available, no <Arguments> element should be emitted (the "
        "frozen exe IS the module, per ADR-0020 §5)"
    )
    # The <Arguments> element should NOT be present (frozen exe takes no args).
    assert "<Arguments>" not in xml, (
        "<Arguments> element present in task XML — the frozen exe takes no "
        "module args (it IS the module); _build_task_xml must omit "
        "<Arguments> when called with arguments=''"
    )
    # LogonTrigger must still be present (the frozen-exe path doesn't change
    # the trigger type).
    assert "LogonTrigger" in xml


# ─── §3. HKCU (current user, no elevation) ───────────────────────────────


def test_task_xml_principal_uses_hkcu_no_elevation():
    """Task Scheduler XML Principal uses InteractiveToken + LeastPrivilege.

    The Principal has:
    - ``<LogonType>InteractiveToken</LogonType>`` — runs in the current
      user's interactive session (no password prompt, no admin token).
    - ``<RunLevel>LeastPrivilege</RunLevel>`` — no UAC elevation.
    - NO ``<UserId>`` element — defaults to the registering (current)
      user, which is the HKCU-equivalent (per-user, no admin) config.

    This is the Task Scheduler equivalent of "created under HKCU": the
    task lives under the current user's scope and never requires
    elevation. Combined with the HKCU Run-key fallback (next test),
    this satisfies the "current user, no elevation required" gate.
    """
    from voice_typer.server import task_scheduler

    xml = task_scheduler._build_task_xml("C:\\frozen\\prewarm-x86_64-pc-windows-msvc.exe", "")

    assert "<LogonType>InteractiveToken</LogonType>" in xml, (
        "Principal must use InteractiveToken (current user's interactive session, no admin token, no password prompt)"
    )
    assert "<RunLevel>LeastPrivilege</RunLevel>" in xml, (
        "Principal must use LeastPrivilege (no UAC elevation required — "
        "the prewarm task must register without an admin prompt)"
    )
    # No <UserId> element means the task runs as the registering (current)
    # user. Combined with InteractiveToken + LeastPrivilege, this is the
    # HKCU-equivalent (per-user, no admin) configuration.
    assert "<UserId>" not in xml, (
        "<UserId> must be absent so the task defaults to the current user "
        "(HKCU-equivalent, no admin needed) — a populated <UserId> with a "
        "different SID would require admin to register"
    )


def test_hkcu_run_key_fallback_writes_to_hkey_current_user(monkeypatch):
    """The HKCU Run-key fallback writes to HKEY_CURRENT_USER (per-user, no admin).

    When ``schtasks /Create`` fails (e.g. a previously-registered task got
    locked), ``task_scheduler._register_prewarm_registry()`` falls back to
    the per-user ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run``
    registry key. HKCU is user-writable, so this needs NO admin privileges.

    This test injects a fake ``winreg`` module into ``sys.modules`` (the
    real ``winreg`` is Windows-only and not importable on the Linux test
    host) and verifies ``OpenKey`` is called with ``HKEY_CURRENT_USER``
    (NOT ``HKEY_LOCAL_MACHINE``, which would require elevation).
    """
    from voice_typer.server import task_scheduler

    # Inject a fake winreg module (winreg is Windows-only).
    fake_winreg = MagicMock()
    fake_winreg.HKEY_CURRENT_USER = 0x80000001  # matches the real winreg constant
    fake_winreg.HKEY_LOCAL_MACHINE = 0x80000002
    fake_winreg.KEY_SET_VALUE = 0x0002
    fake_winreg.KEY_READ = 0x20019
    fake_winreg.REG_SZ = 1
    fake_winreg.OpenKey.return_value = MagicMock()  # fake key handle
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)

    result = task_scheduler._register_prewarm_registry("test-command-string")

    assert result is True, "_register_prewarm_registry should return True on success"

    # Verify OpenKey was called with HKEY_CURRENT_USER (HKCU, not HKLM).
    fake_winreg.OpenKey.assert_called_once()
    open_args = fake_winreg.OpenKey.call_args
    assert open_args.args[0] == fake_winreg.HKEY_CURRENT_USER, (
        "HKCU Run-key fallback must use HKEY_CURRENT_USER (per-user, no admin) — "
        "HKEY_LOCAL_MACHINE would require elevation"
    )
    assert open_args.args[1] == task_scheduler._RUN_KEY, (
        f"OpenKey should target {task_scheduler._RUN_KEY!r}, got {open_args.args[1]!r}"
    )

    # Verify SetValueEx was called with the task name + command.
    fake_winreg.SetValueEx.assert_called_once()
    sv_args = fake_winreg.SetValueEx.call_args
    assert sv_args.args[1] == task_scheduler.TASK_NAME, (
        f"SetValueEx value name should be {task_scheduler.TASK_NAME!r}, got {sv_args.args[1]!r}"
    )
    assert sv_args.args[4] == "test-command-string", (
        f"SetValueEx value data should be the prewarm command, got {sv_args.args[4]!r}"
    )


# ─── §4. CT2 cache warming via file reads (not imports) ──────────────────


def test_prewarm_warms_ct2_cache_via_file_reads_not_imports(monkeypatch, tmp_path):
    """The warming pipeline reads CT2 model files via _warm_file (file reads),
    NOT by importing ctranslate2.

    ADR-0020 §5: the prewarm exe is frozen the same Nuitka way as the
    sidecar; its job is to warm the OS file cache (page in ~7 GB of
    torch + transformers + CT2 model weights) so the app's later
    ``import torch`` / ``from_pretrained()`` calls hit RAM instead of
    disk. The prewarm module docstring is explicit: "Read the installed
    torch + transformers package files into the OS page cache WITHOUT
    importing them."

    The CT2 model cache warming (the ``.bin`` / ``.safetensors`` files
    under the HF cache's ``models--Systran--faster-whisper-*`` dir) uses
    ``_warm_file`` — a sequential ``open(path, "rb")`` + ``read()`` loop
    that populates the standby list without executing any code. This test
    verifies that path is exercised and that ``ctranslate2`` is NOT
    imported by the warming pipeline.
    """
    from voice_typer.server import prewarm

    # Build a fake HF cache dir for the CT2 (faster-whisper) model.
    # Structure mirrors _run_warming_pipeline's expectations:
    #   <cache_dir>/snapshots/<hash>/model.bin
    cache_dir = tmp_path / "models--Systran--faster-whisper-small.en"
    snapshot_dir = cache_dir / "snapshots" / "abc123"
    snapshot_dir.mkdir(parents=True)
    model_file = snapshot_dir / "model.bin"
    model_file.write_bytes(b"\x00" * 1024)  # 1 KB fake CT2 model weights

    # Mock _active_model_cache_dirs to return our fake cache dir
    # (skips the real HF cache lookup + Config.load()).
    monkeypatch.setattr(prewarm, "_active_model_cache_dirs", lambda: [cache_dir])
    # Mock _warm_imports to no-op (avoids actual torch/faster_whisper imports
    # which would slow the test and might fail in the sandbox). The model
    # cache warming (the bulk of the CT2 cache work) is what we're testing.
    monkeypatch.setattr(prewarm, "_warm_imports", lambda: None)
    # Mock _mark_warmed to no-op (avoid sentinel file writes).
    monkeypatch.setattr(prewarm, "_mark_warmed", lambda _elapsed: None)

    # Spy on _warm_file to verify it's called on the model file.
    warmed_files: list[Path] = []
    original_warm_file = prewarm._warm_file

    def spy_warm_file(path: Path) -> int:
        warmed_files.append(path)
        return original_warm_file(path)

    monkeypatch.setattr(prewarm, "_warm_file", spy_warm_file)

    # Snapshot sys.modules BEFORE the pipeline runs so we can verify
    # ctranslate2 is not imported by the warming step.
    modules_before = set(sys.modules.keys())

    rc = prewarm._run_warming_pipeline(
        min_ram_mb=prewarm.DEFAULT_MIN_FREE_RAM_MB,
        force=True,
        t_start=time.perf_counter(),
    )

    # ── Verify _warm_file was called on the CT2 model file ────────────
    assert model_file in warmed_files, (
        f"_warm_file was not called on the CT2 model file {model_file} — warmed files: {warmed_files}"
    )

    # ── Verify ctranslate2 was NOT imported by the warming pipeline ──
    modules_after = set(sys.modules.keys())
    new_modules = modules_after - modules_before
    assert "ctranslate2" not in new_modules, (
        f"_run_warming_pipeline imported ctranslate2 (new modules: "
        f"{sorted(m for m in new_modules if 'trans' in m.lower() or 'ct2' in m.lower())}) "
        "— ADR-0020 §5 mandates file warming (sequential reads via "
        "_warm_file), NOT imports"
    )

    # ── Verify the pipeline succeeded ─────────────────────────────────
    assert rc == prewarm.EXIT_OK, f"_run_warming_pipeline should return EXIT_OK ({prewarm.EXIT_OK}), got {rc}"


# ─── §5. PID file + completion event ─────────────────────────────────────


def test_prewarm_run_writes_pid_file_and_signals_completion_event(monkeypatch):
    """run() writes a PID file + signals a named completion event so the
    app's wait_for_prewarm() can wait without polling.

    ADR-0009 Issue 4 + CPU-04: ``run()`` writes a PID file at the start
    of the warming phase (after all early-exit guards) and removes it in
    a finally block. It also creates a PID-scoped named event
    (``Local\\com.voicetyper.prewarm_completion_<pid>``) and signals it on
    completion. The app's ``wait_for_prewarm()`` opens the event and
    calls ``WaitForSingleObject`` (Windows) / ``pidfd_open`` (Linux) —
    a zero-CPU kernel wait — instead of polling ``is_prewarm_running()``
    every 500ms.

    This test mocks all the side-effecting functions (logging, I/O
    priority, the warming pipeline itself) and tracks the PID file +
    completion event lifecycle to verify the correct call order.
    """
    from voice_typer.server import prewarm

    # Mock all the side-effecting functions called by run().
    # ``_setup_logging`` is called with ``prewarm_only=True`` — the mock
    # must accept kwargs (a plain ``lambda: None`` raises TypeError).
    monkeypatch.setattr(prewarm, "_setup_logging", lambda *a, **kw: None)
    monkeypatch.setattr(prewarm, "_lower_io_priority", lambda *a, **kw: None)
    # Mock the warming pipeline to succeed without doing real work.
    monkeypatch.setattr(prewarm, "_run_warming_pipeline", lambda *a, **kw: prewarm.EXIT_OK)

    # Track the PID file + completion event lifecycle.
    lifecycle: list[tuple] = []

    def _track_write_pid():
        lifecycle.append(("write_pid",))

    def _track_create_event(pid: int):
        lifecycle.append(("create_event", pid))
        return 12345  # fake event handle

    def _track_signal(handle):
        lifecycle.append(("signal", handle))

    def _track_close(handle):
        lifecycle.append(("close", handle))

    def _track_remove_pid():
        lifecycle.append(("remove_pid",))

    monkeypatch.setattr(prewarm, "_write_pid_file", _track_write_pid)
    monkeypatch.setattr(prewarm, "_create_completion_event", _track_create_event)
    monkeypatch.setattr(prewarm, "_signal_completion_event", _track_signal)
    monkeypatch.setattr(prewarm, "_close_completion_event", _track_close)
    monkeypatch.setattr(prewarm, "_remove_pid_file", _track_remove_pid)

    # Also mock the early-exit guards so force=True path is exercised
    # cleanly (force=True skips them, but be defensive).
    monkeypatch.setattr(prewarm, "_fast_startup_enabled", lambda: True)
    monkeypatch.setattr(prewarm, "_already_warmed", lambda: False)

    rc = prewarm.run(force=True, trigger="logon")

    assert rc == prewarm.EXIT_OK, f"run() should return EXIT_OK, got {rc}"

    # ── Verify all lifecycle steps were called ────────────────────────
    assert ("write_pid",) in lifecycle, (
        "_write_pid_file was not called — the app's is_prewarm_running() would never see prewarm as running"
    )
    assert ("create_event", os.getpid()) in lifecycle, (
        f"_create_completion_event was not called with the current PID ({os.getpid()}); lifecycle: {lifecycle}"
    )
    assert ("signal", 12345) in lifecycle, (
        "_signal_completion_event was not called — the app's "
        "wait_for_prewarm() would block until its 60s timeout instead "
        "of returning immediately when prewarm finishes"
    )
    assert ("close", 12345) in lifecycle, "_close_completion_event was not called — the event handle leaks"
    assert ("remove_pid",) in lifecycle, (
        "_remove_pid_file was not called — a stale PID file would make "
        "is_prewarm_running() return True for a dead process"
    )

    # ── Verify the call order ─────────────────────────────────────────
    # run() order (with force=True):
    #   1. _write_pid_file  (start of warming phase)
    #   2. _create_completion_event  (start of warming phase, after PID file)
    #   3. _run_warming_pipeline  (warming work — mocked)
    #   4. _signal_completion_event  (finally block)
    #   5. _close_completion_event  (finally block)
    #   6. _remove_pid_file  (finally block)
    write_pid_idx = lifecycle.index(("write_pid",))
    create_event_idx = lifecycle.index(("create_event", os.getpid()))
    signal_idx = lifecycle.index(("signal", 12345))
    close_idx = lifecycle.index(("close", 12345))
    remove_pid_idx = lifecycle.index(("remove_pid",))

    assert write_pid_idx < create_event_idx, (
        f"_write_pid_file must happen before _create_completion_event; lifecycle: {lifecycle}"
    )
    assert create_event_idx < signal_idx, (
        f"_create_completion_event must happen before _signal_completion_event; lifecycle: {lifecycle}"
    )
    # finally-block order: signal → close → remove_pid.
    assert signal_idx < close_idx < remove_pid_idx, (
        f"finally-block order must be: signal → close → remove_pid; lifecycle: {lifecycle}"
    )
