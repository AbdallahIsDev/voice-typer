"""Teardown helper for the bubble / waveform worker."""

from __future__ import annotations

import logging

# ``_run_with_timeout`` is looked up DYNAMICALLY from
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


log = logging.getLogger(__name__)


def teardown_waveform_wiring(controller) -> None:
    """stop the bubble level / waveform worker so it doesn't"""
    app = controller._app
    try:
        _run_with_timeout(
            "waveform_wiring.stop",
            app.waveform_wiring.stop,
            timeout=5.0,
        )
    except Exception as e:
        log.debug("[SHUTDOWN] bubble level worker stop failed: %s", e)


__all__ = ["teardown_waveform_wiring"]
