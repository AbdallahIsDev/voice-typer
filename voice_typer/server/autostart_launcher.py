"""Bare-script autostart launcher; logger name fixed (C-CROSS-3). Keep [AUTOSTART] observability lines (C-CROSS-5)."""

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

IPC_HOST = "127.0.0.1"
IPC_PORT = _paths.IPC_PORT

# Upper bound on the ``--delay`` pre-launch sleep. Entries registered
_LAUNCHER_DELAY_CAP_S = 5.0

# The OS invokes this file as a BARE SCRIPT (``pythonw.exe
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _setup_logging() -> None:
    """Minimal logging to the app log file (no console, we run hidden)."""
    # _config_dir() already delegates to _paths.config_dir() which
    log_dir = _config_dir()
    from voice_typer.server.log import setup_logging as _setup_logging_shared

    _setup_logging_shared(log_dir)


def _prewarm_would_help() -> bool:
    """Return True when the prewarm delay is worth taking."""
    try:
        from voice_typer.server.config import Config
        from voice_typer.server.tray_models import is_active_model_downloaded

        return is_active_model_downloaded(Config.load())
    except Exception:
        log.debug("[AUTOSTART] prewarm-delay probe failed; keeping delay", exc_info=True)
        return True


def _parse_delay(argv: list[str]) -> float:
    """Parse --delay <seconds> from argv.

    Returns the delay in seconds (0 if absent or malformed). Used by the
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
    """Universal launcher for Lausu.

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

    # Sleep before doing anything so the logon I/O storm
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
        log.info(
            "[AUTOSTART] skipping %.1fs prewarm delay, no installed model to warm",
            delay_seconds,
        )

    # 1) App already running, wake it via single-instance lock.
    from voice_typer.server.backend_pid import _backend_pid_file, _is_pid_alive

    pid_file = _backend_pid_file()
    backend_running = False
    if pid_file.exists():
        try:
            # PID-first format: first line is the PID (a ``port=<n>``
            lines = pid_file.read_text().splitlines()
            pid = int(lines[0].strip()) if lines else 0
            backend_running = _is_pid_alive(pid)
        except (OSError, ValueError):
            pass
    if not backend_running:
        # PID check is best-effort; confirm via the IPC port before
        # treating the backend as down.
        ipc_port = _read_ipc_port_from_pid_file() or IPC_PORT
        backend_running = _is_port_open(IPC_HOST, ipc_port)

    if backend_running:
        log.info("[AUTOSTART] backend already running, focusing existing instance")
        _focus_running_app()
        # No pre-exit sleep: the focus child is spawned detached (POSIX
        return 0

    # Fresh start: Tauri path only (predecessor removed).
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

    log.error("[AUTOSTART] No Tauri binary found; exiting 1 (predecessor launch path removed)")
    return 1


def main() -> int:
    """convention (C-LOG-2)."""
    from voice_typer.server.duration import format_duration

    start = time.perf_counter()
    try:
        rc = launch()
    except Exception:
        # pythonw has no console, an unhandled traceback would vanish.
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
