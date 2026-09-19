"""Uninstaller helpers for the Windows autostart entries."""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger(__name__)


def _unregister_all_voicetyper_runkeys() -> list[str]:
    """Remove ALL Voice Typer HKCU Run-key entries."""
    try:
        import winreg
    except ImportError:
        return []  # not Windows, caller (uninstall script) logs + exits 0
    deleted: list[str] = []
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS,
        )
    except OSError as exc:
        log.warning("[UNINSTALL] Could not open HKCU Run key for cleanup: %s", exc)
        return deleted
    try:
        i = 0
        while True:
            try:
                name, _value, _vtype = winreg.EnumValue(key, i)
            except OSError:
                # End of enumeration (Windows signals "no more values"
                break
            if isinstance(name, str) and name.startswith(("VoiceTyper", "com.voicetyper")):
                try:
                    winreg.DeleteValue(key, name)
                    deleted.append(name)
                    log.info("[UNINSTALL] Removed HKCU Run key: %s", name)
                    # Don't increment i, the next value shifts into the
                    continue
                except OSError as exc:
                    log.warning("[UNINSTALL] Failed to delete HKCU Run key %r: %s", name, exc)
            i += 1
    finally:
        with contextlib.suppress(OSError):
            winreg.CloseKey(key)
    return deleted


def _unregister_all_voicetyper_tasks() -> list[str]:
    """Remove ALL Voice Typer Task Scheduler tasks."""
    try:
        from voice_typer.server import task_scheduler
    except Exception as exc:  # pragma: no cover, defensive
        log.warning("[UNINSTALL] task_scheduler import failed: %s", exc)
        return []
    if not task_scheduler.is_supported():
        return []
    import subprocess

    from voice_typer.server.server_platform.autostart import _windows_create_no_window_flags as _create_no_window_flags

    deleted: list[str] = []
    try:
        # PowerShell pipeline: Get-ScheduledTask returns matching tasks,
        ps_cmd = (
            "Get-ScheduledTask -TaskName 'VoiceTyper*','com.voicetyper*' "
            "-ErrorAction SilentlyContinue | "
            "ForEach-Object { schtasks.exe /Delete /TN $_.TaskName /F; "
            "Write-Output $_.TaskName }"
        )
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
            # CREATE_NO_WINDOW (0x08000000) prevents a console
            creationflags=_create_no_window_flags(),
        )
        if result.returncode == 0:
            for line in (result.stdout or "").splitlines():
                line = line.strip()
                if line.startswith(("VoiceTyper", "com.voicetyper")):
                    deleted.append(line)
                    log.info("[UNINSTALL] Removed Task Scheduler task: %s", line)
        else:
            log.warning(
                "[UNINSTALL] PowerShell task sweep failed (rc=%s): %s",
                result.returncode,
                (result.stderr or "").strip(),
            )
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("[UNINSTALL] Task Scheduler sweep raised: %s", exc)
    return deleted
