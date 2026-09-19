"""atexit cleanup that never blocks exit (RACE-016)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from voice_typer.server.shutdown_controller import ShutdownController

log = logging.getLogger(__name__)


def atexit_log(controller: ShutdownController) -> None:
    """Log when the process exits, even if quit() was not called."""
    app = controller._app
    if not app._shutting_down_event.is_set():
        log.warning(
            "[ATEXIT] Process exiting without quit() -- likely killed externally (console close, task manager, etc.)"
        )


def atexit_cleanup(controller: ShutdownController) -> None:
    """RACE-016: atexit handler for critical cleanup paths."""
    app = controller._app
    try:
        if app._shutting_down:
            # quit() or restart_app() already ran (or is running)
            return
        log.info("[ATEXIT] Running emergency cleanup")
        # NOTE: we call ``app._do_cleanup()`` (the delegate on
        app._do_cleanup()
    except Exception:
        # previously this was a bare ``except Exception: pass``
        log.exception("[ATEXIT] _do_cleanup() raised, emergency cleanup incomplete")


__all__ = [
    "atexit_log",
    "atexit_cleanup",
]
