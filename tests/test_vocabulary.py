"""Tests for voice_typer.vocabulary — VocabularyManager CRUD, merge, apply."""

import json
import pytest
from pathlib import Path


@pytest.fixture
def vocab_dir(tmp_path, monkeypatch):
    """Point config to a temp directory."""
    monkeypatch.setattr("voice_typer.config._config_dir", lambda: tmp_path)
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
    from voice_typer.vocabulary import VocabularyManager
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
        user_file.write_text(json.dumps({
            "misspellings": {"teh": "TEH (custom)"},
        }), encoding="utf-8")
        from voice_typer.vocabulary import VocabularyManager
        vm = VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)
        miss = vm.get_category("misspellings")
        assert miss["teh"] == "TEH (custom)"
        # Bundled entry still present
        assert "recieve" in miss

    def test_user_extends_list_category(self, vocab_dir, bundled):
        user_file = vocab_dir / "voice-typer-vocabulary.json"
        user_file.write_text(json.dumps({
            "phrase_corrections": [["custom phrase", "custom fix"]],
        }), encoding="utf-8")
        from voice_typer.vocabulary import VocabularyManager
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
        json_str = json.dumps({
            "technical_terms": {"dockr": "docker"},
        })
        count = vm.import_json(json_str, merge=True)
        assert count >= 1

    def test_import_json_replace(self, vm):
        json_str = json.dumps({
            "technical_terms": {"dockr": "docker"},
        })
        count = vm.import_json(json_str, merge=False)
        assert count >= 1
