"""IPC-3: coverage tests for the 10 newly-validated handlers."""

from __future__ import annotations

import logging
import os

import pytest


class TestDownloadModelValidation:
    """``_handle_download_model``, IPC-3 invalid-type coverage."""

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"model\": 123}`` → ``code: invalid_field, field: model``."""
        resp = ipc_server._handle_download_model({"model": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"
        fake_service.download_model.assert_not_called()

    def test_list_model_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"model\": [\"small.en\"]}`` → ``code: invalid_field``."""
        resp = ipc_server._handle_download_model({"model": ["small.en"]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"
        fake_service.download_model.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """``\"not-a-dict\"`` → ``code: invalid_payload``."""
        resp = ipc_server._handle_download_model("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.download_model.assert_not_called()


class TestDeleteModelValidation:
    """``_handle_delete_model``, IPC-3 invalid-type coverage."""

    def test_non_string_model_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_delete_model({"model": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "model"
        fake_service.delete_model.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_delete_model(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.delete_model.assert_not_called()


class TestImportModelValidation:
    """``_handle_import_model``, IPC-3 invalid-type coverage."""

    def test_non_string_dir_path_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"dir_path\": 123}`` → ``code: invalid_field, field: dir_path``."""
        resp = ipc_server._handle_import_model({"dir_path": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "dir_path"
        fake_service.import_model.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_import_model(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.import_model.assert_not_called()


class TestMicrophoneTestStartValidation:
    """``_handle_microphone_test_start``, IPC-3 invalid-type coverage."""

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"mic_id\": 123}`` → ``code: invalid_field, field: mic_id``."""
        resp = ipc_server._handle_microphone_test_start({"mic_id": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.microphone_test_start.assert_not_called()

    def test_non_dict_filters_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"filters\": \"not-a-dict\"}`` → ``code: invalid_field, field: filters``."""
        resp = ipc_server._handle_microphone_test_start({"filters": "not-a-dict"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "filters"
        fake_service.microphone_test_start.assert_not_called()

    def test_none_mic_id_is_accepted(self, ipc_server, fake_service):
        """``{\"mic_id\": None}`` → accepted (None is in the allowed type tuple)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"mic_id": None}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=10.0,
            filters=None,
        )


class TestLevelMonitorStartValidation:
    """``_handle_level_monitor_start``, IPC-3 invalid-type coverage."""

    def test_non_string_mic_id_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_level_monitor_start({"mic_id": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "mic_id"
        fake_service.level_monitor_start.assert_not_called()

    def test_non_dict_payload_pre_coerced_to_defaults(self, ipc_server, fake_service):
        """Non-dict ``data`` is pre-coerced to ``{}`` so the existing"""
        fake_service.level_monitor_start.return_value = {"running": True}
        resp = ipc_server._handle_level_monitor_start(None, {})
        assert resp["type"] == "level_monitor_status"
        fake_service.level_monitor_start.assert_called_once_with(mic_id=None)


class TestSetEscCancelPausedValidation:
    """``_handle_set_esc_cancel_paused``, IPC-3 invalid-type coverage."""

    def test_non_bool_paused_returns_invalid_field_error(self, ipc_server, fake_app):
        """``{\"paused\": \"true\"}`` → ``code: invalid_field, field: paused``."""
        resp = ipc_server._handle_set_esc_cancel_paused({"paused": "true"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "paused"

    def test_int_paused_returns_invalid_field_error(self, ipc_server, fake_app):
        """``{\"paused\": 1}`` → ``code: invalid_field, field: paused``."""
        resp = ipc_server._handle_set_esc_cancel_paused({"paused": 1}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "paused"

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_app):
        resp = ipc_server._handle_set_esc_cancel_paused("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"


class TestGetHistoryValidation:
    """``_handle_get_history``, IPC-3 invalid-type coverage."""

    def test_list_limit_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"limit\": [50]}`` → ``code: invalid_field, field: limit``."""
        resp = ipc_server._handle_get_history({"limit": [50]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "limit"
        fake_service.get_history.assert_not_called()

    def test_dict_offset_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"offset\": {\"x\": 0}}`` → ``code: invalid_field, field: offset``."""
        resp = ipc_server._handle_get_history({"offset": {"x": 0}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "offset"

    def test_bool_limit_accepted_due_to_int_subclass(self, ipc_server, fake_service):
        """``bool`` is a subclass of ``int``: ``isinstance(True, int)``"""
        resp = ipc_server._handle_get_history({"limit": True}, {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once()

    def test_string_numeric_limit_accepted(self, ipc_server, fake_service):
        """``{\"limit\": \"25\"}`` → accepted (numeric string is coerced"""
        resp = ipc_server._handle_get_history({"limit": "25"}, {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(25, 0)

    def test_non_dict_payload_pre_coerced_to_defaults(self, ipc_server, fake_service):
        """
        Non-dict ``data`` is pre-coerced to ``{}`` so the existing
        ``test_non_dict_data_falls_back_to_defaults`` contract still
        """
        fake_service.get_history.return_value = []
        resp = ipc_server._handle_get_history(["not", "a", "dict"], {})
        assert resp["type"] == "history"
        fake_service.get_history.assert_called_once_with(50, 0)


class TestGetFavoritesValidation:
    """``_handle_get_favorites``, IPC-3 invalid-type coverage."""

    def test_list_limit_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_get_favorites({"limit": [50]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "limit"
        fake_service.get_favorites.assert_not_called()

    def test_dict_offset_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_get_favorites({"offset": {"x": 0}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "offset"


class TestSearchHistoryValidation:
    """``_handle_search_history``, IPC-3 invalid-type coverage."""

    def test_non_string_query_returns_invalid_field_error(self, ipc_server, fake_service):
        """``{\"query\": 123}`` → ``code: invalid_field, field: query``."""
        resp = ipc_server._handle_search_history({"query": 123}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "query"
        fake_service.search_history.assert_not_called()

    def test_list_limit_returns_invalid_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_search_history({"limit": [50]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "limit"

    def test_non_dict_payload_pre_coerced_to_defaults(self, ipc_server, fake_service):
        """
        Non-dict ``data`` is pre-coerced to ``{}`` so the existing
        ``test_non_dict_data_uses_empty_query`` contract still holds
        """
        fake_service.search_history.return_value = []
        resp = ipc_server._handle_search_history(None, {})
        assert resp["type"] == "history"
        fake_service.search_history.assert_called_once_with("", 50, 0)


class TestToggleDictationValidation:
    """``_handle_toggle_dictation``, IPC-3 invalid-payload coverage."""

    def test_non_dict_string_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A non-None non-dict payload (e.g. a string) is rejected."""
        resp = ipc_server._handle_toggle_dictation("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.toggle_dictation.assert_not_called()

    def test_non_dict_list_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A list payload is rejected."""
        resp = ipc_server._handle_toggle_dictation(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.toggle_dictation.assert_not_called()

    def test_none_payload_pre_coerced_to_empty_dict(self, ipc_server, fake_service):
        """
        ``None`` (the value when the ``data`` key is absent) is
        This preserves the contract that ``{"id": 1, "type":
        """
        resp = ipc_server._handle_toggle_dictation(None, {})
        assert resp["type"] == "ack"
        fake_service.toggle_dictation.assert_called_once_with()

    def test_empty_dict_still_works(self, ipc_server, fake_service):
        """Regression: the happy path ``{}`` must still validate cleanly"""
        resp = ipc_server._handle_toggle_dictation({}, {})
        assert resp["type"] == "ack"
        fake_service.toggle_dictation.assert_called_once_with()


class TestScrubTraceback:
    """DE-38: ``_scrub_traceback`` strips secrets + home-dir paths."""

    def test_scrubs_openai_style_api_key_from_exception_message(self):
        """A ``sk-...`` key embedded in ``str(exc)`` is replaced."""
        from voice_typer.server.handlers._base import _scrub_traceback

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        exc = RuntimeError(f"key not found: {secret}")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert secret not in scrubbed_str, f"API key leaked through scrub: {scrubbed_str!r}"
        # The redaction marker should be present (the canonical
        assert "***" in scrubbed_str or "[REDACTED]" in scrubbed_str, (
            f"Expected a redaction marker, got: {scrubbed_str!r}"
        )

    def test_scrubs_groq_style_api_key_from_exception_message(self):
        """A ``gsk_...`` key embedded in ``str(exc)`` is replaced."""
        from voice_typer.server.handlers._base import _scrub_traceback

        secret = "gsk_" + "a" * 30
        exc = RuntimeError(f"invalid Groq key: {secret}")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert secret not in scrubbed_str, f"Groq API key leaked through scrub: {scrubbed_str!r}"

    def test_scrubs_bearer_token_from_exception_message(self):
        """A ``Bearer <token>`` string in ``str(exc)`` is redacted."""
        from voice_typer.server.handlers._base import _scrub_traceback

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        exc = RuntimeError(f"Authorization: Bearer {secret} rejected")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert secret not in scrubbed_str, f"Bearer token leaked through scrub: {scrubbed_str!r}"

    def test_scrubs_home_directory_path_from_exception_message(self):
        """Home-directory path components are replaced with ``~``."""
        from voice_typer.server.handlers._base import _scrub_traceback

        home = os.path.expanduser("~")
        if home in ("/", "~", ""):
            pytest.skip("HOME is not set or is root, cannot test home-dir scrub")
        exc = RuntimeError(f"failed to open {home}/.config/lausu/config.json")
        scrubbed_str, _ = _scrub_traceback(exc)
        assert home not in scrubbed_str, f"Home directory leaked through scrub: {scrubbed_str!r}"

    def test_scrubs_home_directory_path_from_traceback_text(self):
        """Home-directory paths in the formatted traceback are scrubbed."""
        from voice_typer.server.handlers._base import _scrub_traceback

        home = os.path.expanduser("~")
        if home in ("/", "~", ""):
            pytest.skip("HOME is not set or is root, cannot test home-dir scrub")
        try:
            # Raise an exception whose traceback frames will include
            raise RuntimeError(f"failed at {home}/.cache/model.bin")
        except RuntimeError as exc:
            _, scrubbed_tb = _scrub_traceback(exc)
        assert home not in scrubbed_tb, f"Home directory leaked through traceback scrub: {scrubbed_tb!r}"

    def test_respond_with_error_does_not_leak_secret_to_response(
        self,
    ):
        """The renderer-facing response envelope never carries the secret."""
        from voice_typer.server.handlers._base import HandlerBase

        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        helper = HandlerBase()
        resp: dict = {"id": 1}
        result = helper._respond_with_error(resp, RuntimeError(f"key not found: {secret}"), "test_cmd")
        assert secret not in str(result), f"Secret leaked into response envelope: {result!r}"
        assert result["data"]["message"] == "internal error"

    def test_respond_with_error_scrubs_secret_from_log(self, ipc_server, fake_service, caplog):
        """DE-38: the log record's formatted message must not carry the secret."""
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890ABCDEF"
        fake_service.cancel_model_download.side_effect = RuntimeError(f"key not found: {secret}")
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_cancel_model_download({}, {})

        # The renderer envelope is unchanged.
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

        # The log record's formatted message must not carry the secret.
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "catch-all must log at ERROR level"
        for record in error_records:
            formatted = record.getMessage()
            assert secret not in formatted, f"Secret leaked into log message: {formatted!r}"
            # If a traceback was attached (record.exc_text or via
            if record.exc_text:
                assert secret not in record.exc_text, f"Secret leaked into record.exc_text: {record.exc_text!r}"
        # ``record.exc_info`` must still be set so structured-logging
        assert any(r.exc_info is not None for r in error_records), (
            "DE-38 scrub must preserve record.exc_info (test_catch_all_logs_at_"
            "error_level_with_exc_info asserts it is not None)."
        )

    def test_respond_with_error_scrubs_home_dir_from_log(self, ipc_server, fake_service, caplog, monkeypatch):
        """DE-38: home-directory paths in the log are replaced with ``~``."""
        home = os.path.expanduser("~")
        if home in ("/", "~", ""):
            pytest.skip("HOME is not set or is root, cannot test home-dir scrub")
        fake_service.cancel_model_download.side_effect = RuntimeError(
            f"failed to open {home}/.config/lausu/config.json"
        )
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.ipc_server"):
            ipc_server._handle_cancel_model_download({}, {})

        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records
        for record in error_records:
            formatted = record.getMessage()
            assert home not in formatted, f"Home dir leaked into log message: {formatted!r}"


class TestControlCharRejection:
    """DE-42: ``title`` / ``message`` reject Cc/Cf chars except ``\\t``."""

    def test_ansi_escape_in_title_is_rejected(self, ipc_server):
        """An ANSI color escape (``\\x1b[31m``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_notification(
            {"title": "\x1b[31mRed Title\x1b[0m", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_newline_in_title_is_rejected(self, ipc_server):
        """A newline (``\\n``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_notification(
            {"title": "line1\nline2", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_terminal_bell_in_message_is_rejected(self, ipc_server):
        """A terminal bell (``\\x07``) in ``message`` → invalid_field."""
        resp = ipc_server._handle_show_notification(
            {"title": "ok", "message": "beep\x07beep"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "message"

    def test_rtl_override_in_title_is_rejected(self, ipc_server):
        """A Unicode RTL override (``\\u202e``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_notification(
            {"title": "alert\u202eevah", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_zero_width_joiner_in_message_is_rejected(self, ipc_server):
        """A ZWJ (``\\u200d``) in ``message`` → invalid_field (Cf category)."""
        resp = ipc_server._handle_show_notification(
            {"title": "ok", "message": "a\u200db"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "message"

    def test_bom_in_title_is_rejected(self, ipc_server):
        """A Byte Order Mark (``\\ufeff``) in ``title`` → invalid_field."""
        resp = ipc_server._handle_show_notification(
            {"title": "\ufeffHello", "message": "ok"},
            {},
        )
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "title"

    def test_tab_in_message_is_accepted(self, ipc_server):
        """A horizontal tab (``\\t``) in ``message`` is accepted."""
        captured: list[dict] = []
        from voice_typer.server import event_bus

        sub = captured.append
        event_bus.subscribe(sub)
        try:
            resp = ipc_server._handle_show_notification(
                {"title": "ok", "message": "col1\tcol2"},
                {},
            )
        finally:
            event_bus.unsubscribe(sub)
        assert resp["type"] == "ack", f"Tab should be accepted, got error: {resp}"
        assert captured[0]["data"]["message"] == "col1\tcol2"

    def test_clean_payload_is_accepted(self, ipc_server):
        """A payload with no control chars is accepted (sanity check)."""
        captured: list[dict] = []
        from voice_typer.server import event_bus

        sub = captured.append
        event_bus.subscribe(sub)
        try:
            resp = ipc_server._handle_show_notification(
                {"title": "Hello World", "message": "Just a normal message."},
                {},
            )
        finally:
            event_bus.unsubscribe(sub)
        assert resp["type"] == "ack"
        assert captured[0]["data"]["title"] == "Hello World"


class TestGetStatusValidation:
    """DE-43: ``_handle_get_status`` validates payload + catches exceptions."""

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """
        A non-dict ``data`` (list) → ``code: invalid_payload``.
        ADR-0020 §2 contract.
        """
        resp = ipc_server._handle_get_status(["not", "a", "dict"], {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.get_status.assert_not_called()

    def test_string_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        """A string ``data`` → ``code: invalid_payload``."""
        resp = ipc_server._handle_get_status("not-a-dict", {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"

    def test_none_payload_is_coerced_to_empty_dict(self, ipc_server, fake_service):
        """``None`` is pre-coerced to ``{}`` (matches the toggle_dictation pattern)."""
        fake_service.get_status.return_value = {"status": "idle"}
        resp = ipc_server._handle_get_status(None, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "idle"}

    def test_service_raises_returns_internal_error_envelope(self, ipc_server, fake_service):
        """``service.get_status()`` raising → catch-all envelope."""
        fake_service.get_status.side_effect = RuntimeError("recorder not started")
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestRestoreHistoryPayloadCap:
    """DE-44: ``restore_history`` caps payload size + ``record['text']`` length."""

    def test_oversized_text_field_returns_payload_too_large(self, ipc_server, fake_service):
        """``record['text']`` > 8192 chars → ``code: payload_too_large``."""
        oversized_text = "x" * 10_000  # > 8192-char cap
        record = {"id": 1, "text": oversized_text}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.payload_too_large"
        assert resp["data"]["field"] == "record.text"
        fake_service.restore_history.assert_not_called()

    def test_oversized_whole_payload_returns_invalid_payload(self, ipc_server, fake_service):
        """A >256 KB serialized payload → ``code: invalid_payload``."""
        giant_blob = "y" * 300_000
        record = {"id": 1, "text": "ok", "blob": giant_blob}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.restore_history.assert_not_called()

    def test_text_at_exactly_8192_chars_is_accepted(self, ipc_server, fake_service):
        """``record['text']`` of exactly 8192 chars is on the boundary, accepted."""
        fake_service.restore_history.return_value = 42
        boundary_text = "x" * 8192
        record = {"id": 1, "text": boundary_text}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"id": 42}
        fake_service.restore_history.assert_called_once_with(record)

    def test_normal_payload_is_accepted(self, ipc_server, fake_service):
        """A normal-sized record is accepted (regression check)."""
        fake_service.restore_history.return_value = 7
        record = {"id": 1, "text": "restored transcription"}
        resp = ipc_server._handle_restore_history({"record": record}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"id": 7}


class TestDurationClampRange:
    """DE-45: ``duration`` is clamped to ``[1.0, 30.0]`` (backend cap)."""

    def test_huge_numeric_duration_is_clamped_to_30(self, ipc_server, fake_service):
        """``duration=1e300`` → clamped to 30.0 (DoS guard)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 1e300}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=30.0,
            filters=None,
        )

    def test_negative_numeric_duration_is_clamped_to_1(self, ipc_server, fake_service):
        """``duration=-5.0`` → clamped to 1.0 (lower bound)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": -5.0}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=1.0,
            filters=None,
        )

    def test_huge_string_duration_is_clamped_to_30(self, ipc_server, fake_service):
        """``duration=\"1e300\"`` (string) → coerced + clamped to 30.0."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "1e300"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=30.0,
            filters=None,
        )

    def test_zero_duration_is_clamped_to_1(self, ipc_server, fake_service):
        """``duration=0`` → clamped to 1.0 (lower bound)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 0}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=1.0,
            filters=None,
        )

    def test_in_bounds_numeric_duration_passes_through(self, ipc_server, fake_service):
        """``duration=7.5`` → 7.5 (no clamping needed)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 7.5}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_in_bounds_string_duration_is_coerced(self, ipc_server, fake_service):
        """``duration=\"7.5\"`` → 7.5 (preserves the documented string coercion)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "7.5"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_missing_duration_falls_back_to_default_10(self, ipc_server, fake_service):
        """Empty payload → duration defaults to 10.0 (historical default)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=10.0,
            filters=None,
        )

    def test_above_backend_cap_duration_is_clamped_to_30(self, ipc_server, fake_service):
        """``duration=45`` → clamped to 30.0 (backend ``test_recording`` cap)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": 45}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=30.0,
            filters=None,
        )

    def test_invalid_duration_type_returns_invalid_field(self, ipc_server, fake_service):
        """``duration=[\"list\"]`` → ``code: invalid_field`` (not in (int, float, str))."""
        resp = ipc_server._handle_microphone_test_start({"duration": ["not", "a", "number"]}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "duration"
        fake_service.microphone_test_start.assert_not_called()


class TestExistingContractsPreserved:
    """Regression guards: existing handler contracts still hold after the DE-2H fixes."""

    def test_microphone_test_string_duration_still_coerced(self, ipc_server, fake_service):
        """Existing contract: ``\"7.5\" → 7.5`` (documented string coercion)."""
        fake_service.microphone_test_start.return_value = {"ok": True}
        resp = ipc_server._handle_microphone_test_start({"duration": "7.5"}, {})
        assert resp["type"] == "microphone_test_result"
        fake_service.microphone_test_start.assert_called_once_with(
            mic_id=None,
            duration=7.5,
            filters=None,
        )

    def test_get_status_dict_return_value_still_passes_through(self, ipc_server, fake_service):
        """Existing contract: dict return value passes through unchanged."""
        fake_service.get_status.return_value = {
            "status": "recording",
            "xruns_since_start": 2,
        }
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "recording", "xruns_since_start": 2}

    def test_get_status_legacy_string_return_value_wrapped(self, ipc_server, fake_service):
        """Existing contract: legacy string return value wrapped in dict."""
        fake_service.get_status.return_value = "recording"
        resp = ipc_server._handle_get_status({}, {})
        assert resp["type"] == "status"
        assert resp["data"] == {"status": "recording"}
