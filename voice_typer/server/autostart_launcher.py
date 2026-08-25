"""Universal launcher for Voice Typer (production preferred, dev fallback).

Called by the OS autostart entry (Windows Registry Run key, macOS
LaunchAgent, or Linux ``.desktop``) at login, and by the ``create_launcher_shortcut``
Desktop / Start Menu entries.

This file is the OS-facing ENTRY FACADE: OS schedulers embed this exact
script path, so it stays at ``voice_typer/server/autostart_launcher.py``
while the implementation lives in the :mod:`voice_typer.server.autostart`
package (``log_files``, ``pid_file``, ``port_probe``, ``tauri_spawn``,
``electron_spawn``, ``focus``). Every public/private helper is
re-exported below so existing import sites and monkeypatch targets
(``voice_typer.server.autostart_launcher.X``) keep working unchanged.

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

import logging
import os
import sys
import time

from voice_typer.server import _paths
from voice_typer.server._electron_build import (
    CLIENT_DIR,
    _electron_binary,
    _electron_log_files,
    _launcher_child_env,
    _log_sensitive_env_keys,
    _main_entry_built,
    _npm_command,
    _spawn_flags,
)
from voice_typer.server.autostart.electron_spawn import (
    _ensure_built_and_launch,
    _launch_electron_built,
    _spawn_npm_run_dev,
)
from voice_typer.server.autostart.focus import _focus_running_app
from voice_typer.server.autostart.log_files import _close_log_files, _tauri_log_files
from voice_typer.server.autostart.pid_file import (
    _config_dir,
    _pid_file,
    _read_ipc_port_from_pid_file,
    _write_pid_file,
)
from voice_typer.server.autostart.port_probe import (
    _POST_SPAWN_PORT_POLL_INTERVAL,
    _POST_SPAWN_PORT_POLL_TIMEOUT,
    _is_port_open,
    _wait_for_backend_ready,
    _wait_for_ipc_ready,
)
from voice_typer.server.autostart.tauri_spawn import (
    _TAURI_LAUNCHER_INSTALL_PATHS,
    _client_dir_exists,
    _expand_tauri_install_template,
    _is_tauri_mode,
    _launch_tauri_app,
    _spawn_tauri_host,
    _tauri_binary,
    _tauri_manifest_key,
    _tauri_manifest_path,
    verify_tauri_binary_or_skip,
)

__all__ = [
    "IPC_HOST",
    "IPC_PORT",
    "CLIENT_DIR",
    "_POST_SPAWN_PORT_POLL_INTERVAL",
    "_POST_SPAWN_PORT_POLL_TIMEOUT",
    "_TAURI_LAUNCHER_INSTALL_PATHS",
    "_close_log_files",
    "_config_dir",
    "_client_dir_exists",
    "_electron_binary",
    "_electron_log_files",
    "_ensure_built_and_launch",
    "_expand_tauri_install_template",
    "_focus_running_app",
    "_is_port_open",
    "_is_tauri_mode",
    "_launch_electron_built",
    "_launch_tauri_app",
    "_launcher_child_env",
    "_log_sensitive_env_keys",
    "_main_entry_built",
    "_npm_command",
    "_parse_delay",
    "_pid_file",
    "_prewarm_would_help",
    "_read_ipc_port_from_pid_file",
    "_setup_logging",
    "_spawn_flags",
    "_spawn_npm_run_dev",
    "_spawn_tauri_host",
    "_tauri_binary",
    "_tauri_log_files",
    "_tauri_manifest_key",
    "_tauri_manifest_path",
    "_wait_for_backend_ready",
    "_wait_for_ipc_ready",
    "_write_pid_file",
    "launch",
    "main",
    "verify_tauri_binary_or_skip",
]

# Directory layout:
#   <root>/
#     voice_typer/
#       server/
#         autostart_launcher.py   <- this file (entry facade)
#         autostart/              <- implementation subpackage
#       client/                    <- Electron app
# CLIENT_DIR (above) is re-exported from _electron_build so tests that
# monkeypatch ``voice_typer.server.autostart_launcher.CLIENT_DIR`` still
# work; leaf modules resolve it through this facade at call time.

IPC_HOST = "127.0.0.1"
IPC_PORT = _paths.IPC_PORT

# The OS invokes this file as a BARE SCRIPT (``pythonw.exe
# autostart_launcher.py`` at logon), so ``__name__`` is ``"__main__"`` here
# — ``logging.getLogger(__name__)`` would create a logger hanging off the
# root, where the app's rotating file handler (attached to the
# ``voice_typer`` logger by ``log.setup_logging``) never fires, silently
# dropping EVERY launcher log line. That is why no ``[AUTOSTART]`` lines
# ever appeared in ``voice-typer.log`` despite the launcher running. Use
# the explicit dotted name (same pattern as ``voice_typer/worker/__main__.py``)
# so launcher lines land in the app log and autostart attempts are
# traceable. The leaf modules under ``voice_typer/server/autostart/``
# share this exact logger name (same underlying Logger object).
log = logging.getLogger("voice_typer.server.autostart_launcher")


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


def _prewarm_would_help() -> bool:
    """Return True when the prewarm delay is worth taking.

    The fixed ``--delay`` exists so the prewarm task (which fires at
    logon with low I/O priority) can page the ACTIVE model's weights
    into the OS file cache before the app's cold imports run. When no
    model is installed there are no weights to warm, so the sleep would
    be pure startup latency (the package-file warm-up still runs inside
    the worker regardless). Defaults to True (keep the delay) on any
    failure so a probe error can never make autostart race the prewarm.
    """
    try:
        from voice_typer.server.config import Config
        from voice_typer.server.tray_models import is_active_model_downloaded

        return is_active_model_downloaded(Config.load())
    except Exception:
        log.debug("[AUTOSTART] prewarm-delay probe failed; keeping delay", exc_info=True)
        return True


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
    # hit RAM instead of disk. Skipped when focusing an existing instance,
    # when delay is 0, or when there is nothing meaningful to warm.
    if delay_seconds > 0 and _prewarm_would_help():
        log.info(
            "[AUTOSTART] delaying %.1fs before launch to let prewarm warm the cache",
            delay_seconds,
        )
        time.sleep(delay_seconds)
    elif delay_seconds > 0:
        # No installed model (or an unreadable config) — prewarm has no
        # weights to page into the OS cache, so the fixed delay would be
        # pure startup latency. Log once at INFO so the skip is traceable.
        log.info(
            "[AUTOSTART] skipping %.1fs prewarm delay — no installed model to warm",
            delay_seconds,
        )

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
                _wait_for_ipc_ready()
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
        # DEBUG: the "spawned electron ." + "Build-first launch
        # succeeded" INFO lines below already tell the story — this
        # path marker duplicated them.
        log.debug("[AUTOSTART] Trying build-first path...")
        if _ensure_built_and_launch(hidden=hidden):
            log.info("[AUTOSTART] Build-first launch succeeded")
            _wait_for_ipc_ready()
            return 0
        log.warning("[AUTOSTART] Build-first path failed — falling back to dev mode")

    # 3b) Last-resort: npm run dev (Vite dev server).
    log.info("[AUTOSTART] Starting dev mode (npm run dev)...")
    child = _spawn_npm_run_dev(hidden=hidden)
    if child is None:
        return 1

    _write_pid_file(os.getpid(), getattr(child, "pid", None))
    _wait_for_ipc_ready()
    log.info("[AUTOSTART] launcher exiting; child continues detached")
    return 0


def main() -> int:
    """Entry point. Supports --hidden (autostart) and --dev (force dev mode).

    Wraps :func:`launch` so EVERY autostart attempt leaves a single
    greppable outcome line in the app log:

        [AUTOSTART] RESULT success exit=0 2.3s
        [AUTOSTART] RESULT failure exit=1 1.4s

    plus, on an unhandled exception, ``[AUTOSTART] RESULT failure
    unhandled-exception`` with the traceback. Without this, a pythonw
    launch that crashes mid-way would exit with a traceback written to a
    non-existent console — invisible. The duration suffix follows the
    canonical space-separated ``<duration>`` performance-marker
    convention (C-LOG-2).
    """
    from voice_typer.server.duration import format_duration

    start = time.perf_counter()
    try:
        rc = launch()
    except Exception:
        # pythonw has no console — an unhandled traceback would vanish.
        # Log it to the rotating file so autostart failures are traceable.
        log.exception("[AUTOSTART] RESULT failure unhandled-exception")
        rc = 1
    elapsed = format_duration(time.perf_counter() - start)
    if rc == 0:
        log.info("[AUTOSTART] RESULT success exit=0%s", elapsed)
    else:
        log.error("[AUTOSTART] RESULT failure exit=%d%s", rc, elapsed)
    return rc


if __name__ == "__main__":
    sys.exit(main())
