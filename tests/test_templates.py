"""Tests for voice_typer.templates — TemplateManager CRUD, match, variables."""

import json

import pytest


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


# ─── PI-8 regression tests ────────────────────────────────────────────────


class TestTemplatesBackupAndQuarantine:
    """PI-8: templates.py now routes persistence through PersistedJSON,
    which provides single-slot .bak before overwrite + corrupt-file
    quarantine on load failure. These tests pin the new behavior so a
    future refactor that drops the helper (or replaces it with a
    bare _secure_atomic_write) doesn't silently regress PI-8."""

    def test_templates_creates_bak_on_overwrite(self, template_dir):
        """PI-8: save template A, save template B, assert .bak file
        contains A.

        The .bak is single-slot: each save overwrites the previous .bak
        (so re-saves don't accumulate backup files). The .bak holds the
        PREVIOUS content, byte-for-byte, so the user can recover their
        last good state if a save turns out to be wrong.
        """
        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        templates_file = template_dir / TEMPLATES_FILENAME
        bak_file = template_dir / f"{TEMPLATES_FILENAME}.bak"

        # Save template A.
        tm1 = TemplateManager(config_dir=template_dir)
        tm1.add("hello", "Hello World!")
        del tm1

        content_a = templates_file.read_text(encoding="utf-8")
        assert '"hello"' in content_a
        # No .bak yet — first save has nothing to back up.
        assert not bak_file.exists()

        # Save template B (different trigger).
        tm2 = TemplateManager(config_dir=template_dir)
        tm2.add("goodbye", "Goodbye World!")
        del tm2

        content_b = templates_file.read_text(encoding="utf-8")
        assert '"goodbye"' in content_b
        # The .bak must now exist and contain template A's content
        # (byte-for-byte), so the user can recover their previous
        # state if template B turns out to be wrong.
        assert bak_file.exists(), (
            "PI-8 regression: .bak file should exist after the second "
            "save overwrites the first"
        )
        bak_content = bak_file.read_text(encoding="utf-8")
        assert bak_content == content_a, (
            "PI-8 regression: .bak file should contain the PREVIOUS "
            "template content (byte-for-byte), not the new content"
        )
        assert '"hello"' in bak_content
        assert '"goodbye"' not in bak_content

    def test_templates_quarantines_corrupt_file(self, template_dir):
        """PI-8: write corrupt JSON to the templates file, call load,
        assert the file is moved to .corrupt-<ts> and load returns the
        default (empty templates list).

        Without quarantine, the next save would atomically overwrite the
        corrupt file with the in-memory defaults, destroying any chance
        of forensic recovery. Quarantine preserves the corrupt file at
        ``<path>.corrupt-<timestamp>`` so the user can inspect what
        truncation pattern led to the parse failure.
        """
        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        templates_file = template_dir / TEMPLATES_FILENAME
        # Write corrupt JSON to the templates file.
        corrupt_payload = '{"templates": [{"trigger": "hello", broken'
        templates_file.write_text(corrupt_payload, encoding="utf-8")
        assert templates_file.exists()

        # Construct a TemplateManager — this calls _load which must
        # detect the corrupt JSON, quarantine it, and fall back to the
        # default (empty templates list).
        tm = TemplateManager(config_dir=template_dir)

        # The corrupt file must have been renamed to .corrupt-<ts>.
        assert not templates_file.exists(), (
            "PI-8 regression: corrupt templates file should have been "
            "renamed (quarantined), not left in place"
        )
        corrupt_files = list(template_dir.glob(f"{TEMPLATES_FILENAME}.corrupt-*"))
        assert len(corrupt_files) == 1, (
            f"PI-8 regression: expected exactly one .corrupt-<ts> file, "
            f"got {corrupt_files}"
        )
        # The quarantined file must contain the original corrupt payload
        # (byte-for-byte) so the user can inspect what went wrong.
        assert corrupt_files[0].read_text(encoding="utf-8") == corrupt_payload

        # Load must have returned the default (empty templates list).
        assert len(tm.templates) == 0
