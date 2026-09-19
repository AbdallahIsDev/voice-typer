"""Teardown helper for OS volume restore."""

from __future__ import annotations

import logging

# ``_run_with_timeout`` is looked up DYNAMICALLY from
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


log = logging.getLogger(__name__)


def teardown_restore_volume(controller) -> None:
    """restore OS volume if it was ducked when the app quit."""
    app = controller._app
    try:
        _run_with_timeout(
            "restore_volume",
            lambda: app._restore_volume(fade_ms=0),
            timeout=5.0,
        )
    except Exception:
        log.debug("[CLEANUP] volume restore failed", exc_info=True)


__all__ = ["teardown_restore_volume"]
