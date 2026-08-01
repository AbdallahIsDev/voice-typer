"""Teardown helper for OS volume restore.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_restore_volume`. The body is unchanged;
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


def teardown_restore_volume(controller) -> None:
    """restore OS volume if it was ducked when the app quit.

    Without this, a quit-during-recording leaves volume stuck low.
    Uses ``fade_ms=0`` for instant restore — the app is exiting.
    """
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
