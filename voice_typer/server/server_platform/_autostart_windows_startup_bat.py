"""Windows Startup-folder .bat autostart mechanism (tertiary fallback)."""

from __future__ import annotations

import contextlib
import logging

from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)


def _register_app_autostart_startup() -> bool:
    """Register app autostart via a Windows Startup-folder .bat file."""
    from voice_typer.server.server_platform import autostart_windows as _aw

    if not _aw.is_windows():
        return False
    try:
        cmd = _autostart_mod._autostart_command()
        bat_path = _aw._startup_bat_path()
        bat_path.parent.mkdir(parents=True, exist_ok=True)
        # Build the .bat content. ``@echo off`` suppresses command
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
    """
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        bat_path = _aw._startup_bat_path()
    except Exception:
        return False
    if not bat_path.exists():
        return True  # already absent, idempotent success
    try:
        bat_path.unlink()
        log.info("[CONFIG] Removed Windows Startup-folder .bat: %s", bat_path)
        return True
    except OSError as exc:
        log.warning("[CONFIG] Could not remove Startup-folder .bat: %s", exc)
        return False


def _is_app_autostart_startup_registered() -> bool:
    """True if the Windows Startup-folder .bat exists AND its target"""
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
    target_cmd = None
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.lower().startswith('start ""'):
            # Everything after ``start "" /B `` is the command.
            after_start = stripped[len('start ""') :].strip()
            # Strip leading ``/B`` flag if present.
            if after_start.upper().startswith("/B"):
                after_start = after_start[2:].strip()
            target_cmd = after_start
            break
    if not target_cmd:
        # Malformed .bat, can't validate. Conservatively report True
        log.debug(
            "[AUTOSTART] Startup .bat exists but could not parse command: %s",
            bat_path,
        )
        return True
    # Validate the target command's exe path exists.
    if not _aw._validate_runkey_command(target_cmd):
        log.warning(
            "[AUTOSTART] Startup .bat exists but its target command is stale: %s, cleaning up stale .bat",
            target_cmd,
        )
        with contextlib.suppress(OSError):
            bat_path.unlink()
        return False
    log.debug(
        "[AUTOSTART] Startup .bat check: present with valid command: %s (read-only, no write)",
        target_cmd,
    )
    return True
