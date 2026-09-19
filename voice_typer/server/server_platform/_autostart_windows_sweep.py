"""One-time legacy-entry sweep for the Windows autostart mechanisms."""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
from pathlib import Path

from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)

# PLAT-RUN renamed the autostart entries from fixed strings (and later


def _entry_targets_this_install(value: str) -> bool:
    """True if an autostart command line points at THIS install."""
    if not value:
        return False
    launcher = _autostart_mod._install_identifier()
    if launcher and os.path.normcase(launcher) in os.path.normcase(value):
        return True
    try:
        if _autostart_mod._is_legacy_stale_autostart_reference(value):
            return True
    except Exception:
        pass
    try:
        tokens = shlex.split(value, posix=False)
    except ValueError:
        return False
    if not tokens:
        return False
    exe = tokens[0].strip('"')
    if not exe:
        return False
    tauri_bin = _autostart_mod._resolve_tauri_binary_for_autostart()
    return bool(tauri_bin and os.path.normcase(exe) == os.path.normcase(tauri_bin))


def _sweep_legacy_runkeys() -> list[str]:
    """Remove legacy ``VoiceTyper*`` HKCU Run-key values for this install."""
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        import winreg
    except ImportError:
        return []  # not Windows
    current_name = _aw._run_key_name()
    deleted: list[str] = []
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_ALL_ACCESS,
        )
    except OSError as exc:
        log.warning("[AUTOSTART] Could not open HKCU Run key for legacy sweep: %s", exc)
        return deleted
    try:
        i = 0
        while True:
            try:
                name, value, _vtype = winreg.EnumValue(key, i)
            except OSError:
                # End of enumeration (Windows signals via OSError).
                break
            if (
                isinstance(name, str)
                and name.startswith("VoiceTyper")
                and name != current_name
                and isinstance(value, str)
                and _entry_targets_this_install(value)
            ):
                try:
                    winreg.DeleteValue(key, name)
                    deleted.append(name)
                    log.info("[AUTOSTART] Legacy sweep removed duplicate Run key: %s", name)
                    # Don't increment i, the next value shifts into the
                    continue
                except OSError as exc:
                    log.warning(
                        "[AUTOSTART] Legacy sweep failed to delete Run key %r: %s",
                        name,
                        exc,
                    )
            i += 1
    finally:
        with contextlib.suppress(OSError):
            winreg.CloseKey(key)
    return deleted


def _sweep_legacy_tasks() -> list[str] | None:
    """Remove legacy ``VoiceTyperAutostart*`` scheduled tasks for this install."""
    from voice_typer.server.server_platform import autostart_windows as _aw

    if not _aw.is_windows():
        return []
    try:
        from voice_typer.server import task_scheduler
    except Exception:
        return []
    if not task_scheduler.is_supported():
        return []
    import subprocess

    current_name = _autostart_mod._APP_AUTOSTART_TASK_NAME
    deleted: list[str] = []
    try:
        ps_cmd = (
            "Get-ScheduledTask -TaskName 'VoiceTyperAutostart*' "
            "-ErrorAction SilentlyContinue | "
            "ForEach-Object { Write-Output $_.TaskName }"
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
            timeout=15,
            check=False,
            # CREATE_NO_WINDOW (0x08000000) prevents a console window
            creationflags=_autostart_mod._windows_create_no_window_flags(),
        )
        if result.returncode != 0:
            log.debug(
                "[AUTOSTART] Legacy task sweep enumeration failed (rc=%s), will retry next startup",
                result.returncode,
            )
            return None
        for line in (result.stdout or "").splitlines():
            name = line.strip()
            if not name.startswith("VoiceTyperAutostart") or name == current_name:
                continue
            rc, xml = task_scheduler._schtasks(["/Query", "/TN", name, "/XML"])
            if rc != 0:
                continue
            command_path = _aw._extract_command_from_task_xml(xml) or ""
            arguments = _aw._extract_arguments_from_task_xml(xml) or ""
            if not (_entry_targets_this_install(command_path) or _entry_targets_this_install(arguments)):
                continue
            rc_del, _out = task_scheduler._schtasks(
                ["/Delete", "/TN", name, "/F"],
                capture=True,
            )
            if rc_del == 0:
                deleted.append(name)
                log.info("[AUTOSTART] Legacy sweep removed duplicate task: %s", name)
    except Exception:
        log.debug("[AUTOSTART] Legacy task sweep failed", exc_info=True)
        return None
    return deleted


