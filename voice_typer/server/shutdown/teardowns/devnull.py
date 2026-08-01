"""Teardown helper for devnull streams opened during logging setup.

Phase 4.5 (OI-36) — extracted verbatim from
:meth:`ShutdownController._teardown_devnull_files`. The body is unchanged;
only the class boundary moved.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_devnull_files(controller) -> None:
    """close devnull streams opened during logging setup.

    Looks up ``_close_devnull_files`` dynamically from the app
    module so tests that monkeypatch
    ``voice_typer.server.app._close_devnull_files`` still take
    effect.
    """
    try:
        from voice_typer.server import app as _app_module

        _app_module._close_devnull_files()
    except Exception:
        log.debug("[CLEANUP] close devnull files failed", exc_info=True)


__all__ = ["teardown_devnull_files"]
