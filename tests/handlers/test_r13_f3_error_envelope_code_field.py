"""R13-F3 + G4-CR-09 + G4-M-22: handler catch-all error envelope tests."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest


class TestErrorResponseHelper:
    def test_stamps_code_and_message(self):
        from voice_typer.server.ipc.validation import _error_response

        resp = {"id": 42}
        result = _error_response(resp, "disk full")
        assert result is resp
        assert result["type"] == "error"
        assert result["data"] == {
            "code": "server.handler_error",
            "legacy_code": "handler_error",
            "message": "disk full",
        }
        assert result["id"] == 42

    def test_default_code_is_server_handler_error(self):
        from voice_typer.server.ipc.validation import _error_response

        resp = {}
        _error_response(resp, "boom")
        assert resp["data"]["code"] == "server.handler_error"

    def test_code_can_be_overridden(self):
        from voice_typer.server.ipc.validation import _error_response

        resp = {}
        _error_response(resp, "import path outside allowed roots", code="client.path_not_allowed")
        assert resp["data"]["code"] == "client.path_not_allowed"

    def test_preserves_existing_id_field(self):
        from voice_typer.server.ipc.validation import _error_response

        resp = {"id": 99, "type": "ack", "data": {"in_progress": True}}
        _error_response(resp, "handler raised")
        assert resp["id"] == 99
        assert resp["type"] == "error"
        assert resp["data"] == {
            "code": "server.handler_error",
            "legacy_code": "handler_error",
            "message": "handler raised",
        }

    def test_message_is_not_truncated_or_modified(self):
        from voice_typer.server.ipc.validation import _error_response

        resp = {}
        weird_message = "ValueError: bad input '\\x00' [extra] <html>"
        _error_response(resp, weird_message)
        assert resp["data"]["message"] == weird_message


class TestErrorCodesRegistry:
    def test_registry_is_a_frozenset(self):
        from voice_typer.server.ipc.validation import ERROR_CODES

        assert isinstance(ERROR_CODES, frozenset)

    def test_registry_contains_namespaced_codes(self):
        from voice_typer.server.ipc.validation import ERROR_CODES

        assert "client.invalid_field" in ERROR_CODES
        assert "client.missing_field" in ERROR_CODES
        assert "client.invalid_payload" in ERROR_CODES
        assert "client.rate_limited" in ERROR_CODES
        assert "client.path_not_allowed" in ERROR_CODES
        assert "client.not_found" in ERROR_CODES
        assert "server.internal_error" in ERROR_CODES
        assert "server.handler_error" in ERROR_CODES
        assert "server.file_locked" in ERROR_CODES
        assert "server.model_switch_failed" in ERROR_CODES

    def test_respond_with_error_emits_server_internal_error(self):
        from voice_typer.server.handlers._base import HandlerBase

        helper = HandlerBase()
        resp = {"id": 1}
        try:
            raise RuntimeError("simulated handler crash")
        except RuntimeError as exc:
            result = helper._respond_with_error(resp, exc, "test_cmd")
        assert result is resp
        assert result["type"] == "error"
        assert result["data"] == {
            "code": "server.internal_error",
            "legacy_code": "internal_error",
            "message": "internal error",
        }
        assert "simulated handler crash" not in str(result["data"])


class TestHandlerFilesUseHelper:
    HANDLERS_DIR = Path("voice_typer/server/handlers")
    NON_HANDLER_FILES = {"__init__.py", "_base.py", "_log.py"}
    # UE-15 (2026-07-30): these handlers were reduced to empty stubs
    # (the IPC dispatch routes were deleted; the Tauri host invokes
    # the service layer directly via dedicated Rust commands). They
    # have no catch-all to wrap, so the helper-coverage test must
    # skip them.
    STUB_HANDLER_FILES = {"privacy_handlers.py", "vocabulary_automation_handlers.py"}

    @pytest.fixture(autouse=True)
    def _handlers_dir_exists(self):
        if not self.HANDLERS_DIR.is_dir():
            pytest.skip(f"handlers dir not found: {self.HANDLERS_DIR}")

    def _handler_files(self):
        for fpath in sorted(self.HANDLERS_DIR.glob("*.py")):
            if fpath.name in self.NON_HANDLER_FILES:
                continue
            if fpath.name in self.STUB_HANDLER_FILES:
                continue
            yield fpath, fpath.read_text()

    def test_every_handler_file_uses_a_standardized_helper(self):
        missing = []
        for fpath, src in self._handler_files():
            if "_respond_with_error" not in src and "_error_response" not in src:
                missing.append(fpath.name)
        assert not missing, (
            f"every handler file must use either _respond_with_error or _error_response; missing: {missing}"
        )

    def test_no_handler_file_has_inline_str_e_envelope(self):
        offenders = []
        for fpath, src in self._handler_files():
            if 'resp["data"] = {"message": str(e)}' in src or ("resp['data'] = {'message': str(e)}" in src):
                offenders.append(fpath.name)
        assert not offenders, f"these handler files still contain the pre-R13-F3 inline envelope: {offenders}"

    def test_every_handler_file_uses_helper_in_catch_all(self):
        no_helper_use = []
        for fpath, src in self._handler_files():
            if "except Exception as e:" not in src and "except Exception as exc:" not in src:
                continue
            if "_respond_with_error(resp" not in src and "_error_response(resp" not in src:
                no_helper_use.append(fpath.name)
        assert not no_helper_use, f"these handler files have an except block but don't use a helper: {no_helper_use}"


class TestHandlerCatchAllEnvelopeShape:
    def test_toggle_dictation_catch_all_has_code_field(self, ipc_server, fake_service):
        fake_service.toggle_dictation.side_effect = RuntimeError("simulated boom")
        resp = ipc_server._handle_toggle_dictation(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_get_vocabulary_catch_all_has_code_field(self, ipc_server, fake_service):
        fake_service.get_vocabulary.side_effect = RuntimeError("vocab db corrupt")
        resp = ipc_server._handle_get_vocabulary({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_export_diagnostics_catch_all_has_code_field(self, ipc_server, fake_service):
        """UE-15 (2026-07-30): ``_handle_export_diagnostics`` was deleted
        from ``SystemHandlersMixin`` (the Tauri host handles the
        diagnostics export via a dedicated Rust command). The
        catch-all envelope-shape regression it covered is now exercised
        via ``_handle_cancel_model_download`` (a sibling handler with
        the same catch-all path)."""
        fake_service.cancel_model_download.side_effect = RuntimeError("disk full")
        resp = ipc_server._handle_cancel_model_download({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_get_microphones_catch_all_has_code_field(self, ipc_server, fake_service):
        fake_service.get_microphones.side_effect = RuntimeError("portaudio init failed")
        resp = ipc_server._handle_get_microphones({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"

    def test_repaste_last_catch_all_has_code_field(self, ipc_server, fake_service):
        fake_service.repaste_last.side_effect = RuntimeError("clipboard busy")
        resp = ipc_server._handle_repaste_last({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


class TestHandlerCatchAllLogging:
    def test_catch_all_logs_at_error_level_with_exc_info(self, ipc_server, fake_service, caplog):
        fake_service.toggle_dictation.side_effect = RuntimeError("boom with detail")
        with caplog.at_level(logging.ERROR, logger="voice_typer.server.ipc_server"):
            resp = ipc_server._handle_toggle_dictation(None, {})
        assert resp["data"]["message"] == "internal error"
        assert "Traceback" not in resp["data"]["message"]
        assert "boom with detail" not in resp["data"]["message"]
        assert resp["data"]["code"] == "server.internal_error"
        error_records = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert error_records, "catch-all must log at ERROR level"
        assert any("toggle_dictation failed" in r.getMessage() for r in error_records)
        assert any(r.exc_info is not None for r in error_records)

    def test_catch_all_does_not_leak_traceback_to_client(self, ipc_server, fake_service):
        # UE-15: was ``_handle_export_diagnostics`` — switched to
        # ``_handle_cancel_model_download`` (same catch-all path) after
        # the export_diagnostics handler was deleted.
        fake_service.cancel_model_download.side_effect = ValueError("malformed input\n  detail line 1\n  detail line 2")
        resp = ipc_server._handle_cancel_model_download({}, {})
        msg = resp["data"]["message"]
        assert msg == "internal error"
        assert "Traceback" not in msg
        assert "malformed input" not in msg
        assert resp["data"]["code"] == "server.internal_error"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
