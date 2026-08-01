"""Teardown helper for the bubble / waveform worker.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_waveform_wiring`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

# ``_run_with_timeout`` is looked up DYNAMICALLY from
# :mod:`voice_typer.server.shutdown_controller` at call time so tests
# that ``monkeypatch.setattr(...shutdown_controller._run_with_timeout, ...)
# still take effect (mirrors the convention documented in
# ``shutdown_controller.py``'s module docstring).
from voice_typer.server import shutdown_controller as _sc  # noqa: F401


def _run_with_timeout(*args, **kwargs):
    return _sc._run_with_timeout(*args, **kwargs)


log = logging.getLogger(__name__)


def teardown_waveform_wiring(controller) -> None:
    """stop the bubble level / waveform worker so it doesn't
    try to push to a torn-down IPC server during shutdown.

    PERF- the worker / queue / stop_event live on
    WaveformBubbleWiring; delegate to its stop() helper.
    """
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
