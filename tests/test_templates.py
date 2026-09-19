"""Tests for voice_typer.templates: TemplateManager CRUD, match, variables."""

import json

import pytest


@pytest.fixture
def template_dir(tmp_config_dir):
    """Point config to a temp directory (via the canonical tmp_config_dir fixture)."""
    return tmp_config_dir


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
        (template_dir / "templates.json").write_text('{"templates": []}')
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
        json_str = json.dumps(
            {"templates": [{"trigger": "imported", "output": "Imported text", "match_mode": "exact"}]}
        )
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


class TestTemplatesBackupAndQuarantine:
    """PI-8: templates.py now routes persistence through PersistedJSON,"""

    def test_templates_creates_bak_on_overwrite(self, template_dir):
        """PI-8: save template A, save template B, assert .bak file"""
        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        templates_file = template_dir / TEMPLATES_FILENAME
        bak_file = template_dir / f"{TEMPLATES_FILENAME}.bak"

        # Save template A.
        tm1 = TemplateManager(config_dir=template_dir)
        tm1.add("hello", "Hello World!")
        del tm1

        content_a = templates_file.read_text(encoding="utf-8")
        assert '"hello"' in content_a
        # No .bak yet, first save has nothing to back up.
        assert not bak_file.exists()

        # Save template B (different trigger).
        tm2 = TemplateManager(config_dir=template_dir)
        tm2.add("goodbye", "Goodbye World!")
        del tm2

        content_b = templates_file.read_text(encoding="utf-8")
        assert '"goodbye"' in content_b
        # The .bak must now exist and contain template A's content
        assert bak_file.exists(), "PI-8 regression: .bak file should exist after the second save overwrites the first"
        bak_content = bak_file.read_text(encoding="utf-8")
        assert bak_content == content_a, (
            "PI-8 regression: .bak file should contain the PREVIOUS "
            "template content (byte-for-byte), not the new content"
        )
        assert '"hello"' in bak_content
        assert '"goodbye"' not in bak_content

    def test_templates_quarantines_corrupt_file(self, template_dir):
        """PI-8: write corrupt JSON to the templates file, call load,"""
        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        templates_file = template_dir / TEMPLATES_FILENAME
        # Write corrupt JSON to the templates file.
        corrupt_payload = '{"templates": [{"trigger": "hello", broken'
        templates_file.write_text(corrupt_payload, encoding="utf-8")
        assert templates_file.exists()

        # Construct a TemplateManager, this calls _load which must
        tm = TemplateManager(config_dir=template_dir)

        # The corrupt file must have been renamed to .corrupt-<ts>.
        assert not templates_file.exists(), (
            "PI-8 regression: corrupt templates file should have been renamed (quarantined), not left in place"
        )
        corrupt_files = list(template_dir.glob(f"{TEMPLATES_FILENAME}.corrupt-*"))
        assert len(corrupt_files) == 1, f"PI-8 regression: expected exactly one .corrupt-<ts> file, got {corrupt_files}"
        # The quarantined file must contain the original corrupt payload
        assert corrupt_files[0].read_text(encoding="utf-8") == corrupt_payload

        # Load must have returned the default (empty templates list).
        assert len(tm.templates) == 0


# TemplateManager concurrency ─────────────────────────


class TestTemplateManagerLock:
    """XZ-R11-06: TemplateManager must guard ``_templates`` /"""

    def test_manager_has_lock_attribute(self, tm):
        """The manager must expose a ``_lock`` attribute (RLock or Lock)."""
        import threading

        assert hasattr(tm, "_lock"), "TemplateManager must define _lock (XZ-R11-06)."
        # RLock because _save is called from inside already-locked CRUD
        assert isinstance(tm._lock, type(threading.RLock())), (
            "_lock must be an RLock so nested locking (e.g. _save from add) doesn't deadlock."
        )

    def test_match_concurrent_with_mutations(self, tm):
        """Run ``match`` in a worker thread while the main thread"""
        import threading
        import time

        # Seed with a baseline template set.
        for i in range(20):
            tm.add(f"trigger-{i}", f"output-{i}")

        errors: list[Exception] = []
        stop = threading.Event()

        def matcher():
            while not stop.is_set():
                try:
                    tm.match(f"trigger-{5}")
                except Exception as exc:
                    errors.append(exc)
                    return
                time.sleep(0.001)

        t = threading.Thread(target=matcher, daemon=True)
        t.start()
        try:
            # Aggressively mutate while the matcher runs.
            for i in range(50):
                added = tm.add(f"concurrent-trigger-{i}", f"concurrent-output-{i}")
                if added is not None:
                    tm.delete(0)
                tm.update(0, f"updated-trigger-{i}", f"updated-output-{i}")
        finally:
            stop.set()
            t.join(timeout=2.0)

        assert errors == [], f"match raised during concurrent CRUD mutations (XZ-R11-06 regression): {errors}"

    def test_export_json_concurrent_with_delete(self, tm):
        """``export_json`` snapshot under the lock so a concurrent"""
        import json
        import threading
        import time

        for i in range(50):
            tm.add(f"trigger-{i}", f"output-{i}")

        errors: list[Exception] = []
        stop = threading.Event()

        def exporter():
            while not stop.is_set():
                try:
                    payload = tm.export_json()
                    # Must always be valid JSON.
                    json.loads(payload)
                except Exception as exc:
                    errors.append(exc)
                    return
                time.sleep(0.001)

        t = threading.Thread(target=exporter, daemon=True)
        t.start()
        try:
            for i in range(40):
                if len(tm.templates) > 0:
                    tm.delete(0)
                tm.add(f"new-trigger-{i}", f"new-output-{i}")
        finally:
            stop.set()
            t.join(timeout=2.0)

        assert errors == [], (
            f"export_json raised or produced invalid JSON during concurrent delete (XZ-R11-06): {errors}"
        )

    def test_templates_property_snapshot_is_consistent(self, tm):
        """``templates`` property returns a copy under the lock —"""
        tm.add("hello", "Hello!")
        snapshot = tm.templates
        snapshot.clear()
        # Manager's view must be unchanged.
        assert len(tm.templates) == 1, "templates property returned a non-snapshot list (XZ-R11-06 regression)."


