"""History IPC handler mixin: get_history, get_today_stats, delete_history,"""

import json

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.history_bounds import (
    HISTORY_OFFSET_LIMIT,
    _bound_history_limit,
    _bound_history_offset,
    deep_offset_message,
)
from voice_typer.server.ipc.validation import (  # noqa: F401
    ErrorCodes,
    LegacyErrorCodes,
    ResponseEnvelope,
    _validate_dict_payload,
)

# Maximum serialized response size for ``get_history`` / ``get_favorites`` /
_HISTORY_MAX_FRAME_BYTES = 1 * 1024 * 1024 - 32 * 1024

# Shared keyset-cursor schema fragment for the three list-returning
_HISTORY_CURSOR_FIELDS: dict = {
    "before_timestamp": {"type": str, "required": False, "default": None},
    "before_id": {
        "type": (int, str),
        "required": False,
        "reject_bool": True,
        "default": None,
    },
}


class HistoryHandlersMixin(HandlerBase):
    """Mixin: history-related IPC handlers (get_history / delete_history / ...)."""

    def _run_history_list_body(
        self,
        d: dict,
        resp: ResponseEnvelope,
        *,
        command: str,
        service_call,
        extra_schema: dict | None = None,
        service_args: tuple = (),
    ) -> dict:
        """Shared pipeline for get_history / get_favorites / search_history.

        One home for validate → bound limit/offset → cursor → deep-offset
        reject → service call → frame-cap → envelope. ``service_call`` is
        ``self.service.<list method>``; extra required fields (e.g. query)
        come from ``extra_schema`` and are passed as ``service_args`` first.
        """
        schema = {
            "limit": {"type": (int, str), "required": False, "default": 50},
            "offset": {"type": (int, str), "required": False, "default": 0},
            **_HISTORY_CURSOR_FIELDS,
        }
        if extra_schema:
            schema.update(extra_schema)
        validated, error = _validate_dict_payload(d, schema)
        if error:
            return error
        assert validated is not None
        # SEC-010: bound limit/offset to prevent DoS via huge values.
        limit = _bound_history_limit(validated.get("limit", 50))
        offset = _bound_history_offset(validated.get("offset", 0))
        cursor = self._extract_history_cursor(validated, resp)
        if isinstance(cursor, dict):
            return cursor
        before_timestamp, before_id = cursor
        deep = self._reject_deep_offset(resp, offset, before_timestamp, before_id)
        if deep is not None:
            return deep
        if before_timestamp is not None and before_id is not None:
            rows = service_call(
                *service_args,
                limit,
                offset,
                before_timestamp=before_timestamp,
                before_id=before_id,
            )
        else:
            rows = service_call(*service_args, limit, offset)
        rows = self._enforce_history_frame_cap(rows, command=command)
        if isinstance(rows, dict):
            return rows
        return {"data": rows}

    def _extract_history_cursor(self, validated: dict, resp: dict) -> tuple[str | None, int | None] | dict:
        """Extract the keyset-pagination cursor from a validated payload."""
        before_timestamp = validated.get("before_timestamp")
        before_id_raw = validated.get("before_id")
        before_id = None
        # Schema: ``(int, str)`` with ``reject_bool``. Narrow so the
        if before_id_raw is not None and isinstance(before_id_raw, (int, str)) and not isinstance(before_id_raw, bool):
            try:
                before_id = int(before_id_raw)
            except (TypeError, ValueError):
                return self._error_response(
                    resp,
                    "before_id must be an integer",
                    code=ErrorCodes.INVALID_FIELD,
                    field="before_id",
                )
            if before_id < 0:
                return self._error_response(
                    resp,
                    "before_id must be non-negative",
                    code=ErrorCodes.INVALID_FIELD,
                    field="before_id",
                )
        return before_timestamp, before_id

    def _reject_deep_offset(
        self, resp: dict, offset: int, before_timestamp: str | None, before_id: int | None
    ) -> dict | None:
        """Precise deep-OFFSET rejection, so the DB guard stays defense-in-depth."""
        if (before_timestamp is None or before_id is None) and offset > HISTORY_OFFSET_LIMIT:
            return self._error_response(
                resp,
                deep_offset_message(offset),
                code=ErrorCodes.INVALID_FIELD,
                field="offset",
            )
        return None

    def _handle_get_history(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_history`` IPC command."""

        def body(d: dict) -> dict:
            return self._run_history_list_body(
                d,
                resp,
                command="get_history",
                service_call=self.service.get_history,
            )

        return self._wrap(
            cmd_name="get_history",
            resp_type="history",
            data=data,
            resp=resp,
            body=body,
        )

    def _enforce_history_frame_cap(
        self,
        rows: list[dict],
        *,
        command: str,
    ) -> list[dict] | dict:
        """Progressively truncate row ``text`` previews until the"""
        if not rows:
            return rows
        try:
            serialized = json.dumps(rows, ensure_ascii=False, default=str).encode(
                "utf-8",
            )
        except (TypeError, ValueError):
            # If serialization fails entirely, return the rows as-is —
            return rows
        if len(serialized) <= _HISTORY_MAX_FRAME_BYTES:
            return rows
        log.warning(
            "[IPC] %s response exceeds frame cap (%d > %d bytes); truncating row text previews",
            command,
            len(serialized),
            _HISTORY_MAX_FRAME_BYTES,
        )
        # Halve each row's text preview until we fit. Bound the
        max_iterations = 10
        for _ in range(max_iterations):
            if len(serialized) <= _HISTORY_MAX_FRAME_BYTES:
                break
            shortened_any = False
            for row in rows:
                text = row.get("text")
                if not isinstance(text, str) or len(text) <= 50:
                    continue
                # Halve, but never below the 50-char floor.
                new_len = max(50, len(text) // 2)
                if new_len < len(text):
                    row["text"] = text[:new_len]
                    row["text_truncated"] = True
                    shortened_any = True
            if not shortened_any:
                # Every row is already at the floor, bail out.
                break
            try:
                serialized = json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
            except (TypeError, ValueError):
                break
        # Final size check: even after floor-truncation the response
        if len(serialized) > _HISTORY_MAX_FRAME_BYTES:
            from voice_typer.server.ipc.validation import (
                ErrorCodes as _ErrorCodes,
            )

            log.warning(
                "[IPC] %s response still exceeds frame cap (%d > %d bytes) "
                "after truncation; returning clear error instead of a dropped frame",
                command,
                len(serialized),
                _HISTORY_MAX_FRAME_BYTES,
            )
            return {
                "type": "error",
                "data": {
                    "code": _ErrorCodes.PAYLOAD_TOO_LARGE,
                    "message": (
                        f"History response is too large to transfer over IPC "
                        f"({len(serialized)} bytes exceeds {_HISTORY_MAX_FRAME_BYTES} byte cap)"
                    ),
                },
            }
        return rows

    def _handle_get_today_stats(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_today_stats`` IPC command."""
        try:
            resp["type"] = "today_stats"
            resp["data"] = self.service.get_today_stats()
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_today_stats")
        return resp

    def _handle_delete_history(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``delete_history`` IPC command."""

        def body(d: dict) -> dict:
            self.service.delete_history(d["id"])
            # Broadcast history_changed so open clients refresh their lists.
            _publish_history_changed("deleted")
            return {"type": "ack"}

        return self._wrap(
            cmd_name="delete_history",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"id": {"type": (int, str), "required": True}},
            pre_coerce=False,
        )

    def _handle_restore_history(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``restore_history`` IPC command."""

        # 256 KB whole-payload cap (see below). The helper's
        def body(d: dict) -> dict:
            record = d["record"]
            # Per-field cap on ``record['text']``. 8192 chars is the
            if len(record.get("text", "")) > 8192:
                return self._error_response(
                    resp,
                    "'record.text' exceeds 8192-char cap",
                    code=ErrorCodes.PAYLOAD_TOO_LARGE,
                    field="record.text",
                )
            new_id = self.service.restore_history(record)
            # A restored record must also broadcast history_changed so
            _publish_history_changed("restored")
            return {"type": "ack", "data": {"id": new_id}}

        return self._wrap(
            cmd_name="restore_history",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"record": {"type": dict, "required": True, "max_payload_bytes": 256 * 1024}},
            pre_coerce=False,
        )

    def _handle_clear_history(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``clear_history`` IPC command."""
        try:
            self.service.clear_history()
            resp["type"] = "ack"
            # Broadcast a `history_changed` event so open clients refresh.
            _publish_history_changed("cleared")
        except Exception as exc:
            # generic WS-path envelope.
            self._respond_with_error(resp, exc, "clear_history")
        return resp

    def _handle_toggle_favorite(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``toggle_favorite`` IPC command."""

        def body(d: dict) -> dict:
            new_val = self.service.toggle_favorite(d["id"])
            # A favorite toggle changes which rows are starred, broadcast so
            _publish_history_changed("favorite_toggled")
            return {"type": "ack", "data": {"favorite": new_val}}

        return self._wrap(
            cmd_name="toggle_favorite",
            resp_type="ack",
            data=data,
            resp=resp,
            body=body,
            schema={"id": {"type": (int, str), "required": True}},
            pre_coerce=False,
        )

    def _handle_get_favorites(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_favorites`` IPC command."""

        def body(d: dict) -> dict:
            return self._run_history_list_body(
                d,
                resp,
                command="get_favorites",
                service_call=self.service.get_favorites,
            )

        return self._wrap(
            cmd_name="get_favorites",
            resp_type="history",
            data=data,
            resp=resp,
            body=body,
        )

    def _handle_search_history(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``search_history`` IPC command."""

        def body(d: dict) -> dict:
            validated, error = _validate_dict_payload(
                d,
                {"query": {"type": str, "required": False, "default": ""}},
            )
            if error:
                return error
            assert validated is not None
            query = validated.get("query", "")
            return self._run_history_list_body(
                d,
                resp,
                command="search_history",
                service_call=self.service.search_history,
                extra_schema={"query": {"type": str, "required": False, "default": ""}},
                service_args=(query,),
            )

        return self._wrap(
            cmd_name="search_history",
            resp_type="history",
            data=data,
            resp=resp,
            body=body,
        )

    # On-demand full-text + total-count handlers

    def _handle_get_history_count(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_history_count`` IPC command.

        Returns the total number of transcription rows in the DB.
        """

        def body(d: dict) -> dict:
            count = self.service.get_history_count()
            return {"type": "history_count", "data": {"count": int(count)}}

        return self._wrap(
            cmd_name="get_history_count",
            resp_type="history_count",
            data=data,
            resp=resp,
            body=body,
        )

    def _handle_get_transcription_text(self, data: object | None, resp: ResponseEnvelope) -> ResponseEnvelope | None:
        """Handle the ``get_transcription_text`` IPC command.

        Returns the FULL text of a single transcription row by id.
        """

        def body(d: dict) -> dict:
            result = self.service.get_transcription_text(d["id"])
            return {"type": "transcription_text", "data": result}

        def guarded_body(d: dict) -> dict:
            """Reject payloads the transport would silently drop.

            Dictation length is unbounded upstream, so an extreme row can
            exceed the WS frame cap; that layer DROPS the frame, which
            leaves the renderer waiting forever. Report it instead, the
            same contract the list handlers use.
            """
            payload = body(d)
            try:
                serialized = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            except (TypeError, ValueError):
                return payload
            if len(serialized) <= _HISTORY_MAX_FRAME_BYTES:
                return payload
            from voice_typer.server.ipc.validation import ErrorCodes as _ErrorCodes

            log.warning(
                "[IPC] get_transcription_text response exceeds frame cap "
                "(%d > %d bytes); returning a clear error instead of a dropped frame",
                len(serialized),
                _HISTORY_MAX_FRAME_BYTES,
            )
            return {
                "type": "error",
                "data": {
                    "code": _ErrorCodes.PAYLOAD_TOO_LARGE,
                    "message": (
                        "Transcription text is too large to transfer over IPC "
                        f"({len(serialized)} bytes exceeds {_HISTORY_MAX_FRAME_BYTES} byte cap)"
                    ),
                },
            }

        return self._wrap(
            cmd_name="get_transcription_text",
            resp_type="transcription_text",
            data=data,
            resp=resp,
            body=guarded_body,
            schema={"id": {"type": (int, str), "required": True}},
            pre_coerce=False,
        )


def _publish_history_changed(reason: str) -> None:
    """Broadcast a ``history_changed`` push event via the in-process event bus.

    Best-effort: any failure is logged at DEBUG and swallowed so it never
    breaks the IPC handler's own response. Subscribers (renderer pages) use
    this to invalidate their module-level history caches.
    """
    try:
        from voice_typer.server import event_bus

        event_bus.publish({"type": "history_changed", "data": {"reason": reason}})
    except Exception:
        log.debug("[IPC] history_changed push failed", exc_info=True)
