"""Unit tests for ``PrivacyHandlersMixin`` (CR-87 / CR-88 — XS-79).

Covers the two privacy / GDPR IPC handlers defined in
``voice_typer/server/handlers/privacy_handlers.py``:

- ``_handle_delete_all_personal_data`` — GDPR Art. 17 right-to-erasure
  (wipes every user-data file the app owns).
- ``_handle_export_gdpr_bundle`` — GDPR Art. 20 right-to-data-portability
  (ZIP of every user-data file, unredacted).

Both handlers are thin envelopes over the service layer
(``service.delete_all_personal_data`` / ``service.export_gdpr_bundle``).
The handler-side behaviour under test:

1. **Happy path**: service returns a success dict → handler returns
   ``{"type": "<cmd>_result", "data": <result>}`` (the service's
   ``{"success": bool, "deleted": [...], ...}`` shape is preserved
   unchanged so the renderer can show which files succeeded / failed).
2. **PermissionError / OSError → ``server.file_locked``**: when one or
   more user-data files are locked by another process, the service
   raises ``PermissionError`` / ``OSError`` and the handler surfaces
   a structured ``code: "server.file_locked"`` envelope so the
   renderer can prompt the user to close the locking process and retry.
3. **Non-dict payload → ``invalid_payload``**: a string / list payload
   is rejected by ``_validate_dict_payload`` (the ``data is None`` →
   ``{}`` coercion means only non-None non-dicts reach the rejection
   path; ``None`` itself is accepted as "no fields").
4. **Generic Exception → ``server.internal_error``**: any other
   exception (e.g. service method missing / ``RuntimeError``) is
   caught by the ``except Exception`` block and routed through
   ``HandlerBase._respond_with_error``, which emits the generic
   WS-path envelope (``{"code": "server.internal_error", "message":
   "internal error"}``) without leaking ``str(exc)`` to the renderer.

The fixture plumbing (``ipc_server`` / ``fake_app`` / ``fake_service``)
is shared with the other handler-mixin tests via
``tests/handlers/conftest.py``.
"""

from __future__ import annotations

import pytest

# ── _handle_delete_all_personal_data ────────────────────────────────────


