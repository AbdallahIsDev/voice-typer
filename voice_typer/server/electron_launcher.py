"""Launch the Electron frontend from the standalone Python backend.

P1-1.2: When the user runs ``VoiceTyper`` from a terminal (not spawned by
Electron), the Python backend is the parent process.  This module provides
the logic to spawn Electron as a subprocess and pass it the connection
info so Electron connects to the backend's TCP port instead of spawning
its own Python backend.

Architecture
------------
Normal (Electron-spawns-Python) flow::

    Electron main → spawn python -m voice_typer.server.ipc_server --port 9876
                  → set VOICE_TYPER_IPC_TOKEN env var
                  → connect TCP to 127.0.0.1:9876, send auth line

Standalone (terminal-spawns-Python) flow, enabled by this module::

    Python backend → start TCP server on 9876 (auto-increment if busy)
                   → generate session token, set VOICE_TYPER_IPC_TOKEN
                   → spawn Electron with VT_PYTHON_PORT + VT_IPC_TOKEN env vars
                   → Electron's main process detects these env vars and
                     connects directly to 127.0.0.1:VT_PYTHON_PORT,
                     skipping its own Python spawn

Build-first strategy mirrors ``autostart_launcher._ensure_built_and_launch``:
if the Electron app has been built (``out/main/index.js`` exists), it runs
``electron .`` directly; otherwise it runs ``npm run build`` first, then
``electron .``.  ``npm run dev`` is the last-resort fallback.
"""

from __future__ import annotations

import logging
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

from voice_typer.server.platform_utils import is_windows, is_macos, is_linux

log = logging.getLogger("voice_typer.electron_launcher")

# Directory layout (mirrors autostart_launcher.CLIENT_DIR):
#   <root>/
#     voice_typer/
#       server/
#         electron_launcher.py   <- this file
#       client/                   <- Electron app
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLIENT_DIR = BASE_DIR / "voice_typer" / "client"


def is_spawned_by_electron() -> bool:
    """Return True if this Python process was spawned by Electron.

    Detection rules (any one is sufficient):

    1. ``--port`` is in ``sys.argv`` — Electron passes ``--port <N>``
       when spawning the backend so it listens on TCP instead of
       stdin/stdout.
    2. ``VOICE_TYPER_IPC_TOKEN`` env var is set — Electron sets this
       before spawning so the backend can authenticate the TCP client.

    Both signals are reliable: a user running ``VoiceTyper`` from the
    terminal won't accidentally set either of them.
    """
    if "--port" in sys.argv:
        return True
    if os.environ.get("VOICE_TYPER_IPC_TOKEN"):
        return True
    return False


def _electron_binary() -> str | None:
    """Return the path to the dev-mode Electron binary, or None if absent.

    In dev mode Electron ships under
    ``node_modules/electron/dist/electron.exe`` (Windows) /
    ``.../electron`` (POSIX).
    """
    if is_windows():
        candidate = CLIENT_DIR / "node_modules" / "electron" / "dist" / "electron.exe"
    else:
        candidate = CLIENT_DIR / "node_modules" / "electron" / "dist" / "electron"
    return str(candidate) if candidate.exists() else None


def _main_entry_built() -> bool:
    """Return True if the compiled Electron main bundle exists.

    ``electron .`` loads ``out/main/index.js`` (the electron-vite build
    output).  If the client has never been built (fresh checkout, deleted
    ``out/``), this is False and we must build first.
    """
    return (CLIENT_DIR / "out" / "main" / "index.js").exists()


def _npm_command(script: str = "dev") -> list[str] | None:
    """Return the command list to run ``npm run <script>``.

    Resolves the npm path explicitly via ``shutil.which`` so the list
    form works on Windows too (where npm is npm.cmd).  Returns None if
    npm can't be resolved (caller falls back to ``shell=True``).
    """
    import shutil

    npm_path = shutil.which("npm")
    if npm_path is not None:
        return [npm_path, "run", script]
    return None


def _spawn_flags() -> dict:
    """Platform-specific kwargs for spawning the Electron child."""
    kwargs: dict = {}
    if is_windows():
        # CREATE_NO_WINDOW (0x08000000) prevents a console from flashing.
        kwargs["creationflags"] = 0x08000000
    else:
        # Detach into a new session so the child survives this backend.
        kwargs["start_new_session"] = True
    return kwargs


def _electron_log_files() -> dict:
    """Open rotating log files for Electron stdout/stderr (best-effort).

    Returns a dict suitable for unpacking into ``subprocess.Popen``.
    Falls back to ``DEVNULL`` on any failure so the launch still succeeds.
    """
    try:
        from voice_typer.server.config import _config_dir as _cfg

        log_dir = _cfg() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "electron-stdout.log"
        stderr_path = log_dir / "electron-stderr.log"
        # "a" mode so logs accumulate across launches; line-buffered so
        # the user sees output in near-real-time when tailing.
        stdout_fd = open(stdout_path, "a", encoding="utf-8", buffering=1)
        stderr_fd = open(stderr_path, "a", encoding="utf-8", buffering=1)
        return {
            "stdout": stdout_fd,
            "stderr": stderr_fd,
            "stdin": subprocess.DEVNULL,
        }
    except Exception as exc:
        log.debug("[LAUNCHER] Failed to open Electron log files: %s", exc)
        return {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }


