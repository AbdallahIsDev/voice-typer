"""Vocabulary IPC handler mixin: get_vocabulary, save_vocabulary.

extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.validation import (
    ErrorCodes,
    _enforce_payload_size_cap,
    _error_response,
    _validate_dict_payload,
)
from voice_typer.server.service.vocabulary import VocabularyDuplicateError


class VocabularyHandlersMixin(HandlerBase):
    """Mixin: vocabulary IPC handlers (get_vocabulary / save_vocabulary).

    this mixin's ``except Exception`` catch-alls call
        :meth:`HandlerBase._respond_with_error` (generic WS-path envelope,
        no ``str(e)`` leak). The per-value length-cap validation errors
        still route through :func:`_error_response` with an explicit
        ``code="payload_too_large"`` field so clients can
        branch on the code rather than pattern-matching the message text.
    """

    def _handle_get_vocabulary(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_vocabulary`` IPC command."""
        # delegates to service layer
        try:
            result = self.service.get_vocabulary()
            # Size-cap guard: the merged vocabulary is unbounded at the
            # service layer (the user can accumulate thousands of
            # corrections). Serializing an oversized response would
            # exceed the 1 MiB transport frame cap and be SILENTLY
            # dropped by the WS/TCP layer — fail fast with a clear
            # structured error instead.
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

    def _handle_save_vocabulary(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``save_vocabulary`` IPC command."""
        # delegates to service layer
        # cap payload size to prevent DoS. A 1 GB JSON
        # payload would exhaust disk and CPU; a 10 MB `good` value
        # would re-compile regex per transcription chunk.
        try:
            # route the dict-type + 1 MB payload-size check
            # through ``_validate_dict_payload``. The helper's
            # ``max_payload_bytes`` rule replaces the inline
            # ``len(json.dumps(data)) > _max_vocab_payload`` check.
            # The non-dict case is handled by the helper's first
            # guard (returns ``code: "invalid_payload"`` with the
            # ``"data must be an object"`` message — different from
            # the pre- ``"save_vocabulary requires data: object"``
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
            # express the per-(category, key) check.  centralizes
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
            # lowered from 1024 to 500 to match the
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
        except VocabularyDuplicateError as exc:
            # Backend duplicate enforcement (see
            # ``save_vocabulary_with_diff``) — reject the write with a
            # structured ``client.duplicate_entry`` envelope so the
            # renderer can surface the localized "This correction
            # already exists" message and NOT add the duplicate row.
            # The duplicate phrase is exposed to help the user fix the
            # entry (it is the user's own vocabulary text, not a
            # secret or path).
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

    def _handle_get_correction_usage(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``get_correction_usage`` IPC command.

        Returns the per-correction usage snapshot (counts + last-trigger
        timestamps per entry, plus per-day correction/dictation totals)
        so the Vocabulary page can show "used Nx" and the Analytics
        page can show a corrections-applied rate.
        """
        try:
            result = self.service.get_correction_usage()
            resp["type"] = "correction_usage"
            resp["data"] = result
        except Exception as exc:
            self._respond_with_error(resp, exc, "get_correction_usage")
        return resp

    def _handle_test_vocabulary_correction(self, data: dict | None, resp: dict) -> dict | None:
        """Handle the ``test_vocabulary_correction`` IPC command.

        Applies the LIVE vocabulary rules to a phrase so the "Test
        corrections" panel on the Vocabulary page previews the exact
        engine dictation uses (``VocabularyManager.apply_to_text``)
        instead of a client-side mirror that can drift.

        Migrated to :meth:`HandlerBase._wrap` with ``pre_coerce=False``
        — the helper handles the surrounding ``try/except`` →
        ``_respond_with_error`` catch-all while passing non-dict
        ``data`` through unchanged so the schema still rejects it with
        ``invalid_payload``.
        """

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