class TestDeleteAllPersonalData:
    """``_handle_delete_all_personal_data`` — GDPR Art. 17 right-to-erasure."""

    def test_happy_path_returns_result_envelope(self, ipc_server, fake_service):
        """Service success dict → ``{"type": "delete_all_personal_data_result",
        "data": <result>}`` envelope, shape preserved unchanged.
        """
        expected_result = {
            "success": True,
            "deleted": ["history.db", "config.json"],
            "skipped": ["models/whisper-small.bin"],
            "failed": {},
        }
        fake_service.delete_all_personal_data.return_value = expected_result

        resp = ipc_server._handle_delete_all_personal_data({}, {})

        assert resp["type"] == "delete_all_personal_data_result"
        assert resp["data"] is expected_result
        fake_service.delete_all_personal_data.assert_called_once_with()

    def test_none_payload_is_treated_as_empty_dict(self, ipc_server, fake_service):
        """``data=None`` is coerced to ``{}`` before validation — accepted."""
        fake_service.delete_all_personal_data.return_value = {"success": True}

        resp = ipc_server._handle_delete_all_personal_data(None, {})

        assert resp["type"] == "delete_all_personal_data_result"
        assert resp["data"] == {"success": True}
        fake_service.delete_all_personal_data.assert_called_once_with()

    def test_permission_error_returns_file_locked_envelope(self, ipc_server, fake_service):
        """``PermissionError`` from the service → structured
        ``code: "server.file_locked"`` envelope so the renderer can
        prompt the user to close the locking process and retry.
        """
        fake_service.delete_all_personal_data.side_effect = PermissionError(
            "voice-typer.log is locked by another process"
        )

        resp = ipc_server._handle_delete_all_personal_data({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.file_locked"
        assert "locked" in resp["data"]["message"]
        # CR-20 / G4-CR-09: the raw exception text must NOT leak to the renderer.
        assert "voice-typer.log" not in resp["data"]["message"]

    def test_oserror_subclass_returns_file_locked_envelope(self, ipc_server, fake_service):
        """``OSError`` subclasses (e.g. file-in-use on Windows) are also
        mapped to ``server.file_locked`` so the renderer's retry CTA
        works cross-platform.
        """
        fake_service.delete_all_personal_data.side_effect = OSError("file in use")

        resp = ipc_server._handle_delete_all_personal_data({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.file_locked"

    @pytest.mark.parametrize("bad_payload", ["not-a-dict", [1, 2, 3], 42, ("tuple",)])
    def test_non_dict_payload_returns_invalid_payload_envelope(
        self, ipc_server, fake_service, bad_payload
    ):
        """Non-dict payload (string / list / int / tuple) → ``invalid_payload``.

        The handler's ``data is None`` → ``{}`` coercion means only
        non-None non-dicts reach the validation rejection path.
        """
        resp = ipc_server._handle_delete_all_personal_data(bad_payload, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.delete_all_personal_data.assert_not_called()

    def test_generic_exception_returns_internal_error_envelope(self, ipc_server, fake_service):
        """Any non-permission ``Exception`` (e.g. service method missing)
        is routed through ``_respond_with_error`` and emits the generic
        ``server.internal_error`` envelope — no ``str(exc)`` leak.
        """
        fake_service.delete_all_personal_data.side_effect = RuntimeError("service not wired")

        resp = ipc_server._handle_delete_all_personal_data({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
        # The raw exception text must NOT leak to the renderer.
        assert "service not wired" not in resp["data"]["message"]

    def test_attribute_error_simulating_missing_service_method_returns_internal_error(
        self, ipc_server, fake_service
    ):
        """If the service is missing the method entirely (e.g. an old
        service implementation that pre-dates CR-87), the ``AttributeError``
        from ``self.service.delete_all_personal_data`` is caught by the
        generic ``except Exception`` block and mapped to
        ``server.internal_error`` (not propagated).
        """
        fake_service.delete_all_personal_data.side_effect = AttributeError(
            "ServiceProtocol has no attribute 'delete_all_personal_data'"
        )

        resp = ipc_server._handle_delete_all_personal_data({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"


# ── _handle_export_gdpr_bundle ──────────────────────────────────────────


class TestExportGdprBundle:
    """``_handle_export_gdpr_bundle`` — GDPR Art. 20 right-to-data-portability."""

    def test_happy_path_returns_result_envelope(self, ipc_server, fake_service):
        """Service success dict → ``{"type": "export_gdpr_bundle_result",
        "data": <result>}`` envelope, shape preserved unchanged.
        """
        expected_result = {
            "success": True,
            "path": "/tmp/voice-typer-gdpr-20260721.zip",
            "files": ["history.db", "config.json"],
            "size_bytes": 12345,
        }
        fake_service.export_gdpr_bundle.return_value = expected_result

        resp = ipc_server._handle_export_gdpr_bundle({}, {})

        assert resp["type"] == "export_gdpr_bundle_result"
        assert resp["data"] is expected_result
        fake_service.export_gdpr_bundle.assert_called_once_with()

    def test_none_payload_is_treated_as_empty_dict(self, ipc_server, fake_service):
        """``data=None`` is coerced to ``{}`` before validation — accepted."""
        fake_service.export_gdpr_bundle.return_value = {"success": True, "path": "/tmp/x.zip"}

        resp = ipc_server._handle_export_gdpr_bundle(None, {})

        assert resp["type"] == "export_gdpr_bundle_result"
        assert resp["data"] == {"success": True, "path": "/tmp/x.zip"}
        fake_service.export_gdpr_bundle.assert_called_once_with()

    def test_permission_error_returns_file_locked_envelope(self, ipc_server, fake_service):
        """``PermissionError`` while reading a user-data file into the
        ZIP → ``server.file_locked`` envelope so the renderer can prompt
        the user to close the locking process and retry.
        """
        fake_service.export_gdpr_bundle.side_effect = PermissionError(
            "voice-typer.log is locked by another process"
        )

        resp = ipc_server._handle_export_gdpr_bundle({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.file_locked"
        assert "locked" in resp["data"]["message"]
        # The raw exception text must NOT leak to the renderer.
        assert "voice-typer.log" not in resp["data"]["message"]

    def test_oserror_subclass_returns_file_locked_envelope(self, ipc_server, fake_service):
        """``OSError`` while reading a file into the ZIP → ``server.file_locked``."""
        fake_service.export_gdpr_bundle.side_effect = OSError("file in use")

        resp = ipc_server._handle_export_gdpr_bundle({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.file_locked"

    @pytest.mark.parametrize("bad_payload", ["not-a-dict", [1, 2, 3], 42, ("tuple",)])
    def test_non_dict_payload_returns_invalid_payload_envelope(
        self, ipc_server, fake_service, bad_payload
    ):
        """Non-dict payload (string / list / int / tuple) → ``invalid_payload``."""
        resp = ipc_server._handle_export_gdpr_bundle(bad_payload, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "client.invalid_payload"
        fake_service.export_gdpr_bundle.assert_not_called()

    def test_generic_exception_returns_internal_error_envelope(self, ipc_server, fake_service):
        """Any non-permission ``Exception`` (e.g. ZIP write failure) →
        ``server.internal_error`` envelope, no ``str(exc)`` leak.
        """
        fake_service.export_gdpr_bundle.side_effect = RuntimeError("disk full")

        resp = ipc_server._handle_export_gdpr_bundle({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
        # The raw exception text must NOT leak to the renderer.
        assert "disk full" not in resp["data"]["message"]

    def test_attribute_error_simulating_missing_service_method_returns_internal_error(
        self, ipc_server, fake_service
    ):
        """If the service is missing the method entirely (e.g. an old
        service implementation that pre-dates CR-88), the ``AttributeError``
        from ``self.service.export_gdpr_bundle`` is caught by the
        generic ``except Exception`` block and mapped to
        ``server.internal_error`` (not propagated).
        """
        fake_service.export_gdpr_bundle.side_effect = AttributeError(
            "ServiceProtocol has no attribute 'export_gdpr_bundle'"
        )

        resp = ipc_server._handle_export_gdpr_bundle({}, {})

        assert resp["type"] == "error"
        assert resp["data"]["code"] == "server.internal_error"
        assert resp["data"]["message"] == "internal error"
