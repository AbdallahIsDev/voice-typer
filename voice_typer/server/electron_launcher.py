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

import contextlib
import logging
import os
import secrets
import signal
import subprocess
import time

from voice_typer.server._electron_build import (
    CLIENT_DIR,
    _build_electron,
    _electron_binary,
    _electron_log_files,
    _log_sensitive_env_keys,
    _main_entry_built,
    _npm_command,
    _spawn_flags,
)
from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


# WN-12: import the canonical env-var name from the single source of
# truth. See voice_typer/server/_paths.py:IPC_TOKEN_ENV_VAR for the
# rationale. Bare literals elsewhere are now routed through this
# constant so a typo in any single file can't silently break IPC
# auth.
from voice_typer.server._paths import IPC_TOKEN_ENV_VAR  # noqa: E402

# env-var names that are ALWAYS stripped from the Electron
# child's environment, regardless of pattern matching. These are the
# well-known cloud-provider API keys / model download tokens that the
# Python backend NEVER reads from env (it uses the keyring instead).
# Listed explicitly so a typo in a future key name doesn't slip past the
# substring markers below.
_SENSITIVE_ENV_NAMES = frozenset(
    {
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "GEMINI_API_KEY",
        "HF_TOKEN",
        "HUGGING_FACE_HUB_TOKEN",
        "DEEPGRAM_API_KEY",
        "GROQ_API_KEY",
    }
)

# substring markers for sensitive env-var names. Any key whose
# UPPER-cased name contains one of these substrings is stripped from the
# child env (with the exception of the IPC token trio, which is restored
# AFTER stripping). The markers catch the common SaaS API-key / OS
# secret conventions (OPENAI_API_KEY, *_SECRET, *_TOKEN, *_PASSWORD,
# *_CREDENTIAL) without flagging benign vars (PATH, HOME, LANG,
# VT_PYTHON_PORT, etc.).
_SENSITIVE_ENV_MARKERS = (
    "API_KEY",
    "SECRET",
    "TOKEN",
    "PASSWORD",
    "CREDENTIAL",
)

# env-var names that are ALWAYS preserved even if they match a
# sensitive marker (because the Electron child needs them to talk back
# to the Python backend). The IPC token is the ONLY token the child
# needs; it is restored AFTER stripping in launch_electron_frontend.
_PRESERVED_ENV_NAMES = frozenset(
    {
        IPC_TOKEN_ENV_VAR,
        "VT_IPC_TOKEN",
        "VT_PYTHON_PORT",
    }
)


