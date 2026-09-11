"""Shared login-child spawn recipe (BP-130).

All five autostart spawn sites (Tauri host, Tauri focus probe, lean
Electron focus, built Electron, npm run dev) shared the same shape:
child env + sensitive-key audit + log-file/flags kwargs + Popen +
pid log + parent handle close. The copies drifted, the
built-Electron copy closed parent handles only on success, leaking
them whenever ``Popen`` raised. One recipe, try/except/finally;
every site routes through it. Env construction and kwargs stay at
the call sites (they genuinely differ per child).
"""

from __future__ import annotations

import logging
import subprocess

from voice_typer.server._electron_build import _log_sensitive_env_keys
from voice_typer.server.autostart.log_files import _close_log_files

# C-CROSS-3: explicit dotted logger name, the launcher runs as a bare
# script (``pythonw.exe autostart_launcher.py``), so ``__name__`` would
# be ``"__main__"`` and miss the app's rotating file handler.
log = logging.getLogger("voice_typer.server.autostart_launcher")


def _spawn_login_child(
    cmd: list[str],
    *,
    env: dict,
    spawn_kwargs: dict,
    describe: str,
) -> subprocess.Popen | None:
    """Spawn one login child with the unified cleanup shape.

    *describe* names the child for logs (e.g. ``"tauri app /bin
    (hidden=True)"``). Returns the child on success, ``None`` on
    failure. Parent copies of log-file handles are ALWAYS closed
    (the child inherited them), on success and on ``Popen``-raise
    alike.
    """
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
