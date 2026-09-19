"""Teardown helper for the level_monitor module's PortAudio stream."""

from __future__ import annotations

import logging

# ``_run_with_timeout`` is looked up DYNAMICALLY from
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


log = logging.getLogger(__name__)


def teardown_level_monitor(controller) -> None:
    """stop the level_monitor module's PortAudio InputStream +"""
    try:
        from voice_typer.server import level_monitor

        _run_with_timeout(
            "level_monitor.stop_monitoring",
            level_monitor.stop_monitoring,
            timeout=5.0,
        )
    except Exception:
        log.warning(
            "[SHUTDOWN] level_monitor.stop_monitoring failed",
            exc_info=True,
        )


__all__ = ["teardown_level_monitor"]
