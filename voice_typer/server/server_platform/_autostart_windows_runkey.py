"""Windows HKCU Run-key autostart mechanism (last-resort fallback)."""

from __future__ import annotations

import logging

from voice_typer.server._paths import APP_IDENTIFIER, APP_RDNN_ROOT
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

        # policy as this loop, PLUS the C-CROSS-4 doubled-backslash
        try:
            run_key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_ALL_ACCESS
            )
            i = 0
            while True:
                try:
                    name, value, _ = winreg.EnumValue(run_key, i)
                    if (
                        name.startswith((APP_IDENTIFIER, APP_RDNN_ROOT))
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
    """True if the HKCU Run key has the Lausu entry AND the command"""
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
