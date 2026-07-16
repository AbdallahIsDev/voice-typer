"""Universal launcher for Voice Typer (production preferred, dev fallback).

Called by the OS autostart entry (Windows Registry Run key, macOS
LaunchAgent, or Linux ``.desktop``) at login, and by the ``create_launcher_shortcut``
Desktop / Start Menu entries.

Architecture
------------
The launcher uses a **build-first** strategy: if the Electron app has been
built (``out/main/index.js`` exists), it runs ``electron .`` directly —
no Vite dev server, no HMR watcher, just the compiled production bundles.
If the build output is missing, it runs ``npm run build`` first, then
``electron .``.  ``npm run dev`` is used ONLY as a last-resort fallback
when the build fails or when the user explicitly passes ``--dev``.

This means the app starts faster and uses less memory in normal use;
the Vite dev server is exclusively for development.

Cross-platform behaviour
------------------------
- **Windows**: launched as ``pythonw.exe autostart_launcher.py`` (no
  console).  ``npm run build`` / ``npm run dev`` are spawned with
  ``CREATE_NO_WINDOW``.
- **macOS / Linux**: launched as ``python3 autostart_launcher.py``.
  Build/dev are spawned detached (``start_new_session=True``) so they
  survive this launcher exiting.

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

    python -m voice_typer.server.autostart_launcher       # build + run
    python -m voice_typer.server.autostart_launcher --dev  # force dev mode
"""

from __future__ import annotations

import contextlib
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from voice_typer.server import _paths
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

log = logging.getLogger(__name__)


def _close_log_files(sk: dict) -> None:
    """Close Electron log file handles in the parent process.

    Called after ``subprocess.Popen`` to close the parent's copies of
    the stdout/stderr log files.  The child process has inherited the
    file descriptors, so the files remain open for the child's lifetime.
    Without this, the parent leaks file handles and triggers
    ``ResourceWarning`` on GC.
    """
    for key in ("stdout", "stderr"):
        fd = sk.get(key)
        if fd is not None and fd is not subprocess.DEVNULL:
            with contextlib.suppress(Exception):
                fd.close()


# Directory layout (mirrors ``_electron_build.CLIENT_DIR``):
#   <root>/
#     voice_typer/
#       server/
#         autostart_launcher.py   <- this file
#       client/                    <- Electron app
# CLIENT_DIR is re-exported from _electron_build so tests that monkeypatch
# ``voice_typer.server.autostart_launcher.CLIENT_DIR`` still work.

IPC_HOST = "127.0.0.1"
IPC_PORT = 9876


# Where to write the PID file (under the app's data dir).
# RW-7: delegates to voice_typer.server._paths so the path respects the
# platform-aware _config_dir() logic (Windows %APPDATA%, macOS
# ~/Library/Application Support, Linux $XDG_DATA_HOME, the
# VOICE_TYPER_CONFIG_DIR override, and the legacy ~/.voice-typer
# migration check) instead of the previous hardcoded Path.home() /
# ".voice-typer".
def _config_dir() -> Path:
    """Return the voice-typer data directory.

    RW-7: thin wrapper around :func:`voice_typer.server._paths.config_dir`
    kept for backwards compatibility — tests monkeypatch this name to
    redirect PID-file writes to a tmp dir.
    """
    return _paths.config_dir()


def _pid_file() -> Path:
    """Return the path to the autostart launcher's PID file.

    RW-7: derives from :func:`_config_dir` (not ``_paths.pid_file``
    directly) so tests that monkeypatch this module's ``_config_dir``
    continue to redirect the PID file as well.
    """
    return _config_dir() / "autostart.pid"


def _setup_logging() -> None:
    """Minimal logging to the app log file (no console — we run hidden)."""
    # RW-7: _config_dir() already delegates to _paths.config_dir() which
    # delegates to config._config_dir(). The previous try/except fallback
    # to Path.home() / ".voice-typer" was needed when the local
    # _config_dir() bypassed config.py; now that both go through the
    # canonical resolution chain, the fallback is no longer needed.
    log_dir = _config_dir()
    from voice_typer.server.log import setup_logging as _setup_logging_shared

    _setup_logging_shared(log_dir)


