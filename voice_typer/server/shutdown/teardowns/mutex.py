"""Teardown helper for the single-instance mutex handle."""

from __future__ import annotations

import logging

from voice_typer.server.platform_utils import is_windows

log = logging.getLogger(__name__)


def teardown_mutex_handle(controller) -> None:
    """release the single-instance mutex handle."""
    app = controller._app
    try:
        if hasattr(app, "_mutex_handle") and app._mutex_handle:
            if is_windows():
                import ctypes

                ctypes.windll.kernel32.CloseHandle(app._mutex_handle)
            else:
                # POSIX: release the flock-based single-instance
                app._mutex_handle.release()
            app._mutex_handle = None
    except Exception:
        log.debug("[CLEANUP] mutex handle release failed", exc_info=True)


__all__ = ["teardown_mutex_handle"]
