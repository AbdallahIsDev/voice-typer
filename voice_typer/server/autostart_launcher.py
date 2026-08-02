"""Universal launcher for Voice Typer (production preferred, dev fallback).

Called by the OS autostart entry (Windows Registry Run key, macOS
LaunchAgent, or Linux ``.desktop``) at login, and by the ``create_launcher_shortcut``
Desktop / Start Menu entries.

Architecture
------------
The launcher supports two production shapes:

- **Tauri** (post-cutover): a native binary (``voice-typer-tauri``)
  built from ``src-tauri/Cargo.toml``. The launcher detects the
  Tauri binary at well-known install paths (or via the
  ``VT_TAURI_BINARY`` env override) and spawns it directly. Tauri's
  ``tauri-plugin-single-instance`` plugin handles focus / fresh-start
  deduplication, so the launcher does not need a separate lean binary
  for the focus path (unlike Electron).

- **Electron** (legacy / dev): the launcher uses a **build-first**
  strategy: if the Electron app has been built (``out/main/index.js``
  exists), it runs ``electron .`` directly — no Vite dev server, no
  HMR watcher, just the compiled production bundles. If the build
  output is missing, it runs ``npm run build`` first, then
  ``electron .``. ``npm run dev`` is used ONLY as a last-resort
  fallback when the build fails or when the user explicitly passes
  ``--dev``.

Tauri mode takes precedence over the Electron paths when a Tauri
binary is found at a known install path AND the Electron dev binary
(``node_modules/electron/dist/electron``) is NOT present locally —
i.e. production Tauri installs (which don't ship the
``node_modules/`` tree). Dev checkouts that DO ship Electron keep
using the Electron path so developers can exercise the Electron
build. The ``VT_TAURI_AUTOSTART=1`` env var forces Tauri mode
regardless of the local Electron tree (used by the autostart
registration when it knows it is registering under a Tauri install).

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
already listening.  If so, a backend is already running (e.g.
the user logged in twice, or a previous launcher is still alive) and
the script exits silently — no double launch, no mutex conflict.

PID tracking
------------
Writes its own PID and the spawned child's PID to
``~/.voice-typer/autostart.pid`` so the host's ``killStalePython()``
reaper and other tooling can discover and clean up the autostarted
session when the user starts the app manually.

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
from voice_typer.server.branding import APP_NAME
from voice_typer.server.platform_utils import is_macos, is_windows

log = logging.getLogger(__name__)


def _tauri_log_files() -> dict:
    """Open rotating log files for Tauri host stdout/stderr (best-effort).

    Mirrors :func:`_electron_log_files` but writes to ``tauri-stdout.log``
    and ``tauri-stderr.log`` so Tauri crashes can be diagnosed separately
    from Electron crashes. On any failure (disk full, permission denied),
    falls back to :data:`subprocess.DEVNULL` so the launch still succeeds.
    """
    try:
        from voice_typer.server._electron_build import _rotate_if_oversized
        from voice_typer.server.config import _config_dir as _cfg

        log_dir = _cfg() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = log_dir / "tauri-stdout.log"
        stderr_path = log_dir / "tauri-stderr.log"
        _rotate_if_oversized(stdout_path)
        _rotate_if_oversized(stderr_path)
        stdout_fd = open(stdout_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        stderr_fd = open(stderr_path, "a", encoding="utf-8", buffering=1)  # noqa: SIM115
        return {
            "stdout": stdout_fd,
            "stderr": stderr_fd,
            "stdin": subprocess.DEVNULL,
        }
    except Exception as exc:
        log.debug(
            "[AUTOSTART] Failed to open Tauri log files, using DEVNULL: %s",
            exc,
        )
        return {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "stdin": subprocess.DEVNULL,
        }


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
IPC_PORT = _paths.IPC_PORT


def _read_ipc_port_from_pid_file() -> int | None:
    """MED-Y /  (partial): read the backend's IPC
    port from the backend PID file if the writer included a ``port=``
    line.

    The backend PID file is the canonical source of truth for "is a
    Voice Typer backend running on this machine?" — it is written by
    :func:`voice_typer.server.single_instance._write_backend_pid_file`
    after the single-instance mutex is acquired. The current writer
    emits ONLY the PID (``{pid}\\n``); a future change to that function
    will extend the format to also include ``port=<n>\\n`` so the
    autostart launcher does not have to assume the default port 9876
    (which may have been auto-incremented if 9876 was busy).

    This function parses the PID file looking for a ``port=<n>`` line.
    Returns the port as an int if found, otherwise ``None`` (the caller
    falls back to :data:`IPC_PORT`).

    NOTE: the full fix requires :mod:`voice_typer.server.single_instance`
    to write the port line — see ``review.md`` MED-Y.
    This function alone is forward-compatible: once
    ``single_instance._write_backend_pid_file`` is updated to also emit
    the port, the autostart launcher will pick it up without further
    changes here.

    The function never raises — a missing/unreadable/malformed PID file
    simply yields ``None`` and the caller falls back to the default.
    """
    try:
        from voice_typer.server.app import _backend_pid_file

        pid_file = _backend_pid_file()
        if not pid_file.exists():
            return None
        # Read the file once and scan for a ``port=<n>`` line. We
        # tolerate both the legacy single-int format (no port line)
        # and the future multi-line
        # ``launcher=..\\nchild=..\\nport=..\\n`` format.
        text = pid_file.read_text()
    except OSError:
        return None
    except Exception:
        # Defensive: the PID file is shared state and a partial write
        # could surface as any error. Never let a port-read failure
        # propagate to launch() — fall back to IPC_PORT instead.
        return None

    for line in text.splitlines():
        line = line.strip()
        if line.startswith("port="):
            port_str = line[len("port=") :].strip()
            try:
                port = int(port_str)
            except ValueError:
                continue
            # Reject obviously invalid port numbers (0 is reserved on
            # most OSes; >65535 is out of range).
            if 1 <= port <= 65535:
                return port
    return None


# Where to write the PID file (under the app's data dir).
# delegates to voice_typer.server._paths so the path respects the
# platform-aware _config_dir() logic (Windows %APPDATA%, macOS
# ~/Library/Application Support, Linux $XDG_DATA_HOME, the
# VOICE_TYPER_CONFIG_DIR override, and the legacy ~/.voice-typer
# migration check) instead of the previous hardcoded Path.home() /
# ".voice-typer".
def _config_dir() -> Path:
    """Return the voice-typer data directory.

    thin wrapper around :func:`voice_typer.server._paths.config_dir`
        kept for backwards compatibility — tests monkeypatch this name to
        redirect PID-file writes to a tmp dir.
    """
    return _paths.config_dir()


def _pid_file() -> Path:
    """Return the path to the autostart launcher's PID file.

    derives from :func:`_config_dir` so tests that monkeypatch
        this module's ``_config_dir`` continue to redirect the PID file as
        well. (The previous ``_paths.pid_file`` helper was removed in
    production code never adopted it; this local helper
        remains the single source of truth for the autostart PID file.)
    """
    return _config_dir() / "autostart.pid"


def _setup_logging() -> None:
    """Minimal logging to the app log file (no console — we run hidden)."""
    # _config_dir() already delegates to _paths.config_dir() which
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


# How long to wait for the freshly-spawned backend to bind its IPC port
# before the launcher exits. The launcher's job is to spawn + detach the
# child; the OS autostart parent (Windows Run key, macOS LaunchAgent,
# Linux ``.desktop``) waits for the launcher to exit before considering
# login complete. Polling the IPC port lets the launcher exit AS SOON AS
# the backend is ready (typically 200-500 ms on a warm start with
# prewarm) instead of a fixed 2 s sleep — saving up to 1.5 s on the
# user's login critical path. The 5 s ceiling preserves the original
# "give the child time to detach" safety net for slow systems where the
# backend takes longer to start (cold start without prewarm, slow disk,
# antivirus scan).
_POST_SPAWN_PORT_POLL_TIMEOUT = 5.0
_POST_SPAWN_PORT_POLL_INTERVAL = 0.1


def _wait_for_backend_ready(timeout: float = _POST_SPAWN_PORT_POLL_TIMEOUT) -> None:
    """Bounded poll for the backend's IPC port to open after spawning.

    Polls ``_is_port_open`` every ``_POST_SPAWN_PORT_POLL_INTERVAL``
    seconds for up to ``timeout`` seconds. Returns as soon as the port
    opens (early-exit on fast systems) or after the timeout (preserves
    the original "give the child time to detach" safety net on slow
    systems). Never raises — a port that never opens is the backend's
    problem to surface (crash dialog, log), not the launcher's.

    The IPC port is re-read from the backend PID file on every iteration
    (via ``_read_ipc_port_from_pid_file``) so the poll picks up the
    actual port the backend bound to (which may differ from
    :data:`IPC_PORT` if 9876 was busy and the backend auto-incremented).

    The loop is bounded by an iteration count (derived from
    ``timeout / interval``) rather than a ``time.monotonic()`` deadline
    so that tests which monkeypatch ``time.sleep`` to a no-op don't
    busy-wait for the full ``timeout`` in real time — the loop runs
    ``int(timeout / interval)`` iterations and exits regardless of
    wall-clock elapsed time.
    """
    max_iterations = max(1, int(timeout / _POST_SPAWN_PORT_POLL_INTERVAL))
    for _ in range(max_iterations):
        ipc_port = _read_ipc_port_from_pid_file() or IPC_PORT
        if _is_port_open(IPC_HOST, ipc_port):
            return
        time.sleep(_POST_SPAWN_PORT_POLL_INTERVAL)


def _client_dir_exists() -> bool:
    """Return True if the Electron client directory (with package.json) exists."""
    return CLIENT_DIR.is_dir() and (CLIENT_DIR / "package.json").exists()


def _tauri_binary() -> str | None:
    """Return the path to the installed Voice Typer Tauri binary, or ``None``.

    The Tauri cutover ships a native binary (built from
    ``src-tauri/Cargo.toml`` → ``voice-typer-tauri``) instead of the
    Electron ``node_modules/`` tree. This helper locates that binary
    so the autostart launcher can spawn it directly at login — without
    it, the launcher would try ``electron .`` against a missing
    ``node_modules/`` and autostart-at-login would silently break.

    Lookup order:

    1. ``VT_TAURI_BINARY`` env var — explicit override used by
       installers / users that place the binary at a non-standard path.
    2. Well-known install paths per OS:

       - **Linux**: ``/usr/bin/voice-typer-tauri``,
         ``/usr/local/bin/voice-typer-tauri``,
         ``~/.local/bin/voice-typer-tauri`` (the .deb / .rpm install
         target, mirrored by the freedesktop ``.desktop`` template's
         ``Exec=voice-typer-tauri`` line).
       - **macOS**: ``/Applications/Voice Typer.app/Contents/MacOS/voice-typer-tauri``
         and the user-local ``~/Applications/…`` counterpart. The
          bundle name comes from ``productName`` (APP_NAME) while
         the inner executable is the Cargo binary name
         (``voice-typer-tauri``).
       - **Windows**: ``%LOCALAPPDATA%\\Programs\\Voice Typer\
         voice-typer-tauri.exe`` (preferred — the per-user NSIS
         ``installMode=currentUser`` target) and
         ``%PROGRAMFILES%\\Voice Typer\\voice-typer-tauri.exe`` (the
         admin-install fallback). The per-user path is checked FIRST
         because the NSIS installer defaults to ``currentUser``.

    On POSIX the candidate must additionally be executable
    (``os.access(..., X_OK)``) — a stale non-executable file at one of
    these paths shouldn't fool us into thinking Tauri is installed.

    Returns ``None`` in dev checkouts and CI environments where the
    Tauri binary hasn't been installed system-wide; the launcher then
    falls back to the legacy Electron path.
    """
    env_path = os.environ.get("VT_TAURI_BINARY")
    if env_path and Path(env_path).is_file():
        log.debug("[AUTOSTART] _tauri_binary: using VT_TAURI_BINARY env override: %s", env_path)
        return env_path

    candidates: list[Path] = []
    if is_windows():
        # Check LOCALAPPDATA first because the NSIS installer defaults
        # to ``installMode=currentUser`` which installs to
        # ``%LOCALAPPDATA%\Programs\Voice Typer\``. The admin-install
        # path (PROGRAMFILES) is the fallback.
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            candidates.append(Path(local_appdata) / "Programs" / APP_NAME / "voice-typer-tauri.exe")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        candidates.append(Path(program_files) / APP_NAME / "voice-typer-tauri.exe")
    elif is_macos():
        candidates.append(Path("/Applications") / f"{APP_NAME}.app" / "Contents" / "MacOS" / "voice-typer-tauri")
        candidates.append(Path.home() / "Applications" / f"{APP_NAME}.app" / "Contents" / "MacOS" / "voice-typer-tauri")
    else:  # Linux / other POSIX
        candidates.append(Path("/usr/bin/voice-typer-tauri"))
        candidates.append(Path("/usr/local/bin/voice-typer-tauri"))
        candidates.append(Path.home() / ".local" / "bin" / "voice-typer-tauri")

    for cand in candidates:
        if not cand.is_file():
            continue
        if not is_windows() and not os.access(cand, os.X_OK):
            continue
        log.debug("[AUTOSTART] _tauri_binary: resolved Tauri binary at install path: %s", cand)
        return str(cand)
    log.debug("[AUTOSTART] _tauri_binary: no Tauri binary found at any install path (dev/CI mode)")
    return None


def _is_tauri_mode() -> bool:
    """Return ``True`` if the launcher should spawn the Tauri binary.

    Tauri mode is active when ANY of the following holds:

    - ``VOICE_TYPER_TAURI=1`` (or the legacy alias ``VT_TAURI_AUTOSTART=1``)
      is set in the env (explicit opt-in by the Tauri Rust host before
      spawning the Python sidecar, or by the autostart registration
      when registering the launcher entry under a Tauri install), OR
    - the basename of ``sys.executable`` contains ``voice-typer-tauri``
      (we are already running inside the Tauri sidecar process), OR
    - a Tauri binary is found at a known install path AND the Electron
      dev binary (``node_modules/electron/dist/electron``) is NOT
      present locally.

    The third condition ensures dev checkouts that DO ship a local
    Electron ``node_modules`` tree keep using the Electron path even
    when the user has also installed the Tauri binary system-wide —
    the developer's intent is to exercise the Electron build, not the
    installed Tauri binary. In production Tauri installs (no
    ``node_modules/`` shipped), the Tauri binary wins.
    """
    if os.environ.get("VOICE_TYPER_TAURI") == "1":
        return True
    if os.environ.get("VT_TAURI_AUTOSTART") == "1":
        return True
    # also detect Tauri mode from sys.executable basename —
    # the Tauri Rust host renames the Python sidecar executable to
    # ``voice-typer-tauri`` when freezing, so this is a reliable signal
    # that we are running inside a Tauri install.
    exe_basename = os.path.basename(sys.executable).lower()
    if "voice-typer-tauri" in exe_basename:
        return True
    if _tauri_binary() is None:
        return False
    # Tauri binary exists; prefer it only when the local Electron
    # dev binary is absent (production Tauri install).
    return _electron_binary() is None


def _spawn_tauri_host(binary: str, hidden: bool = False) -> subprocess.Popen | None:
    """Spawn the Tauri host binary (``voice-typer-tauri``) with ``VT_START_HIDDEN`` if *hidden*.

        The Tauri app's ``tauri-plugin-single-instance`` plugin (declared
        in ``src-tauri/tauri.conf.json``) handles the focus / fresh-start
        distinction itself: a second spawn of the same binary causes the
        first instance to be focused and the second to exit. So unlike the
        Electron path (which spawns a LEAN electron with ``VT_FOCUS_ONLY=1``
        to trigger ``requestSingleInstanceLock``), here we always spawn the
        full Tauri binary — the single-instance plugin does the rest.

        Returns the child process on success, or ``None`` on failure (the
    caller logs and exits 1 — no silent Electron fallback per ).
    """
    env = dict(os.environ)
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    # same-app restart — full env intentionally inherited
    # (see _launch_electron_built for rationale). Only sensitive KEY
    # NAMES are logged for audit; values are never printed.
    _log_sensitive_env_keys(env, context="autostart_launcher._spawn_tauri_host")
    sk: dict = {}
    sk.update(_tauri_log_files())
    sk.update(_spawn_flags(hidden=hidden))
    try:
        child = subprocess.Popen([binary], env=env, **sk)
        log.info(
            "[AUTOSTART] spawned tauri app %s (child pid=%s, hidden=%s)",
            binary,
            getattr(child, "pid", "?"),
            hidden,
        )
        return child
    except Exception:
        log.exception("[AUTOSTART] tauri spawn failed: %s", binary)
        return None
    finally:
        _close_log_files(sk)


# Backward-compat alias — older test imports use the previous name.
# Both names refer to the same function object.
_launch_tauri_app = _spawn_tauri_host


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
    # intentional — same-app restart needs the same env.
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
    # surface (without values) any sensitive env keys the
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
    # Tauri focus path: spawn the Tauri binary — the single-instance
    # plugin handles the focus + second-instance-quit dance.
    tauri_mode = _is_tauri_mode()
    if tauri_mode:
        binary = _tauri_binary()
        if not binary:
            log.info("[AUTOSTART] tauri focus: binary missing; cannot focus existing instance")
            return False
        env = dict(os.environ)
        env["VT_FOCUS_ONLY"] = "1"
        # same-app restart — full env intentionally
        # inherited (see _launch_electron_built for rationale). Only
        # sensitive KEY NAMES are logged for audit; values are never
        # printed.
        _log_sensitive_env_keys(env, context="autostart_launcher._focus_running_app")
        sk: dict = {}
        sk.update(_tauri_log_files())
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
        # same-app restart — full env intentionally inherited
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
        _close_log_files(spawn_kwargs)
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
    # same-app restart — full env intentionally inherited
    # (see _spawn_electron above for rationale). Only sensitive KEY
    # NAMES are logged for audit; values are never printed.
    env = dict(os.environ)
    if hidden:
        env["VT_START_HIDDEN"] = "1"
    _log_sensitive_env_keys(env, context="autostart_launcher._spawn_npm_run_dev")

    try:
        # S-7: prefer list form over shell=True.
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
    except FileNotFoundError:
        log.exception("[AUTOSTART] npm not found")
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

    1. **Already running** (port 9876 open): focus via single-instance
       lock. On Tauri that means spawning the Tauri binary (its
       ``tauri-plugin-single-instance`` plugin focuses the first
       instance); on Electron that means spawning a lean
       ``electron .`` with ``VT_FOCUS_ONLY=1``.

    2. **Fresh start, Tauri mode** (Tauri binary found at a known
       install path AND no local Electron ``node_modules`` tree):
       spawn the Tauri binary directly. The Tauri cutover removed the
       Electron ``node_modules/`` tree from production installs, so the
       legacy ``electron .`` / ``npm run dev`` paths would silently
       fail — this branch keeps autostart-at-login working.

    3. **Fresh start, Electron mode** (dev checkout or legacy install):
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
        # MED-Y /  (partial): the backend may be
        # listening on a non-default IPC port (auto-incremented when
        # 9876 was busy). Read the actual port from the backend PID
        # file if it was written there; otherwise fall back to the
        # default IPC_PORT. This avoids the launcher spuriously
        # concluding "backend not running" and spawning a SECOND
        # backend that conflicts with the existing one.
        ipc_port = _read_ipc_port_from_pid_file() or IPC_PORT
        backend_running = _is_port_open(IPC_HOST, ipc_port)

    if backend_running:
        log.info("[AUTOSTART] backend already running — focusing existing instance")
        _focus_running_app()
        time.sleep(0.5)
        return 0

    # Tauri mode takes precedence over the Electron build/dev paths so
    # autostart-at-login keeps working after the Tauri cutover (the
    # Electron ``node_modules/`` tree is not shipped in production
    # Tauri installs, so the legacy ``electron .`` / ``npm run dev``
    # paths would silently fail).
    tauri_mode = _is_tauri_mode()
    tauri_bin = _tauri_binary() if tauri_mode else None
    log.info(
        "[AUTOSTART] launch decision: tauri_mode=%s, tauri_binary=%s, electron_binary=%s, force_dev=%s",
        tauri_mode,
        tauri_bin or "(none)",
        _electron_binary() or "(none)",
        force_dev,
    )
    if tauri_mode:
        binary = tauri_bin
        if binary:
            log.info("[AUTOSTART] Tauri mode: spawning %s", binary)
            child = _spawn_tauri_host(binary, hidden=hidden)
            if child is not None:
                _write_pid_file(os.getpid(), getattr(child, "pid", None))
                _wait_for_backend_ready()
                log.info("[AUTOSTART] launcher exiting; tauri child continues detached")
                return 0
            # no silent Electron fallback — if the Tauri spawn
            # fails, exit 1 so the user sees a non-zero exit code and
            # can diagnose, rather than silently launching a stale
            # Electron dev binary that may not exist.
            log.error("[AUTOSTART] Tauri spawn failed; exiting 1 (no Electron fallback)")
            return 1
        # Tauri mode detected but no binary resolvable — also
        # exit 1 with a clear log message rather than silently falling
        # back to a stale Electron path.
        log.error("[AUTOSTART] Tauri mode detected but no binary resolvable; exiting 1 (no Electron fallback)")
        return 1

    # 2) Fresh start — legacy Electron path.
    if not _client_dir_exists():
        log.error(
            "[AUTOSTART] client directory not found at %s — cannot launch",
            CLIENT_DIR,
        )
        return 1

    # 3a) Build-first: build if needed, then launch with electron .
    if not force_dev:
        log.info("[AUTOSTART] Trying build-first path...")
        if _ensure_built_and_launch(hidden=hidden):
            log.info("[AUTOSTART] Build-first launch succeeded")
            _wait_for_backend_ready()
            return 0
        log.warning("[AUTOSTART] Build-first path failed — falling back to dev mode")

    # 3b) Last-resort: npm run dev (Vite dev server).
    log.info("[AUTOSTART] Starting dev mode (npm run dev)...")
    child = _spawn_npm_run_dev(hidden=hidden)
    if child is None:
        return 1

    _write_pid_file(os.getpid(), getattr(child, "pid", None))
    _wait_for_backend_ready()
    log.info("[AUTOSTART] launcher exiting; child continues detached")
    return 0


def main() -> int:
    """Entry point. Supports --hidden (autostart) and --dev (force dev mode)."""
    return launch()


if __name__ == "__main__":
    sys.exit(main())
