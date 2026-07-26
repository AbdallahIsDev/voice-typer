"""Tests for voice_typer.vocabulary — VocabularyManager CRUD, merge, apply."""

import json

import pytest


@pytest.fixture
def vocab_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.server.config._config_dir", lambda: tmp_path)
    return tmp_path


@pytest.fixture
def bundled(tmp_path):
    """Create a minimal bundled corrections.json."""
    data = {
        "misspellings": {"teh": "the", "recieve": "receive"},
        "phrase_corrections": [["voice to 2 text", "voice to text"]],
        "extra_word_patterns": [["without whether", "whether"]],
        "technical_terms": {},
        "names": {},
        "products": {},
    }
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def vm(vocab_dir, bundled):
    """Create a VocabularyManager with bundled data."""
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


class TestVocabularyLoad:
    def test_loads_bundled_misspellings(self, vm):
        miss = vm.get_category("misspellings")
        assert isinstance(miss, dict)
        assert "teh" in miss
        assert miss["teh"] == "the"

    def test_loads_bundled_phrase_corrections(self, vm):
        phrases = vm.get_category("phrase_corrections")
        assert isinstance(phrases, list)
        assert len(phrases) > 0

    def test_empty_categories_present(self, vm):
        for cat in ("technical_terms", "names", "products"):
            data = vm.get_category(cat)
            assert isinstance(data, dict)


class TestVocabularyMerge:
    def test_user_overrides_bundled(self, vocab_dir, bundled):
        """User vocabulary should override bundled entries."""
        user_file = vocab_dir / "voice-typer-vocabulary.json"
        user_file.write_text(
            json.dumps(
                {
                    "misspellings": {"teh": "TEH (custom)"},
                }
            ),
            encoding="utf-8",
        )
        from voice_typer.server.vocabulary import VocabularyManager

        vm = VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)
        miss = vm.get_category("misspellings")
        assert miss["teh"] == "TEH (custom)"
        # Bundled entry still present
        assert "recieve" in miss

    def test_user_extends_list_category(self, vocab_dir, bundled):
        user_file = vocab_dir / "voice-typer-vocabulary.json"
        user_file.write_text(
            json.dumps(
                {
                    "phrase_corrections": [["custom phrase", "custom fix"]],
                }
            ),
            encoding="utf-8",
        )
        from voice_typer.server.vocabulary import VocabularyManager

        vm = VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)
        phrases = vm.get_category("phrase_corrections")
        assert len(phrases) >= 2  # bundled + user


class TestVocabularyCRUD:
    def test_add_entry_dict_category(self, vm):
        result = vm.add_entry("technical_terms", "pyathon", "python")
        assert result is True
        tech = vm.get_category("technical_terms")
        assert tech["pyathon"] == "python"

    def test_add_entry_wrong_category(self, vm):
        result = vm.add_entry("phrase_corrections", "key", "val")
        assert result is False

    def test_remove_entry(self, vm):
        vm.add_entry("technical_terms", "pyathon", "python")
        result = vm.remove_entry("technical_terms", "pyathon")
        assert result is True

    def test_remove_entry_not_found(self, vm):
        result = vm.remove_entry("technical_terms", "nonexistent")
        assert result is False

    def test_add_phrase(self, vm):
        result = vm.add_phrase("phrase_corrections", "bad phrase", "good phrase")
        assert result is True

    def test_add_phrase_wrong_category(self, vm):
        result = vm.add_phrase("misspellings", "bad", "good")
        assert result is False

    def test_remove_phrase(self, vm):
        vm.add_phrase("phrase_corrections", "bad phrase", "good phrase")
        phrases = vm.get_category("phrase_corrections")
        initial_len = len(phrases)
        result = vm.remove_phrase("phrase_corrections", initial_len - 1)
        assert result is True


class TestVocabularyApplyToText:
    def test_applies_misspellings(self, vm):
        result = vm.apply_to_text("I went teh wrong way")
        assert "the" in result
        assert "teh" not in result

    def test_applies_phrase_corrections(self, vm):
        result = vm.apply_to_text("voice to 2 text is great")
        assert "voice to text" in result

    def test_applies_technical_terms(self, vm):
        vm.add_entry("technical_terms", "pyathon", "python")
        result = vm.apply_to_text("I write pyathon code")
        assert "python" in result

    def test_applies_names(self, vm):
        vm.add_entry("names", "jonh", "john")
        result = vm.apply_to_text("jonh said hello")
        assert "john" in result

    def test_applies_products(self, vm):
        vm.add_entry("products", "vscode", "Visual Studio Code")
        result = vm.apply_to_text("open vscode now")
        assert "Visual Studio Code" in result


