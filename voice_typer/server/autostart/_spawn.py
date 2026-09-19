"""Autostart process spawn helpers."""

from __future__ import annotations

import logging
import subprocess

from voice_typer.server.autostart._spawn_env import _log_sensitive_env_keys
from voice_typer.server.autostart.log_files import _close_log_files

# C-CROSS-3: explicit dotted logger name, the launcher runs as a bare
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _spawn_login_child(
    cmd: list[str],
    *,
    env: dict,
    spawn_kwargs: dict,
    describe: str,
) -> subprocess.Popen | None:
    """Spawn one login child with the unified cleanup shape."""
    _log_sensitive_env_keys(env, context="autostart")
    try:
        child = subprocess.Popen(cmd, env=env, **spawn_kwargs)
    except Exception:
        log.exception("[AUTOSTART] failed to spawn %s", describe)
        return None
    finally:
        _close_log_files(spawn_kwargs)
    log.info(
        "[AUTOSTART] spawned %s (child pid=%s)",
        describe,
        getattr(child, "pid", "?"),
    )
    return child
