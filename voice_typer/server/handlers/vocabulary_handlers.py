"""Vocabulary IPC handler mixin: get_vocabulary, save_vocabulary.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from typing import Any
from voice_typer.server.ipc_server import log


class VocabularyHandlersMixin:
    """Mixin: vocabulary IPC handlers (get_vocabulary / save_vocabulary)."""

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

    def _handle_get_vocabulary(self, data, resp) -> dict | None:
        """Handle the ``get_vocabulary`` IPC command."""
        # ARCH-005: delegates to service layer
        try:
            result = self.service.get_vocabulary()
            resp["type"] = "vocabulary"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] get_vocabulary failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp

    def _handle_save_vocabulary(self, data, resp) -> dict | None:
        """Handle the ``save_vocabulary`` IPC command."""
        # ARCH-005: delegates to service layer
        # NEW-SEC-011: cap payload size to prevent DoS. A 1 GB JSON
        # payload would exhaust disk and CPU; a 10 MB `good` value
        # would re-compile regex per transcription chunk.
        try:
            if not isinstance(data, dict):
                resp["type"] = "error"
                resp["data"] = {"message": "save_vocabulary requires data: object"}
                return resp
            # Cap total JSON payload at 1 MB
            _MAX_VOCAB_PAYLOAD = 1 * 1024 * 1024
            import json as _json_mod
            payload_size = len(_json_mod.dumps(data))
            if payload_size > _MAX_VOCAB_PAYLOAD:
                resp["type"] = "error"
                resp["data"] = {"message": (
                    f"vocabulary payload too large ({payload_size}"
                    f" bytes; max {_MAX_VOCAB_PAYLOAD})"
                )}
                log.warning("[IPC] save_vocabulary rejected: payload %d > %d", payload_size, _MAX_VOCAB_PAYLOAD)
                return resp
            # Cap individual string values at 1024 chars
            _MAX_VALUE_LEN = 1024
            for cat, entries in data.items():
                if isinstance(entries, dict):
                    for k, v in entries.items():
                        if isinstance(v, str) and len(v) > _MAX_VALUE_LEN:
                            resp["type"] = "error"
                            resp["data"] = {"message": (
                                f"vocabulary value too long in {cat}.{k}"
                                f" ({len(v)} > {_MAX_VALUE_LEN})"
                            )}
                            return resp
                elif isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, (list, tuple)):
                            for v in entry:
                                if isinstance(v, str) and len(v) > _MAX_VALUE_LEN:
                                    resp["type"] = "error"
                                    resp["data"] = {"message": (
                                        f"vocabulary value too long in {cat}"
                                        f" ({len(v)} > {_MAX_VALUE_LEN})"
                                    )}
                                    return resp
            result = self.service.save_vocabulary_with_diff(data)
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] save_vocabulary failed: %s", e, exc_info=True)
            resp["type"] = "error"
            resp["data"] = {"message": str(e)}
        return resp
