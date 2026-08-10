@echo off
REM Voice Typer - Windows uninstaller autostart cleanup hook (S2-CR-69).
REM
REM Wrapper that invokes the Python cleanup script
REM (scripts/windows/uninstall_permissions.py) which removes:
REM   - HKCU\Software\Microsoft\Windows\CurrentVersion\Run\com.voicetyper.autostart_<hash>
REM     (per-install hash of the install path - see
REM     _run_key_name in autostart_windows.py). Pre-rename installs used
REM     VoiceTyper_<hash>; both forms are swept. This is a REGISTRY
REM     value, NOT a file, so deleteAppDataOnUninstall in
REM     electron-builder.yml does NOT remove it.
REM   - Task Scheduler tasks named "com.voicetyper.autostart<hash>"
REM     (the fallback autostart mechanism when the Run key fails).
REM   - Task Scheduler task named "com.voicetyper.prewarm" (the prewarm
REM     logon-trigger task registered by
REM     voice_typer/server/task_scheduler.py with TASK_NAME =
REM     "com.voicetyper.prewarm"). Distinct from the autostart
REM     tasks above - without cleanup it survives uninstall and Task
REM     Scheduler keeps trying to launch the (now-deleted) frozen prewarm
REM     binary at every login.
REM   - Legacy pre-rename names (VoiceTyperAutostart<hash>,
REM     VoiceTyperPrewarm) from installs that predate the com.voicetyper.*
REM     namespace rename.
REM
REM Wired in two places:
REM   1. voice_typer/client/electron-builder.yml -> nsis.include points
REM      at scripts/windows/uninstaller.nsh which defines the
REM      customUnInstall macro. The .nsh does its OWN native NSIS
REM      registry + schtasks cleanup (faster, no Python dependency at
REM      uninstall time) and then optionally calls this .bat as a
REM      belt-and-suspenders second sweep (the .nsh's native loop and
REM      the Python script use different code paths; if either has a
REM      bug, the other catches it).
REM   2. src-tauri/tauri.conf.json -> bundle.windows.nsis.installerHooks
REM      points at scripts/windows/uninstaller.nsh (NOT this .bat):
REM      Tauri v2's NSIS installerHooks are `!include`d into the
REM      generated installer.nsi, and NSIS cannot `!include` a batch
REM      file (makensis aborts with "Invalid command: @echo"). The .nsh
REM      performs the native sweep directly, so this .bat is NOT part
REM      of the Tauri uninstall path.
REM
REM Python-first strategy: try the Python script first (preferred - it
REM shares parsing/logging with the production autostart_windows.py). If
REM Python is not on PATH (rare - the bundled PyInstaller exe is gone by
REM uninstall time, but a system Python install may still exist), fall
REM back to a native PowerShell sweep that does NOT need Python.
REM
REM Exit codes: 0 on success or best-effort completion; non-zero only on
REM TOTAL failure (neither Python nor PowerShell available). The NSIS
REM uninstaller ignores the exit code (the .nsh wraps the call in
REM nsExec::ExecToLog which does NOT propagate the exit code).
REM
REM VALIDATE ON WINDOWS HOST:
REM   1. Build the NSIS installer:
REM        cd voice_typer\client && npm run build:win
REM   2. Install the resulting *-setup.exe.
REM   3. Launch Voice Typer -> enable autostart via Settings.
REM   4. Verify the Run key is present:
REM        reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run | findstr com.voicetyper
REM   5. Uninstall via "Add or remove programs".
REM   6. After uninstall completes, verify the Run key is GONE:
REM        reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run | findstr com.voicetyper
REM        (Expected: no matches)

setlocal enabledelayedexpansion

REM Resolve the directory of THIS .bat (so the script works regardless
REM of the caller's cwd - NSIS runs the .bat from $INSTDIR, Tauri runs
REM it from the bundle resources dir).
set "SCRIPT_DIR=%~dp0"
REM Strip the trailing backslash from %~dp0.
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "PY_SCRIPT=%SCRIPT_DIR%\uninstall_permissions.py"

