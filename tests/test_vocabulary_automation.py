"""Tests for voice_typer.server.vocabulary_automation — P5 confidence-based suggestions.

These tests cover the VocabularyAutomation class and its helpers:
  * CorrectionSuggestion dataclass
  * _levenshtein helper
  * analyze_transcription (low-confidence + vocabulary-match signals)
  * apply_suggestion / dismiss_suggestion / get_pending_suggestions
  * auto_apply_high_confidence_suggestions
  * Respects the ``vocabulary_automation_enabled`` config flag.

The tests use a real VocabularyManager with a temp config dir so the
vocabulary CRUD side-effects (file writes) don't leak to disk.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

# ─── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture
def vocab_dir(tmp_config_dir):
    """Point the config dir at a temp directory and create a small bundled corrections file."""
    return tmp_config_dir


@pytest.fixture
def bundled(tmp_path):
    """Create a minimal bundled corrections.json."""
    data = {
        "misspellings": {"teh": "the", "recieve": "receive"},
        "phrase_corrections": [["voice to 2 text", "voice to text"]],
        "extra_word_patterns": [["without whether", "whether"]],
        "technical_terms": {"pyathon": "python"},
        "names": {"jonh": "john"},
        "products": {"vscode": "Visual Studio Code"},
    }
    path = tmp_path / "corrections.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def vm(vocab_dir, bundled):
    """Create a VocabularyManager with bundled data."""
    from voice_typer.server.vocabulary import VocabularyManager

    return VocabularyManager(config_dir=vocab_dir, bundled_path=bundled)


@pytest.fixture
def config():
    """Create a simple config namespace with the relevant fields."""
    return SimpleNamespace(
        vocabulary_automation_enabled=True,
        vocabulary_auto_confidence_threshold=0.7,
        vocabulary_auto_apply_threshold=0.95,
    )


@pytest.fixture
def automation(vm, config):
    """Create a VocabularyAutomation instance."""
    from voice_typer.server.vocabulary_automation import VocabularyAutomation

    return VocabularyAutomation(vm, config)


# ─── _levenshtein helper ────────────────────────────────────────────────────


class TestLevenshtein:
    def test_identical_strings(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        assert _levenshtein("hello", "hello") == 0

    def test_single_substitution(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        assert _levenshtein("cat", "cut") == 1

    def test_single_insertion(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        assert _levenshtein("cat", "cats") == 1

    def test_single_deletion(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        assert _levenshtein("cats", "cat") == 1

    def test_classic_example(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        # kitten → sitting: 3 edits (k→s, e→i, +g)
        assert _levenshtein("kitten", "sitting") == 3

    def test_bounded_short_circuit(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        # Length difference exceeds the bound — can't match.
        assert _levenshtein("cat", "abcdefg", max_distance=2) == 3

    def test_bounded_within_range(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        # Distance is 1, within bound of 2.
        assert _levenshtein("cat", "cut", max_distance=2) == 1

    def test_empty_string(self):
        from voice_typer.server.vocabulary_automation import _levenshtein

        assert _levenshtein("", "abc") == 3
        assert _levenshtein("abc", "") == 3
        assert _levenshtein("", "") == 0


# ─── CorrectionSuggestion dataclass ─────────────────────────────────────────


class TestCorrectionSuggestion:
    def test_to_dict_excludes_internal_flags(self):
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        s = CorrectionSuggestion(
            original="teh",
            corrected="the",
            confidence=0.5,
            context="teh quick brown fox",
            timestamp=12345.0,
            applied=False,
            dismissed=False,
        )
        d = s.to_dict()
        assert "applied" not in d
        assert "dismissed" not in d
        assert d["original"] == "teh"
        assert d["corrected"] == "the"
        assert d["confidence"] == 0.5
        assert d["timestamp"] == 12345.0


# ─── analyze_transcription ──────────────────────────────────────────────────


class TestAnalyzeTranscription:
    def test_analyze_transcription_flags_low_confidence_words(self, automation):
        """Words with confidence below the threshold should be flagged."""
        # Single low-confidence word in a longer sentence.
        suggestions = automation.analyze_transcription(
            "the qucik brown fox jumps over the lazy dog",
            segments=[],  # no segment metadata — use overall confidence
            confidence=0.5,  # below the 0.7 threshold
        )
        assert len(suggestions) > 0
        # All suggestions should have confidence < 0.7.
        for s in suggestions:
            assert s.confidence < 0.7

    def test_analyze_transcription_suggests_vocabulary_matches(self, automation, vm):
        """Words close to a vocabulary entry should be suggested as corrections.

        We add a vocabulary entry "definitely" and feed in "definately"
        (a common misspelling).  Even with high confidence (so the
        low-confidence branch doesn't fire), the Levenshtein-match
        branch should suggest the correction.
        """
        vm.add_entry("misspellings", "definately", "definitely")
        # Re-create automation so it sees the updated vocabulary.
        from voice_typer.server.vocabulary_automation import VocabularyAutomation

        config = SimpleNamespace(
            vocabulary_automation_enabled=True,
            vocabulary_auto_confidence_threshold=0.7,
            vocabulary_auto_apply_threshold=0.95,
        )
        automation = VocabularyAutomation(vm, config)

        suggestions = automation.analyze_transcription(
            "this is definately the right answer",
            segments=[],
            confidence=0.95,  # high confidence — low-confidence branch won't fire
        )
        # Should have flagged "definately" via the Levenshtein-match
        # branch (since the user has explicitly added this correction
        # to the vocabulary).
        matches = [s for s in suggestions if s.original == "definately"]
        assert len(matches) >= 1
        assert matches[0].corrected == "definitely"

    def test_analyze_transcription_respects_disabled_flag(self, vm):
        """When vocabulary_automation_enabled is False, no suggestions."""
        from voice_typer.server.vocabulary_automation import VocabularyAutomation

        config = SimpleNamespace(
            vocabulary_automation_enabled=False,  # disabled
            vocabulary_auto_confidence_threshold=0.7,
            vocabulary_auto_apply_threshold=0.95,
        )
        automation = VocabularyAutomation(vm, config)
        suggestions = automation.analyze_transcription(
            "this is a suspicious word with low confidence",
            segments=[],
            confidence=0.1,  # very low, but feature is off
        )
        assert suggestions == []

    def test_analyze_transcription_empty_text(self, automation):
        """Empty / whitespace-only text should produce no suggestions."""
        assert automation.analyze_transcription("", [], 0.1) == []
        assert automation.analyze_transcription("   ", [], 0.1) == []

    def test_analyze_transcription_skips_short_words(self, automation):
        """Words shorter than 3 chars should not be flagged (too noisy)."""
        suggestions = automation.analyze_transcription(
            "a b c ab",
            segments=[],
            confidence=0.1,  # very low confidence
        )
        # All words are too short — no suggestions.
        assert suggestions == []

    def test_analyze_transcription_uses_segment_confidence(self, automation):
        """Per-segment confidence should override the global confidence."""
        # Build segments where the first segment has high confidence
        # and the second has low confidence.  Only words from the
        # second segment should be flagged.
        segments = [
            {"text": "hello world", "avg_logprob": -0.05},  # exp(-0.05) ≈ 0.95
            {"text": "suspicious word", "avg_logprob": -1.5},  # exp(-1.5) ≈ 0.22
        ]
        suggestions = automation.analyze_transcription(
            "hello world suspicious word",
            segments=segments,
            confidence=0.99,
        )
        # Only "suspicious" and "word" should be flagged.
        flagged = {s.original for s in suggestions}
        assert "suspicious" in flagged
        assert "word" in flagged
        assert "hello" not in flagged
        assert "world" not in flagged


# ─── apply_suggestion ───────────────────────────────────────────────────────


class TestApplySuggestion:
    def test_apply_suggestion_adds_to_vocabulary(self, automation, vm):
        """Applying a suggestion should add the correction to the vocabulary."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        suggestion = CorrectionSuggestion(
            original="definately",
            corrected="definitely",
            confidence=0.5,
            context="this is definately wrong",
            timestamp=0.0,
        )
        automation._pending.append(suggestion)
        automation.apply_suggestion(suggestion)

        # Verify the correction was added to the misspellings category.
        miss = vm.get_category("misspellings")
        assert isinstance(miss, dict)
        assert miss.get("definately") == "definitely"

    def test_apply_suggestion_removes_from_pending(self, automation):
        """Applied suggestions should no longer be in the pending list."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        suggestion = CorrectionSuggestion(
            original="teh",
            corrected="the",
            confidence=0.5,
            context="teh",
            timestamp=0.0,
        )
        automation._pending.append(suggestion)
        automation.apply_suggestion(suggestion)

        pending = automation.get_pending_suggestions()
        assert suggestion not in pending

    def test_apply_suggestion_idempotent(self, automation, vm):
        """Applying the same suggestion twice should be a no-op."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        suggestion = CorrectionSuggestion(
            original="teh",
            corrected="the",
            confidence=0.5,
            context="teh",
            timestamp=0.0,
        )
        automation._pending.append(suggestion)
        automation.apply_suggestion(suggestion)
        # Second call should be a no-op (suggestion.applied is True).
        automation.apply_suggestion(suggestion)
        miss = vm.get_category("misspellings")
        # The original bundled "teh" → "the" entry is still there,
        # plus no duplicates.
        assert miss.get("teh") == "the"


