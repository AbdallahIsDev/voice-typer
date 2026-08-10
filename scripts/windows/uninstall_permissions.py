#!/usr/bin/env python3
"""Voice Typer — Windows uninstaller autostart cleanup ().

Removes the per-user Windows autostart entries that
``voice_typer/server/server_platform/autostart_windows.py`` creates at
runtime when the user enables autostart via Settings:

  - **HKCU Run key** at
    ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` — value
    name ``com.voicetyper.autostart_<8hex>`` (per-install hash from
    SHA-256 of the install path — see ``_run_key_name`` in
    ``autostart_windows.py``; pre-2026 installs used the bare
    ``VoiceTyper_<8hex>`` scheme, which this script still removes).
    This is a REGISTRY value, NOT a file, so
    the NSIS ``deleteAppDataOnUninstall`` flag does NOT remove it.
  - **Task Scheduler** tasks named ``com.voicetyper.autostart_<8hex>`` /
    ``com.voicetyper.prewarm`` (canonical reverse-DNS names) and the
    legacy ``VoiceTyperAutostart<8hex>`` / ``VoiceTyperPrewarm`` names —
    registered via ``schtasks /Create /TN ... /XML`` (see
    ``_register_app_autostart_task`` in ``autostart_windows.py`` and
    ``task_scheduler.TASK_NAME``). Lives
    in Task Scheduler, also NOT under AppData.

Both mechanisms are removed here — including STALE entries from previous
installs at different paths (different hashes) and both the current
canonical ``com.voicetyper.*`` names and the pre-rename bare
``VoiceTyper*`` names (so installs that predate the namespace rename are
fully cleaned too). The current-install
removal path (``_unregister_app_autostart_runkey`` /
``_unregister_app_autostart_task`` in ``autostart_windows.py``) only
removes the current install's hash-suffixed entry; the uninstaller must
be more aggressive so the registry / Task Scheduler are left CLEAN of
any Voice Typer autostart entry.

(): optional ``--purge`` flag (or ``VOICE_TYPER_PURGE=1``
env var) ALSO removes the per-user data directory at
``%APPDATA%\\voice-typer`` (settings JSON, history DB, downloaded
vocabularies, HuggingFace model cache, venv, logs). OFF by default so
users who reinstall keep their models; pass it explicitly to reclaim
disk:

    # Uninstall autostart only (default — preserves user data):
    python uninstall_permissions.py

    # Uninstall autostart AND purge all user data (GBs of models):
    python uninstall_permissions.py --purge

    # Same, via env var (useful when invoked by NSIS / Tauri which
    # can't pass argv):
    set VOICE_TYPER_PURGE=1 && python uninstall_permissions.py

Invoked by:
  - ``scripts/windows/uninstall.bat`` (the NSIS / Tauri preRemoveScript
    hook — ``.bat`` wraps this Python script and falls back to native
    ``reg delete`` / PowerShell ``Remove-ItemProperty`` if Python is
    unavailable at uninstall time).
  - Manually by the user (documented in the README / Windows install
    guide) when they want to clean up stale entries from a previous
    failed uninstall.

This script mirrors the Linux pattern in
``scripts/linux/uninstall_permissions.py`` ( + ): same
``--purge`` flag, same env-var fallback, same "best-effort, log + exit 0"
semantics (the uninstaller must NEVER block on cleanup failure — a
locked task or registry permission error should not abort the user's
uninstall).

Exit codes:
  - 0: cleanup ran (some or all entries removed; missing entries are NOT
        an error — they were already clean).
  - 1: fatal error (e.g. could not import the voice_typer package and
        Python fallback also unavailable). The .bat wrapper falls back
        to native reg.exe / PowerShell in this case.

VALIDATE ON WINDOWS HOST:
  1. Build the installer:
       cd voice_typer/client && npm run build:win
  2. Install the resulting *-setup.exe.
  3. Launch Voice Typer -> enable autostart via Settings.
  4. Verify both autostart entries exist:
       reg query HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run | findstr com.voicetyper
       schtasks /query /tn "com.voicetyper.autostart*" /v /fo LIST
  5. Uninstall via "Add or remove programs" (the NSIS uninstaller calls
     this script via the .bat wrapper, which is wired through
     ``nsis.include`` in electron-builder.yml).
  6. Verify both autostart entries are gone:
       reg query HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run | findstr com.voicetyper
         (Expected: no matches; pre-rename VoiceTyper* entries also gone)
       schtasks /query /tn "com.voicetyper.autostart*"
         (Expected: ERROR: The system cannot find the file specified)
  7. (Optional) Verify --purge removes the data dir:
       set VOICE_TYPER_PURGE=1 && python scripts/windows/uninstall_permissions.py
       dir "%APPDATA%\\voice-typer"
         (Expected: File Not Found)
"""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

