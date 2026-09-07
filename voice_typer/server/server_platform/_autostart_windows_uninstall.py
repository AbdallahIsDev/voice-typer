"""Uninstaller helpers for the Windows autostart entries.

Extracted from ``voice_typer/server/server_platform/autostart_windows.py``
(the orchestrating facade). This module owns the uninstaller-path
cleanup that removes ALL autostart entries in the app's namespace —
HKCU Run-key values and Task Scheduler tasks — regardless of which
install registered them. Unlike the per-install register/unregister
helpers, these enumerate and delete every matching entry (current
canonical ``com.voicetyper.*`` names AND pre-rename bare ``VoiceTyper*``
names).

Patch contract: the Windows-only dependencies (``winreg``,
``task_scheduler``, ``subprocess``) are imported at CALL time inside the
functions, so tests inject a fake ``winreg`` module into
``sys.modules`` and patch ``task_scheduler`` / ``subprocess.run`` on the
owning modules — no module-level binding of this module depends on them.
The uninstaller script (``scripts/windows/uninstall_permissions.py``)
and tests reach these helpers through the facade module
(``voice_typer.server.server_platform.autostart_windows``), which
re-imports them at module level.
"""

from __future__ import annotations

import contextlib
import logging

log = logging.getLogger(__name__)


def _unregister_all_voicetyper_runkeys() -> list[str]:
    """Remove ALL Voice Typer HKCU Run-key entries.

    Unlike :func:`autostart_windows._unregister_app_autostart_runkey`
    (which removes ONLY the current install's hash-suffixed entry), this
    function enumerates every value under
    ``HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run`` whose
    name starts with the app's namespace — ``com.voicetyper`` (current
    canonical rename-DNS names) OR ``VoiceTyper`` (pre-rename bare
    names) — and deletes it. It is intended
    for the **uninstaller** path (NSIS ``customUnInstall`` macro /
    Tauri ``preRemoveScript`` / manual ``uninstall_permissions.py``
    invocation) where the goal is to leave the registry CLEAN of any
    Voice Typer autostart entry — including stale entries from previous
    installs at different paths (different hashes — see PLAT-RUN).

    Returns the list of value names that were deleted (empty list if
    nothing matched / not Windows / registry inaccessible). The caller
    can log the list for the uninstall summary.

    Non-fatal: any per-value error (e.g. a value vanishes between
    EnumValue and DeleteValue) is logged and skipped so a single bad
    value doesn't abort the whole sweep.

    Tested on Linux via the ``fake_winreg`` fixture pattern (see
    ``tests/test_uninstall_windows.py``) — the ``winreg`` import is
    deferred to call time so the module imports cleanly on non-Windows
    hosts.
    """
    try:
        import winreg
    except ImportError:
        return []  # not Windows — caller (uninstall script) logs + exits 0
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
                # via OSError, not StopIteration).
                break
            if isinstance(name, str) and name.startswith(("VoiceTyper", "com.voicetyper")):
                try:
                    winreg.DeleteValue(key, name)
                    deleted.append(name)
                    log.info("[UNINSTALL] Removed HKCU Run key: %s", name)
                    # Don't increment i — the next value shifts into the
                    # current slot after DeleteValue (same pattern as the
                    # stale-entry cleanup loop in _register_app_autostart_runkey).
                    continue
                except OSError as exc:
                    log.warning("[UNINSTALL] Failed to delete HKCU Run key %r: %s", name, exc)
            i += 1
    finally:
        with contextlib.suppress(OSError):
            winreg.CloseKey(key)
    return deleted


def _unregister_all_voicetyper_tasks() -> list[str]:
    """Remove ALL Voice Typer Task Scheduler tasks.

    Companion to :func:`_unregister_all_voicetyper_runkeys`. The Task
    Scheduler ``schtasks`` CLI does NOT accept wildcards in ``/TN``,
    so we shell out to PowerShell's ``Get-ScheduledTask`` (which DOES
    support ``-TaskName`` wildcards) to enumerate matching
    tasks, then ``schtasks /Delete`` each one.  The wildcard union
    covers the current canonical names (``com.voicetyper.autostart*``,
    ``com.voicetyper.prewarm``) AND the pre-rename bare names
    (``VoiceTyperAutostart*``, ``VoiceTyperPrewarm``) so installs that
    predate the namespace rename are fully cleaned too.

    Returns the list of task names deleted (best-effort — empty list on
    any failure including non-Windows or Task Scheduler not running).
    The caller can log the list for the uninstall summary.

    Non-fatal: a single task delete failure (e.g. locked task created
    by an admin install) is logged and skipped.
    """
    try:
        from voice_typer.server import task_scheduler
    except Exception as exc:  # pragma: no cover — defensive
        log.warning("[UNINSTALL] task_scheduler import failed: %s", exc)
        return []
    if not task_scheduler.is_supported():
        return []
    import subprocess

    from voice_typer.server.server_platform.autostart import _windows_create_no_window_flags as _create_no_window_flags

    deleted: list[str] = []
    try:
        # PowerShell pipeline: Get-ScheduledTask returns matching tasks,
        # ForEach-Object runs schtasks /Delete for each. We capture stdout
        # and parse the task names from the Get-ScheduledTask output as a
        # best-effort log (the actual delete happens via the pipeline).
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
            # window from flashing on the user's screen during the
            # uninstall sweep. The sweep runs at uninstall time, often
            # from a UI-driven flow where a flashing console would look
            # broken (flag value shared with the autostart import probe
            # and the legacy-entry sweep via the ``autostart`` helper).
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
