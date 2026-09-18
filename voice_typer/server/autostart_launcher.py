"""Universal launcher for Voice Typer (production preferred, dev fallback).

Called by the OS autostart entry (Windows Registry Run key, macOS
LaunchAgent, or Linux ``.desktop``) at login, and by the ``create_launcher_shortcut``
Desktop / Start Menu entries.

This file is the OS-facing ENTRY FACADE: OS schedulers embed this exact
script path, so it stays at ``voice_typer/server/autostart_launcher.py``
while the implementation lives in the :mod:`voice_typer.server.autostart`
package (``log_files``, ``pid_file``, ``port_probe``, ``tauri_spawn``,
``focus``). Every public/private helper is
re-exported below so existing import sites and monkeypatch targets
(``voice_typer.server.autostart_launcher.X``) keep working unchanged.

Architecture
------------
The launcher is **Tauri-only** (Electron host removed 2026-09-17):

- Detect the Tauri binary (``voice-typer-tauri``) at well-known
  install paths (or via the ``VT_TAURI_BINARY`` env override) and
  spawn it directly. Tauri's ``tauri-plugin-single-instance`` plugin
  handles focus / fresh-start deduplication.

- If no Tauri binary is resolvable, exit 1 (no fallback host).

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
the script exits silently, no double launch, no mutex conflict.

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
from voice_typer.server.autostart._spawn_env import (
    _launcher_child_env,
    _log_sensitive_env_keys,
    _spawn_flags,
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
    "_POST_SPAWN_PORT_POLL_INTERVAL",
    "_POST_SPAWN_PORT_POLL_TIMEOUT",
    "_TAURI_LAUNCHER_INSTALL_PATHS",
    "_close_log_files",
    "_config_dir",
    "_client_dir_exists",
    "_expand_tauri_install_template",
    "_focus_running_app",
    "_is_port_open",
    "_is_tauri_mode",
    "_launch_tauri_app",
    "_launcher_child_env",
    "_log_sensitive_env_keys",
    "_parse_delay",
    "_pid_file",
    "_prewarm_would_help",
    "_read_ipc_port_from_pid_file",
    "_setup_logging",
    "_spawn_flags",
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
#       client/                    <- renderer app sources

IPC_HOST = "127.0.0.1"
IPC_PORT = _paths.IPC_PORT

# Upper bound on the ``--delay`` pre-launch sleep. Entries registered
# before the worker-phase prewarm cutover still carry ``--delay 15``
# (baked into the Run key / Task Scheduler XML / .bat at registration
# time); the standalone prewarm task that sleep used to serve no longer
# exists, so honoring the full legacy value would be pure logon latency.
# New registrations use ``_APP_AUTOSTART_DELAY_SECONDS`` (3 s); this cap
# lets already-registered entries pick up the improvement without
# requiring an autostart re-registration. Explicitly small delays pass
# through untouched.
_LAUNCHER_DELAY_CAP_S = 5.0

# The OS invokes this file as a BARE SCRIPT (``pythonw.exe
# autostart_launcher.py`` at logon), so ``__name__`` is ``"__main__"`` here
# , ``logging.getLogger(__name__)`` would create a logger hanging off the
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
    """Minimal logging to the app log file (no console, we run hidden)."""
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

    1. **Already running** (backend PID file or port open): focus via
       single-instance lock. Spawns the Tauri binary; its
       ``tauri-plugin-single-instance`` plugin focuses the first
       instance.

    2. **Fresh start, Tauri mode** (Tauri binary found at a known
       install path): spawn the Tauri binary directly.

    Returns a process exit code (0 = success).
    """
    _setup_logging()
    hidden = "--hidden" in sys.argv[1:]
    delay_seconds = _parse_delay(sys.argv[1:])

    log.info(
        "[AUTOSTART] launcher starting (pid=%d, hidden=%s, delay=%.1fs)",
        os.getpid(),
        hidden,
        delay_seconds,
    )

    # STARTUP-2: sleep before doing anything so the logon I/O storm
    # settles before the app's cold imports contend for disk. The sleep
    # is skipped when focusing an existing instance, when delay is 0,
    # or when there is nothing meaningful to warm. Legacy large delays
    # (registered when a standalone prewarm task still fired at logon)
    # are clamped to ``_LAUNCHER_DELAY_CAP_S``: that task is gone
    # (prewarm is a worker phase now), so the full legacy value would
    # be pure startup latency.
    effective_delay = min(delay_seconds, _LAUNCHER_DELAY_CAP_S) if delay_seconds > 0 else 0.0
    if effective_delay < delay_seconds:
        log.info(
            "[AUTOSTART] clamping legacy %.1fs prewarm delay to %.1fs "
            "(standalone prewarm task no longer exists; prewarm runs in the worker)",
            delay_seconds,
            effective_delay,
        )
    if effective_delay > 0 and _prewarm_would_help():
        log.info(
            "[AUTOSTART] delaying %.1fs before launch to let the logon I/O storm settle",
            effective_delay,
        )
        time.sleep(effective_delay)
    elif delay_seconds > 0:
        # No installed model (or an unreadable config), prewarm has no
        # weights to page into the OS cache, so the fixed delay would be
        # pure startup latency. Log once at INFO so the skip is traceable.
        log.info(
            "[AUTOSTART] skipping %.1fs prewarm delay, no installed model to warm",
            delay_seconds,
        )

    # 1) App already running, wake it via single-instance lock.
    # Check if a VoiceTyper backend is already running via the
    # backend PID file (authoritative) and port 9876 (belt-and-suspenders).
    # Previously only checked port 9876, which is unreliable because the
    # actual IPC port may be different (auto-incremented if 9876 was busy).
    # Resolve via the runtime-neutral backend_pid leaf (light: stdlib +
    # config), never via voice_typer.server.app (pulls the full
    # orchestrator into every login run just to read a PID file).
    from voice_typer.server.backend_pid import _backend_pid_file, _is_pid_alive

    pid_file = _backend_pid_file()
    backend_running = False
    if pid_file.exists():
        try:
            # PID-first format: first line is the PID (a ``port=<n>``
            # line recorded by ``_record_backend_ipc_port`` may follow).
            lines = pid_file.read_text().splitlines()
            pid = int(lines[0].strip()) if lines else 0
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
        log.info("[AUTOSTART] backend already running, focusing existing instance")
        _focus_running_app()
        # No pre-exit sleep: the focus child is spawned detached (POSIX
        # ``start_new_session=True``; Windows inherits the process tree, and
        # Explorer does not wait on autostart entries' children), and neither
        # the OS login sequence nor the parent process needs this launcher to
        # linger, the sleep only delayed "login complete" by a fixed 0.5s on
        # every autostart login with a prewarmed backend. The RESULT line
        # below still records the outcome + duration (C-CROSS-5).
        return 0

    # Fresh start: Tauri path only (Electron removed).
    tauri_mode = _is_tauri_mode()
    tauri_bin = _tauri_binary() if tauri_mode else None
    log.info(
        "[AUTOSTART] launch decision: tauri_mode=%s | tauri_binary=%s",
        tauri_mode,
        tauri_bin or "(none)",
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
            log.error("[AUTOSTART] Tauri spawn failed; exiting 1")
            return 1
        log.error("[AUTOSTART] Tauri mode detected but no binary resolvable; exiting 1")
        return 1

    log.error("[AUTOSTART] No Tauri binary found; exiting 1 (Electron launch path removed)")
    return 1


def main() -> int:
    """Entry point. Supports --hidden (autostart) and --dev (force dev mode).

    Wraps :func:`launch` so EVERY autostart attempt leaves a single
    greppable outcome line in the app log:

        [AUTOSTART] RESULT success exit=0 2.3s
        [AUTOSTART] RESULT failure exit=1 1.4s

    plus, on an unhandled exception, ``[AUTOSTART] RESULT failure
    unhandled-exception`` with the traceback. Without this, a pythonw
    launch that crashes mid-way would exit with a traceback written to a
    non-existent console, invisible. The duration suffix follows the
    canonical space-separated ``<duration>`` performance-marker
    convention (C-LOG-2).
    """
    from voice_typer.server.duration import format_duration

    start = time.perf_counter()
    try:
        rc = launch()
    except Exception:
        # pythonw has no console, an unhandled traceback would vanish.
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
