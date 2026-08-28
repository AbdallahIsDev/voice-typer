"""Export payload size-cap enforcement (AP-3).

The 1 MiB IPC frame cap (``_TCP_MAX_OUTBOUND_BYTES`` in
``ipc/sender.py`` / ``_MAX_FRAME_BYTES`` in ``sidecar_ws.py``) silently
DROPS an oversized outbound frame — the client sees no response and
eventually times out. Bulk-data handlers must therefore fail fast with
a clear structured error instead of producing a frame that gets
dropped.

These tests pin:
- ``_enforce_payload_size_cap`` (the shared helper + its exported cap)
- ``_handle_get_vocabulary`` / ``_handle_get_templates`` oversized paths
- ``_enforce_history_frame_cap`` residual-oversize path (rows whose
  non-text columns are too large to shrink below the frame cap)
"""

from __future__ import annotations

from voice_typer.server.ipc.validation import (
    MAX_EXPORT_PAYLOAD_BYTES,
    _enforce_payload_size_cap,
)

from tests.fixtures.ipc_test_helpers import make_ipc_server_with_fakes


class TestEnforcePayloadSizeCap:
    """The shared guard helper contract."""

    def test_small_payload_returns_none(self) -> None:
        assert _enforce_payload_size_cap({"entries": []}) is None

    def test_oversized_payload_returns_error_envelope(self) -> None:
        payload = {"data": "x" * (MAX_EXPORT_PAYLOAD_BYTES + 100)}
        err = _enforce_payload_size_cap(payload)
        assert err is not None
        assert err["type"] == "error"
        assert err["data"]["code"] == "client.payload_too_large"
        assert "exceeds" in err["data"]["message"]

    def test_custom_error_message(self) -> None:
        payload = {"data": "x" * (MAX_EXPORT_PAYLOAD_BYTES + 100)}
        err = _enforce_payload_size_cap(payload, error_message="Vocabulary is too large")
        assert err is not None
        assert err["data"]["message"].startswith("Vocabulary is too large")

    def test_custom_max_bytes(self) -> None:
        err = _enforce_payload_size_cap({"data": "x" * 100}, max_bytes=10)
        assert err is not None
        assert err["data"]["code"] == "client.payload_too_large"


class TestGetVocabularyCap:
    """``_handle_get_vocabulary`` must reject an oversized merged vocabulary."""

    def test_oversized_vocabulary_returns_clear_error(self) -> None:
        server, _app, service = make_ipc_server_with_fakes()
        # ~1.5 MB merged vocabulary — well over the 1 MiB frame cap.
        service.get_vocabulary.return_value = {
            "misspellings": {f"bad{i:04d}": "x" * 500 for i in range(3000)},
        }
        resp = server._handle_get_vocabulary({}, {})
        # Clear structured error (NOT a silently dropped frame).
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.payload_too_large"
        assert "too large" in resp["data"]["message"]

    def test_small_vocabulary_passes_through(self) -> None:
        server, _app, service = make_ipc_server_with_fakes()
        service.get_vocabulary.return_value = {"entries": [{"word": "hello"}]}
        resp = server._handle_get_vocabulary({}, {})
        assert resp["type"] == "vocabulary"
        assert resp["data"] == {"entries": [{"word": "hello"}]}


class TestGetTemplatesCap:
    """``_handle_get_templates`` must reject an oversized template store."""

    def test_oversized_templates_returns_clear_error(self) -> None:
        server, _app, service = make_ipc_server_with_fakes()
        # ~1.5 MB template store — well over the 1 MiB frame cap.
        service.get_templates.return_value = [{"trigger": f"t{i:04d}", "output": "x" * 500} for i in range(3000)]
        resp = server._handle_get_templates({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.payload_too_large"
        assert "too large" in resp["data"]["message"]

    def test_small_templates_passes_through(self) -> None:
        server, _app, service = make_ipc_server_with_fakes()
        service.get_templates.return_value = [{"trigger": "t1", "output": "out"}]
        resp = server._handle_get_templates({}, {})
        assert resp["type"] == "templates"
        assert resp["data"] == {"templates": [{"trigger": "t1", "output": "out"}]}


class TestHistoryFrameCapFallback:
    """``_enforce_history_frame_cap`` must not silently drop when truncation can't fit."""

    def test_residual_oversize_returns_error_envelope(self) -> None:
        server, _app, _service = make_ipc_server_with_fakes()
        # Rows whose text is already at the 50-char floor but whose
        # non-text columns are huge — text truncation can't shrink them
        # below the frame cap. ~1.5 MB serialized.
        rows = [
            {
                "id": i,
                "text": "x" * 50,  # at the truncation floor
                "model": "y" * 3000,  # huge non-text column
            }
            for i in range(500)
        ]
        result = server._enforce_history_frame_cap(rows, command="get_history")
        # A clear error envelope, NOT an oversized rows list that the
        # WS/TCP layer would silently drop.
        assert isinstance(result, dict)
        assert result["type"] == "error"
        assert result["data"]["code"] == "client.payload_too_large"

    def test_truncatable_rows_still_trimmed_not_dropped(self) -> None:
        server, _app, _service = make_ipc_server_with_fakes()
        # 500 rows with ~10 KB text each — ~5 MB serialized, so the
        # truncation loop halves the text previews until it fits.
        rows = [{"id": i, "text": "x" * 10000} for i in range(500)]
        result = server._enforce_history_frame_cap(rows, command="get_history")
        assert isinstance(result, list), "truncatable rows must come back as a list, not an error"
        assert result, "rows must not be emptied"
        assert any(r.get("text_truncated") for r in result), "at least one row should have been truncated"
        assert all(len(r["text"]) < 10000 for r in result), "rows should have been truncated below the original length"

    def test_fit_rows_untouched(self) -> None:
        server, _app, _service = make_ipc_server_with_fakes()
        rows = [{"id": i, "text": "hello"} for i in range(3)]
        result = server._enforce_history_frame_cap(rows, command="get_history")
        assert isinstance(result, list)
        assert result == rows
