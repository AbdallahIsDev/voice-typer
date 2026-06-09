"""Tests for voice_typer.templates — TemplateManager CRUD, match, variables."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def template_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def tm(template_dir):
    """Create a TemplateManager with a clean temp dir."""
    from voice_typer.server.templates import TemplateManager
    return TemplateManager(config_dir=template_dir)


class TestTemplateCRUD:
    def test_add_template(self, tm):
        t = tm.add("hello", "Hello World!")
        assert t["trigger"] == "hello"
        assert t["output"] == "Hello World!"
        assert len(tm.templates) == 1

    def test_add_template_with_match_mode(self, tm):
        t = tm.add("hello", "Hello!", match_mode="contains")
        assert t["match_mode"] == "contains"

    def test_update_template(self, tm):
        tm.add("hello", "Hello!")
        result = tm.update(0, "hi", "Hi there!", match_mode="contains")
        assert result is not None
        assert result["trigger"] == "hi"
        assert result["output"] == "Hi there!"
        assert result["match_mode"] == "contains"

    def test_update_invalid_index(self, tm):
        result = tm.update(99, "x", "y")
        assert result is None

    def test_delete_template(self, tm):
        tm.add("hello", "Hello!")
        assert tm.delete(0) is True
        assert len(tm.templates) == 0

    def test_delete_invalid_index(self, tm):
        assert tm.delete(99) is False

    def test_templates_property_returns_copy(self, tm):
        tm.add("hello", "Hello!")
        t = tm.templates
        assert len(t) == 1
        # Modifying the copy shouldn't affect the manager
        t.clear()
        assert len(tm.templates) == 1


class TestTemplateMatch:
    def test_exact_match(self, tm):
        tm.add("code review", "Please review this code.")
        result = tm.match("code review")
        assert result is not None
        assert "Please review this code." in result

    def test_exact_match_case_insensitive(self, tm):
        tm.add("code review", "Please review this code.")
        result = tm.match("Code Review")
        assert result is not None

    def test_exact_match_whitespace_normalized(self, tm):
        tm.add("code review", "Please review this code.")
        result = tm.match("code  review")
        assert result is not None

    def test_contains_match(self, tm):
        tm.add("code review", "Please review this code.", match_mode="contains")
        result = tm.match("let's do a code review now")
        assert result is not None

    def test_no_match(self, tm):
        tm.add("code review", "Please review this code.")
        result = tm.match("something else")
        assert result is None

    def test_shortest_trigger_wins(self, tm):
        tm.add("review", "Short review text.", match_mode="contains")
        tm.add("code review", "Full code review text.", match_mode="contains")
        result = tm.match("code review")
        assert result is not None
        assert "Short review text." in result

    def test_empty_text_no_match(self, tm):
        tm.add("code review", "Please review this code.")
        assert tm.match("") is None
        assert tm.match(None) is None

    def test_no_templates_no_match(self, tm):
        assert tm.match("anything") is None


class TestTemplateVariables:
    def test_today_variable(self, tm):
        from datetime import datetime
        tm.add("date", "{today}")
        result = tm.match("date")
        assert result is not None
        assert datetime.now().strftime("%Y-%m-%d") in result

    def test_now_variable(self, tm):
        tm.add("time", "{now}")
        result = tm.match("time")
        assert result is not None
        # Should contain HH:MM format
        assert ":" in result

    def test_username_variable(self, tm):
        import getpass
        tm.add("user", "{username}")
        result = tm.match("user")
        assert result is not None
        assert getpass.getuser() in result

    def test_multiple_variables(self, tm):
        tm.add("meeting", "Meeting on {today} at {now} with {username}")
        result = tm.match("meeting")
        assert result is not None
        assert "{today}" not in result
        assert "{now}" not in result
        assert "{username}" not in result


class TestTemplatePersistence:
    def test_templates_persist_across_instances(self, template_dir):
        from voice_typer.server.templates import TemplateManager
        tm1 = TemplateManager(config_dir=template_dir)
        tm1.add("hello", "Hello!")
        del tm1

        tm2 = TemplateManager(config_dir=template_dir)
        assert len(tm2.templates) == 1
        assert tm2.templates[0]["trigger"] == "hello"

    def test_empty_templates_file(self, template_dir):
        from voice_typer.server.templates import TemplateManager
        # Write empty templates file
        (template_dir / "voice-typer-templates.json").write_text('{"templates": []}')
        tm = TemplateManager(config_dir=template_dir)
        assert len(tm.templates) == 0


class TestTemplateImportExport:
    def test_export_json(self, tm):
        tm.add("hello", "Hello!")
        exported = tm.export_json()
        data = json.loads(exported)
        assert "templates" in data
        assert len(data["templates"]) == 1

    def test_import_json(self, tm):
        json_str = json.dumps({"templates": [
            {"trigger": "imported", "output": "Imported text", "match_mode": "exact"}
        ]})
        count = tm.import_json(json_str)
        assert count == 1
        assert len(tm.templates) == 1

    def test_import_invalid_json(self, tm):
        count = tm.import_json("not json{{{")
        assert count == 0

    def test_import_missing_fields(self, tm):
        json_str = json.dumps({"templates": [{"trigger": "no_output"}]})
        count = tm.import_json(json_str)
        assert count == 0
