"""Backend duplicate enforcement for vocabulary saves.

``save_vocabulary_with_diff`` is the SINGLE write path every entry
point goes through (quick-add, edit dialog, import, delete, clear) —
the renderer always sends the full merged list. The authoritative
duplicate check therefore lives here: a save is rejected when it would
CREATE a duplicate wrong phrase (case-insensitive, whitespace-
collapsed — the same key the matcher uses), while a plain echo of
pre-existing duplicates (e.g. the bundled "to 2" pair in legacy data)
is allowed so normal saves keep working.

Also covers ``test_vocabulary_correction`` — the "Test corrections"
panel runs against the LIVE engine (``VocabularyManager.apply_to_text``).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from voice_typer.server.service.vocabulary import (
    VocabularyDuplicateError,
    _find_new_duplicate,
)


@pytest.fixture
def vocab_dir(tmp_config_dir):
    """Point ``_config_dir`` at a tmp_path (canonical fixture)."""
    return tmp_config_dir


@pytest.fixture
def bundled(tmp_path):
    """Bundled corrections with the legacy "to 2" duplicate pair —
    the exact case the user reported: ``"to 2 " → "to "`` and
    ``" to 2" → " to"`` differ only by surrounding whitespace and
    normalize to the same wrong phrase.
    """
    data = {
        "misspellings": {"teh": "the", "recieve": "receive"},
        "phrase_corrections": [["to 2 ", "to "], [" to 2", " to"]],
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
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


@pytest.fixture
def vocab_mixin(live_vm):
    """Bare ``VocabularyMixin`` whose ``self._app`` exposes the live vm
    (same pattern as tests/test_audio_chain_and_vocab_reload.py)."""
    from voice_typer.server.service.vocabulary import VocabularyMixin

    instance = VocabularyMixin.__new__(VocabularyMixin)
    app = MagicMock()
    app._vocabulary_manager = live_vm
    instance._app = app
    return instance


def _full_payload(vm, extra_misspellings=None, extra_phrases=None):
    """Build the category-bucketed payload the renderer sends: the
    current merged state (bundled + user) plus optional extras."""
    data = {cat: vm.get_category(cat) for cat in (
        "misspellings",
        "phrase_corrections",
        "extra_word_patterns",
        "technical_terms",
        "names",
        "products",
    )}
    if extra_misspellings:
        data["misspellings"] = {**data["misspellings"], **extra_misspellings}
    if extra_phrases:
        data["phrase_corrections"] = [
            *data["phrase_corrections"],
            *extra_phrases,
        ]
    return data


class TestSaveRejectsNewDuplicates:
    def test_echo_of_pre_existing_duplicate_pair_is_allowed(self, vocab_mixin, live_vm):
        """A normal save that merely echoes the (legacy) bundled "to 2"
        duplicate pair must NOT be rejected — otherwise every unrelated
        edit on an old install would hard-fail."""
        payload = _full_payload(live_vm)
        result = vocab_mixin.save_vocabulary_with_diff(payload)
        assert "imported_categories" in result

    def test_rejects_adding_third_to2_occurrence(self, vocab_mixin, live_vm):
        """Adding one more "to 2 → to" entry (a new occurrence of an
        already-existing wrong phrase) must be rejected — the flat list
        would show three near-identical rows."""
        payload = _full_payload(live_vm, extra_phrases=[["to 2", "to"]])
        with pytest.raises(VocabularyDuplicateError) as exc_info:
            vocab_mixin.save_vocabulary_with_diff(payload)
        assert exc_info.value.phrase == "to 2"
        assert exc_info.value.count == 3

    def test_rejects_same_phrase_twice_in_incoming(self, vocab_mixin, live_vm):
        """Two NEW entries sharing a wrong phrase (neither in the
        baseline) must be rejected."""
        payload = _full_payload(
            live_vm,
            extra_phrases=[["they working", "they're working"], ["They Working", "they're working"]],
        )
        with pytest.raises(VocabularyDuplicateError) as exc_info:
            vocab_mixin.save_vocabulary_with_diff(payload)
        assert exc_info.value.phrase == "they working"

    def test_rejects_case_insensitive_collision_in_dict_category(self, vocab_mixin, live_vm):
        """The matcher lowercases word tokens, so "Teh" collides with
        the existing "teh" entry — the save must be rejected instead of
        silently overwriting the dict key."""
        payload = _full_payload(live_vm, extra_misspellings={"Teh": "THE"})
        with pytest.raises(VocabularyDuplicateError) as exc_info:
            vocab_mixin.save_vocabulary_with_diff(payload)
        assert exc_info.value.phrase == "teh"

    def test_rejects_new_duplicate_against_bundled_phrase(self, vocab_mixin, live_vm):
        """A user entry duplicating a BUNDLED wrong phrase (different
        correction) is a duplicate from the flat list's perspective."""
        payload = _full_payload(live_vm, extra_phrases=[["to 2 ", "to the 2"]])
        with pytest.raises(VocabularyDuplicateError):
            vocab_mixin.save_vocabulary_with_diff(payload)

    def test_normal_save_with_new_entries_allowed(self, vocab_mixin, live_vm):
        """A save adding genuinely new wrong phrases passes."""
        payload = _full_payload(
            live_vm,
            extra_misspellings={"accomodate": "accommodate"},
            extra_phrases=[["teh team", "the team"]],
        )
        result = vocab_mixin.save_vocabulary_with_diff(payload)
        assert "imported_categories" in result

    def test_delete_of_duplicate_is_allowed(self, vocab_mixin, live_vm):
        """Removing one of the two "to 2" occurrences (via the flat
        list delete) collapses the group to one entry — allowed."""
        phrases = live_vm.get_category("phrase_corrections")
        payload = _full_payload(live_vm)
        payload["phrase_corrections"] = [
            p for p in phrases if not (len(p) >= 2 and p[0] == " to 2")
        ]
        result = vocab_mixin.save_vocabulary_with_diff(payload)
        assert "imported_categories" in result


class TestFindNewDuplicate:
    def test_returns_none_for_disjoint_phrases(self):
        assert (
            _find_new_duplicate(
                {"misspellings": {"teh": "the"}},
                {"misspellings": {"recieve": "receive"}},
            )
            is None
        )

    def test_returns_phrase_for_unmatched_occurrence(self):
        dup = _find_new_duplicate(
            {"phrase_corrections": [["to 2 ", "to "], ["to 2", "to"]]},
            {"phrase_corrections": [["to 2 ", "to "]]},
        )
        assert dup == ("to 2", 2)


class TestTestVocabularyCorrection:
    def test_applies_live_engine_rules(self, vocab_mixin):
        """The panel preview runs the REAL engine — phrase-level and
        word-level corrections both fire."""
        result = vocab_mixin.test_vocabulary_correction("voice of teh to 2 x")
        assert isinstance(result["output"], str)
        assert result["applied"] is True

    def test_no_change_returns_applied_false(self, vocab_mixin):
        result = vocab_mixin.test_vocabulary_correction("nothing to see")
        assert result["output"] == "nothing to see"
        assert result["applied"] is False

    def test_fallback_when_no_live_manager(self):
        """Cold-start path (no live vm) constructs a throwaway manager."""
        from voice_typer.server.service.vocabulary import VocabularyMixin

        instance = VocabularyMixin.__new__(VocabularyMixin)
        app = MagicMock()
        app._vocabulary_manager = None
        app.config.config_dir = None  # fallback path uses default config dir
        instance._app = app
        result = instance.test_vocabulary_correction("plain text")
        assert isinstance(result["output"], str)