class TestVocabularyImportExport:
    def test_export_json(self, vm):
        exported = vm.export_json()
        data = json.loads(exported)
        assert "misspellings" in data

    def test_import_json_merge(self, vm):
        json_str = json.dumps(
            {
                "technical_terms": {"dockr": "docker"},
            }
        )
        # G4-M-37: import_json now returns a tuple of
        # (categories_imported, dropped_entries).
        count, dropped = vm.import_json(json_str, merge=True)
        assert count >= 1
        assert dropped == 0

    def test_import_json_replace(self, vm):
        json_str = json.dumps(
            {
                "technical_terms": {"dockr": "docker"},
            }
        )
        count, dropped = vm.import_json(json_str, merge=False)
        assert count >= 1
        assert dropped == 0


# ─── G4-M-37 / G4-M-38 regression tests ──────────────────────────────────────


class TestVocabularyImportValidation:
    """G4-M-37: import_json must validate entries before mutating state."""

    def test_import_json_rejects_oversized_entries(self, vm, caplog):
        """Entries whose key/pattern or value/replacement exceed the
        SEC-011 length caps must be dropped with a logged warning.
        """
        import logging

        from voice_typer.server.vocabulary import (
            MAX_PATTERN_LENGTH,
            MAX_REPLACEMENT_LENGTH,
        )

        long_key = "x" * (MAX_PATTERN_LENGTH + 1)
        long_val = "y" * (MAX_REPLACEMENT_LENGTH + 1)
        json_str = json.dumps(
            {
                "misspellings": {
                    long_key: "valid",
                    "valid_key": long_val,
                    "good_key": "good_val",
                }
            }
        )
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.vocabulary"):
            count, dropped = vm.import_json(json_str, merge=True)
        # 1 category was imported (misspellings had at least 1 valid entry).
        assert count == 1
        # 2 oversized entries were dropped (long_key + valid_key).
        assert dropped == 2
        miss = vm.get_category("misspellings")
        assert isinstance(miss, dict)
        assert "good_key" in miss
        assert miss["good_key"] == "good_val"
        assert long_key not in miss
        assert "valid_key" not in miss
        # A warning must have been logged with the dropped count.
        assert any("Dropped" in r.getMessage() and "2" in r.getMessage() for r in caplog.records)

    def test_import_json_rejects_oversized_phrase_entries(self, vm):
        """List-based categories (phrase_corrections) must also drop
        oversized entries (mirrors text_cleanup._load_external_corrections)."""
        from voice_typer.server.vocabulary import MAX_PATTERN_LENGTH

        long_phrase = "x" * (MAX_PATTERN_LENGTH + 1)
        json_str = json.dumps(
            {
                "phrase_corrections": [
                    [long_phrase, "ok"],
                    ["good phrase", "good fix"],
                ]
            }
        )
        count, dropped = vm.import_json(json_str, merge=True)
        assert count == 1
        assert dropped == 1
        phrases = vm.get_category("phrase_corrections")
        assert isinstance(phrases, list)
        # Only the valid entry is added (plus the bundled entry).
        assert any(p == ["good phrase", "good fix"] for p in phrases)

    def test_import_json_rejects_oversized_replacement(self, vm):
        """Oversized replacements (good side) must be dropped too."""
        from voice_typer.server.vocabulary import MAX_REPLACEMENT_LENGTH

        long_val = "z" * (MAX_REPLACEMENT_LENGTH + 1)
        json_str = json.dumps(
            {
                "phrase_corrections": [
                    ["bad", long_val],
                    ["good", "good fix"],
                ]
            }
        )
        count, dropped = vm.import_json(json_str, merge=True)
        assert count == 1
        assert dropped == 1

    def test_import_json_rejects_category_over_cap(self, vm, monkeypatch):
        """If ``len(existing) + len(new)`` would exceed
        MAX_CORRECTIONS_ENTRIES, the entire category import must be
        dropped (no partial mutation of ``self._data``)."""
        import voice_typer.server.vocabulary as vocab_mod

        # Lower the cap so the test doesn't have to build 5000 entries.
        monkeypatch.setattr(vocab_mod, "MAX_CORRECTIONS_ENTRIES", 3)
        # The bundled misspellings already has 2 entries (teh, recieve).
        # Importing 2 more would put us at 4 > 3 -> entire category rejected.
        json_str = json.dumps(
            {
                "misspellings": {
                    "k1": "v1",
                    "k2": "v2",
                }
            }
        )
        count, dropped = vm.import_json(json_str, merge=True)
        # Category was rejected because it would exceed the cap.
        assert count == 0
        # 2 new entries were dropped (rejected as a batch).
        assert dropped == 2
        # Bundled entries are still present (no partial mutation).
        miss = vm.get_category("misspellings")
        assert "teh" in miss
        assert "recieve" in miss

    def test_import_json_malformed_entry_in_list_dropped(self, vm):
        """Malformed entries in list categories (wrong shape, missing
        fields) must be counted as dropped, not silently swallowed."""
        json_str = json.dumps(
            {
                "phrase_corrections": [
                    ["good phrase", "good fix"],
                    ["only_one_field"],
                    "not_a_list",
                    {"not": "a list either"},
                    ["another_good", "another_fix"],
                ]
            }
        )
        count, dropped = vm.import_json(json_str, merge=True)
        assert count == 1
        # 3 malformed entries dropped (only_one_field, not_a_list, dict).
        assert dropped == 3


