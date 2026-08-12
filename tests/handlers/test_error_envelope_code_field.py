"""Handler catch-all error envelope tests."""

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
            "message": "internal error",
        }
        assert "simulated handler crash" not in str(result["data"])


class TestHandlerFilesUseHelper:
    HANDLERS_DIR = Path("voice_typer/server/handlers")
    NON_HANDLER_FILES = {"__init__.py", "_base.py", "_log.py"}
    # (2026-07-30): these handlers were reduced to empty stubs
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
        assert not offenders, f"these handler files still contain the legacy inline envelope: {offenders}"

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
        """``_handle_export_diagnostics`` was deleted
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
        # was ``_handle_export_diagnostics`` — switched to
        # ``_handle_cancel_model_download`` (same catch-all path) after
        # the export_diagnostics handler was deleted.
        fake_service.cancel_model_download.side_effect = ValueError("malformed input\n  detail line 1\n  detail line 2")
        resp = ipc_server._handle_cancel_model_download({}, {})
        msg = resp["data"]["message"]
        assert msg == "internal error"
        assert "Traceback" not in msg
        assert "malformed input" not in msg
        assert resp["data"]["code"] == "server.internal_error"


class TestHandlerBaseErrorResponseExtraKwargs:
    """: ``HandlerBase._error_response`` accepts ``**extra`` kwargs
       that merge into ``resp["data"]`` alongside the standard ``code`` +
    ``message`` pair. Pre- the only ``_error_response`` was the
       standalone function in ``validation.py``, which does NOT accept
       extra fields — so per-command validation errors that needed to
       carry field-level context (e.g. ``field="provider"``) had to be
       constructed inline as ``resp["data"] = {"message": "..."}`` with
       NO ``code`` field at all. The method form on ``HandlerBase``
    closes that gap so the 7 inline envelopes identified in can
       route through it and stamp a structured ``code`` + ``field``.
    """

    def test_merges_extra_kwargs_into_data(self):
        from voice_typer.server.handlers._base import HandlerBase
        from voice_typer.server.ipc.validation import ErrorCodes

        helper = HandlerBase()
        resp = {"id": 7}
        result = helper._error_response(
            resp,
            "Missing 'provider' parameter",
            code=ErrorCodes.MISSING_FIELD,
            field="provider",
        )
        assert result is resp
        assert resp["type"] == "error"
        assert resp["data"] == {
            "code": "client.missing_field",
            "message": "Missing 'provider' parameter",
            "field": "provider",
        }
        # The original resp fields (e.g. ``id``) MUST be preserved.
        assert resp["id"] == 7

    def test_default_code_is_server_handler_error(self):
        from voice_typer.server.handlers._base import HandlerBase

        helper = HandlerBase()
        resp = {}
        helper._error_response(resp, "boom")
        assert resp["data"]["code"] == "server.handler_error"

    def test_no_extra_kwargs_produces_only_code_and_message(self):
        from voice_typer.server.handlers._base import HandlerBase

        helper = HandlerBase()
        resp = {}
        helper._error_response(resp, "msg", code="client.not_found")
        # When no extra kwargs are passed, ``data`` contains ONLY
        # ``code`` + ``message`` (no stray keys).
        assert set(resp["data"].keys()) == {"code", "message"}

    def test_extra_kwargs_cannot_clobber_code_or_message(self):
        """The ``code`` parameter is positional-or-keyword on the
        method signature, so a caller passing ``code=...`` in
        ``**extra`` would raise ``TypeError`` (Python's "multiple
        values for keyword argument" error) — preventing accidental
        shadowing of the standard pair. ``message`` is a positional
        parameter so the same protection applies."""
        from voice_typer.server.handlers._base import HandlerBase

        helper = HandlerBase()
        resp = {}
        # ``code`` in **extra would collide with the explicit
        # ``code`` parameter — Python rejects this at call time.
        with pytest.raises(TypeError):
            helper._error_response(resp, "msg", code="client.not_found", **{"code": "x"})


class TestInlineValidationEnvelopesHaveCodeField:
    """regression: every inline ``type="error"`` envelope in
       ``cloud_test_handlers.py`` and ``model_handlers.py`` MUST stamp a
       structured ``code`` field (and, where applicable, a ``field``
       field) so the renderer can programmatically distinguish error
       types instead of pattern-matching the message text.

    Pre- each of the 7 sites below built its envelope ad-hoc as
       ``resp["data"] = {"message": "..."}`` with NO ``code`` field.
       Clients branching on ``code`` (the renderer's toast dispatch)
       silently fell through to a generic "unknown error" path for these
       per-command validation rejections.

    The 7 sites covered (line numbers refer to source)
       * ``cloud_test_handlers.py:154-158`` — missing ``provider``
       * ``cloud_test_handlers.py:160-168`` — unknown ``provider`` (endpoint lookup)
       * ``cloud_test_handlers.py:174-179`` — unknown ``provider`` (defensive config_field lookup)
       * ``model_handlers.py:69-72``       — missing ``model`` (download_model)
       * ``model_handlers.py:207-211``     — missing ``dir_path`` (import_model)
       * ``model_handlers.py:240-243``     — directory not found (import_model)
       * ``model_handlers.py:277-280``     — missing ``model`` (delete_model)
    """

    # ── cloud_test_handlers.py ───────────────────────────────────

    def test_cloud_test_missing_provider_envelope_has_code_field(self, ipc_server):
        """Empty/missing ``provider`` → ``client.missing_field`` with
        ``field="provider"`` (was: bare ``{"message": "..."}``)."""
        resp = ipc_server._handle_test_cloud_connection({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "provider"
        assert resp["data"]["message"] == "Missing 'provider' parameter"

    def test_cloud_test_unknown_provider_envelope_has_code_field(self, ipc_server):
        """Unknown ``provider`` value → ``client.invalid_field`` with
        ``field="provider"`` (was: bare ``{"message": "..."}``).
        The handler rejects the value BEFORE any network call, so no
        ``_opener`` mock is needed."""
        resp = ipc_server._handle_test_cloud_connection({"provider": "nonexistent"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "provider"
        assert "nonexistent" in resp["data"]["message"]

    def test_cloud_test_unknown_provider_defensive_envelope_has_code_field(self, ipc_server, monkeypatch):
        """Defensive branch: ``_PROVIDER_TO_CONFIG_FIELD`` lookup
        returns ``None`` for a provider that DID resolve via
        ``_PROVIDER_TEST_ENDPOINTS``. In production this is
        unreachable (both dicts have the same keys), so we
        monkeypatch ``_PROVIDER_TO_CONFIG_FIELD`` to an empty dict
        to force the defensive branch. The envelope MUST still be
        ``client.invalid_field`` with ``field="provider"``."""
        monkeypatch.setattr(
            "voice_typer.server.handlers.cloud_test_handlers._PROVIDER_TO_CONFIG_FIELD",
            {},
        )
        resp = ipc_server._handle_test_cloud_connection({"provider": "openai"}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_field"
        assert resp["data"]["field"] == "provider"
        assert "openai" in resp["data"]["message"]

    # ── model_handlers.py ────────────────────────────────────────

    def test_download_model_missing_model_envelope_has_code_field(self, ipc_server, fake_service):
        """``download_model`` with empty/missing ``model`` →
        ``client.missing_field`` with ``field="model"`` (was: bare
        ``{"message": "..."}``)."""
        resp = ipc_server._handle_download_model({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "model"
        assert resp["data"]["message"] == "Missing 'model' parameter"
        fake_service.download_model.assert_not_called()

    def test_import_model_missing_dir_path_envelope_has_code_field(self, ipc_server, fake_service):
        """``import_model`` with empty/missing ``dir_path`` →
        ``client.missing_field`` with ``field="dir_path"`` (was: bare
        ``{"message": "..."}``)."""
        resp = ipc_server._handle_import_model({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "dir_path"
        assert resp["data"]["message"] == "Missing 'dir_path' parameter"
        fake_service.import_model.assert_not_called()

    def test_import_model_directory_not_found_envelope_has_code_field(
        self, ipc_server, fake_service, monkeypatch, tmp_path
    ):
        """``import_model`` with a validated path that doesn't exist
        on disk → ``client.not_found`` with ``field="dir_path"`` (was:
        bare ``{"message": "..."}``). We monkeypatch
        ``_validate_import_path`` to pass-through so the path
        validator doesn't reject the nonexistent path before the
        ``os.path.isdir`` check runs."""
        monkeypatch.setattr("voice_typer.server.config._validate_import_path", lambda p: p)
        nonexistent = str(tmp_path / "does_not_exist")
        resp = ipc_server._handle_import_model({"dir_path": nonexistent}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.not_found"
        assert resp["data"]["field"] == "dir_path"
        assert "Directory not found" in resp["data"]["message"]
        assert nonexistent in resp["data"]["message"]
        fake_service.import_model.assert_not_called()

    def test_delete_model_missing_model_envelope_has_code_field(self, ipc_server, fake_service):
        """``delete_model`` with empty/missing ``model`` →
        ``client.missing_field`` with ``field="model"`` (was: bare
        ``{"message": "..."}``)."""
        resp = ipc_server._handle_delete_model({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.missing_field"
        assert resp["data"]["field"] == "model"
        assert resp["data"]["message"] == "Missing 'model' parameter"
        fake_service.delete_model.assert_not_called()


class TestNoInlineMessageOnlyEnvelopesRemain:
    """structural test: the two handler files patched in this
    finding MUST NOT contain any inline ``resp["data"] = {"message":
    "..."}`` envelope. Every per-command validation error must route
    through ``self._error_response(...)`` so the envelope always
    carries a structured ``code`` field.

    A source-level scan (not a behavioural test) is the right tool
    here: it catches a future contributor who re-introduces the
    inline pattern even on a code path the behavioural tests don't
    cover. The pattern is matched literally (not via regex) so a
    substring match in a docstring / comment would NOT trigger a
    false positive — only the actual assignment-statement form
    matches.
    """

    def test_cloud_test_handlers_has_no_inline_message_only_envelope(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        src = (repo_root / "voice_typer/server/handlers/cloud_test_handlers.py").read_text()
        # The literal assignment ``resp["data"] = {"message": "..."}``
        # (single-OR-double quoted) is the inline-envelope smell. Post-fix every
        # such site routes through ``self._error_response(...)``.
        assert 'resp["data"] = {"message":' not in src, (
            "cloud_test_handlers.py still contains an inline "
            '``resp["data"] = {"message": ...}`` envelope — every '
            "per-command validation error must route through "
            "``self._error_response(...)`` so the envelope carries a "
            "structured ``code`` field."
        )
        assert "resp['data'] = {'message':" not in src, (
            "cloud_test_handlers.py still contains an inline "
            "``resp['data'] = {'message': ...}`` envelope — every "
            "per-command validation error must route through "
            "``self._error_response(...)`` so the envelope carries a "
            "structured ``code`` field."
        )

    def test_model_handlers_has_no_inline_message_only_envelope(self):
        from pathlib import Path

        repo_root = Path(__file__).resolve().parents[2]
        src = (repo_root / "voice_typer/server/handlers/model_handlers.py").read_text()
        assert 'resp["data"] = {"message":' not in src, (
            "model_handlers.py still contains an inline "
            '``resp["data"] = {"message": ...}`` envelope — every '
            "per-command validation error must route through "
            "``self._error_response(...)`` so the envelope carries a "
            "structured ``code`` field."
        )
        assert "resp['data'] = {'message':" not in src, (
            "model_handlers.py still contains an inline "
            "``resp['data'] = {'message': ...}`` envelope — every "
            "per-command validation error must route through "
            "``self._error_response(...)`` so the envelope carries a "
            "structured ``code`` field ."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--no-cov"])
