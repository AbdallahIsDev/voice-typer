"""Vocabulary IPC handler mixin: get_vocabulary, save_vocabulary.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import _error_response, _validate_dict_payload


class VocabularyHandlersMixin(HandlerBase):
    """Mixin: vocabulary IPC handlers (get_vocabulary / save_vocabulary).

    CR-20: this mixin's ``except Exception`` catch-alls call
    :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
    no ``str(e)`` leak). The per-value length-cap validation errors
    still route through :func:`_error_response` with an explicit
    ``code="payload_too_large"`` field so clients can
    branch on the code rather than pattern-matching the message text.
    """

    def _handle_get_vocabulary(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_vocabulary`` IPC command."""
        # ARCH-005: delegates to service layer
        try:
            result = self.service.get_vocabulary()
            resp["type"] = "vocabulary"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_vocabulary")
        return resp

    def _handle_save_vocabulary(self, data: dict | None, resp: dict) -> dict | None:
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
                # Narrow error["data"] to dict before indexing.
                # ``_validate_dict_payload`` has no return-type annotation,
                # so pyrefly infers its error return as
                # ``dict[str, str | dict[str, str]]`` (unifying all the
                # ``"type": "error", "data": {...}`` branches). At runtime
                # ``error["data"]`` is always a dict, but the type system
                # can't prove it — narrow with ``isinstance`` so the
                # ``["code"]`` / ``["message"]`` indexing type-checks.
                _err_data = error.get("data")
                if isinstance(_err_data, dict) and _err_data.get("code") == "invalid_payload":
                    log.warning(
                        "[IPC] save_vocabulary rejected: %s",
                        _err_data.get("message"),
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
            #
            # The inline envelope is routed through
            # ``_error_response`` with ``code="payload_too_large"`` so
            # the renderer can branch on the code (matches the
            # ``max_payload_bytes`` envelope the helper emits for the
            # whole-payload size check above).
            #
            # XZ-R11-07: lowered from 1024 to 500 to match the
            # vocabulary-layer limit ``MAX_REPLACEMENT_LENGTH = 500``
            # (``vocabulary.py:46-47``). The previous 1024 cap allowed
            # values 2× the CRUD-layer ceiling, which
            # ``save_vocabulary_with_diff`` would then reject anyway —
            # but only after writing them through the diff path that
            # bypasses the CRUD methods. A 500-char IPC cap fails fast
            # at the handler layer with a clear ``payload_too_large``
            # envelope instead of letting the larger value reach the
            # vocabulary layer's own validation. ``MAX_PATTERN_LENGTH``
            # (200) is enforced inside the CRUD methods; replacement
            # values up to 500 chars are accepted here (the looser of
            # the two SEC-011 limits) so legitimate long replacements
            # (e.g. multi-sentence expansion templates) still pass.
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
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "save_vocabulary")
        return resp