def _strip_sensitive_env(env: dict) -> None:
    """delete sensitive env vars from ``env`` in place.

    Strips:
      - The explicit ``_SENSITIVE_ENV_NAMES`` list
        (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, HF_TOKEN,
        HUGGING_FACE_HUB_TOKEN, DEEPGRAM_API_KEY, GROQ_API_KEY).
      - Any key whose upper-cased name contains one of
        ``_SENSITIVE_ENV_MARKERS`` (API_KEY / SECRET / TOKEN / PASSWORD
        / CREDENTIAL) — except the IPC token trio in
        ``_PRESERVED_ENV_NAMES`` which is needed by the child.

    The Python server reads NO API keys from env (cloud keys come from
    the keyring), so the Electron child has no legitimate need for
    them. Stripping prevents exfiltration via ``/proc/<pid>/environ``
    if the renderer is ever compromised.

    Parameters
    ----------
    env:
        The environment dict passed to :class:`subprocess.Popen`. It is
        mutated in place; nothing is returned.
    """
    keys = list(env.keys())
    for key in keys:
        if key in _PRESERVED_ENV_NAMES:
            continue
        if key in _SENSITIVE_ENV_NAMES:
            env.pop(key, None)
            continue
        upper = key.upper()
        if any(marker in upper for marker in _SENSITIVE_ENV_MARKERS):
            env.pop(key, None)


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
    # Guard the client directory BEFORE spawning. The pip-installed
    # backend has no ``voice_typer/client`` tree, so ``Popen(cwd=CLIENT_DIR)``
    # would raise ``NotADirectoryError`` ([WinError 267]) — a confusing
    # traceback for a user who ran ``voice-typer`` from a terminal. Fail
    # gracefully with an actionable message instead (the backend keeps
    # running in standalone mode with no UI).
    if not CLIENT_DIR.is_dir():
        log.warning(
            "[LAUNCHER] Electron client directory not found (%s) — cannot "
            "launch the UI. This backend has no bundled frontend (e.g. a "
            "pip install); run from a source checkout with "
            "`voice_typer/client`, or use the packaged desktop app.",
            CLIENT_DIR,
        )
        return None
    # Strip sensitive env vars the Electron child does not need.
    # The Python server reads NO API keys from env (cloud keys come from
    # the keyring). The IPC token trio (VOICE_TYPER_IPC_TOKEN /
    # VT_IPC_TOKEN / VT_PYTHON_PORT) and standard OS vars (PATH, HOME,
    # LANG, etc.) are kept; everything matching the sensitive-key
    # markers (``API_KEY`` / ``SECRET`` / ``TOKEN`` / ``PASSWORD`` /
    # ``CREDENTIAL``) is deleted so it cannot leak into the child's
    # ``/proc/<pid>/environ`` and be exfiltrated by a compromised
    # renderer. This converts the previous ``_log_sensitive_env_keys``
    # audit log into an enforcement point.
    env = dict(os.environ)
    _strip_sensitive_env(env)
    env["VT_PYTHON_PORT"] = str(port)
    env["VT_IPC_TOKEN"] = token
    # Also set VOICE_TYPER_IPC_TOKEN so the backend's TCP auth check
    # (which reads this env var) sees the same value we told Electron
    # to send. The IPC token is restored AFTER stripping so it is
    # guaranteed to be present in the child env.
    env[IPC_TOKEN_ENV_VAR] = token
    # surface (without values) any sensitive env keys the
    # parent had, so a future leak in a downstream log is auditable.
    # Only KEY NAMES are logged — values are never printed.
    # Short context label: the PII filter would otherwise mangle the
    # 20+ char function name (``electron_launcher.***``) in the audit line.
    _log_sensitive_env_keys(dict(os.environ), context="electron_launcher")

    spawn_kwargs: dict = dict(cwd=str(CLIENT_DIR))
    spawn_kwargs.update(_electron_log_files())
    spawn_kwargs.update(_spawn_flags(hidden=True))

    exe = _electron_binary()
    if exe and not _main_entry_built():
        log.info("[LAUNCHER] No pre-built output — building first")
        if not _build_electron():
            # include the operation inputs (exe path + CLIENT_DIR)
            # so operators can tell which build attempt failed.
            log.warning(
                "[LAUNCHER] Build failed (exe=%s, cwd=%s); will try npm run dev",
                exe,
                CLIENT_DIR,
            )
            exe = None
        elif not _main_entry_built():
            log.warning("[LAUNCHER] Build succeeded but out/main/index.js still missing")
            exe = None

    if exe:
        try:
            child = subprocess.Popen([exe, "."], env=env, **spawn_kwargs)
            pid = getattr(child, "pid", None)
            log.info(
                "[LAUNCHER] spawned electron . (pid=%s) on port=%d",
                pid,
                port,
            )
            return pid
        except Exception:
            # include the operation inputs (exe path + cwd) so
            # operators can tell which Electron binary was attempted
            # without having to dig through the rest of the launcher log.
            # ``log.exception`` already captures the underlying traceback.
            log.exception(
                "[LAUNCHER] electron . (exe=%s, cwd=%s) failed; will try npm run dev",
                exe,
                CLIENT_DIR,
            )

    # Fallback: npm run dev (Vite dev server + Electron).
    # Pre-bind ``cmd`` to None so the ``except`` handlers below have a
    # defined value to log even when ``_npm_command("dev")`` itself
    # raises (e.g. an ``OSError`` from the underlying ``shutil.which``
    # lookup) before the assignment completes.
    cmd: list[str] | None = None
    try:
        cmd = _npm_command("dev")
        if cmd is None:
            # S-7: npm truly not resolvable — log and bail (no shell=True).
            log.error(
                "[LAUNCHER] npm not found on PATH; cannot launch dev mode. Install Node.js / npm or add it to PATH."
            )
            return None
        child = subprocess.Popen(cmd, env=env, **spawn_kwargs)
        pid = getattr(child, "pid", None)
        log.info(
            "[LAUNCHER] spawned npm run dev (pid=%s) on port=%d",
            pid,
            port,
        )
        return pid
    except FileNotFoundError:
        # include the resolved npm command (cmd) so operators
        # can see which npm path the launcher tried to exec when the
        # FileNotFoundError was raised.
        log.exception("[LAUNCHER] npm not found (cmd=%s)", cmd)
        return None
    except Exception:
        # include the operation inputs (cmd + port) so operators
        # can tell which npm invocation failed and which backend port
        # was supposed to receive the dev-server connection.
        log.exception(
            "[LAUNCHER] spawn npm run dev (cmd=%s, port=%d) failed",
            cmd,
            port,
        )
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
        with contextlib.suppress(OSError, ProcessLookupError):
            os.kill(pid, signal.SIGKILL)
        with contextlib.suppress(OSError, ChildProcessError):
            os.waitpid(pid, 0)
    except Exception:
        log.debug("[LAUNCHER] terminate_electron(%s) failed", pid, exc_info=True)


def generate_session_token() -> str:
    """Generate a 32-byte hex session token for IPC auth.

    Uses ``secrets.token_hex`` (cryptographically secure) for 256 bits
    of entropy — matches the token size Electron generates on its side.
    """
    return secrets.token_hex(32)
