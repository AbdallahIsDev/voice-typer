"""Focus-running-instance probe for the autostart launcher.

The cheap path (~100ms, no Vite, no extra ports) for "user clicked
Start Menu while the app is already in the background": spawn a
duplicate whose single-instance lock hands focus to the first
instance.
"""

from __future__ import annotations

import logging

from voice_typer.server.autostart._spawn import _spawn_login_child
from voice_typer.server.autostart._spawn_env import (
    _launcher_child_env,
    _spawn_flags,
)

# C-CROSS-3: explicit dotted logger name: see log_files.py for why
# ``__name__`` cannot be used here.
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _focus_running_app() -> bool:
    """Wake an already-running instance via its single-instance lock.

    Tauri path: spawns the Tauri binary itself.  Tauri's
    ``tauri-plugin-single-instance`` plugin (declared in
    ``src-tauri/tauri.conf.json``) detects the duplicate instance,
    focuses the first, and quits the second.

    This is the cheap path (~100ms, no Vite, no extra ports) for "user
    clicked Start Menu while app is already in the background."  Returns
    True if the focus probe was spawned, False if it couldn't be.
    """
    from voice_typer.server import autostart_launcher as _pkg

    binary = _pkg._tauri_binary()
    if not binary:
        log.info("[AUTOSTART] focus: tauri binary missing; cannot focus existing instance")
        return False
    # Fail-closed integrity gate, same contract as
    # ``_spawn_tauri_host`` (the focus probe spawns the real binary).
    if not _pkg.verify_tauri_binary_or_skip(binary):
        log.error(
            "[AUTOSTART] focus: refusing to spawn %s, integrity verification failed (fail-closed).",
            binary,
        )
        return False
    # ``_launcher_child_env`` force-disables ANSI colour + npm notices
    # (the child's output is redirected to the tauri log files).
    env = _launcher_child_env()
    env["VT_FOCUS_ONLY"] = "1"
    # Audit note: the sensitive-env audit line is emitted once by
    # ``_spawn_login_child`` (the single choke point for all login
    # spawns), so this call site must NOT pre-log it (that doubled
    # the identical [ENV] line on every spawn).
    sk: dict = {}
    sk.update(_pkg._tauri_log_files())
    sk.update(_spawn_flags(hidden=False))  # focus probe is intentionally foreground
    child = _spawn_login_child(
        [binary],
        env=env,
        spawn_kwargs=sk,
        describe="tauri focus probe",
    )
    return child is not None
