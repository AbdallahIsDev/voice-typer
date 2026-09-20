"""Autostart focus/window helpers."""

from __future__ import annotations

import logging

from voice_typer.server.autostart._spawn import _spawn_login_child
from voice_typer.server.autostart._spawn_env import (
    _launcher_child_env,
    _spawn_flags,
)

# C-CROSS-3: explicit dotted logger name: see log_files.py for why
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _focus_running_app() -> bool:
    """Wake an already-running instance via its single-instance lock."""
    from voice_typer.server import autostart_launcher as _pkg

    binary = _pkg._tauri_binary()
    if not binary:
        log.info("[AUTOSTART] focus: tauri binary missing; cannot focus existing instance")
        return False
    # Fail-closed integrity gate, same contract as
    if not _pkg.verify_tauri_binary_or_skip(binary):
        log.error(
            "[AUTOSTART] focus: refusing to spawn %s, integrity verification failed (fail-closed).",
            binary,
        )
        return False
    # ``_launcher_child_env`` force-disables ANSI colour + npm notices
    env = _launcher_child_env()
    env["VT_FOCUS_ONLY"] = "1"
    # Audit note: the sensitive-env audit line is emitted once by
    sk: dict = {}
    sk.update(_pkg._tauri_log_files())
    sk.update(_spawn_flags(hidden=False))
    child = _spawn_login_child(
        [binary],
        env=env,
        spawn_kwargs=sk,
        describe="tauri focus probe",
    )
    return child is not None
