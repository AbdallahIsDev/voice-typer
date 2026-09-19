"""CleanupMixin, thin cleanup delegates on ``ShutdownController``."""

from __future__ import annotations

from voice_typer.server.shutdown.cleanup import do_cleanup, do_fast_cleanup
from voice_typer.server.shutdown.ws_drain import drain_ws_dispatch_pool


class CleanupMixin:
    """Thin cleanup-delegate mixin for :class:`ShutdownController`."""

    def _do_cleanup(self) -> None:
        """shared cleanup body used by ``quit()``, ``restart_app()``,"""
        do_cleanup(self)

    def _drain_ws_dispatch_pool(self, app) -> None:
        """Early bookend: stop the IPC server + drain the WS dispatch pool."""
        drain_ws_dispatch_pool(self, app)

    def _do_fast_cleanup(self) -> None:
        """critical-only cleanup for Windows logoff/shutdown."""
        do_fast_cleanup(self)
