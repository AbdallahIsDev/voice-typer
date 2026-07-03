"""Level-monitor IPC handler mixin: 3 level_monitor_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log


class LevelMonitorHandlersMixin:
    """Mixin: level-monitor IPC handlers (start / stop / status)."""

    # ARCH-REFAC-002 / TASK-10: pyrefly null-safety fix.
    # These attributes are provided at runtime by the IPCServer host
    # class via multiple inheritance. Declaring them as ``Any`` here
    # lets pyrefly type-check the mixin methods in isolation without
    # requiring a Protocol that would couple the mixin to a specific
    # service/app implementation (MagicMock fixtures in tests rely on
    # the loose typing).
    service: "Any"
    app: "Any"
    _send: "Any"

    def _handle_level_monitor_start(self, data, resp) -> dict | None:
        """Handle the ``level_monitor_start`` IPC command."""
        try:
            mic_id = (data or {}).get("mic_id", None) if isinstance(data, dict) else None
            result = self.service.level_monitor_start(mic_id=mic_id)
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] level_monitor_start failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_level_monitor_stop(self, data, resp) -> dict | None:
        """Handle the ``level_monitor_stop`` IPC command."""
        try:
            result = self.service.level_monitor_stop()
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] level_monitor_stop failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_level_monitor_status(self, data, resp) -> dict | None:
        """Handle the ``level_monitor_status`` IPC command."""
        try:
            result = self.service.level_monitor_status()
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] level_monitor_status failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
