"""Focus-running-instance probe for the autostart launcher.

The cheap path (~100ms, no Vite, no extra ports) for "user clicked
Start Menu while the app is already in the background": spawn a
duplicate whose single-instance lock hands focus to the first
instance.
"""

from __future__ import annotations

import logging
import subprocess

from voice_typer.server._electron_build import (
    _launcher_child_env,
    _log_sensitive_env_keys,
    _spawn_flags,
)
from voice_typer.server.autostart.log_files import _close_log_files

# C-CROSS-3: explicit dotted logger name — see log_files.py for why
# ``__name__`` cannot be used here.
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _focus_running_app() -> bool:
    """Wake an already-running instance via its single-instance lock.

    Electron path: spawns a LEAN second ``electron .`` process.  That
    process:
      1. loads the prebuilt main bundle,
      2. calls requestSingleInstanceLock() → returns false,
      3. quits, which is what triggers the FIRST instance's
         ``second-instance`` event → it shows + focuses the dashboard.

    Tauri path: spawns the Tauri binary itself.  Tauri's
    ``tauri-plugin-single-instance`` plugin (declared in
    ``src-tauri/tauri.conf.json``) detects the duplicate instance,
    focuses the first, and quits the second — same effect as the
    Electron lean-spawn pattern, but without a separate lean binary.

    This is the cheap path (~100ms, no Vite, no extra ports) for "user
    clicked Start Menu while app is already in the background."  Returns
    True if the focus probe was spawned, False if it couldn't be.
    """
    from voice_typer.server import autostart_launcher as _pkg

    # Tauri focus path: spawn the Tauri binary — the single-instance
    # plugin handles the focus + second-instance-quit dance.
    focus_tauri = _pkg._is_tauri_mode()
    if focus_tauri:
        binary = _pkg._tauri_binary()
        if not binary:
            log.info("[AUTOSTART] tauri focus: binary missing; cannot focus existing instance")
            return False
        # Fail-closed integrity gate — same contract as
        # ``_spawn_tauri_host`` (the focus probe spawns the real binary).
        if not _pkg.verify_tauri_binary_or_skip(binary):
            log.error(
                "[AUTOSTART] tauri focus: refusing to spawn %s — integrity verification failed (fail-closed).",
                binary,
            )
            return False
        # ``_launcher_child_env`` force-disables ANSI colour + npm notices
        # (the child's output is redirected to the electron/tauri log files).
        env = _launcher_child_env()
        env["VT_FOCUS_ONLY"] = "1"
        # same-app restart — full env intentionally
        # inherited (see _launch_electron_built for rationale). Only
        # sensitive KEY NAMES are logged for audit; values are never
        # printed.
        _log_sensitive_env_keys(env, context="autostart")
        sk: dict = {}
        sk.update(_pkg._tauri_log_files())
        sk.update(_spawn_flags(hidden=False))  # focus probe is intentionally foreground
        try:
            child = subprocess.Popen([binary], env=env, **sk)
            log.info(
                "[AUTOSTART] spawned tauri focus probe (pid=%s)",
                getattr(child, "pid", "?"),
            )
            return True
        except Exception:
            log.exception("[AUTOSTART] failed to spawn tauri focus probe")
            return False
        finally:
            _close_log_files(sk)

    # Legacy Electron focus path.
    exe = _pkg._electron_binary()
    if not exe or not _pkg._main_entry_built():
        # No lean binary available — caller falls back to npm run dev,
        # which itself will fail the lock and focus the existing window
        # (at the cost of spinning up a Vite server briefly).
        log.info("[AUTOSTART] lean electron unavailable; will use npm run dev to focus")
        return False

    spawn_kwargs: dict = dict(cwd=str(_pkg.CLIENT_DIR))
    # RACE-009: redirect Electron stdout/stderr to log files.
    spawn_kwargs.update(_pkg._electron_log_files())
    # _focus_running_app() always spawns the lean electron in the
    # foreground (hidden=False) so the user sees the focused window.
    spawn_kwargs.update(_spawn_flags(hidden=False))
    try:
        # ``electron .`` runs the app pointed at by package.json "main",
        # i.e. ./out/main/index.js.  VT_FOCUS_ONLY is a marker env var the
        # duplicate reads to know it should not attempt any heavy init.
        # same-app restart — full env intentionally inherited
        # (see _spawn_electron above for rationale). Only sensitive KEY
        # NAMES are logged for audit; values are never printed.
        # ``_launcher_child_env`` force-disables ANSI colour + npm notices
        # (the child's output is redirected to the electron/tauri log files).
        env = _launcher_child_env()
        env["VT_FOCUS_ONLY"] = "1"
        _log_sensitive_env_keys(env, context="autostart")
        child = subprocess.Popen([exe, "."], env=env, **spawn_kwargs)
        log.info(
            "[AUTOSTART] spawned lean electron to focus running instance (pid=%s)",
            getattr(child, "pid", "?"),
        )
        _close_log_files(spawn_kwargs)
        return True
    except Exception:
        log.exception("[AUTOSTART] failed to spawn lean electron for focus")
        _close_log_files(spawn_kwargs)
        return False