REM ── Path 1: Python ───────────────────────────────────────────────────
REM Try `python` first (user install), then `py` (the official Python
REM Launcher for Windows, installed with python.org installers).
where python >nul 2>nul
if %errorlevel%==0 (
    python "%PY_SCRIPT%" %*
    if !errorlevel!==0 (
        echo [voice-typer-uninstall] Python cleanup completed.
        exit /b 0
    )
    echo [voice-typer-uninstall] Python script exited with code !errorlevel! - falling back to PowerShell sweep.
) else (
    echo [voice-typer-uninstall] python.exe not found on PATH - trying py launcher.
)

where py >nul 2>nul
if %errorlevel%==0 (
    py "%PY_SCRIPT%" %*
    if !errorlevel!==0 (
        echo [voice-typer-uninstall] Python cleanup completed (via py launcher).
        exit /b 0
    )
    echo [voice-typer-uninstall] py script exited with code !errorlevel! - falling back to PowerShell sweep.
) else (
    echo [voice-typer-uninstall] py launcher not found - falling back to PowerShell sweep.
)

REM ── Path 2: native PowerShell sweep (no Python) ─────────────────────
REM reg.exe does NOT support wildcards in /v, so we use PowerShell's
REM Remove-ItemProperty which DOES support -Name wildcards. Same
REM pipeline as the Python fallback in uninstall_permissions.py.
echo [voice-typer-uninstall] Running native PowerShell registry sweep...

powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
    "$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run';" ^
    "Get-ItemProperty -Path $runKey -ErrorAction SilentlyContinue |" ^
    "Get-Member -MemberType NoteProperty |" ^
    "Where-Object { $_.Name -like 'VoiceTyper*' -or $_.Name -like 'com.voicetyper*' } |" ^
    "ForEach-Object {" ^
    "  Remove-ItemProperty -Path $runKey -Name $_.Name -ErrorAction SilentlyContinue;" ^
    "  Write-Output ('Removed HKCU Run key: ' + $_.Name)" ^
    "}"

echo [voice-typer-uninstall] Running native PowerShell Task Scheduler sweep...

REM Sweep widened from 'VoiceTyperAutostart*' to 'VoiceTyper*' so it ALSO
REM catches the prewarm task `VoiceTyperPrewarm` (registered by
REM voice_typer/server/task_scheduler.py with TASK_NAME = "VoiceTyperPrewarm"),
REM not just the autostart fallback tasks `VoiceTyperAutostart_<hash>`.
REM The union with 'com.voicetyper*' covers the current canonical
REM reverse-DNS names (com.voicetyper.autostart_<hash>,
REM com.voicetyper.prewarm) from installs that postdate the namespace
REM rename.
powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command ^
    "Get-ScheduledTask -TaskName 'VoiceTyper*','com.voicetyper*' -ErrorAction SilentlyContinue |" ^
    "ForEach-Object {" ^
    "  schtasks.exe /Delete /TN $_.TaskName /F;" ^
    "  Write-Output ('Removed Task Scheduler task: ' + $_.TaskName)" ^
    "}"

REM Belt-and-suspenders: explicit delete of the prewarm task name in case
REM the wildcard sweep above missed it (e.g. PowerShell Get-ScheduledTask
REM wildcard behavior differs across Windows versions). /F = force (no
REM prompt). Non-fatal if the task is already gone. Both the current
REM canonical name and the pre-rename legacy name are deleted.
schtasks.exe /Delete /TN "com.voicetyper.prewarm" /F >nul 2>nul
schtasks.exe /Delete /TN "VoiceTyperPrewarm" /F >nul 2>nul
echo [voice-typer-uninstall] Explicit prewarm task delete attempted (best-effort).

REM Optional --purge: remove %APPDATA%\voice-typer if VOICE_TYPER_PURGE=1.
if /i "%VOICE_TYPER_PURGE%"=="1" (
    echo [voice-typer-uninstall] VOICE_TYPER_PURGE=1 - removing user data at %APPDATA%\voice-typer
    if exist "%APPDATA%\voice-typer" (
        rmdir /s /q "%APPDATA%\voice-typer"
        echo [voice-typer-uninstall] Removed user data directory.
    ) else (
        echo [voice-typer-uninstall] No user data directory to remove.
    )
)

echo [voice-typer-uninstall] Done (native sweep).
exit /b 0