def _is_port_open(host: str, port: int) -> bool:
    """Return True if *host:port* accepts TCP connections."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1.0)
        return s.connect_ex((host, port)) == 0


def _client_dir_exists() -> bool:
    """Return True if the Electron client directory (with package.json) exists."""
    return CLIENT_DIR.is_dir() and (CLIENT_DIR / "package.json").exists()


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
    # RACE-009: redirect Electron stdout/stderr to log files so
    # crashes are diagnosable. Pre-fix this used DEVNULL.
    sk = dict(cwd=str(CLIENT_DIR))
    sk.update(_electron_log_files())
    sk.update(_spawn_flags(hidden=hidden))
    # NEW-PRIV-003: intentional — same-app restart needs the same env.
    # The child here is the Voice Typer Electron frontend itself (not a
    # less-trusted process). It needs the full env for native module
    # loading, PATH resolution, and platform-specific init. Unlike the
    # Python IPC server restart path, Electron does NOT expose env vars
    # via IPC, so the risk of key exfiltration is lower. Stripping the
    # env would break the app's own functionality. The risk model would
    # only change if we ever spawn a DIFFERENT, less-trusted binary here.
    env = dict(os.environ)
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    # NEW-PRIV-003: surface (without values) any sensitive env keys the
    # child will inherit, so a future leak in a downstream log is
    # auditable. Only KEY NAMES are logged — values are never printed.
    _log_sensitive_env_keys(env, context="autostart_launcher._spawn_electron")
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


def _write_pid_file(launcher_pid: int, child_pid: int | None) -> None:
    """Persist our PID + the child's PID for the reaper to find.

    SEC-003: Uses _secure_atomic_write to ensure 0o600 permissions
    on POSIX and O_NOFOLLOW symlink protection.
    """
    try:
        _config_dir().mkdir(parents=True, exist_ok=True)
        from voice_typer.server.config import _secure_atomic_write

        _secure_atomic_write(
            _pid_file(),
            f"launcher={launcher_pid}\nchild={child_pid or ''}\n",
        )
    except OSError as exc:
        log.warning("[AUTOSTART] could not write pid file: %s", exc)


def _ensure_built_and_launch(hidden: bool = False) -> bool:
    """Build the Electron app if needed, then launch with ``electron .``.

    Returns True if the app was launched successfully, False otherwise.

    Strategy:
      1. Find the dev-mode electron binary (``node_modules/electron/dist/``).
      2. If the build output (``out/main/index.js``) is absent, run
         ``npm run build`` first.
      3. Launch ``electron .`` with the compiled bundles.
      4. If any step fails, return False so the caller can decide what
         to do (fall back to dev mode, show error, etc.).
    """
    exe = _electron_binary()
    if not exe:
        log.warning("[AUTOSTART] electron binary not found -- cannot build+launch")
        return False

    if not _main_entry_built():
        log.info("[AUTOSTART] No pre-built output found -- building first")
        if not _build_electron():
            log.warning("[AUTOSTART] Build failed; cannot launch built app")
            return False
        if not _main_entry_built():
            log.warning("[AUTOSTART] Build succeeded but out/main/index.js still missing")
            return False

    child = _launch_electron_built(exe, hidden=hidden)
    if child is None:
        return False

    _write_pid_file(os.getpid(), getattr(child, "pid", None))
    return True


def _focus_running_app() -> bool:
    """Wake an already-running Electron instance via its single-instance lock.

    Spawns a LEAN second ``electron .`` process.  That process:
      1. loads the prebuilt main bundle,
      2. calls requestSingleInstanceLock() → returns false,
      3. quits, which is what triggers the FIRST instance's
         ``second-instance`` event → it shows + focuses the dashboard.

    This is the cheap path (~100ms, no Vite, no extra ports) for "user
    clicked Start Menu while app is already in the background."  Returns
    True if the lean electron was spawned, False if it couldn't be.
    """
    exe = _electron_binary()
    if not exe or not _main_entry_built():
        # No lean binary available — caller falls back to npm run dev,
        # which itself will fail the lock and focus the existing window
        # (at the cost of spinning up a Vite server briefly).
        log.info("[AUTOSTART] lean electron unavailable; will use npm run dev to focus")
        return False

    spawn_kwargs: dict = dict(cwd=str(CLIENT_DIR))
    # RACE-009: redirect Electron stdout/stderr to log files.
    spawn_kwargs.update(_electron_log_files())
    # _focus_running_app() always spawns the lean electron in the
    # foreground (hidden=False) so the user sees the focused window.
    spawn_kwargs.update(_spawn_flags(hidden=False))
    try:
        # ``electron .`` runs the app pointed at by package.json "main",
        # i.e. ./out/main/index.js.  VT_FOCUS_ONLY is a marker env var the
        # duplicate reads to know it should not attempt any heavy init.
        # NEW-PRIV-003: same-app restart — full env intentionally inherited
        # (see _spawn_electron above for rationale). Only sensitive KEY
        # NAMES are logged for audit; values are never printed.
        env = dict(os.environ)
        env["VT_FOCUS_ONLY"] = "1"
        _log_sensitive_env_keys(env, context="autostart_launcher._focus_running_app")
        child = subprocess.Popen([exe, "."], env=env, **spawn_kwargs)
        log.info(
            "[AUTOSTART] spawned lean electron to focus running instance (pid=%s)",
            getattr(child, "pid", "?"),
        )
        _close_log_files(spawn_kwargs)
        return True
    except Exception:
        log.exception("[AUTOSTART] failed to spawn lean electron for focus")
        return False


def _spawn_npm_run_dev(hidden: bool = False) -> subprocess.Popen | None:
    """Launch ``npm run dev`` (Vite dev server + Electron).

    This is the LAST-RESORT fallback path, used only when the build
    fails or when ``--dev`` is explicitly passed.
    """
    spawn_kwargs: dict = dict(cwd=str(CLIENT_DIR))
    # RACE-009: redirect Electron stdout/stderr to log files.
    spawn_kwargs.update(_electron_log_files())
    spawn_kwargs.update(_spawn_flags(hidden=hidden))
    # NEW-PRIV-003: same-app restart — full env intentionally inherited
    # (see _spawn_electron above for rationale). Only sensitive KEY
    # NAMES are logged for audit; values are never printed.
    env = dict(os.environ)
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    _log_sensitive_env_keys(env, context="autostart_launcher._spawn_npm_run_dev")

    try:
        # NEW-CQ-033/NEW-SEC-009/S-7: prefer list form over shell=True.
        cmd = _npm_command("dev")
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
            CLIENT_DIR,
            getattr(child, "pid", "?"),
            hidden,
        )
        return child
    except FileNotFoundError as exc:
        log.error("[AUTOSTART] npm not found: %s", exc)
        _close_log_files(spawn_kwargs)
        return None
    except Exception:
        log.exception("[AUTOSTART] failed to spawn npm run dev")
        _close_log_files(spawn_kwargs)
        return None


def _parse_delay(argv: list[str]) -> float:
    """STARTUP-2: parse --delay <seconds> from argv.

    Returns the delay in seconds (0 if absent or malformed). Used by the
    autostart entry to give the prewarm task a head start on warming the
    OS file cache before the app's cold imports contend for disk.
    """
    for i, arg in enumerate(argv):
        if arg == "--delay" and i + 1 < len(argv):
            try:
                return max(0.0, float(argv[i + 1]))
            except (TypeError, ValueError):
                log.warning("[AUTOSTART] invalid --delay value: %r", argv[i + 1])
                return 0.0
        # Also accept --delay=30 form
        if arg.startswith("--delay="):
            try:
                return max(0.0, float(arg.split("=", 1)[1]))
            except (TypeError, ValueError):
                log.warning("[AUTOSTART] invalid --delay= value: %r", arg)
                return 0.0
    return 0.0


def launch() -> int:
    """Universal launcher for Voice Typer.

    Decision tree, checked in order:

    1. **Already running** (port 9876 open): focus via single-instance lock
       (lean ``electron .``, ~100ms, no Vite or Python).

    2. **Fresh start, build-first**:
       a. Build the Electron app if needed (``npm run build`` → ``electron .``).
       b. If ``--dev`` is passed OR the build path fails, fall back to
          ``npm run dev`` as a last resort.

    This means ``npm run dev`` (Vite dev mode) is NEVER the default — it is
    exclusively a fallback when the build fails or when the user explicitly
    requests it via ``--dev``.

    Returns a process exit code (0 = success).
    """
    _setup_logging()
    force_dev = "--dev" in sys.argv[1:]
    hidden = "--hidden" in sys.argv[1:]
    delay_seconds = _parse_delay(sys.argv[1:])

    log.info(
        "[AUTOSTART] launcher starting (pid=%d, force_dev=%s, hidden=%s, delay=%.1fs)",
        os.getpid(),
        force_dev,
        hidden,
        delay_seconds,
    )

    # STARTUP-2: sleep before doing anything so prewarm (which fires at
    # logon+0s with low I/O priority) has a head start on warming the
    # OS file cache. The app's cold imports of torch/transformers then
    # hit RAM instead of disk. Skipped when focusing an existing instance
    # or when delay is 0.
    if delay_seconds > 0:
        log.info(
            "[AUTOSTART] delaying %.1fs before launch to let prewarm warm the cache",
            delay_seconds,
        )
        time.sleep(delay_seconds)

    # 1) App already running — wake it via single-instance lock.
    # Check if a VoiceTyper backend is already running via the
    # backend PID file (authoritative) and port 9876 (belt-and-suspenders).
    # Previously only checked port 9876, which is unreliable because the
    # actual IPC port may be different (auto-incremented if 9876 was busy).
    from voice_typer.server.app import _backend_pid_file, _is_pid_alive

    pid_file = _backend_pid_file()
    backend_running = False
    if pid_file.exists():
        try:
            pid = int(pid_file.read_text().strip())
            backend_running = _is_pid_alive(pid)
        except (OSError, ValueError):
            pass
    if not backend_running:
        # Fallback: check the default port as a quick heuristic
        backend_running = _is_port_open(IPC_HOST, IPC_PORT)

    if backend_running:
        log.info("[AUTOSTART] backend already running — focusing existing instance")
        _focus_running_app()
        time.sleep(0.5)
        return 0

    # 2) Fresh start.
    if not _client_dir_exists():
        log.error(
            "[AUTOSTART] client directory not found at %s — cannot launch",
            CLIENT_DIR,
        )
        return 1

    # 2a) Build-first: build if needed, then launch with electron .
    if not force_dev:
        log.info("[AUTOSTART] Trying build-first path...")
        if _ensure_built_and_launch(hidden=hidden):
            log.info("[AUTOSTART] Build-first launch succeeded")
            time.sleep(2)
            return 0
        log.warning("[AUTOSTART] Build-first path failed — falling back to dev mode")

    # 2b) Last-resort: npm run dev (Vite dev server).
    log.info("[AUTOSTART] Starting dev mode (npm run dev)...")
    child = _spawn_npm_run_dev(hidden=hidden)
    if child is None:
        return 1

    _write_pid_file(os.getpid(), getattr(child, "pid", None))
    time.sleep(2)
    log.info("[AUTOSTART] launcher exiting; child continues detached")
    return 0


def main() -> int:
    """Entry point. Supports --hidden (autostart) and --dev (force dev mode)."""
    return launch()


if __name__ == "__main__":
    sys.exit(main())
