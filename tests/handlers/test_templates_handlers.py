"""Unit tests for ``TemplatesHandlersMixin`` (CR-12).

Covers the 2 templates IPC handlers defined in
``voice_typer/server/handlers/templates_handlers.py``:

- ``_handle_get_templates`` — returns ``{type: templates, data: {templates: [...]}}``.
- ``_handle_save_templates`` — validates ``templates`` is a list, then
  delegates to ``service.save_templates``.
"""

from __future__ import annotations


class TestGetTemplates:
    """``_handle_get_templates`` — returns the saved templates list."""

    def test_happy_path_returns_templates_type(self, ipc_server, fake_service):
        fake_service.get_templates.return_value = [
            {"name": "Email", "body": "Hi {name},"},
        ]
        resp = ipc_server._handle_get_templates({}, {})
        assert resp["type"] == "templates"
        assert resp["data"] == {"templates": [{"name": "Email", "body": "Hi {name},"}]}
        fake_service.get_templates.assert_called_once_with()

    def test_service_raises_returns_error(self, ipc_server, fake_service):
        fake_service.get_templates.side_effect = RuntimeError("disk error")
        resp = ipc_server._handle_get_templates({}, {})
        assert resp["type"] == "error"
        assert "disk error" in resp["data"]["message"]


class TestSaveTemplates:
    """``_handle_save_templates`` — validates ``templates`` list, saves."""

    def test_happy_path_returns_ack_with_count(self, ipc_server, fake_service):
        templates = [
            {"name": "Email", "body": "Hi {name},"},
            {"name": "Meeting", "body": "Agenda:"},
        ]
        resp = ipc_server._handle_save_templates({"templates": templates}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"saved": 2}
        fake_service.save_templates.assert_called_once_with(templates)

    def test_missing_templates_returns_missing_field_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_save_templates({}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "missing_field"
        assert resp["data"]["field"] == "templates"
        fake_service.save_templates.assert_not_called()

    def test_non_list_templates_returns_invalid_field_error(self, ipc_server, fake_service):
        """``templates`` must be a list — dict/string/int are rejected."""
        resp = ipc_server._handle_save_templates({"templates": {"not": "a list"}}, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_field"
        assert resp["data"]["field"] == "templates"
        fake_service.save_templates.assert_not_called()

    def test_non_dict_payload_returns_invalid_payload_error(self, ipc_server, fake_service):
        resp = ipc_server._handle_save_templates(None, {})
        assert resp["type"] == "error"
        assert resp["data"]["code"] == "invalid_payload"
        fake_service.save_templates.assert_not_called()

    def test_empty_list_returns_ack_with_zero(self, ipc_server, fake_service):
        """An empty templates list is valid (clears all templates)."""
        resp = ipc_server._handle_save_templates({"templates": []}, {})
        assert resp["type"] == "ack"
        assert resp["data"] == {"saved": 0}
        fake_service.save_templates.assert_called_once_with([])
