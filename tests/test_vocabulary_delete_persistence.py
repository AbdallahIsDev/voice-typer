"""Delete / Clear-All persistence for vocabulary saves."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from voice_typer.server.service.vocabulary import VocabularyMixin
from voice_typer.server.vocabulary import VocabularyManager

CATEGORIES = [
    "misspellings",
    "phrase_corrections",
    "extra_word_patterns",
    "technical_terms",
    "names",
    "products",
]


@pytest.fixture
def vocab_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path (canonical fixture)."""
    return tmp_config_dir


@pytest.fixture
def bundled(tmp_path):
    """Minimal bundled corrections.json used by VocabularyManager."""
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
def live_vm(vocab_dir, bundled):
    """A real VocabularyManager with populated ``_data`` / ``_lock``."""
    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


@pytest.fixture
def vocab_mixin(live_vm):
    """Bare ``VocabularyMixin`` whose ``self._app`` exposes the live vm"""
    instance = VocabularyMixin.__new__(VocabularyMixin)
    app = MagicMock()
    app._vocabulary_manager = live_vm
    instance._app = app
    return instance


def _payload_for(vm):
    """The exact category-bucketed payload the renderer sends on save:"""
    return {
        cat: vm.get_category(cat)
        for cat in (
            "misspellings",
            "phrase_corrections",
            "extra_word_patterns",
            "technical_terms",
            "names",
            "products",
        )
    }


def _empty_payload():
    """The payload the renderer sends for Clear All: every category"""
    return {
        "misspellings": {},
        "phrase_corrections": [],
        "extra_word_patterns": [],
        "technical_terms": {},
        "names": {},
        "products": {},
    }


def _fresh_reload(vocab_dir, bundled):
    """A brand-new VocabularyManager, reads the ACTUAL on-disk user"""
    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


class TestDeletePersists:
    def test_single_delete_of_user_entry_persists(self, vocab_mixin, live_vm, vocab_dir, bundled):
        """Quick-add an entry via the same diff path the renderer uses,"""
        payload = _payload_for(live_vm)
        payload["misspellings"]["zzz"] = "zz"
        vocab_mixin.save_vocabulary_with_diff(payload)
        assert "zzz" in _fresh_reload(vocab_dir, bundled).get_all()["misspellings"]

        delete_payload = _payload_for(live_vm)
        del delete_payload["misspellings"]["zzz"]
        vocab_mixin.save_vocabulary_with_diff(delete_payload)

        fresh = _fresh_reload(vocab_dir, bundled)
        miss = fresh.get_all()["misspellings"]
        assert "zzz" not in miss, (
            "deleted user entry 'zzz' resurrected after a fresh disk reload, "
            "the delete did not persist to the user vocabulary file"
        )
        assert miss.get("teh") == "the"
        assert miss.get("recieve") == "receive"

    def test_single_delete_of_bundled_entry_persists(self, vocab_mixin, live_vm, vocab_dir, bundled):
        """diff-style user file alone can't express \"remove a bundled"""
        payload = _payload_for(live_vm)
        del payload["misspellings"]["teh"]
        vocab_mixin.save_vocabulary_with_diff(payload)

        fresh = _fresh_reload(vocab_dir, bundled)
        miss = fresh.get_all()["misspellings"]
        assert "teh" not in miss, (
            "deleted BUNDLED entry 'teh' resurrected after a fresh disk reload, "
            "the deletion tombstone was not written/applied"
        )
        assert "recieve" in miss

    def test_delete_reflected_in_live_manager(self, vocab_mixin, live_vm, vocab_dir, bundled):
        """user navigates away and back, must reflect the deletion too,"""
        payload = _payload_for(live_vm)
        payload["misspellings"]["zzz"] = "zz"
        vocab_mixin.save_vocabulary_with_diff(payload)

        delete_payload = _payload_for(live_vm)
        del delete_payload["misspellings"]["zzz"]
        vocab_mixin.save_vocabulary_with_diff(delete_payload)

        assert "zzz" not in live_vm.get_all()["misspellings"], (
            "live VocabularyManager still serves the deleted entry after the save, "
            "get_vocabulary would show a stale list on navigation back"
        )


class TestClearAllPersists:
    def test_clear_all_empties_every_category_on_fresh_reload(self, vocab_mixin, live_vm, vocab_dir, bundled):
        """Clear All (renderer sends all-empty categories) must wipe the"""
        payload = _payload_for(live_vm)
        payload["misspellings"]["zzz"] = "zz"
        vocab_mixin.save_vocabulary_with_diff(payload)

        vocab_mixin.save_vocabulary_with_diff(_empty_payload())

        fresh = _fresh_reload(vocab_dir, bundled)
        data = fresh.get_all()
        for cat in CATEGORIES:
            if cat in ("misspellings", "technical_terms", "names", "products"):
                assert data.get(cat) == {}, f"Clear All left dict entries in {cat}: {data.get(cat)}"
            else:
                assert data.get(cat) == [], f"Clear All left list entries in {cat}: {data.get(cat)}"

    def test_clear_all_reflected_in_live_manager(self, vocab_mixin, live_vm):
        """After Clear All, the live manager serves an empty merged"""
        payload = _payload_for(live_vm)
        payload["misspellings"]["zzz"] = "zz"
        vocab_mixin.save_vocabulary_with_diff(payload)

        vocab_mixin.save_vocabulary_with_diff(_empty_payload())

        data = live_vm.get_all()
        for cat in CATEGORIES:
            if cat in ("misspellings", "technical_terms", "names", "products"):
                assert data.get(cat) == {}
            else:
                assert data.get(cat) == []
