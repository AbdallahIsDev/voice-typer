"""One-time legacy-entry sweep for the Windows autostart mechanisms.

Extracted from ``voice_typer/server/server_platform/autostart_windows.py``
(the orchestrating facade). This module owns the marker-gated cleanup of
legacy same-install autostart entries (Run keys, scheduled tasks,
Startup-folder .bat files, v1 sweep markers).

Patch contract: cross-module names are resolved through sibling
MODULE-OBJECT attribute reads at call time, so patches on the owning
module propagate:

  - ``is_windows`` / ``_run_key_name`` / ``_startup_bat_name`` /
    ``_extract_command_from_task_xml`` / ``_extract_arguments_from_task_xml``
    are owned by the facade module (``autostart_windows``) — read lazily
    (inside the function, avoiding a circular import) as ``_aw.X``.
  - ``_install_identifier`` / ``_resolve_tauri_binary_for_autostart`` /
    ``get_autostart_dir`` / ``_APP_AUTOSTART_TASK_NAME`` / ``_install_hash``
    are owned by :mod:`.autostart` — bound once at module import time as
    ``_autostart_mod`` and read through its attribute at call time.
  - Names defined IN THIS MODULE (the sweep functions themselves) are
    plain module-global lookups, patchable on this module.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
from pathlib import Path

from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)

# ─── One-time legacy-entry sweep (AUTOSTART-LEGACY) ──────────────────
#
# PLAT-RUN renamed the autostart entries from fixed strings (and later
# from a ``sys.executable``-derived hash) to a stable install-path hash.
# Upgraded installs can therefore carry legacy ``VoiceTyper*`` entries
# that ALL point at the same install and ALL fire at logon — duplicate
# autostart. The sweep below removes those legacy entries once per
# install (marker-gated), while preserving the current install's own
# entry and other installs' entries (multi-install support).


def _entry_targets_this_install(value: str) -> bool:
    """True if an autostart command line points at THIS install.

    The sweep must only delete entries targeting the SAME install —
    other installs' entries (PLAT-RUN multi-install support) must be
    preserved. A command targets this install when either:

      • it references the current install's autostart launcher script
        (``autostart_launcher.py`` — the stable per-install identifier
        from :func:`autostart._install_identifier`). Every Python-backed
        entry embeds it in the arguments, so this covers the
        overwhelming majority of legacy entries regardless of which
        interpreter registered them.
      • its executable is the current install's Tauri binary (the
        ``_tauri_binary()`` fallback used when no Python interpreter is
        available — a bare binary with no launcher in the arguments).

    Conservative by design: if neither check matches, the entry is
    treated as NOT belonging to this install and is left alone.
    """
    if not value:
        return False
    launcher = _autostart_mod._install_identifier()
    if launcher and os.path.normcase(launcher) in os.path.normcase(value):
        return True
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
    """Remove legacy ``VoiceTyper*`` HKCU Run-key values for this install.

    Enumerates every value under ``HKCU\\...\\Run`` whose name starts
    with ``VoiceTyper``, and deletes those whose name differs from the
    current install's ``_run_key_name()`` AND whose command targets this
    install (:func:`_entry_targets_this_install`). The current entry is
    never touched; other installs' entries (different launcher path /
    exe) are preserved.

    Returns the list of deleted value names. Non-fatal: registry errors
    are logged and skipped so a single bad value can't abort the sweep.
    """
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
                    # Don't increment i — the next value shifts into the
                    # current slot after DeleteValue.
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
    """Remove legacy ``VoiceTyperAutostart*`` scheduled tasks for this install.

    Enumerates matching tasks via PowerShell ``Get-ScheduledTask``
    (schtasks does not support wildcards in ``/TN``), then for each
    candidate whose name differs from the current
    ``_APP_AUTOSTART_TASK_NAME`` queries its XML and deletes it only if
    its command targets this install (:func:`_entry_targets_this_install`
    on the ``<Command>`` path + ``<Arguments>`` text).

    Returns:
      - ``list`` — the deleted task names. An empty list means the sweep
        ran and found nothing (or the platform doesn't use scheduled
        tasks);
      - ``None`` — the enumeration FAILED (PowerShell unavailable /
        non-zero exit / exception). ``None`` tells the marker-gated
        orchestrator NOT to write the completion marker, so the task
        portion is retried on the next startup instead of being lost
        to a transient failure.

    The PowerShell call is bounded by a 15s timeout (not the
    uninstaller's 60s) because this runs from ``sync_autostart`` on the
    startup path — a hung PowerShell must not stall app startup.
    """
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
            # from flashing during the sweep (shared with the autostart
            # import probe and the uninstall sweep via the
            # ``autostart`` helper).
            creationflags=_autostart_mod._windows_create_no_window_flags(),
        )
        if result.returncode != 0:
            log.debug(
                "[AUTOSTART] Legacy task sweep enumeration failed (rc=%s) — will retry next startup",
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
    """Remove legacy ``VoiceTyper*.bat`` Startup-folder files for this install.

    Enumerates ``VoiceTyper*.bat`` files under the Windows Startup folder
    and deletes those whose name differs from the current
    ``_startup_bat_name()`` AND whose content targets this install
    (:func:`_entry_targets_this_install` on the file text).

    Returns the list of deleted file names. Best-effort — unreadable /
    undeletable files are logged and skipped.
    """
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
    """Delete leftover v1 legacy-sweep markers (``autostart-sweep-<hash>.done``).

    The v2 sweep marker (``autostart-sweep-v2-<hash>.done``) is
    version-scoped precisely so installs that already ran the v1 sweep
    re-run once after the PLAT-RUN rename — but nothing ever removed the
    v1 marker files themselves, so they linger in ``config_dir`` forever
    (one per legacy ``python.exe`` / ``pythonw.exe`` install hash). They
    are tiny but pure clutter once their sweep has been superseded.

    Pure filesystem work (no winreg / PowerShell), so it is safe on every
    platform and runs before the Windows-only gate in the caller.
    Best-effort: per-file errors are logged and skipped so one bad file
    never aborts the sweep.

    Returns the names of deleted marker files.
    """
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
    """Path to the per-install legacy-sweep completion marker.

    Keyed by the install hash so each install (installs share the
    per-user config dir) sweeps its own legacy entries exactly once.

    The filename is version-scoped (``v2``): the Windows autostart /
    prewarm names moved from the bare ``VoiceTyper*`` scheme into the
    canonical ``com.voicetyper.*`` reverse-DNS namespace, so installs
    that already ran the v1 sweep (old marker name) MUST re-run the
    sweep once — the version bump makes the v1 marker miss and the new
    sweep removes the pre-rename entries that would otherwise linger as
    duplicate autostart triggers.
    """
    return config_dir / f"autostart-sweep-v2-{_autostart_mod._install_hash()}.done"


def sweep_legacy_autostart_entries(config_dir: Path) -> dict:
    """One-time cleanup of legacy same-install autostart entries (PLAT-RUN).

    Upgraded installs can carry legacy ``VoiceTyper*`` Run-key values,
    ``VoiceTyperAutostart*`` scheduled tasks, and ``VoiceTyper*.bat``
    Startup-folder files registered under the OLD naming schemes (the
    fixed pre-PLAT-RUN names, the buggy ``sys.executable``-derived
    hashes, or the bare ``VoiceTyper*`` names used before the move to
    the canonical ``com.voicetyper.*`` reverse-DNS namespace).
    Because the OLD hash differed between ``python.exe`` and
    ``pythonw.exe`` launch contexts, one install can accumulate MULTIPLE
    live entries that all fire at logon.

    This sweep removes every legacy entry whose command targets THIS
    install (:func:`_entry_targets_this_install`) while preserving:
      • the current install's own entry (``_run_key_name`` /
        ``_APP_AUTOSTART_TASK_NAME`` / ``_startup_bat_name``), and
      • other installs' entries (different launcher path → different
        install → preserved — PLAT-RUN multi-install support).

    It runs AT MOST ONCE per install: after the sweep (whether or not
    anything was removed) a marker file keyed by the install hash is
    written into ``config_dir``, so the expensive Task Scheduler
    enumeration (PowerShell) is paid once and steady-state startup cost
    is a single ``Path.exists()`` check (plus the tiny v1-marker glob in
    ``_sweep_v1_marker_files``). Callers invoke it on every
    startup (e.g. from ``sync_autostart``); the marker makes it
    one-time.

    Returns ``{"swept": bool, "removed": {"runkeys": [...],
    "tasks": [...], "bats": [...]}}``.
    """
    # One-time cleanup of v1 sweep markers (``autostart-sweep-<hash>.done``)
    # left behind by the pre-v2 sweep — see ``_sweep_v1_marker_files``.
    # Runs BEFORE the marker check so installs whose v2 marker already
    # exists (written by a pre-fix version) still get their v1 clutter
    # removed, and before the winreg gate because it is pure filesystem
    # work.
    _sweep_v1_marker_files(config_dir)
    marker = _legacy_sweep_marker_path(config_dir)
    if marker.exists():
        return {"swept": False, "removed": {"runkeys": [], "tasks": [], "bats": []}}
    # Windows-only + test-safety gate. The run-key sweep needs winreg,
    # and ``tests/conftest.py`` blocks the REAL ``winreg`` module
    # (``sys.modules["winreg"] = None``) so tests can never touch the
    # developer's actual HKCU registry — that same guard makes this
    # whole sweep inert under test unless a ``fake_winreg`` fixture is
    # injected, which also stops the task/bat sweeps (real PowerShell /
    # real Startup folder) from running during pytest.
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
        # exit). Do NOT write the completion marker — the sweep retries
        # next startup so a transient failure can't permanently skip the
        # task portion. The run-key / .bat sweeps already ran (both are
        # idempotent, so re-running them is harmless).
        log.warning("[AUTOSTART] Legacy autostart sweep: task enumeration failed — will retry on next startup")
        return {
            "swept": False,
            "removed": {"runkeys": removed["runkeys"], "tasks": [], "bats": removed["bats"]},
        }
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
        marker.write_text("", encoding="utf-8")
    except OSError as exc:
        # Best-effort: if the marker can't be written the sweep re-runs
        # next startup (still safe — every sub-sweep is idempotent).
        log.warning(
            "[AUTOSTART] Could not write legacy-sweep marker %s: %s",
            marker,
            exc,
        )
    return {"swept": True, "removed": removed}
