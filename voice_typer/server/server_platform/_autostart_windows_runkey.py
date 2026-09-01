"""Windows HKCU Run-key autostart mechanism (last-resort fallback).

Extracted from ``voice_typer/server/server_platform/autostart_windows.py``
(the orchestrating facade for the three Windows autostart mechanisms —
Task Scheduler, Startup-folder .bat, HKCU Run key). This module owns the
register / unregister / is-registered trio for the HKCU Run-key
mechanism, mirroring the layout of :mod:`._autostart_windows_startup_bat`.

AUTOSTART-ORDER-FIX: the Run key is the LAST resort — its value is a
raw command line that the Windows 11 StartupApp launcher can reject at
logon (observed: Shell-Core 9707/9708 with PID 0 on every logon for a
malformed value — see ``autostart_windows._validate_runkey_command``).
The enable order (Task Scheduler → Startup .bat → HKCU Run key) is owned
by the facade orchestrators and is FIXED.

The command written to the Run key comes from
``autostart._autostart_command()`` — platform-gated quoting
(Windows → ``subprocess.list2cmdline``), NOT the freedesktop
``_desktop_quote`` path (that bug baked doubled backslashes into the
value and broke logon autostart for a month).

Patch contract: cross-module names are resolved through sibling
MODULE-OBJECT attribute reads at call time, so patches on the owning
module propagate:

  - ``_run_key_name`` / ``_validate_runkey_command`` /
    ``_cleanup_stale_runkey_entry`` are owned by the facade module
    (``autostart_windows``) — read lazily (inside the function,
    avoiding a circular import) as ``_aw.X``.
  - ``_autostart_command`` is owned by :mod:`.autostart` — bound once at
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
import shlex
from pathlib import Path

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
        # parse the Run-key command line with a Windows-aware
        # splitter before extracting the exe path. Pre-fix, the code did
        # ``value.strip('"').split('"')[0] if '"' in value else value.split()[0]``
        # which misparses UNQUOTED spaced paths (e.g.
        # ``C:\Program Files\VoiceTyper\app.exe --delay 15``) — the
        # ``value.split()[0]`` branch returns ``C:\Program`` (NOT a real
        # path), ``Path('C:\\Program').exists()`` is False, and the
        # cleanup silently DELETES the other install's Run-key entry.
        # This breaks multi-install autostart (a PLAT-RUN supported
        # scenario) when any install lives in a spaced path (common:
        # ``C:\Program Files\...``).
        #
        # ``shlex.split(value, posix=False)`` parses a Windows-style
        # command line: it preserves backslashes, treats double quotes
        # as argument delimiters (the quoted token is returned as a
        # single element WITH the surrounding quotes preserved), and
        # splits on whitespace outside quotes. The first token is the
        # exe path (quoted or not); we strip the surrounding quotes to
        # get the actual filesystem path.
        #
        # CONSERVATIVE-DELETE policy: an UNQUOTED value with multiple
        # tokens (spaces in the command line) is ambiguous — the actual
        # exe path might be a longer space-separated prefix that we
        # can't recover without quotes. For such entries, we DO NOT
        # delete even if the first token doesn't exist as a file,
        # because deleting a legitimate entry is worse than leaving a
        # stale one in the registry. We only delete when we're CERTAIN
        # the entry is stale:
        #   - quoted path that doesn't exist (unambiguous), OR
        #   - unquoted single-token path that doesn't exist (unambiguous).
        #
        # Note: ``shlex.split(value, posix=False)`` is the documented
        # cross-platform-safe Windows-command-line splitter that does
        # NOT require the Windows-only ``shell32.CommandLineToArgvW``
        # — which keeps this code testable on non-Windows CI.
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
                    ):
                        # use shlex.split(posix=False) so quoted
                        # spaced paths are parsed correctly (the quoted
                        # token is a single element). For unquoted
                        # spaced paths, the parse is inherently ambiguous
                        # — see the CONSERVATIVE-DELETE policy above.
                        tokens = shlex.split(value, posix=False)
                        if not tokens:
                            # Malformed / empty value — skip cleanup.
                            i += 1
                            continue
                        exe_token = tokens[0]
                        # shlex.split(posix=False) preserves the
                        # surrounding quotes in the token; strip them so
                        # we get the actual filesystem path.
                        exe_path = exe_token.strip('"')
                        if not exe_path:
                            # Malformed entry (e.g. just quotes) — skip.
                            i += 1
                            continue
                        was_quoted = exe_token.startswith('"')
                        has_multiple_tokens = len(tokens) > 1
                        path_exists = Path(exe_path).exists()
                        if not path_exists:
                            # Only delete if we're CERTAIN the entry is
                            # stale (see CONSERVATIVE-DELETE policy).
                            # Ambiguous unquoted spaced paths are
                            # preserved (never deleted) to avoid
                            # breaking legitimate multi-install autostart.
                            if was_quoted or not has_multiple_tokens:
                                winreg.DeleteValue(run_key, name)
                                log.info("[AUTOSTART] Removed stale entry: %s", name)
                                continue
                            # else: ambiguous unquoted spaced path —
                            # be conservative, skip deletion.
                            log.debug(
                                "[AUTOSTART] Skipping ambiguous unquoted "
                                "spaced-path entry (cannot determine if "
                                "stale): %s",
                                name,
                            )
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
    but its command would point at a nonexistent pythonw.exe — the
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
            # Run-key entry is stale — clean it up and return False.
            if not _aw._validate_runkey_command(val):
                log.warning(
                    "[AUTOSTART] Run-key entry %s exists but its command "
                    "path is stale (target file does not exist): %s — "
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
