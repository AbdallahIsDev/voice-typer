"""Microphone-test IPC handler mixin: 5 microphone_test_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log


class MicrophoneTestHandlersMixin:
    """Mixin: microphone-test IPC handlers (start / stop / cancel / status / get_level)."""

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

    def _handle_microphone_test_start(self, data, resp) -> dict | None:
        """Handle the ``microphone_test_start`` IPC command."""
        try:
            d = data if isinstance(data, dict) else {}
            mic_id = d.get("mic_id", None)
            duration = float(d.get("duration", 10.0))
            filters = d.get("filters", None)
            result = self.service.microphone_test_start(mic_id=mic_id, duration=duration, filters=filters)
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] microphone_test_start failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_microphone_test_stop(self, data, resp) -> dict | None:
        """Handle the ``microphone_test_stop`` IPC command."""
        try:
            result = self.service.microphone_test_stop()
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] microphone_test_stop failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_microphone_test_cancel(self, data, resp) -> dict | None:
        """Handle the ``microphone_test_cancel`` IPC command."""
        try:
            result = self.service.microphone_test_cancel()
            resp["type"] = "microphone_test_result"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] microphone_test_cancel failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_microphone_test_status(self, data, resp) -> dict | None:
        """Handle the ``microphone_test_status`` IPC command."""
        try:
            result = self.service.microphone_test_status()
            resp["type"] = "microphone_test_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] microphone_test_status failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_microphone_test_get_level(self, data, resp) -> dict | None:
        """Handle the ``microphone_test_get_level`` IPC command."""
        try:
            result = self.service.microphone_test_get_level()
            resp["type"] = "microphone_test_level"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] microphone_test_get_level failed: %s", e)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
