"""Level-monitor IPC handler mixin: 3 level_monitor_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import logging

from voice_typer.server.handlers._base import HandlerMixinBase
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload

log = logging.getLogger("voice_typer.server.ipc_server")


class LevelMonitorHandlersMixin(HandlerMixinBase):
    """Mixin: level-monitor IPC handlers (start / stop / status)."""

    def _handle_level_monitor_start(self, data, resp) -> dict | None:
        """Handle the ``level_monitor_start`` IPC command."""
        try:
            # IPC-3: validate ``mic_id`` type via the shared
            # ``_validate_dict_payload`` helper. Non-dict ``data`` is
            # pre-coerced to ``{}`` so the
            # ``test_non_dict_data_defaults_mic_id_to_none`` contract
            # (None → mic_id=None) still holds; ``_validate_dict_payload``
            # would otherwise reject non-dict with ``invalid_payload``.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "mic_id": {
                        "type": (str, type(None)),
                        "required": False,
                        "default": None,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            mic_id = validated.get("mic_id")
            result = self.service.level_monitor_start(mic_id=mic_id)
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] level_monitor_start failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp

    def _handle_level_monitor_stop(self, data, resp) -> dict | None:
        """Handle the ``level_monitor_stop`` IPC command."""
        try:
            result = self.service.level_monitor_stop()
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] level_monitor_stop failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp

    def _handle_level_monitor_status(self, data, resp) -> dict | None:
        """Handle the ``level_monitor_status`` IPC command."""
        try:
            result = self.service.level_monitor_status()
            resp["type"] = "level_monitor_status"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] level_monitor_status failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp
