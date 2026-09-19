"""Teardown helper for devnull streams opened during logging setup."""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def teardown_devnull_files(controller) -> None:
    """close devnull streams opened during logging setup."""
    try:
        from voice_typer.server import app as _app_module

        closer = getattr(_app_module, "_close_devnull_files", None)
        if closer is None:
            from voice_typer.server.log import close_devnull_files

            closer = close_devnull_files
        closer()
    except Exception:
        log.debug("[CLEANUP] close devnull files failed", exc_info=True)


__all__ = ["teardown_devnull_files"]
