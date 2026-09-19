"""Vocabulary IPC handler mixin: get_vocabulary, save_vocabulary."""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    ResponseEnvelope,
    _enforce_payload_size_cap,
    _error_response,
    _validate_dict_payload,
)
from voice_typer.server.service.vocabulary import VocabularyDuplicateError


class VocabularyHandlersMixin(HandlerBase):
    """Mixin: vocabulary IPC handlers (get_vocabulary / save_vocabulary)."""

    def _handle_get_vocabulary(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_vocabulary`` IPC command."""
        # delegates to service layer
        try:
            result = self.service.get_vocabulary()
            # Size-cap guard: the merged vocabulary is unbounded at the
            cap_error = _enforce_payload_size_cap(
                result,
                error_message="Vocabulary is too large to transfer over IPC",
            )
            if cap_error is not None:
                resp["type"] = cap_error["type"]
                resp["data"] = cap_error["data"]
                log.warning(
                    "[IPC] get_vocabulary response exceeds payload cap; "
                    "returning clear error instead of a dropped frame"
                )
                return resp
            resp["type"] = "vocabulary"
            resp["data"] = result
        except Exception as exc:
            # generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_vocabulary")
        return resp

    def _handle_save_vocabulary(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``save_vocabulary`` IPC command."""
        # delegates to service layer
        try:
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
                # Narrow error["data"] to dict before indexing.
                _err_data = error.get("data")
                if isinstance(_err_data, dict) and _err_data.get("code") == "invalid_payload":
                    log.warning(
                        "[IPC] save_vocabulary rejected: %s",
                        _err_data.get("message"),
                    )
                return resp

            # helper above already rejects a non-dict payload with
            if not isinstance(data, dict):
                return _error_response(
                    resp,
                    "save_vocabulary requires data: object",
                    code="invalid_payload",
                )

            # the two SEC-011 limits) so legitimate long replacements
            _max_value_len = 500
            for cat, entries in data.items():
                if isinstance(entries, dict):
                    for k, v in entries.items():
                        if isinstance(v, str) and len(v) > _max_value_len:
                            return _error_response(
                                resp,
                                f"vocabulary value too long in {cat}.{k} ({len(v)} > {_max_value_len})",
                                code="payload_too_large",
                            )
                elif isinstance(entries, list):
                    for entry in entries:
                        if isinstance(entry, list | tuple):
                            for v in entry:
                                if isinstance(v, str) and len(v) > _max_value_len:
                                    return _error_response(
                                        resp,
                                        f"vocabulary value too long in {cat} ({len(v)} > {_max_value_len})",
                                        code="payload_too_large",
                                    )
            result = self.service.save_vocabulary_with_diff(data)
            resp["type"] = "ack"
            resp["data"] = result
        except VocabularyDuplicateError as exc:
            # structured ``client.duplicate_entry`` envelope so the
            log.info(
                "[IPC] save_vocabulary rejected (duplicate '%s', %d occurrences): %s",
                exc.phrase,
                exc.count,
                exc,
            )
            return _error_response(
                resp,
                f"duplicate correction: '{exc.phrase}' ({exc.count} entries)",
                code=ErrorCodes.DUPLICATE_ENTRY,
            )
        except Exception as exc:
            # generic WS-path envelope (no ``str(e)`` leak).
            self._respond_with_error(resp, exc, "save_vocabulary")
        return resp

    def _handle_get_correction_usage(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_correction_usage`` IPC command.

        Returns the per-correction usage snapshot (counts + last-trigger
        """
        try:
            result = self.service.get_correction_usage()
            resp["type"] = "correction_usage"
            resp["data"] = result
        except Exception as exc:
            self._respond_with_error(resp, exc, "get_correction_usage")
        return resp

    def _handle_test_vocabulary_correction(
        self, data: object | None, resp: ResponseEnvelope
    ) -> ResponseEnvelope | None:
        """Handle the ``test_vocabulary_correction`` IPC command."""

        def body(d: dict) -> dict:
            text = str(d.get("text", "") or "")
            result = self.service.test_vocabulary_correction(text)
            return {"type": "ack", "data": result}

        return self._wrap(
            cmd_name="test_vocabulary_correction",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={
                "text": {
                    "type": str,
                    "required": True,
                    "max_value_len": 2000,
                },
            },
            pre_coerce=False,
        )
