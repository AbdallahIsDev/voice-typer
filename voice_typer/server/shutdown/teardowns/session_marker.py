"""Teardown helper for the session-active marker (crash-detection gate)."""

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
