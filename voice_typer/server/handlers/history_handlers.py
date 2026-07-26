"""History IPC handler mixin: get_history, get_today_stats, delete_history,
restore_history, clear_history, toggle_favorite, get_favorites, search_history,
get_history_count, get_transcription_text.

ARCH-REFAC-002: extracted verbatim from ``voice_typer/server/ipc_server.py``.
The methods are mixed into :class:`IPCServer` via multiple inheritance and
access ``self.app`` / ``self.service`` as before.
"""

import json

from voice_typer.server.handlers._base import HandlerBase
from voice_typer.server.handlers._log import log
from voice_typer.server.ipc.history_bounds import (
    _bound_history_limit,
    _bound_history_offset,
)
from voice_typer.server.ipc.validation import _validate_dict_payload

# Maximum serialized response size for ``get_history`` / ``get_favorites`` /
# ``search_history`` before the handler proactively truncates row ``text``
# previews further. Mirrors ``sidecar_ws._MAX_FRAME_BYTES`` (1 MiB) — the
# Tauri WS layer SILENTLY DROPS frames exceeding this cap, which previously
# manifested as the Dashboard's "Total Dictations" stat never updating once
# dictation texts grew past ~5KB avg × 200 rows = 1MB. The 32KB headroom
# below the cap covers the response envelope (type/data/JSON framing) so the
# serialized payload stays comfortably under the WS-layer limit.
_HISTORY_MAX_FRAME_BYTES = 1 * 1024 * 1024 - 32 * 1024


