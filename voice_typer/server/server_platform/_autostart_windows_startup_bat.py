"""Windows Startup-folder .bat autostart mechanism (tertiary fallback).

Extracted from ``voice_typer/server/server_platform/autostart_windows.py``
(the orchestrating facade). This module owns the register / unregister /
is-registered trio for the Startup-folder ``.bat`` fallback mechanism.

AUTOSTART-STARTUP-FALLBACK: when BOTH the HKCU Run key and Task
Scheduler registration fail (e.g. HKCU locked by group policy,
schtasks unavailable, UAC declined), we write a ``.bat`` file to the
Windows Startup folder as a tertiary mechanism. The Startup folder
(``%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup``) is
always honored by Windows Explorer at login and requires no special
permissions — it's the most reliable fallback.

The .bat sets ``VT_START_HIDDEN=1`` (so the Tauri app / Electron
launcher starts hidden) and spawns the autostart command via
``start "" /B`` (no console window flash). The file is named
``com.voicetyper.autostart_<hash>.bat`` to match the Run-key naming
convention (PLAT-RUN — multi-install support via the install-path hash,
canonical ``com.voicetyper.*`` reverse-DNS namespace).

Patch contract: cross-module names are resolved through sibling
MODULE-OBJECT attribute reads at call time, so patches on the owning
module propagate:

  - ``is_windows`` / ``_startup_bat_path`` / ``_validate_runkey_command``
    are owned by the facade module (``autostart_windows``) — read lazily
    (inside the function, avoiding a circular import) as ``_aw.X``.
  - ``_autostart_command`` is owned by :mod:`.autostart` — bound once at
    module import time as ``_autostart_mod`` and read through its
    attribute at call time.
  - Names defined IN THIS MODULE are plain module-global lookups,
    patchable on this module.
"""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)


def _register_app_autostart_startup() -> bool:
    """Register app autostart via a Windows Startup-folder .bat file.

    Writes a ``.bat`` to ``%APPDATA%\\Microsoft\\Windows\\Start Menu\\
    Programs\\Startup\\VoiceTyper_<hash>.bat`` that sets
    ``VT_START_HIDDEN=1`` and spawns the autostart command via
    ``start "" /B`` (no console window flash).

    Returns ``True`` on success, ``False`` on failure (e.g. the
    Startup folder is not writable, or the autostart command can't be
    resolved). Non-Windows platforms return ``False`` (the Startup
    folder concept doesn't apply).
    """
    from voice_typer.server.server_platform import autostart_windows as _aw

    if not _aw.is_windows():
        return False
    try:
        cmd = _autostart_mod._autostart_command()
        bat_path = _aw._startup_bat_path()
        bat_path.parent.mkdir(parents=True, exist_ok=True)
        # Build the .bat content. ``@echo off`` suppresses command
        # echo; ``set VT_START_HIDDEN=1`` passes the hidden flag to
        # the spawned app (the Run key / Task Scheduler can't set env
        # vars, but the .bat can). ``start "" /B`` spawns the command
        # without a new console window and doesn't wait for it.
        bat_content = f'@echo off\r\nset VT_START_HIDDEN=1\r\nstart "" /B {cmd}\r\n'
        bat_path.write_text(bat_content, encoding="utf-8")
        log.info(
            "[CONFIG] Autostart enabled via Windows Startup-folder .bat: %s",
            bat_path,
        )
        return True
    except OSError as exc:
        log.warning("[CONFIG] Could not write Startup-folder .bat: %s", exc)
        return False
    except Exception as exc:
        log.warning("[CONFIG] Startup-folder .bat registration raised: %s", exc)
        return False


def _unregister_app_autostart_startup() -> bool:
    """Remove the Windows Startup-folder .bat file.

    Returns ``True`` on success (including when the file was already
    absent — idempotent). Returns ``False`` only if the file exists
    but couldn't be deleted (e.g. permission denied).
    """
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        bat_path = _aw._startup_bat_path()
    except Exception:
        return False
    if not bat_path.exists():
        return True  # already absent — idempotent success
    try:
        bat_path.unlink()
        log.info("[CONFIG] Removed Windows Startup-folder .bat: %s", bat_path)
        return True
    except OSError as exc:
        log.warning("[CONFIG] Could not remove Startup-folder .bat: %s", exc)
        return False


def _is_app_autostart_startup_registered() -> bool:
    """True if the Windows Startup-folder .bat exists AND its target
    command is valid (points at an existing file).

    AUTOSTART-CMD-VALIDATE: mirrors the validation in
    :func:`autostart_windows._is_app_autostart_runkey_registered` — the
    .bat file's existence alone is not enough; we also verify the
    spawned command's exe path exists on disk. If the .bat is stale
    (target deleted), we clean it up and return False.
    """
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        bat_path = _aw._startup_bat_path()
    except Exception:
        return False
    if not bat_path.exists():
        return False
    try:
        content = bat_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    # Extract the command from the ``start "" /B <cmd>`` line.
    # We look for the line starting with ``start ""`` and parse the
    # remainder as a Windows command line.
    target_cmd = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith('start ""'):
            # Everything after ``start "" /B `` is the command.
            # Find the command portion (skip ``start ""`` and optional ``/B``).
            after_start = stripped[len('start ""') :].strip()
            # Strip leading ``/B`` flag if present.
            if after_start.upper().startswith("/B"):
                after_start = after_start[2:].strip()
            target_cmd = after_start
            break
    if not target_cmd:
        # Malformed .bat — can't validate. Conservatively report True
        # (the file exists; we just can't parse it).
        log.debug(
            "[AUTOSTART] Startup .bat exists but could not parse command: %s",
            bat_path,
        )
        return True
    # Validate the target command's exe path exists.
    if not _aw._validate_runkey_command(target_cmd):
        log.warning(
            "[AUTOSTART] Startup .bat exists but its target command is stale: %s — cleaning up stale .bat",
            target_cmd,
        )
        with contextlib.suppress(OSError):
            bat_path.unlink()
        return False
    log.debug(
        "[AUTOSTART] Startup .bat registered with valid command: %s",
        target_cmd,
    )
    return True
