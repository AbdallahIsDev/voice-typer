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

from voice_typer.server._electron_build import (
    CLIENT_DIR,
    _build_electron,
    _electron_binary,
    _electron_log_files,
    _main_entry_built,
    _npm_command,
    _spawn_flags,
)
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger("voice_typer.electron_launcher")


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
    spawn_kwargs.update(_spawn_flags(hidden=True))

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
