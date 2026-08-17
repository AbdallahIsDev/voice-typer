"""Teardown helper for the session-active marker (crash-detection gate).

Clears ``session_active`` (see :mod:`voice_typer.server.session_state`)
on every clean-shutdown path so the NEXT launch treats the previous
session as clean — no "previous session crashed" notification, even if
daemon-thread teardown noise left ``python_crash.*.txt`` markers behind.

Runs as the FIRST sequenced teardown (see ``_build_sequenced_plan``) so
a kill mid-teardown (watchdog ``os._exit(0)``, SIGKILL fallback, Windows
logoff force-kill) still counts as a clean shutdown — the user
initiated it.

Resolves the config dir lazily via ``voice_typer.server.app._config_dir``
so tests that monkeypatch it (the ``tmp_config_dir`` fixture) are
honored — mirrors ``shutdown/teardowns/pid_file.py``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


def teardown_session_marker(controller: ShutdownController) -> None:
    """Clear the session-active marker (best-effort, idempotent)."""
    try:
        from voice_typer.server import app as _app_module, session_state

        session_state.clear_session_marker(_app_module._config_dir())
    except Exception:
        log.debug("[SHUTDOWN] could not clear session marker", exc_info=True)


__all__ = ["teardown_session_marker"]