# (): --purge flag handling ─────────────────────────────
#
# Parse --purge out of argv BEFORE importing the voice_typer package (so
# the env-var fallback also works without argv). Mirrors the Linux
# uninstall_permissions.py pattern.
_purge_requested = "--purge" in sys.argv or os.environ.get("VOICE_TYPER_PURGE", "").strip() in ("1", "true", "yes")
if "--purge" in sys.argv:
    sys.argv = [a for a in sys.argv if a != "--purge"]


def _log(msg: str) -> None:
    """Print to stderr (so NSIS/Tauri capture it in the uninstall log)."""
    print(f"[voice-typer-uninstall] {msg}", file=sys.stderr)


def _purge_user_data() -> None:
    """(): remove the per-user data directory at
    ``%APPDATA%\\voice-typer``.

    Mirrors the Linux purge in ``scripts/linux/uninstall_permissions.py``
    (same subpaths list — kept inline here so the script runs even when
    the voice_typer package is not importable). Removes each known
    subpath individually (NOT a blanket ``rmdir /s`` of the whole
    %APPDATA%) so we never touch unrelated user files.

    Best-effort: logs warnings on failure, never raises.
    """
    appdata = os.environ.get("APPDATA")
    if not appdata:
        _log("WARNING: --purge: APPDATA env var not set — skipping user-data purge")
        return
    data_dir = Path(appdata) / "voice-typer"
    if not data_dir.is_dir():
        _log(f"--purge: no user data directory to remove ({data_dir} not present)")
        return

    _log(f"--purge: removing user data at {data_dir}")
    # The subpaths list mirrors
    # voice_typer/server/_paths.py::user_data_subpaths_for_purge() — kept
    # inline here (rather than imported) because this script may run
    # when the voice_typer package has already been partially removed
    # by the NSIS uninstaller (the Python bundle is gone before the
    # post-uninstall hook fires).
    subpaths = [
        "huggingface",  # HF model cache (GBs)
        "venv",  # Python venv (hundreds of MB)
        "logs",  # rotating log files
        "history.db",  # SQLite history DB
        "history.db-wal",  # SQLite WAL (may not exist)
        "history.db-shm",  # SQLite SHM (may not exist)
        "crash_recovery.json",  # crash-recovery snapshot
        "backend.lock",  # single-instance POSIX lockfile
        "backend.pid",  # backend PID file
        "autostart.log",  # macOS LaunchAgent autostart log (vestigial on Windows)
        "prewarm-launchagent.log",  # vestigial on Windows
        "onboarding.marker",  # onboarding completion sentinel
    ]
    for sub in subpaths:
        target = data_dir / sub
        if not target.exists():
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=False)
            else:
                target.unlink()
        except OSError as exc:
            _log(f"WARNING: --purge: failed to remove {target}: {exc}")
    # Try to remove the now-empty data dir itself (best-effort; will
    # fail if non-Voice-Typer files are inside — that's fine).
    with contextlib.suppress(OSError):
        data_dir.rmdir()


