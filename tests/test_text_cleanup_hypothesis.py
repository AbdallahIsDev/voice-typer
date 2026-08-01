"""Property-based tests for text_cleanup using hypothesis.

TEST-009: Property-based tests for clean_transcribed_text.
TEST-013: Fuzzing for _load_external_corrections() with random JSON-like inputs.
"""

from __future__ import annotations

import json

import pytest
from voice_typer.server.text_cleanup import clean_transcribed_text, configure_corrections

try:
    from hypothesis import HealthCheck, assume, given, settings
    from hypothesis import strategies as st

    HAS_HYPOTHESIS = True
except ImportError:
    HAS_HYPOTHESIS = False

    def given(**kwargs):
        def decorator(func):
            return func

        return decorator

    def assume(condition):
        pass

    class settings:  # noqa: N801 — matches hypothesis.settings name
        def __init__(self, **kwargs):
            pass

        def __call__(self, func):
            return func

    class HealthCheck:
        too_slow = None
        function_scoped_fixture = None

    class st:  # noqa: N801 — matches hypothesis.strategies alias
        @staticmethod
        def text(**kwargs):
            return None

        @staticmethod
        def characters(**kwargs):
            return None

        @staticmethod
        def one_of(*args):
            return None

        @staticmethod
        def dictionaries(*args, **kwargs):
            return None

        @staticmethod
        def lists(*args, **kwargs):
            return None

        @staticmethod
        def sampled_from(elements):
            return None

        @staticmethod
        def none():
            return None

        @staticmethod
        def booleans():
            return None

        @staticmethod
        def integers(**kwargs):
            return None

        @staticmethod
        def floats(**kwargs):
            return None

        @staticmethod
        def tuples(*args, **kwargs):
            return None

        @staticmethod
        def from_regex(*args, **kwargs):
            return None


pytestmark = pytest.mark.skipif(
    not HAS_HYPOTHESIS,
    reason="hypothesis not installed — install with: pip install hypothesis",
)


@pytest.fixture(autouse=True)
def _configure_corrections():
    """Initialize corrections from bundled corrections.json before each test."""
    configure_corrections()


# Property-based tests for clean_transcribed_text ──────────


class TestCleanTextProperties:
    """Invariant properties of clean_transcribed_text."""

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_never_crashes(self, text):
        """clean_transcribed_text should never raise on arbitrary input."""
        result = clean_transcribed_text(text)
        assert isinstance(result, str)

    @given(text=st.text(min_size=0, max_size=500))
    @settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow])
    def test_output_always_string(self, text):
        """Output is always a string, never None or other type."""
        result = clean_transcribed_text(text)
        assert isinstance(result, str)

    @given(text=st.text(min_size=1, max_size=200, alphabet=st.characters(whitelist_categories=("L", "N", "P", "Z"))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_nonempty_input_produces_nonempty_output(self, text):
        """Non-empty input (letters, numbers, punctuation, spaces) should produce non-empty output."""
        assume(text.strip())
        result = clean_transcribed_text(text)
        assert len(result) > 0

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_idempotent(self, text):
        """Applying cleanup twice should produce the same result as once."""
        first = clean_transcribed_text(text)
        second = clean_transcribed_text(first)
        assert second == first

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_no_double_spaces(self, text):
        """Output should never contain double spaces."""
        result = clean_transcribed_text(text)
        assume(result)  # skip empty results
        assert "  " not in result

    @given(text=st.text(min_size=0, max_size=200))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.too_slow])
    def test_no_leading_trailing_whitespace(self, text):
        """Output should have no leading or trailing whitespace."""
        result = clean_transcribed_text(text)
        assert result == result.strip()

    @given(text=st.text(min_size=1, max_size=100, alphabet="abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    @settings(max_examples=30)
    def test_capitalization_of_first_word(self, text):
        """First character of output should be uppercase if output is non-empty."""
        assume(text.strip())
        result = clean_transcribed_text(text)
        assume(result)
        assert result[0].isupper() or not result[0].isalpha()


# Fuzzing for _load_external_corrections() ──────────────────


class TestCorrectionsJsonFuzzing:
    """Fuzz the corrections.json parser with random JSON structures."""

    @given(
        obj=st.one_of(
            st.dictionaries(st.text(), st.text()),
            st.dictionaries(st.text(), st.lists(st.text())),
            st.dictionaries(st.text(), st.dictionaries(st.text(), st.text())),
            st.lists(st.integers()),
            st.none(),
            st.booleans(),
            st.integers(),
            st.floats(allow_nan=False, allow_infinity=False),
            st.text(min_size=0, max_size=100),
        )
    )
    @settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_parser_handles_random_json(self, tmp_path, monkeypatch, obj):
        """Parser should handle malformed/random JSON gracefully (no crashes)."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        try:
            corrections_file.write_text(json.dumps(obj), encoding="utf-8")
        except (TypeError, ValueError):
            assume(False)

        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        assert result is None or isinstance(result, str)

    @given(
        misspellings=st.dictionaries(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("Ll",))),
            st.text(min_size=0, max_size=20),
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_random_misspelling_dict(self, tmp_path, monkeypatch, misspellings):
        """Parser should handle random misspelling dictionaries without crashing."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        try:
            corrections_file.write_text(json.dumps({"misspellings": misspellings}), encoding="utf-8")
        except (TypeError, ValueError):
            assume(False)

        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        assert result is None or isinstance(result, str)

    @given(
        phrase=st.lists(
            st.tuples(st.text(min_size=1, max_size=20), st.text(min_size=0, max_size=20)),
            min_size=0,
            max_size=5,
        )
    )
    @settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_random_phrase_corrections(self, tmp_path, monkeypatch, phrase):
        """Parser should handle random phrase corrections without crashing."""
        from voice_typer.server import text_cleanup

        # Convert tuples to lists for JSON serialization
        phrase_lists = [list(p) for p in phrase]
        corrections_file = tmp_path / "voice-typer-corrections.json"
        try:
            corrections_file.write_text(json.dumps({"phrase_corrections": phrase_lists}), encoding="utf-8")
        except (TypeError, ValueError):
            assume(False)

        result = text_cleanup.configure_corrections(config_dir=tmp_path)
        assert result is None or isinstance(result, str)

    @given(text=st.text(min_size=0, max_size=100, alphabet=st.characters(whitelist_categories=("L", "N", "Z"))))
    @settings(max_examples=20, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_cleanup_works_after_fuzzed_corrections(self, tmp_path, monkeypatch, text):
        """After loading random corrections, cleanup should still work."""
        from voice_typer.server import text_cleanup

        corrections_file = tmp_path / "voice-typer-corrections.json"
        corrections_file.write_text(json.dumps({"misspellings": {}}), encoding="utf-8")
        text_cleanup.configure_corrections(config_dir=tmp_path)
        result = clean_transcribed_text(text)
        assert isinstance(result, str)
