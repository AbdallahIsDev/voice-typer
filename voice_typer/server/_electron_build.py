"""Shared Electron-build helpers for the two launcher modules.

Single source of truth for the **build-first → ``electron .`` → ``npm run dev``
fallback** strategy that was previously copy-pasted into both
:mod:`voice_typer.server.electron_launcher` and
:mod:`voice_typer.server.autostart_launcher`.

Why this module exists
----------------------
The two launchers each defined their own copies of ``_electron_binary``,
``_main_entry_built``, ``_npm_command``, ``_spawn_flags``,
``_electron_log_files`` and ``_build_electron``.  Bug fixes had to be
applied to both copies, and they had already drifted in intent:

* ``autostart_launcher._npm_command`` carried the NEW-CQ-033 / NEW-SEC-009
  fix (avoid ``shell=True`` on POSIX when ``shutil.which("npm")`` misses
  by returning the list form ``["npm", "run", script]``), while
  ``electron_launcher._npm_command`` still returned ``None`` (forcing
  ``shell=True``).
* ``autostart_launcher._spawn_flags`` took a ``hidden`` kwarg so the
  autostart-at-login path could suppress the Windows console flash while
  the desktop-shortcut path left normal creation flags.  The electron
  launcher had no such knob because it always wants the hidden behaviour.

Unifying on this module means future fixes land in one place.  All
functions are stateless; platform detection goes through
:mod:`voice_typer.server.platform_utils` so Windows / macOS / Linux all
behave correctly.

Strategy summary
----------------
1. **Locate** the dev-mode Electron binary
   (``node_modules/electron/dist/electron[.exe]``).
2. **Build if needed**: if ``out/main/index.js`` is missing, run
   ``npm run build`` to produce the compiled bundles.
3. **Launch ``electron .``** with ``VT_PYTHON_PORT`` / ``VT_IPC_TOKEN``
   env vars (set by the caller) so Electron's main process connects to
   the Python backend instead of spawning its own.
4. **Last-resort fallback**: if the binary is missing or the build
   fails, fall back to ``npm run dev`` (Vite dev server + Electron).

The orchestration of these steps (when to fall back, what env vars to
set, what to do with the child PID) lives in the launcher modules — this
file only provides the primitives.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)

# Directory layout (mirrors both launcher modules):
#   <root>/
#     voice_typer/
#       server/
#         _electron_build.py    <- this file
#       client/                  <- Electron app
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLIENT_DIR = BASE_DIR / "voice_typer" / "client"


def _electron_binary() -> str | None:
    """Return the path to the dev-mode Electron binary, or ``None`` if absent.

    In dev mode Electron ships under
    ``node_modules/electron/dist/electron.exe`` (Windows) /
    ``.../electron`` (POSIX).  Returns ``None`` when not found, in which
    case the caller falls back to ``npm run dev`` (which itself starts
    Electron via the npm script).
    """
    if is_windows():
        candidate = CLIENT_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
    else:
        candidate = CLIENT_DIR / "node_modules" / "electron" / "dist" / "electron"
    return str(candidate) if candidate.exists() else None


def _main_entry_built() -> bool:
    """Return ``True`` if the compiled Electron main bundle exists.

    ``electron .`` loads ``out/main/index.js`` (the electron-vite build
    output).  If the client has never been built (fresh checkout,
    deleted ``out/``), this is ``False`` and the caller must run
    ``npm run build`` first — or fall back to ``npm run dev``, which
    builds-and-runs in one step.
    """
    return (CLIENT_DIR / "out" / "main" / "index.js").exists()


def _npm_command(script: str = "dev") -> list[str] | None:
    """Return the command list to run ``npm run <script>``.

    Parameters
    ----------
    script : str
        npm script name, e.g. ``"dev"`` or ``"build"``.

    Returns
    -------
    list[str] | None
        The argv list to pass to :class:`subprocess.Popen`, or ``None``
        to signal the caller to use the ``shell=True`` form (Windows
        only, when ``npm.cmd`` can't be resolved).

    NEW-CQ-033 / NEW-SEC-009: On Windows, npm is ``npm.cmd`` (a batch
    file).  Previously this returned ``None`` to signal "use
    ``shell=True``" which propagated PATH/env to a shell.  We now
    resolve the .cmd path directly via :func:`shutil.which` so we can
    use the list form (no shell) on every platform.  Falls back to
    ``shell=True`` only on Windows when ``npm.cmd`` can't be found.
    On POSIX, when ``shutil.which`` misses, we still return the list
    form ``["npm", "run", script]`` so :func:`subprocess.Popen` does
    the PATH lookup itself (functionally equivalent to the shell form
    but without spawning ``/bin/sh``).
    """
    import shutil

    npm_path = shutil.which("npm")
    if npm_path is not None:
        return [npm_path, "run", script]
    # Fallback for unusual setups where npm isn't on PATH.
    if is_windows():
        return None  # signal: use shell=True form (npm.cmd resolution)
    return ["npm", "run", script]


def _spawn_flags(hidden: bool = False) -> dict:
    """Platform-specific kwargs for spawning the Electron child process.

    Parameters
    ----------
    hidden : bool
        If ``True`` (autostart at login), prevents console windows from
        flashing on Windows by adding ``CREATE_NO_WINDOW``.  If
        ``False`` (default — used by ``electron_launcher`` and by the
        autostart desktop-shortcut path), Windows child processes get
        normal process creation so they can create their own console
        windows if needed (e.g. for ``npm run dev``).

    On POSIX, the child is detached into a new session
    (``start_new_session=True``) so it survives the launcher process
    exiting — this is required for both the autostart path (the
    launcher exits immediately after spawning) and the standalone
    backend path (the backend may exit before Electron does).
    """
    kwargs: dict = {}
    if is_windows():
        if hidden:
            # CREATE_NO_WINDOW (0x08000000) prevents a console from
            # flashing during autostart (the user is logging in, not
            # clicking a shortcut).
            kwargs["creationflags"] = 0x08000000
        # else: no creation flags — processes get normal console
        # behavior, which lets `npm run dev` open its own console.
    else:
        # Detach into a new session so the child survives this launcher.
        kwargs["start_new_session"] = True
    return kwargs


def _electron_log_files() -> dict:
    """Open rotating log files for Electron stdout/stderr (best-effort).

    Returns a dict suitable for unpacking into :class:`subprocess.Popen`::

        sk = {}
        sk.update(_electron_log_files())
        child = subprocess.Popen([...], **sk)

    RACE-009: pre-fix, Electron launches used :data:`subprocess.DEVNULL`
    for stdout/stderr, making Electron crashes invisible.  We now
    redirect to appending log files in the platform-aware config dir so
    crashes can be diagnosed.  The caller is responsible for keeping
    the returned file objects alive for the lifetime of the child
    process (they're closed automatically by GC after the child exits
    and the file descriptors are inherited by the child).

    On any failure (disk full, permission denied), falls back to
    :data:`subprocess.DEVNULL` so the launch still succeeds.
    """
    try:
        from voice_typer.server.config import _config_dir as _cfg

        log_dir = _cfg() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "electron-stdout.log"
        stderr_path = log_dir / "electron-stderr.log"
        # "a" mode so logs accumulate across launches; line-buffered so
        # the user sees output in near-real-time when tailing.
        stdout_fd = open(stdout_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        stderr_fd = open(stderr_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        return {
            "stdout": stdout_fd,
            "stderr": stderr_fd,
            "stdin": subprocess.DEVNULL,
        }
    except Exception as exc:
        log.debug(
            "[ELECTRON_BUILD] Failed to open Electron log files, using DEVNULL: %s",
            exc,
        )
        return {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }


def _build_electron() -> bool:
    """Run ``npm run build`` to produce the compiled Electron bundles.

    Returns ``True`` on success, ``False`` on failure.  On success,
    ``out/main/index.js``, ``out/preload/index.js``, and the renderer
    bundles will all be present.

    Uses :func:`_npm_command` to resolve the npm path (NEW-CQ-033 /
    NEW-SEC-009: prefer the list form to avoid ``shell=True`` so we
    don't propagate PATH/env to a shell).  Falls back to the shell form
    only on Windows when ``npm.cmd`` can't be resolved.

    Captures stdout/stderr and logs the last 500 chars of stderr on
    failure for diagnosability.  Times out after 180 seconds — long
    enough for a cold Vite build, short enough that a hung build
    doesn't wedge the launcher.
    """
    log.info("[ELECTRON_BUILD] Building Electron app (npm run build)...")
    try:
        cmd = _npm_command("build")
        if cmd is not None:
            result = subprocess.run(
                cmd,
                cwd=str(CLIENT_DIR),
                capture_output=True,
                timeout=180,
            )
        else:
            # Fallback: shell=True (npm.cmd not on PATH on Windows)
            result = subprocess.run(
                "npm run build",
                cwd=str(CLIENT_DIR),
                shell=True,
                capture_output=True,
                timeout=180,
            )
        if result.returncode == 0:
            log.info("[ELECTRON_BUILD] npm run build succeeded")
            return True
        log.warning(
            "[ELECTRON_BUILD] npm run build failed (exit=%d): %s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace")[-500:],
        )
        return False
    except subprocess.TimeoutExpired:
        log.warning("[ELECTRON_BUILD] npm run build timed out after 180s")
        return False
    except Exception as exc:
        log.warning("[ELECTRON_BUILD] npm run build raised: %s", exc)
        return False