def _sweep_legacy_startup_bats() -> list[str]:
    """Remove legacy ``VoiceTyper*.bat`` Startup-folder files for this install."""
    from voice_typer.server.server_platform import autostart_windows as _aw

    if not _aw.is_windows():
        return []
    current_name = _aw._startup_bat_name()
    try:
        autostart_dir = _autostart_mod.get_autostart_dir()
    except Exception:
        return []
    if not autostart_dir.is_dir():
        return []
    deleted: list[str] = []
    for bat_path in autostart_dir.glob("VoiceTyper*.bat"):
        if bat_path.name == current_name:
            continue
        try:
            content = bat_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _entry_targets_this_install(content):
            try:
                bat_path.unlink()
                deleted.append(bat_path.name)
                log.info(
                    "[AUTOSTART] Legacy sweep removed duplicate Startup .bat: %s",
                    bat_path.name,
                )
            except OSError as exc:
                log.warning(
                    "[AUTOSTART] Legacy sweep failed to remove %s: %s",
                    bat_path.name,
                    exc,
                )
    return deleted


def _sweep_v1_marker_files(config_dir: Path) -> list[str]:
    """Delete leftover v1 legacy-sweep markers (``autostart-sweep-<hash>.done``)."""
    deleted: list[str] = []
    try:
        for marker in config_dir.glob("autostart-sweep-*.done"):
            if marker.name.startswith("autostart-sweep-v2-"):
                continue
            try:
                marker.unlink()
                deleted.append(marker.name)
                log.info("[AUTOSTART] removed legacy v1 sweep marker %s", marker.name)
            except OSError as exc:
                log.debug(
                    "[AUTOSTART] could not remove legacy v1 sweep marker %s: %s",
                    marker.name,
                    exc,
                )
    except OSError as exc:
        log.debug("[AUTOSTART] glob error for legacy v1 sweep markers: %s", exc)
    return deleted


def _legacy_sweep_marker_path(config_dir: Path) -> Path:
    """Path to the per-install legacy-sweep completion marker."""
    return config_dir / f"autostart-sweep-v2-{_autostart_mod._install_hash()}.done"


def sweep_legacy_autostart_entries(config_dir: Path) -> dict:
    """One-time cleanup of legacy same-install autostart entries (PLAT-RUN)."""
    # One-time cleanup of v1 sweep markers (``autostart-sweep-<hash>.done``)
    _sweep_v1_marker_files(config_dir)
    marker = _legacy_sweep_marker_path(config_dir)
    if marker.exists():
        return {"swept": False, "removed": {"runkeys": [], "tasks": [], "bats": []}}
    # Windows-only + test-safety gate. The run-key sweep needs winreg,
    try:
        import winreg  # noqa: F401
    except ImportError:
        return {"swept": False, "removed": {"runkeys": [], "tasks": [], "bats": []}}
    removed = {
        "runkeys": _sweep_legacy_runkeys(),
        "tasks": _sweep_legacy_tasks(),
        "bats": _sweep_legacy_startup_bats(),
    }
    if removed["tasks"] is None:
        # The task enumeration failed (PowerShell unavailable / non-zero
        log.warning("[AUTOSTART] Legacy autostart sweep: task enumeration failed, will retry on next startup")
        return {
            "swept": False,
            "removed": {"runkeys": removed["runkeys"], "tasks": [], "bats": removed["bats"]},
        }
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
    except OSError as exc:
        # Best-effort: if the marker can't be written the sweep re-runs
        log.warning(
            "[AUTOSTART] Could not write legacy-sweep marker %s: %s",
            marker,
            exc,
        )
    return {"swept": True, "removed": removed}