# ─── PI-8 regression tests ────────────────────────────────────────────────


class TestVocabularyBackupAndQuarantine:
    """PI-8: vocabulary.py now routes persistence through PersistedJSON,
    which provides single-slot .bak before overwrite + corrupt-file
    quarantine on load failure. These tests pin the new behavior so a
    future refactor that drops the helper (or replaces it with a
    bare _secure_atomic_write) doesn't silently regress PI-8."""

    def test_vocabulary_creates_bak_on_overwrite(self, vocab_dir, bundled):
        """PI-8: save vocab A, save vocab B, assert .bak file contains A.

        The .bak is single-slot: each save overwrites the previous .bak
        (so re-saves don't accumulate backup files). The .bak holds the
        PREVIOUS content, byte-for-byte, so the user can recover their
        last good state if a save turns out to be wrong.
        """
        from voice_typer.server.vocabulary import VOCAB_FILENAME, VocabularyManager

        user_file = vocab_dir / VOCAB_FILENAME
        bak_file = vocab_dir / f"{VOCAB_FILENAME}.bak"

        # Save vocab A: an entry "teh" -> "the".
        vm1 = VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)
        vm1.add_entry("misspellings", "teh", "the")
        vm1.add_entry("misspellings", "recieve", "receive")
        del vm1

        content_a = user_file.read_text(encoding="utf-8")
        assert '"teh"' in content_a
        # No .bak yet — first save has nothing to back up.
        assert not bak_file.exists()

        # Save vocab B: a different entry "whitespace" -> "white space".
        vm2 = VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)
        vm2.add_entry("misspellings", "whitespace", "white space")
        del vm2

        content_b = user_file.read_text(encoding="utf-8")
        assert '"whitespace"' in content_b
        # The .bak must now exist and contain vocab A's content
        # (byte-for-byte), so the user can recover their previous
        # state if vocab B turns out to be wrong.
        assert bak_file.exists(), "PI-8 regression: .bak file should exist after the second save overwrites the first"
        bak_content = bak_file.read_text(encoding="utf-8")
        assert bak_content == content_a, (
            "PI-8 regression: .bak file should contain the PREVIOUS vocab content (byte-for-byte), not the new content"
        )
        assert '"teh"' in bak_content
        assert '"whitespace"' not in bak_content

    def test_vocabulary_quarantines_corrupt_file(self, vocab_dir, bundled):
        """PI-8: write corrupt JSON to the vocab file, call load, assert
        the file is moved to .corrupt-<ts> and load returns the default
        (empty user vocab — bundled still loads).

        Without quarantine, the next save would atomically overwrite the
        corrupt file with the in-memory defaults, destroying any chance
        of forensic recovery. Quarantine preserves the corrupt file at
        ``<path>.corrupt-<timestamp>`` so the user can inspect what
        truncation pattern led to the parse failure.
        """
        from voice_typer.server.vocabulary import VOCAB_FILENAME, VocabularyManager

        user_file = vocab_dir / VOCAB_FILENAME
        # Write corrupt JSON to the user vocab file.
        corrupt_payload = '{"misspellings": {"teh": "the", broken'
        user_file.write_text(corrupt_payload, encoding="utf-8")
        assert user_file.exists()

        # Construct a VocabularyManager — this calls _load_user which
        # must detect the corrupt JSON, quarantine it, and fall back
        # to the default (empty user vocab). Bundled corrections still
        # load normally (they're a separate file).
        vm = VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)

        # The corrupt file must have been renamed to .corrupt-<ts>.
        assert not user_file.exists(), (
            "PI-8 regression: corrupt vocab file should have been renamed (quarantined), not left in place"
        )
        corrupt_files = list(vocab_dir.glob(f"{VOCAB_FILENAME}.corrupt-*"))
        assert len(corrupt_files) == 1, f"PI-8 regression: expected exactly one .corrupt-<ts> file, got {corrupt_files}"
        # The quarantined file must contain the original corrupt payload
        # (byte-for-byte) so the user can inspect what went wrong.
        assert corrupt_files[0].read_text(encoding="utf-8") == corrupt_payload

        # Load must have returned the default (empty user vocab). The
        # bundled misspellings ("teh" -> "the") still load.
        miss = vm.get_category("misspellings")
        assert isinstance(miss, dict)
        assert "teh" in miss  # bundled
        # No user "broken" key leaked through.
        assert "broken" not in miss


