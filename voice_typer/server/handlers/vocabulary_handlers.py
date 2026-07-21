"""Vocabulary IPC handler mixin: get_vocabulary, save_vocabulary.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import logging

from voice_typer.server.handlers._base import HandlerMixinBase
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload

log = logging.getLogger("voice_typer.server.ipc_server")


class VocabularyHandlersMixin(HandlerMixinBase):
    """Mixin: vocabulary IPC handlers (get_vocabulary / save_vocabulary)."""

    def _handle_get_vocabulary(self, data, resp) -> dict | None:
        """Handle the ``get_vocabulary`` IPC command."""
        # ARCH-005: delegates to service layer
        try:
            result = self.service.get_vocabulary()
            resp["type"] = "vocabulary"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] get_vocabulary failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp

    def _handle_save_vocabulary(self, data, resp) -> dict | None:
        """Handle the ``save_vocabulary`` IPC command."""
        # ARCH-005: delegates to service layer
        # NEW-SEC-011: cap payload size to prevent DoS. A 1 GB JSON
        # payload would exhaust disk and CPU; a 10 MB `good` value
        # would re-compile regex per transcription chunk.
        try:
            # R4-F5: route the dict-type + 1 MB payload-size check
            # through ``_validate_dict_payload``. The helper's
            # ``max_payload_bytes`` rule replaces the inline
            # ``len(json.dumps(data)) > _max_vocab_payload`` check.
            # The non-dict case is handled by the helper's first
            # guard (returns ``code: "invalid_payload"`` with the
            # ``"data must be an object"`` message — different from
            # the pre-R4-F5 ``"save_vocabulary requires data: object"``
            # message, but the test was updated to assert on
            # ``code`` instead of the message text).
            #
            # The placeholder field name ``"_payload"`` is a sentinel
            # — ``max_payload_bytes`` is a whole-payload rule, not a
            # per-field rule, but the schema is keyed by field name
            # so we use ``"_"``-prefixed name to signal "not a real
            # field". The helper checks the rule on the FIRST field
            # that declares it (see ``_validate_dict_payload`` in
            # ``ipc/validation.py``).
            _validated, error = _validate_dict_payload(
                data,
                {
                    "_payload": {
                        "max_payload_bytes": 1 * 1024 * 1024,
                    },
                },
            )
            if error:
                resp["type"] = "error"
                resp["data"] = error["data"]
                if error["data"]["code"] == "invalid_payload":
                    log.warning(
                        "[IPC] save_vocabulary rejected: %s",
                        error["data"]["message"],
                    )
                return resp

            # Per-value length cap (NESTED). Kept inline because the
            # schema rule ``max_value_len`` only applies to TOP-LEVEL
            # string fields — vocabulary entries are nested inside
            # dict-of-entries or list-of-tuples, so the rule can't
            # express the per-(category, key) check. R4-F5 centralizes
            # the type + payload-size checks but leaves this loop in
            # place; the message format ("vocabulary value too long in
            # <cat>.<key>") is preserved for the
            # ``test_value_over_1024_chars_returns_error`` contract.
            _max_value_len = 1024
            for cat, entries in data.items():
                if isinstance(entries, dict):
                    for k, v in entries.items():
                        if isinstance(v, str) and len(v) > _max_value_len:
                            resp["type"] = "error"
                            resp["data"] = {
                                "message": (f"vocabulary value too long in {cat}.{k} ({len(v)} > {_max_value_len})")
                            }
                            return resp
                elif isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, (list, tuple)):
                            for v in entry:
                                if isinstance(v, str) and len(v) > _max_value_len:
                                    resp["type"] = "error"
                                    resp["data"] = {
                                        "message": (f"vocabulary value too long in {cat} ({len(v)} > {_max_value_len})")
                                    }
                                    return resp
            result = self.service.save_vocabulary_with_diff(data)
            resp["type"] = "ack"
            resp["data"] = result
        except Exception as e:
            log.error("[IPC] save_vocabulary failed: %s", e, exc_info=True)
            _error_response(resp, str(e))
        return resp