class HistoryHandlersMixin(HandlerBase):
    """Mixin: history-related IPC handlers (get_history / delete_history / ...).

    CR-20: this mixin is one of the four "representative" handlers
    migrated to :meth:`HandlerBase._respond_with_error` for the
    catch-all ``except Exception`` path. See
    ``voice_typer/server/handlers/_base.py`` for the migration plan.
    """

    # The ``service`` / ``app`` / ``_send`` annotations are inherited
    # from :class:`HandlerMixinBase` — no per-mixin re-declaration
    # needed (the duplicate block removed here was one of four that
    # the R4-F3 centralization refactor missed).

    def _handle_get_history(self, data, resp) -> dict | None:
        """Handle the ``get_history`` IPC command."""
        try:
            # IPC-3: validate ``limit`` / ``offset`` types via the
            # shared ``_validate_dict_payload`` helper. Non-dict
            # ``data`` is pre-coerced to ``{}`` so the
            # ``test_non_dict_data_falls_back_to_defaults`` contract
            # (list → defaults) still holds. ``required: False`` (no
            # default) means absent fields fall through to the
            # ``_bound_history_limit`` / ``_bound_history_offset``
            # clamping helpers (50 / 0) — preserving the existing
            # ``test_default_limit_offset_when_payload_missing``
            # contract.
            #
            # The schema accepts ``(int, str)`` for ``limit`` /
            # ``offset`` because the renderer sometimes sends numeric
            # strings from form inputs (see
            # ``test_get_history_with_string_limit_accepted`` in
            # ``tests/test_server.py``); the ``_bound_history_limit``
            # helper coerces the string to int. A non-int, non-str
            # value (e.g. a list or dict) is rejected with
            # ``invalid_field``.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "limit": {"type": (int, str), "required": False},
                    "offset": {"type": (int, str), "required": False},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            # SEC-010: bound limit/offset to prevent DoS via huge values.
            limit = _bound_history_limit(validated.get("limit", 50))
            offset = _bound_history_offset(validated.get("offset", 0))
            rows = self.service.get_history(limit, offset)
            # Defense-in-depth size check. ``get_recent`` already
            # projects ``text`` to a 500-char preview at the SQL layer,
            # so a 500-row response is ~350KB worst-case — well under
            # the 1 MiB WS frame cap. But other columns (``model``,
            # ``device``, ``language``) are unbounded at the schema
            # level, and a future schema change could re-introduce a
            # large column. The size check here is the safety net:
            # progressively halve each row's ``text`` preview until
            # the serialized response fits, logging a warning so the
            # issue surfaces in diagnostics. ``text_truncated`` is
            # forced True on every row that was further truncated so
            # the renderer's "show more" affordance stays accurate.
            rows = self._enforce_history_frame_cap(rows, command="get_history")
            resp["type"] = "history"
            resp["data"] = rows
        except Exception as exc:
            # CR-20: generic WS-path envelope (no ``str(exc)`` leak).
            self._respond_with_error(resp, exc, "get_history")
        return resp

    def _enforce_history_frame_cap(
        self,
        rows: list[dict],
        *,
        command: str,
    ) -> list[dict]:
        """Progressively truncate row ``text`` previews until the
        serialized response fits under ``_HISTORY_MAX_FRAME_BYTES``.

        Called by :meth:`_handle_get_history` (and the sibling list
        handlers below) AFTER the service returns rows. The SQL layer
        already truncates ``text`` to ``_HISTORY_TEXT_PREVIEW_LENGTH``
        (500) chars per row, so this method is a no-op in the common
        case — it only kicks in when the *cumulative* serialized size
        exceeds the cap (e.g. a future schema change adds a large
        column, or the renderer requests ``limit=500`` with rows whose
        ``model`` / ``device`` fields are unusually long).

        Halving each row's ``text`` per iteration is O(log L) per row
        where L is the preview length — at most ~9 iterations to bring
        a 500-char preview down to 1 char. The loop bails out as soon
        as the serialized size fits. ``text_truncated`` is forced
        ``True`` on every row that was shortened so the renderer's
        "show more" affordance stays accurate (the user can still
        fetch the full text via ``get_transcription_text``).

        The 50-char floor preserves a usable preview even in the
        degenerate case where the response still doesn't fit after
        aggressive truncation — at that point we accept the WS-layer
        drop rather than returning an empty-text list.
        """
        if not rows:
            return rows
        try:
            serialized = json.dumps(rows, ensure_ascii=False, default=str).encode(
                "utf-8",
            )
        except (TypeError, ValueError):
            # If serialization fails entirely, return the rows as-is —
            # the WS layer will surface the error rather than us
            # silently dropping data we can't measure.
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
        # iteration count so a pathological case (every row is huge
        # in non-text fields) doesn't loop forever.
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
                # Every row is already at the floor — bail out to
                # avoid an infinite loop. The WS layer will drop the
                # frame; logged above so the issue is diagnosable.
                break
            try:
                serialized = json.dumps(rows, ensure_ascii=False, default=str).encode("utf-8")
            except (TypeError, ValueError):
                break
        return rows

    def _handle_get_today_stats(self, data, resp) -> dict | None:
        """Handle the ``get_today_stats`` IPC command."""
        try:
            resp["type"] = "today_stats"
            resp["data"] = self.service.get_today_stats()
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_today_stats")
        return resp

    def _handle_delete_history(self, data, resp) -> dict | None:
        """Handle the ``delete_history`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "id": {"type": (int, str), "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            self.service.delete_history(validated["id"])
            resp["type"] = "ack"
            # F11-FIX (b-review Finding 11): broadcast history_changed so
            # every renderer page that keeps a module-level history cache
            # (Home, History, Dashboard) invalidates it. Without this, an
            # external delete (tray menu, another window, CLI) left ghost
            # records in the cache until the next transcription_final /
            # manual refresh. clear_history already does the same above.
            _publish_history_changed("deleted")
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "delete_history")
        return resp

    def _handle_restore_history(self, data, resp) -> dict | None:
        """Handle the ``restore_history`` IPC command.

        NEW-UX-004: re-insert a previously-deleted record so the
        renderer's Undo-delete toast can recover the entry.

        DE-44: added a 256 KB whole-payload cap
        (``max_payload_bytes``) and an inline 8192-char per-field
        cap on ``record['text']``. Without these guards, a
        misbehaving caller could push a multi-MB ``record`` blob
        (or a single 1 MB ``text`` field) that the history store
        would happily persist, bloating the SQLite DB and the
        diagnostics bundle. The whole-payload cap catches a
        caller who stuffs a giant blob into a non-``text`` field;
        the per-field cap catches a caller who stuffs it into
        ``text`` specifically.
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "record": {
                        "type": dict,
                        "required": True,
                        # 256 KB whole-payload cap. The helper's
                        # ``max_payload_bytes`` rule serializes
                        # ``data`` via ``json.dumps`` and rejects if
                        # the size exceeds N bytes.
                        "max_payload_bytes": 256 * 1024,
                    },
                },
            )
            if error:
                # The helper emits namespaced ``client.invalid_payload``
                # for the ``max_payload_bytes`` rule and namespaced
                # ``client.missing_field`` / ``client.invalid_field``
                # for the per-field checks. Test assertions expect the
                # namespaced form, so the error passes through unchanged.
                return error
            assert validated is not None  # narrowed by the error guard above
            record = validated["record"]
            # Per-field cap on ``record['text']``. 8192 chars is the
            # practical upper bound for a single transcription entry
            # (a 10-minute dictation at 150 wpm ≈ 7500 chars). Anything
            # larger is almost certainly a bug or abuse.
            if len(record.get("text", "")) > 8192:
                resp["type"] = "error"
                resp["data"] = {
                    "code": "client.payload_too_large",
                    "legacy_code": "payload_too_large",
                    "field": "record.text",
                    "message": "'record.text' exceeds 8192-char cap",
                }
                return resp
            new_id = self.service.restore_history(record)
            resp["type"] = "ack"
            resp["data"] = {"id": new_id}
            # F11-FIX (b-review Finding 11): a restored record must also
            # invalidate the history caches (see _publish_history_changed).
            _publish_history_changed("restored")
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "restore_history")
        return resp

    def _handle_clear_history(self, data, resp) -> dict | None:
        """Handle the ``clear_history`` IPC command."""
        try:
            self.service.clear_history()
            resp["type"] = "ack"
            # F11-FIX (b-review Finding 11): broadcast a `history_changed`
            # event so every renderer page that keeps a module-level cache
            # of history (Home, History, Dashboard) invalidates it. Without
            # this, clearing history from outside the page (e.g. a tray-menu
            # action or another window) left ghost records in the cache until
            # the next transcription_final / manual refresh.
            _publish_history_changed("cleared")
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "clear_history")
        return resp

    def _handle_toggle_favorite(self, data, resp) -> dict | None:
        """Handle the ``toggle_favorite`` IPC command."""
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "id": {"type": (int, str), "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            new_val = self.service.toggle_favorite(validated["id"])
            resp["type"] = "ack"
            resp["data"] = {"favorite": new_val}
            # F11-FIX (b-review Finding 11): a favorite toggle changes which
            # records show under the "Favorites only" filter and the favorites
            # count on the Dashboard, so invalidate history caches too.
            _publish_history_changed("favorite_toggled")
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "toggle_favorite")
        return resp

    def _handle_get_favorites(self, data, resp) -> dict | None:
        """Handle the ``get_favorites`` IPC command."""
        try:
            # IPC-3: validate ``limit`` / ``offset`` types via the
            # shared ``_validate_dict_payload`` helper. Same pattern as
            # ``_handle_get_history`` (above) — ``(int, str)`` accepts
            # numeric strings from form inputs.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "limit": {"type": (int, str), "required": False},
                    "offset": {"type": (int, str), "required": False},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            # SEC-010: bound limit/offset.
            limit = _bound_history_limit(validated.get("limit", 50))
            offset = _bound_history_offset(validated.get("offset", 0))
            rows = self.service.get_favorites(limit, offset)
            # Same defense-in-depth frame-cap check as
            # ``_handle_get_history`` (see that handler for rationale).
            rows = self._enforce_history_frame_cap(rows, command="get_favorites")
            resp["type"] = "history"
            resp["data"] = rows
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_favorites")
        return resp

    def _handle_search_history(self, data, resp) -> dict | None:
        """Handle the ``search_history`` IPC command."""
        try:
            # IPC-3: validate ``query`` / ``limit`` / ``offset`` types
            # via the shared ``_validate_dict_payload`` helper. Non-dict
            # ``data`` is pre-coerced to ``{}`` so the
            # ``test_non_dict_data_uses_empty_query`` contract (None →
            # empty query, default limit/offset) still holds.
            # ``limit`` / ``offset`` accept ``(int, str)`` for the same
            # form-input coercion reason as ``_handle_get_history``.
            if not isinstance(data, dict):
                data = {}
            validated, error = _validate_dict_payload(
                data,
                {
                    "query": {"type": str, "required": False, "default": ""},
                    "limit": {"type": (int, str), "required": False},
                    "offset": {"type": (int, str), "required": False},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            query = validated.get("query", "")
            # SEC-010: bound limit/offset.
            limit = _bound_history_limit(validated.get("limit", 50))
            offset = _bound_history_offset(validated.get("offset", 0))
            rows = self.service.search_history(query, limit, offset)
            # Same defense-in-depth frame-cap check as
            # ``_handle_get_history`` (see that handler for rationale).
            rows = self._enforce_history_frame_cap(rows, command="search_history")
            resp["type"] = "history"
            resp["data"] = rows
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "search_history")
        return resp

    # ──────────────────────────────────────────────────────────────
    # On-demand full-text + total-count handlers
    # ──────────────────────────────────────────────────────────────

    def _handle_get_history_count(self, data, resp) -> dict | None:
        """Handle the ``get_history_count`` IPC command.

        Returns the total number of transcription rows in the DB.
        The value is cached for 60s in ``HistoryDB`` (mirroring the
        ``get_model_status`` TTL pattern) so the Dashboard's
        ``transcription_final``-triggered refresh doesn't run a full
        ``COUNT(*)`` scan on every dictation.

        The handler takes no required payload — an empty dict (or no
        ``data`` at all) is the expected request shape. A non-dict
        ``data`` is coerced to ``{}`` for forward compatibility (a
        future caller could pass ``{"force_refresh": true}`` to bypass
        the cache — not currently implemented, but the schema accepts
        any dict so the request doesn't fail).
        """
        try:
            if not isinstance(data, dict):
                data = {}
            count = self.service.get_history_count()
            resp["type"] = "history_count"
            resp["data"] = {"count": int(count)}
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_history_count")
        return resp

    def _handle_get_transcription_text(self, data, resp) -> dict | None:
        """Handle the ``get_transcription_text`` IPC command.

        Returns the FULL text of a single transcription row by id.
        Companion to the 500-char ``text`` preview returned by
        ``get_history`` / ``get_favorites`` / ``search_history``.
        The renderer fetches the full text on demand (when the user
        expands a row in the History page) so list responses stay
        under the 1 MiB WS frame cap.

        Request shape: ``{"id": int | str}`` — same ``id`` schema as
        ``delete_history`` / ``toggle_favorite`` (accepts string IDs
        from form inputs). Response shape: ``{"type": "transcription_text",
        "data": {"id": int, "text": str}}`` — the renderer uses the
        ``text`` field to replace the row's truncated preview.

        If the row doesn't exist, the response returns ``text: ""``
        (matching the ``HistoryDB.get_transcription_text`` sentinel).
        The renderer treats an empty string as "no text to show".
        """
        try:
            validated, error = _validate_dict_payload(
                data,
                {
                    "id": {"type": (int, str), "required": True},
                },
            )
            if error:
                return error
            assert validated is not None  # narrowed by the error guard above
            result = self.service.get_transcription_text(validated["id"])
            resp["type"] = "transcription_text"
            resp["data"] = result
        except Exception as exc:
            # CR-20: generic WS-path envelope.
            self._respond_with_error(resp, exc, "get_transcription_text")
        return resp


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
