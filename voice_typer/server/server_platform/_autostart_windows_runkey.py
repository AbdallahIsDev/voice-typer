"""Windows HKCU Run-key autostart mechanism (last-resort fallback).

Extracted from ``voice_typer/server/server_platform/autostart_windows.py``
(the orchestrating facade for the three Windows autostart mechanisms —
Task Scheduler, Startup-folder .bat, HKCU Run key). This module owns the
register / unregister / is-registered trio for the HKCU Run-key
mechanism, mirroring the layout of :mod:`._autostart_windows_startup_bat`.

AUTOSTART-ORDER-FIX: the Run key is the LAST resort, its value is a
raw command line that the Windows 11 StartupApp launcher can reject at
logon (observed: Shell-Core 9707/9708 with PID 0 on every logon for a
malformed value: see ``autostart_windows._validate_runkey_command``).
The enable order (Task Scheduler → Startup .bat → HKCU Run key) is owned
by the facade orchestrators and is FIXED.

The command written to the Run key comes from
``autostart._autostart_command()``: platform-gated quoting
(Windows → ``subprocess.list2cmdline``), NOT the freedesktop
``_desktop_quote`` path (that bug baked doubled backslashes into the
value and broke logon autostart for a month).

Patch contract: cross-module names are resolved through sibling
MODULE-OBJECT attribute reads at call time, so patches on the owning
module propagate:

  - ``_run_key_name`` / ``_validate_runkey_command`` /
    ``_cleanup_stale_runkey_entry`` are owned by the facade module
    (``autostart_windows``), read lazily (inside the function,
    avoiding a circular import) as ``_aw.X``.
  - ``_autostart_command`` is owned by :mod:`.autostart`, bound once at
    module import time as ``_autostart_mod`` and read through its
    attribute at call time.
  - Names defined IN THIS MODULE are re-imported by the facade at module
    level; facade callers (the orchestrators and the tests that patch
    ``monkeypatch.setattr(autostart_windows, "X", ...)``) resolve them
    through the facade's plain module-global lookup, so facade patches
    are seen.
"""

from __future__ import annotations

import logging

from voice_typer.server.server_platform import autostart as _autostart_mod

log = logging.getLogger(__name__)


def _register_app_autostart_runkey() -> bool:
    """Register app autostart via HKCU Run key (admin-free fallback)."""
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: Use deterministic key name based on install path
    # to prevent conflicting entries from different installs
    reg_key_name = _aw._run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            cmd = _autostart_mod._autostart_command()
            winreg.SetValueEx(key, reg_key_name, 0, winreg.REG_SZ, cmd)
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart enabled via HKCU Run key (fallback): %s", cmd)

        # PLAT-RUN: Clean stale entries whose path no longer exists.
        # Matches BOTH the current reverse-DNS scheme (com.voicetyper.*)
        # and the pre-rename bare scheme (VoiceTyper_*).
        #
        # Per-entry validity routes through the canonical
        # ``_aw._validate_runkey_command`` (same CONSERVATIVE-DELETE
        # policy as this loop, PLUS the C-CROSS-4 doubled-backslash
        # raw-string check. BP-128: a freedesktop-quoting-mangled
        # value passes ``Path.exists()`` (it collapses ``\\``) and
        # would otherwise survive the sweep forever).
        try:
            run_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS
            )
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(run_key, i)
                    if (
                        name.startswith(("VoiceTyper", "com.voicetyper"))
                        and name != reg_key_name
                        and isinstance(value, str)
                        and not _aw._validate_runkey_command(value)
                    ):
                        winreg.DeleteValue(run_key, name)
                        log.info("[AUTOSTART] Removed stale entry: %s", name)
                        continue
                    i += 1
                except OSError:
                    break
            winreg.CloseKey(run_key)
        except Exception:
            log.debug("[AUTOSTART] registry Run-key cleanup failed", exc_info=True)

        return True
    except OSError as e:
        log.warning("[CONFIG] HKCU Run key autostart failed: %s", e)
        return False


def _unregister_app_autostart_runkey() -> bool:
    """Remove app autostart from HKCU Run key."""
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _aw._run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, reg_key_name)
        except FileNotFoundError:
            pass
        finally:
            winreg.CloseKey(key)
        log.info("[CONFIG] Autostart removed from HKCU Run key")
        return True
    except OSError:
        return False


def _is_app_autostart_runkey_registered() -> bool:
    """True if the HKCU Run key has the VoiceTyper entry AND the command
    path it points at actually exists on disk.

    AUTOSTART-CMD-VALIDATE: previously this function returned True if
    the registry value existed (existence-only check). If the venv was
    deleted after registration, the Run-key value would still exist
    but its command would point at a nonexistent pythonw.exe, the
    Run key would fire at login, fail silently, and the Settings toggle
    would show "autostart enabled" while the app never started. We now
    parse the stored command line, extract the exe path, and verify it
    exists. If the path is dead, we delete the stale entry (best-effort)
    and return False so the Settings toggle reflects the actual state.
    """
    from voice_typer.server.server_platform import autostart_windows as _aw

    try:
        import winreg
    except ImportError:
        return False  # not Windows
    # PLAT-RUN: use the same deterministic key name
    reg_key_name = _aw._run_key_name()
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0,
            winreg.KEY_READ,
        )
        try:
            val, _ = winreg.QueryValueEx(key, reg_key_name)
            if not val:
                return False
            # AUTOSTART-CMD-VALIDATE: verify the command's exe path
            # exists on disk. If the path is dead (venv deleted), the
            # Run-key entry is stale, clean it up and return False.
            if not _aw._validate_runkey_command(val):
                log.warning(
                    "[AUTOSTART] Run-key entry %s exists but its command "
                    "path is stale (target file does not exist): %s, "
                    "cleaning up stale entry",
                    reg_key_name,
                    val,
                )
                _aw._cleanup_stale_runkey_entry(reg_key_name)
                return False
            log.debug(
                "[AUTOSTART] Run-key entry %s has valid command: %s",
                reg_key_name,
                val,
            )
            return True
        except FileNotFoundError:
            return False
        finally:
            winreg.CloseKey(key)
    except FileNotFoundError:
        return False
    except OSError:
        return False
