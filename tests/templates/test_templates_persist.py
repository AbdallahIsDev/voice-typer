"""Tests that ``TemplateManager`` persists templates to disk on save.

Split out of the former ``tests/test_history_and_models.py`` catch-all
(Phase 4.5 split). Verbatim mechanical move — same test names +
assertions, only the file location changed.
"""

from __future__ import annotations


class TestTemplatesPersistToDisk:
    """Templates survive to disk via TemplateManager._save."""

    def test_templates_persisted_to_json_file(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=templates_dir)
        tm.add("hello", "Hello World!")
        tm.add("bye", "Goodbye!", match_mode="contains")

        templates_file = templates_dir / "templates.json"
        assert templates_file.exists()
        import json

        data = json.loads(templates_file.read_text(encoding="utf-8"))
        assert "templates" in data
        assert len(data["templates"]) == 2
        assert data["templates"][0]["trigger"] == "hello"
        assert data["templates"][1]["match_mode"] == "contains"

    def test_templates_loaded_on_restart(self, templates_dir):
        from voice_typer.server.templates import TemplateManager

        tm1 = TemplateManager(config_dir=templates_dir)
        tm1.add("persist_test", "persisted value")
        del tm1

        tm2 = TemplateManager(config_dir=templates_dir)
        templates = tm2.templates
        assert len(templates) == 1
        assert templates[0]["trigger"] == "persist_test"
        assert templates[0]["output"] == "persisted value"

    def test_service_save_and_get_round_trip(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())

        templates_to_save = [
            {"trigger": "my_email", "output": "me@example.com", "match_mode": "exact"},
            {"trigger": "signature", "output": "Best regards,\nJohn", "match_mode": "contains"},
        ]
        assert service.save_templates(templates_to_save) is True

        FakeApp._template_manager = None
        service2 = VoiceTyperService(FakeApp())
        loaded = service2.get_templates()
        assert len(loaded) == 2
        assert loaded[0]["trigger"] == "my_email"
        assert loaded[0]["output"] == "me@example.com"
        assert loaded[1]["match_mode"] == "contains"

    def test_service_save_rejects_invalid_entries(self, templates_dir):
        from voice_typer.server.service import VoiceTyperService

        class FakeApp:
            _template_manager = None

        service = VoiceTyperService(FakeApp())
        bad_input = [
            {"trigger": "valid", "output": "ok", "match_mode": "exact"},
            {"trigger": "", "output": "missing trigger"},
            {"trigger": "missing output", "output": ""},
            {"trigger": "bad mode", "output": "ok", "match_mode": "invalid"},
            "not a dict",
            None,
        ]
        assert service.save_templates(bad_input) is True
        loaded = service.get_templates()
        assert len(loaded) == 2
        triggers = [t["trigger"] for t in loaded]
        assert "valid" in triggers
        assert "bad mode" in triggers
        bad_mode_entry = next(t for t in loaded if t["trigger"] == "bad mode")
        assert bad_mode_entry["match_mode"] == "exact"