class TestTemplatesEnforcesCaps:
    """G4-M-38: templates.add() and templates.import_json() must enforce
    MAX_TEMPLATES, MAX_TRIGGER_LENGTH, MAX_OUTPUT_LENGTH caps."""

    def test_templates_add_enforces_max_count(self, tmp_path, monkeypatch):
        """add() must reject new templates once MAX_TEMPLATES is reached."""
        import voice_typer.server.templates as templates_mod

        # Lower the cap so the test is fast.
        monkeypatch.setattr(templates_mod, "MAX_TEMPLATES", 3)
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=tmp_path)
        # Fill up to the cap.
        for i in range(3):
            result = tm.add(f"trigger{i}", f"output{i}")
            assert result is not None, f"add #{i} should succeed"
        # Next add must be rejected (returns None).
        result = tm.add("overflow", "overflow output")
        assert result is None
        # Internal list stays at the cap.
        assert len(tm._templates) == 3
        # The overflow entry was NOT appended.
        assert all(t["trigger"] != "overflow" for t in tm._templates)

    def test_templates_add_rejects_oversized_trigger(self, tmp_path, monkeypatch):
        """add() must reject triggers exceeding MAX_TRIGGER_LENGTH."""
        import voice_typer.server.templates as templates_mod

        monkeypatch.setattr(templates_mod, "MAX_TRIGGER_LENGTH", 5)
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=tmp_path)
        result = tm.add("way too long trigger", "ok")
        assert result is None
        assert len(tm._templates) == 0

    def test_templates_add_rejects_oversized_output(self, tmp_path, monkeypatch):
        """add() must reject outputs exceeding MAX_OUTPUT_LENGTH."""
        import voice_typer.server.templates as templates_mod

        monkeypatch.setattr(templates_mod, "MAX_OUTPUT_LENGTH", 5)
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=tmp_path)
        result = tm.add("ok", "way too long output text")
        assert result is None
        assert len(tm._templates) == 0

    def test_templates_import_json_drops_oversized(self, tmp_path, monkeypatch, caplog):
        """import_json() must drop oversized entries with a warning
        (mirrors text_cleanup._load_external_corrections)."""
        import logging

        import voice_typer.server.templates as templates_mod

        monkeypatch.setattr(templates_mod, "MAX_TRIGGER_LENGTH", 5)
        monkeypatch.setattr(templates_mod, "MAX_OUTPUT_LENGTH", 10)
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=tmp_path)
        json_str = json.dumps(
            {
                "templates": [
                    {"trigger": "ok", "output": "ok"},  # valid
                    {"trigger": "way too long trigger", "output": "ok"},  # oversized trigger
                    {"trigger": "ok2", "output": "way too long output text"},  # oversized output
                    {"trigger": "ok3", "output": "ok3"},  # valid
                ]
            }
        )
        with caplog.at_level(logging.WARNING, logger="voice_typer.server.templates"):
            count = tm.import_json(json_str)
        assert count == 2  # 2 valid templates imported
        assert len(tm._templates) == 2
        # A warning must have been logged about the dropped entries.
        assert any("Dropped" in r.getMessage() for r in caplog.records)

    def test_templates_import_json_truncates_at_cap(self, tmp_path, monkeypatch):
        """import_json() must truncate the import if it would exceed
        MAX_TEMPLATES."""
        import voice_typer.server.templates as templates_mod

        monkeypatch.setattr(templates_mod, "MAX_TEMPLATES", 3)
        from voice_typer.server.templates import TemplateManager

        tm = TemplateManager(config_dir=tmp_path)
        json_str = json.dumps({"templates": [{"trigger": f"t{i}", "output": f"o{i}"} for i in range(5)]})
        count = tm.import_json(json_str)
        # Only the first 3 fit within the cap.
        assert count == 3
        assert len(tm._templates) == 3