# ─── dismiss_suggestion ─────────────────────────────────────────────────────


class TestDismissSuggestion:
    def test_dismiss_suggestion_removes_from_pending(self, automation):
        """Dismissed suggestions should be removed from the pending list."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        suggestion = CorrectionSuggestion(
            original="teh",
            corrected="the",
            confidence=0.5,
            context="teh",
            timestamp=0.0,
        )
        automation._pending.append(suggestion)
        automation.dismiss_suggestion(suggestion)

        pending = automation.get_pending_suggestions()
        assert suggestion not in pending

    def test_dismiss_does_not_add_to_vocabulary(self, automation, vm):
        """Dismissed suggestions should NOT be added to the vocabulary."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        suggestion = CorrectionSuggestion(
            original="definately",
            corrected="definitely",
            confidence=0.5,
            context="definately",
            timestamp=0.0,
        )
        automation._pending.append(suggestion)
        automation.dismiss_suggestion(suggestion)

        miss = vm.get_category("misspellings")
        # No new entry should have been added.
        assert "definately" not in miss or miss.get("definately") != "definitely"


# ─── get_pending_suggestions ────────────────────────────────────────────────


class TestGetPendingSuggestions:
    def test_returns_copy_not_internal_list(self, automation):
        """get_pending_suggestions should return a copy so callers can't mutate internals."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        s = CorrectionSuggestion(
            original="teh",
            corrected="the",
            confidence=0.5,
            context="teh",
            timestamp=0.0,
        )
        automation._pending.append(s)
        pending = automation.get_pending_suggestions()
        pending.clear()  # mutate the returned list
        # Internal list should be unaffected.
        assert len(automation._pending) == 1


# ─── auto_apply_high_confidence_suggestions ────────────────────────────────


class TestAutoApplyHighConfidence:
    def test_auto_apply_high_confidence_suggestions(self, automation, vm):
        """Suggestions above the threshold should be auto-applied."""
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        # Two suggestions: one high-confidence, one low-confidence.
        high = CorrectionSuggestion(
            original="definately",
            corrected="definitely",
            confidence=0.97,  # above the default 0.95 threshold
            context="definately",
            timestamp=0.0,
        )
        low = CorrectionSuggestion(
            original="seperate",
            corrected="separate",
            confidence=0.5,  # below threshold — not auto-applied
            context="seperate",
            timestamp=0.0,
        )
        automation._pending.extend([high, low])

        count = automation.auto_apply_high_confidence_suggestions(threshold=0.95)
        assert count == 1  # only the high-confidence one

        # The high-confidence correction should be in the vocabulary.
        miss = vm.get_category("misspellings")
        assert miss.get("definately") == "definitely"
        # The low-confidence one should still be pending.
        pending = automation.get_pending_suggestions()
        pending_originals = {s.original for s in pending}
        assert "seperate" in pending_originals
        assert "definately" not in pending_originals

    def test_auto_apply_skips_no_match_suggestions(self, automation, vm):
        """Suggestions where corrected == original should NOT be auto-applied.

        These represent "low-confidence word with no vocabulary match"
        — the user needs to supply the correction themselves.
        """
        from voice_typer.server.vocabulary_automation import CorrectionSuggestion

        s = CorrectionSuggestion(
            original="xyzzy",
            corrected="xyzzy",  # no proposed correction
            confidence=0.99,
            context="xyzzy",
            timestamp=0.0,
        )
        automation._pending.append(s)
        count = automation.auto_apply_high_confidence_suggestions(threshold=0.95)
        assert count == 0
        # Should still be pending.
        pending = automation.get_pending_suggestions()
        assert any(p.original == "xyzzy" for p in pending)


# ─── Disabled-flag integration ──────────────────────────────────────────────


class TestRespectsDisabledFlag:
    def test_respects_disabled_flag(self, vm):
        """The full automation pipeline should be a no-op when disabled."""
        from voice_typer.server.vocabulary_automation import VocabularyAutomation

        config = SimpleNamespace(
            vocabulary_automation_enabled=False,
            vocabulary_auto_confidence_threshold=0.7,
            vocabulary_auto_apply_threshold=0.95,
        )
        automation = VocabularyAutomation(vm, config)
        # Analyze should return empty.
        suggestions = automation.analyze_transcription(
            "suspicious words with low confidence",
            [],
            0.1,
        )
        assert suggestions == []
        # Auto-apply should return 0.
        assert automation.auto_apply_high_confidence_suggestions(0.95) == 0
        # Pending should be empty.
        assert automation.get_pending_suggestions() == []
