"""Hidden launcher that starts Electron dev mode at login.

Called by the OS autostart entry (Windows Registry Run key, macOS
LaunchAgent, or Linux ``.desktop``) at login.  Runs via ``pythonw`` /
``&`` so no console window flashes.  It spawns ``npm run dev`` in the
client directory, detached and hidden, then exits — the long-lived
processes are Electron + the Python IPC backend that ``npm run dev``
itself spawns.

Cross-platform behaviour
------------------------
- **Windows**: launched as ``pythonw.exe autostart_launcher.py`` (no
  console).  ``npm run dev`` is spawned with ``CREATE_NO_WINDOW``.
- **macOS / Linux**: launched as ``python3 autostart_launcher.py``.
  ``npm run dev`` is spawned detached (``start_new_session=True``) so it
  survives this launcher exiting.

Idempotency
-----------
Before launching, the script checks whether the IPC port (9876) is
already listening.  If so, an Electron backend is already running (e.g.
the user logged in twice, or a previous launcher is still alive) and the
script exits silently — no double launch, no mutex conflict.

PID tracking
------------
Writes its own PID and the spawned child's PID to
``~/.voice-typer/autostart.pid`` so Electron's ``killStalePython()``
reaper and other tooling can discover and clean up the autostarted
session when the user starts Electron manually.

Usage
-----
Invoked directly by the OS; not intended to be run by hand, but safe to
invoke for diagnostics::

    python -m voice_typer.server.autostart_launcher
"""

from __future__ import annotations

import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("voice_typer.autostart")

# Directory layout:
#   <root>/
#     voice_typer/
#       server/
#         autostart_launcher.py   <- this file
#       client/                    <- Electron app
# voice_typer/server -> voice_typer -> root
BASE_DIR = Path(__file__).resolve().parent.parent.parent
CLIENT_DIR = BASE_DIR / "voice_typer" / "client"

IPC_HOST = "127.0.0.1"
IPC_PORT = 9876

# Where to write the PID file (under the app's data dir).
def _config_dir() -> Path:
    return Path.home() / ".voice-typer"


def _pid_file() -> Path:
    return _config_dir() / "autostart.pid"


def _setup_logging() -> None:
    """Minimal logging to the app log file (no console — we run hidden)."""
    try:
        from voice_typer.server.config import _config_dir as _cfg
        log_dir = _cfg()
        log_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        log_dir = _config_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_dir / "voice-typer.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root = logging.getLogger("voice_typer")
    root.setLevel(logging.INFO)
    # Avoid duplicate handlers if this process is somehow re-entered.
    if not any(isinstance(h, logging.FileHandler) for h in root.handlers):
        root.addHandler(handler)


def _is_port_open(host: str, port: int) -> bool:
    """Return True if *host:port* accepts TCP connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def _client_dir_exists() -> bool:
    """Return True if the Electron client directory (with package.json) exists."""
    return CLIENT_DIR.is_dir() and (CLIENT_DIR / "package.json").exists()


def _spawn_flags() -> dict:
    """Platform-specific kwargs for spawning ``npm run dev`` hidden."""
    kwargs: dict = {}
    if sys.platform == "win32":
        # CREATE_NO_WINDOW (0x08000000) prevents a console from flashing.
        kwargs["creationflags"] = 0x08000000
    else:
        # Detach into a new session so the child survives this launcher.
        kwargs["start_new_session"] = True
    return kwargs


def _npm_command() -> list[str] | None:
    """Return the command list to run ``npm run dev``, or None if npm
    can't be resolved on this platform."""
    # On Windows npm is npm.cmd (a batch file) — must go through cmd.exe
    # when shell=True, or resolve the .cmd path directly.
    if sys.platform == "win32":
        # ``shell=True`` with "npm run dev" finds npm.cmd via PATHEXT.
        return None  # signal: use shell=True form
    # POSIX: npm is on PATH.
    return ["npm", "run", "dev"]


def _write_pid_file(launcher_pid: int, child_pid: int | None) -> None:
    """Persist our PID + the child's PID for the reaper to find."""
    try:
        _config_dir().mkdir(parents=True, exist_ok=True)
        _pid_file().write_text(
            f"launcher={launcher_pid}\nchild={child_pid or ''}\n",
            encoding="utf-8",
        )
    except OSError as exc:
        log.warning("[AUTOSTART] could not write pid file: %s", exc)


def launch() -> int:
    """Launch ``npm run dev`` hidden and exit.

    Returns a process exit code (0 = success).
    """
    _setup_logging()
    log.info("[AUTOSTART] launcher starting (pid=%d)", os.getpid())

    # 1. Idempotency: if the backend port is already listening, an
    #    Electron session is already up — don't start a second one.
    if _is_port_open(IPC_HOST, IPC_PORT):
        log.info(
            "[AUTOSTART] port %d already in use — backend already running, "
            "exiting without launching", IPC_PORT,
        )
        return 0

    # 2. Locate the client directory.
    if not _client_dir_exists():
        log.error(
            "[AUTOSTART] client directory not found at %s — cannot launch "
            "Electron dev mode", CLIENT_DIR,
        )
        return 1

    # 3. Spawn ``npm run dev`` detached + hidden.
    spawn_kwargs: dict = dict(
        cwd=str(CLIENT_DIR),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )
    spawn_kwargs.update(_spawn_flags())

    child: subprocess.Popen | None = None
    try:
        if sys.platform == "win32":
            # shell=True lets cmd.exe resolve npm.cmd via PATHEXT.
            child = subprocess.Popen(
                "npm run dev", shell=True, **spawn_kwargs,
            )
        else:
            child = subprocess.Popen(
                ["npm", "run", "dev"], **spawn_kwargs,
            )
        log.info(
            "[AUTOSTART] spawned 'npm run dev' in %s (child pid=%s)",
            CLIENT_DIR, getattr(child, "pid", "?"),
        )
    except FileNotFoundError as exc:
        log.error("[AUTOSTART] npm not found: %s", exc)
        return 1
    except Exception:
        log.exception("[AUTOSTART] failed to spawn npm run dev")
        return 1

    # 4. Record PIDs so the reaper can find this session later.
    _write_pid_file(os.getpid(), getattr(child, "pid", None))

    # 5. Brief wait to let the child initialize, then exit.  The child is
    #    detached, so it keeps running after we exit.  We don't wait for
    #    Electron fully (it may take 10-30s) — login shouldn't block.
    time.sleep(2)
    log.info("[AUTOSTART] launcher exiting; child continues detached")
    return 0


def main() -> int:
    return launch()


if __name__ == "__main__":
    sys.exit(main())
