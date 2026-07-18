"""Microphone-test IPC handler mixin: 5 microphone_test_* commands.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any

from voice_typer.server.ipc_server import (
    _validate_dict_payload,
    log,
)


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
            # IPC-3: validate ``mic_id`` and ``filters`` types via the
            # shared ``_validate_dict_payload`` helper. ``duration`` is
            # intentionally NOT in the schema — the existing inline
            # ``float(d.get("duration") or 10.0)`` coercion accepts
            # numeric strings ("7.5" → 7.5), and adding a strict
            # numeric type check would break that documented coercion
            # (see ``test_string_duration_is_coerced_to_float``).
            # Non-dict ``data`` is pre-coerced to ``{}`` so the
            # ``test_non_dict_data_uses_defaults`` contract (None →
            # defaults) still holds; ``_validate_dict_payload`` would
            # otherwise reject non-dict with ``invalid_payload``.
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
                    "filters": {
                        "type": (list, type(None)),
                        "required": False,
                        "default": None,
                    },
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            mic_id = validated.get("mic_id")
            duration = float(data.get("duration") or 10.0)
            filters = validated.get("filters")
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