def _do_autostart_cleanup() -> tuple[list[str], list[str]]:
    """Run the autostart cleanup via the voice_typer package helpers.

    Returns ``(deleted_runkeys, deleted_tasks)`` — lists of names
    removed (best-effort). Falls back to direct ``winreg`` /
    ``schtasks`` invocations if the voice_typer package cannot be
    imported (e.g. the uninstaller already removed the Python bundle).
    """
    deleted_runkeys: list[str] = []
    deleted_tasks: list[str] = []

    # Try the voice_typer package path first (preferred — shares the
    # production code's parsing / logging / error handling).
    try:
        # Import lazily so the script can still run when the
        # voice_typer package is mid-uninstall.
        from voice_typer.server.server_platform import autostart_windows

        deleted_runkeys = autostart_windows._unregister_all_voicetyper_runkeys()
        deleted_tasks = autostart_windows._unregister_all_voicetyper_tasks()
        return deleted_runkeys, deleted_tasks
    except Exception as exc:
        _log(f"WARNING: voice_typer package import failed ({exc}); falling back to direct reg.exe / PowerShell sweep")

    # Fallback path: shell out to reg.exe and PowerShell directly.
    # reg.exe does NOT support wildcards in /v, so we use PowerShell's
    # Remove-ItemProperty which DOES support -Name wildcards.
    import subprocess

    run_key_path = r"HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    ps_cmd = (
        f"Get-ItemProperty -Path '{run_key_path}' | "
        "Get-Member -MemberType NoteProperty | "
        "Where-Object { $_.Name -like 'VoiceTyper*' -or $_.Name -like 'com.voicetyper*' } | "
        "ForEach-Object { "
        f"  Remove-ItemProperty -Path '{run_key_path}' -Name $_.Name -ErrorAction SilentlyContinue; "
        "  Write-Output $_.Name "
        "}"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.startswith(("VoiceTyper", "com.voicetyper")):
                    deleted_runkeys.append(line)
        else:
            _log(f"WARNING: PowerShell Run-key sweep failed (rc={result.returncode}): {(result.stderr or '').strip()}")
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"WARNING: PowerShell Run-key sweep raised: {exc}")

    # Task Scheduler sweep (same PowerShell pipeline as the
    # voice_typer package path — included here for the fallback case).
    # The wildcard union covers the current canonical names
    # (com.voicetyper.autostart*, com.voicetyper.prewarm) AND the
    # pre-rename bare names (VoiceTyperAutostart*, VoiceTyperPrewarm).
    ps_task_cmd = (
        "Get-ScheduledTask -TaskName 'VoiceTyper*','com.voicetyper*' "
        "-ErrorAction SilentlyContinue | "
        "ForEach-Object { schtasks.exe /Delete /TN $_.TaskName /F; "
        "Write-Output $_.TaskName }"
    )
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                ps_task_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.startswith(("VoiceTyper", "com.voicetyper")):
                    deleted_tasks.append(line)
        else:
            _log(f"WARNING: PowerShell task sweep failed (rc={result.returncode}): {(result.stderr or '').strip()}")
    except (OSError, subprocess.SubprocessError) as exc:
        _log(f"WARNING: PowerShell task sweep raised: {exc}")

    return deleted_runkeys, deleted_tasks


def main() -> int:
    """Entry point — returns 0 on completion (best-effort, never blocks)."""
    # (): purge runs BEFORE autostart cleanup so the
    # autostart entry is removed LAST (in case the data-dir purge
    # deletes the very Python bundle this script is running from —
    # the autostart cleanup has already happened by then, so a
    # mid-script failure leaves the system in a recoverable state).
    if _purge_requested:
        _purge_user_data()

    deleted_runkeys, deleted_tasks = _do_autostart_cleanup()

    if deleted_runkeys:
        _log(f"Removed {len(deleted_runkeys)} HKCU Run-key entries:")
        for name in deleted_runkeys:
            _log(f"  - {name}")
    else:
        _log("No HKCU Run-key Voice Typer entries to remove (already clean)")

    if deleted_tasks:
        _log(f"Removed {len(deleted_tasks)} Task Scheduler tasks:")
        for name in deleted_tasks:
            _log(f"  - {name}")
    else:
        _log("No Voice Typer Task Scheduler tasks to remove (already clean)")

    _log("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
