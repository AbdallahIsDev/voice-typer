"""Electron launch paths for the autostart launcher.

Build-first strategy: run ``electron .`` against the pre-built bundles
(``out/main/index.js``); never build from source here. ``npm run dev``
is the last-resort fallback (build missing or ``--dev`` passed).
"""

from __future__ import annotations

import logging
import os
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


def _launch_electron_built(exe: str, hidden: bool = False) -> subprocess.Popen | None:
    """Launch the pre-built Electron app with ``electron .``.

    Parameters
    ----------
    exe : str
        Path to the electron binary.
    hidden : bool
        If True, sets ``VT_START_HIDDEN=1`` so the dashboard starts
        in the background (tray + bubble only).

    Returns the child process on success, or None on failure.
    """
    from voice_typer.server import autostart_launcher as _pkg

    # RACE-009: redirect Electron stdout/stderr to log files so
    # crashes are diagnosable. Pre-fix this used DEVNULL.
    sk = dict(cwd=str(_pkg.CLIENT_DIR))
    sk.update(_pkg._electron_log_files())
    sk.update(_spawn_flags(hidden=hidden))
    # intentional — same-app restart needs the same env.
    # The child here is the Voice Typer Electron frontend itself (not a
    # less-trusted process). It needs the full env for native module
    # loading, PATH resolution, and platform-specific init. Unlike the
    # Python IPC server restart path, Electron does NOT expose env vars
    # via IPC, so the risk of key exfiltration is lower. Stripping the
    # env would break the app's own functionality. The risk model would
    # only change if we ever spawn a DIFFERENT, less-trusted binary here.
    # ``_launcher_child_env`` force-disables ANSI colour + npm notices
    # (the child's output is redirected to the electron/tauri log files).
    env = _launcher_child_env()
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    # surface (without values) any sensitive env keys the
    # child will inherit, so a future leak in a downstream log is
    # auditable. Only KEY NAMES are logged — values are never printed.
    _log_sensitive_env_keys(env, context="autostart")
    try:
        child = subprocess.Popen([exe, "."], env=env, **sk)
        log.info(
            "[AUTOSTART] spawned electron . (child pid=%s, hidden=%s)",
            getattr(child, "pid", "?"),
            hidden,
        )
        # Close parent copies of log file handles — the child has
        # inherited them, so they remain open for the child's lifetime.
        # Without this, the parent leaks file handles and triggers
        # ResourceWarning on GC.
        _close_log_files(sk)
        return child
    except Exception:
        log.exception("[AUTOSTART] electron . failed")
        return None


def _ensure_built_and_launch(hidden: bool = False) -> bool:
    """Launch the pre-built Electron app with ``electron .``.

    Returns True if the app was launched successfully, False otherwise.

    The app is NEVER built from source here — the packaged install
    ships pre-built bundles (``out/main/index.js`` + renderer + preload)
    and the dev path
    uses ``npm run dev`` via :func:`_spawn_npm_run_dev`. When the
    pre-built output is missing we fail fast so the caller falls back
    to the dev path instead of silently invoking a build.

    Strategy:
      1. Find the dev-mode electron binary (``node_modules/electron/dist/``).
      2. Verify the build output (main + renderer + preload bundles)
         exists — if any is missing, return False (no auto-build).
      3. Launch ``electron .`` with the compiled bundles.
      4. If any step fails, return False so the caller can decide what
         to do (fall back to dev mode, show error, etc.).
    """
    from voice_typer.server import autostart_launcher as _pkg

    exe = _pkg._electron_binary()
    if not exe:
        log.warning("[AUTOSTART] electron binary not found -- cannot launch built app")
        return False

    if not _pkg._main_entry_built():
        log.warning(
            "[AUTOSTART] No pre-built output found -- launch via the dev path (npm run dev) or a packaged install"
        )
        return False

    child = _pkg._launch_electron_built(exe, hidden=hidden)
    if child is None:
        return False

    _pkg._write_pid_file(os.getpid(), getattr(child, "pid", None))
    return True


def _spawn_npm_run_dev(hidden: bool = False) -> subprocess.Popen | None:
    """Launch ``npm run dev`` (Vite dev server + Electron).

    This is the LAST-RESORT fallback path, used only when the build
    fails or when ``--dev`` is explicitly passed.
    """
    from voice_typer.server import autostart_launcher as _pkg

    spawn_kwargs: dict = dict(cwd=str(_pkg.CLIENT_DIR))
    # RACE-009: redirect Electron stdout/stderr to log files.
    spawn_kwargs.update(_pkg._electron_log_files())
    spawn_kwargs.update(_spawn_flags(hidden=hidden))
    # same-app restart — full env intentionally inherited
    # (see _spawn_electron above for rationale). Only sensitive KEY
    # NAMES are logged for audit; values are never printed.
    # ``_launcher_child_env`` force-disables ANSI colour + npm notices
    # (the child's output is redirected to the electron/tauri log files).
    env = _launcher_child_env()
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    _log_sensitive_env_keys(env, context="autostart")

    try:
        # S-7: prefer list form over shell=True.
        cmd = _pkg._npm_command("dev")
        if cmd is None:
            # S-7: npm truly not resolvable — log and bail (no shell=True).
            log.error(
                "[AUTOSTART] npm not found on PATH; cannot launch dev mode. Install Node.js / npm or add it to PATH."
            )
            _close_log_files(spawn_kwargs)
            return None
        child = subprocess.Popen(
            cmd,
            env=env,
            **spawn_kwargs,
        )
        # Close parent copies of log file handles — the child has
        # inherited them, so they remain open for the child's lifetime.
        _close_log_files(spawn_kwargs)
        log.info(
            "[AUTOSTART] spawned 'npm run dev' in %s (child pid=%s, hidden=%s)",
            _pkg.CLIENT_DIR,
            getattr(child, "pid", "?"),
            hidden,
        )
        return child
    except FileNotFoundError:
        log.exception("[AUTOSTART] npm not found")
        _close_log_files(spawn_kwargs)
        return None
    except Exception:
        log.exception("[AUTOSTART] failed to spawn npm run dev")
        _close_log_files(spawn_kwargs)
        return None