def _build_electron() -> bool:
    """Run ``npm run build`` to produce the compiled Electron bundles.

    Returns True on success, False on failure.
    """
    log.info("[LAUNCHER] Building Electron app (npm run build)...")
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
            result = subprocess.run(
                "npm run build",
                cwd=str(CLIENT_DIR),
                shell=True,
                capture_output=True,
                timeout=180,
            )
        if result.returncode == 0:
            log.info("[LAUNCHER] npm run build succeeded")
            return True
        log.warning(
            "[LAUNCHER] npm run build failed (exit=%d): %s",
            result.returncode,
            result.stderr.decode("utf-8", errors="replace")[-500:],
        )
        return False
    except subprocess.TimeoutExpired:
        log.warning("[LAUNCHER] npm run build timed out after 180s")
        return False
    except Exception as exc:
        log.warning("[LAUNCHER] npm run build raised: %s", exc)
        return False


def launch_electron_frontend(port: int, token: str) -> int | None:
    """Launch the Electron frontend as a subprocess.

    Strategy (mirrors ``autostart_launcher._ensure_built_and_launch``):

    1. Locate the dev-mode Electron binary.
    2. If ``out/main/index.js`` is missing, run ``npm run build`` first.
    3. Spawn ``electron .`` with ``VT_PYTHON_PORT`` + ``VT_IPC_TOKEN`` +
       ``VOICE_TYPER_IPC_TOKEN`` env vars so Electron's main process
       connects to our TCP port instead of spawning its own Python
       backend.
    4. If Electron binary is missing or build fails, fall back to
       ``npm run dev``.

    Parameters
    ----------
    port:
        The TCP port our backend is listening on.
    token:
        The session token Electron must send as the first TCP line to
        authenticate.  Also passed via ``VOICE_TYPER_IPC_TOKEN`` so the
        backend's auth check (which reads that env var) matches.

    Returns
    -------
    The child PID on success, or None on failure.
    """
    env = dict(os.environ)
    env["VT_PYTHON_PORT"] = str(port)
    env["VT_IPC_TOKEN"] = token
    # Also set VOICE_TYPER_IPC_TOKEN so the backend's TCP auth check
    # (which reads this env var) sees the same value we told Electron
    # to send.
    env["VOICE_TYPER_IPC_TOKEN"] = token

    spawn_kwargs: dict = dict(cwd=str(CLIENT_DIR))
    spawn_kwargs.update(_electron_log_files())
    spawn_kwargs.update(_spawn_flags())

    exe = _electron_binary()
    if exe:
        if not _main_entry_built():
            log.info("[LAUNCHER] No pre-built output — building first")
            if not _build_electron():
                log.warning("[LAUNCHER] Build failed; will try npm run dev")
                exe = None
            elif not _main_entry_built():
                log.warning(
                    "[LAUNCHER] Build succeeded but out/main/index.js still missing"
                )
                exe = None

    if exe:
        try:
            child = subprocess.Popen([exe, "."], env=env, **spawn_kwargs)
            pid = getattr(child, "pid", None)
            log.info(
                "[LAUNCHER] spawned electron . (pid=%s) on port=%d",
                pid, port,
            )
            return pid
        except Exception:
            log.exception("[LAUNCHER] electron . failed; will try npm run dev")

    # Fallback: npm run dev (Vite dev server + Electron).
    try:
        cmd = _npm_command("dev")
        if cmd is not None:
            child = subprocess.Popen(cmd, env=env, **spawn_kwargs)
        else:
            child = subprocess.Popen(
                "npm run dev", shell=True, env=env, **spawn_kwargs,
            )
        pid = getattr(child, "pid", None)
        log.info(
            "[LAUNCHER] spawned npm run dev (pid=%s) on port=%d",
            pid, port,
        )
        return pid
    except FileNotFoundError as exc:
        log.error("[LAUNCHER] npm not found: %s", exc)
        return None
    except Exception:
        log.exception("[LAUNCHER] failed to spawn npm run dev")
        return None


def terminate_electron(pid: int) -> None:
    """Terminate an Electron subprocess cleanly.

    Sends SIGTERM, waits up to 3 seconds for graceful exit, then SIGKILL
    if still alive.  On Windows, uses ``taskkill /T /F`` to kill the
    entire process tree (Electron spawns child renderer/GPU processes
    that don't die on a single SIGTERM).

    Best-effort: any exception is logged at DEBUG and swallowed so the
    shutdown path doesn't fail.
    """
    if not pid:
        return
    try:
        if is_windows():
            # taskkill /T /F kills the entire process tree.
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
            return
        # POSIX: SIGTERM, wait 3s, SIGKILL if still alive.
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError):
            return
        deadline = time.time() + 3.0
        while time.time() < deadline:
            try:
                pid_result, _ = os.waitpid(pid, os.WNOHANG)
                if pid_result != 0:
                    return  # reaped
            except (OSError, ChildProcessError):
                return
            time.sleep(0.1)
        # Still alive — SIGKILL.
        try:
            os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            os.waitpid(pid, 0)
        except (OSError, ChildProcessError):
            pass
    except Exception:
        log.debug("[LAUNCHER] terminate_electron(%s) failed", pid, exc_info=True)


def generate_session_token() -> str:
    """Generate a 32-byte hex session token for IPC auth.

    Uses ``secrets.token_hex`` (cryptographically secure) for 256 bits
    of entropy — matches the token size Electron generates on its side.
    """
    return secrets.token_hex(32)