class TestTemplatesLoadValidatesStructure:
    """FR-36: ``TemplateManager._load`` must validate each item's"""

    def test_load_drops_non_dict_items(self, template_dir, caplog):
        """Items that aren't dicts (ints, strings, null) must be"""
        import logging

        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        # Valid JSON, wrong structure: mixed-type list.
        (template_dir / TEMPLATES_FILENAME).write_text(
            '{"templates": [42, "foo", null, {"trigger": "ok", "output": "OK"}]}',
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.templates"):
            tm = TemplateManager(config_dir=template_dir)
        # The one valid template survived.
        assert len(tm.templates) == 1
        assert tm.templates[0]["trigger"] == "ok"
        # A warning was logged about the dropped items.
        assert any("Dropped" in r.getMessage() for r in caplog.records)

    def test_load_drops_dict_missing_output(self, template_dir, caplog):
        """Dict items missing ``output`` must be dropped (FR-36 +"""
        import logging

        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        (template_dir / TEMPLATES_FILENAME).write_text(
            '{"templates": ['
            '{"trigger": "no_output"},'  # missing output
            '{"output": "no_trigger"},'  # missing trigger
            '{"trigger": "ok", "output": "OK"}'  # valid
            "]}",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.templates"):
            tm = TemplateManager(config_dir=template_dir)
        # Only the valid template survived.
        assert len(tm.templates) == 1
        assert tm.templates[0]["trigger"] == "ok"
        # A warning was logged about the 2 dropped items.
        dropped_msgs = [r.getMessage() for r in caplog.records if "Dropped" in r.getMessage()]
        assert dropped_msgs, "Expected a warning about dropped malformed templates"
        assert "2" in dropped_msgs[0], f"Expected 2 dropped items, got: {dropped_msgs[0]}"

    def test_load_does_not_crash_on_bare_list_payload(self, template_dir):
        """A bare list (not wrapped in ``{\"templates\": [...]}``) of"""
        from voice_typer.server.templates import TEMPLATES_FILENAME, TemplateManager

        (template_dir / TEMPLATES_FILENAME).write_text(
            '[42, "foo", null, {"trigger": "ok", "output": "OK"}]',
            encoding="utf-8",
        )
        tm = TemplateManager(config_dir=template_dir)
        assert len(tm.templates) == 1
        assert tm.templates[0]["trigger"] == "ok"


class TestTemplatesMatchHandlesMissingOutput:
    """FR-37: ``TemplateManager.match`` must not raise ``KeyError``"""

    def test_match_does_not_keyerror_on_missing_output(self, tm):
        """Directly mutate the index to inject a template without"""
        # Seed with a valid template so ``match`` doesn't early-exit
        tm.add("seed-trigger", "seed-output")
        # Inject a malformed template directly into the live index
        with tm._lock:
            tm._exact_index["trigger-no-output"] = {"trigger": "trigger-no-output"}
        # match must NOT raise KeyError, it must return "" (the
        result = tm.match("trigger-no-output")
        assert result == "", f"FR-37 regression: match should return '' for a template without 'output', got {result!r}"

    def test_rebuild_indexes_skips_templates_without_output(self, tm):
        """``_rebuild_indexes`` must NOT index templates that lack"""
        # Add a malformed template directly to the internal list
        with tm._lock:
            tm._templates.append({"trigger": "no-output-trigger"})
            tm._rebuild_indexes()
            # The malformed template must NOT be in the exact index.
            assert "no-output-trigger" not in tm._exact_index
            # And must NOT be in the contains list.
            assert all(trigger != "no-output-trigger" for trigger, _ in tm._contains_list), (
                "FR-37 regression: _rebuild_indexes indexed a template without 'output'"
            )


class TestMatchIteratesContainsListDirectly:
    """``match`` must iterate ``_contains_list`` WITHOUT a per-call copy."""

    def test_no_snapshot_copy_in_source(self):
        import inspect

        from voice_typer.server.templates import TemplateManager

        src = inspect.getsource(TemplateManager.match)
        assert "list(self._contains_list)" not in src, (
            "match must not copy the contains-list per dictation, index rebuilds "
            "reassign the attribute under the same lock, so direct iteration is safe"
        )
        assert "for trigger_norm, t in self._contains_list:" in src, "match must iterate the contains-list directly"

    def test_match_survives_concurrent_rebuilds(self, tm):
        """Behavioral guard: concurrent CRUD mutations (each rebuilding"""
        import threading

        tm.add("code review", "did a code review", match_mode="contains")
        mutator_errors: list[Exception] = []

        def mutator() -> None:
            try:
                for i in range(30):
                    tm.add(f"filler trigger {i}", "ok", match_mode="contains")
            except Exception as exc:  # pragma: no cover, surfaced via the assert below
                mutator_errors.append(exc)

        thread = threading.Thread(target=mutator)
        thread.start()
        try:
            for _ in range(100):
                assert tm.match("let's do a code review now") == "did a code review"
        finally:
            thread.join()

        assert not mutator_errors, f"concurrent rebuild broke match: {mutator_errors}"
