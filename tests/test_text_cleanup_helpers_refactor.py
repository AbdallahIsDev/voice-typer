"""Focused tests for the text-cleanup refactor."""

from __future__ import annotations

import json

import pytest
from voice_typer.server.text_cleanup import (
    _apply_phrase_substitutions,
    _capitalize_pronoun_i,
    _correct_whisper_phrases,
    _corrections_data as text_cleanup,
    _engine as tc_engine,
    _load_bundled_corrections,
    _load_external_corrections,
    _load_user_corrections,
    _remove_extra_words,
    configure_corrections,
)


@pytest.fixture(autouse=True)
def _configure_corrections():
    """Reset corrections state from the bundled corrections.json before"""
    # Reset state BEFORE configure_corrections (configure_corrections
    text_cleanup._user_roman_numeral_context_extensions = set()
    text_cleanup._user_roman_numeral_following_extensions = set()
    configure_corrections()


class TestAc80ThreadingImportAntipattern:
    """``_active_state_lock`` is now ``threading.Lock()``,"""

    def test_threading_is_top_level_import(self):
        """``threading`` is in the module's namespace as a top-level"""
        assert hasattr(tc_engine, "threading"), "expected `threading` to be a top-level import in text_cleanup"
        import threading as _threading

        assert tc_engine.threading is _threading

    def test_active_state_lock_is_threading_lock_instance(self):
        """``_active_state_lock`` is a ``threading.Lock`` instance."""

        # ``threading.Lock`` is a factory function; the resulting lock
        assert type(tc_engine._active_state_lock).__name__ == "lock"
        # The lock is acquired/released like a normal Lock.
        with tc_engine._active_state_lock:
            assert True

    def test_no_more_dunder_import_call_for_threading(self):
        """The module source no longer contains ``__import__(\"threading\")``."""
        import pathlib

        import voice_typer.server.text_cleanup as tc_pkg

        pkg_dir = pathlib.Path(tc_pkg.__file__).parent
        source = "\n".join(p.read_text(encoding="utf-8") for p in sorted(pkg_dir.glob("*.py")))
        assert '__import__("threading")' not in source, 'regression: ``__import__("threading")`` is back in the source'


class TestAc81UnifiedPhraseSubstitutionsHelper:
    """``_correct_whisper_phrases`` and ``_remove_extra_words``"""

    def test_helper_exists_and_is_callable(self):
        """The unified helper exists in the module namespace."""
        assert callable(_apply_phrase_substitutions)

    def test_helper_short_circuits_when_no_pattern(self):
        """When ``get_regex`` returns ``(None, {})``, the helper returns"""
        calls: list[int] = []

        def get_regex():
            calls.append(1)
            return None, {}

        def replacer(match, lookup):
            raise AssertionError("replacer should not be called when pattern is None")

        out = _apply_phrase_substitutions("hello world", get_regex, replacer)
        assert out == "hello world"
        assert len(calls) == 1

    def test_helper_invokes_replacer_with_lookup(self):
        """When ``get_regex`` returns a real pattern, the helper calls"""
        import re

        pattern = re.compile(r"foo")
        lookup = {"foo": "BAR"}
        seen: list[str] = []

        def get_regex():
            return pattern, lookup

        def replacer(match, lk):
            seen.append(match.group(0))
            return lk[match.group(0)]

        out = _apply_phrase_substitutions("foo and foo", get_regex, replacer)
        assert out == "BAR and BAR"
        assert seen == ["foo", "foo"]

    def test_correct_whisper_phrases_delegates_to_helper(self):
        """``_correct_whisper_phrases`` produces the same output as a"""
        text = "looks like they working"
        # Configure corrections so the phrases are loaded.
        configure_corrections()
        # The function's output is what we compare against.
        expected = _correct_whisper_phrases(text)
        from voice_typer.server.text_cleanup import (
            _apply_case_preserving_replacement,
            _get_phrases_regex,
        )

        actual = _apply_phrase_substitutions(
            text,
            _get_phrases_regex,
            lambda m, lookup: _apply_case_preserving_replacement(m, lookup[m.group(0).lower()]),
        )
        assert actual == expected

    def test_remove_extra_words_delegates_to_helper(self):
        """``_remove_extra_words`` produces the same output as a direct"""
        text = "didn't and catch"
        configure_corrections()
        expected = _remove_extra_words(text)
        from voice_typer.server.text_cleanup import _get_extra_words_regex

        actual = _apply_phrase_substitutions(
            text,
            _get_extra_words_regex,
            lambda m, lookup: lookup[m.group(0).lower()],
        )
        assert actual == expected

    def test_no_inline_pattern_sub_in_either_function(self):
        """Neither function inlines the ``pattern.sub`` plumbing"""
        import ast
        import inspect

        for fn in (_correct_whisper_phrases, _remove_extra_words):
            source = inspect.getsource(fn)
            # Both functions should be ONE-LINER delegates: the body
            assert "_apply_phrase_substitutions(" in source, (
                f"{fn.__name__} does not delegate to _apply_phrase_substitutions"
            )
            # Parse the function source and check the BODY (excluding
            tree = ast.parse(source)
            func_def = tree.body[0]
            assert isinstance(func_def, ast.FunctionDef)
            # Walk the function body, skipping the docstring (if any).
            body = list(func_def.body)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                body = body[1:]
            has_inline_pattern_sub = False
            for node in ast.walk(ast.Module(body=body, type_ignores=[])):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "sub"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "pattern"
                ):
                    has_inline_pattern_sub = True
                    break
            assert not has_inline_pattern_sub, f"{fn.__name__} still inlines pattern.sub (regression)"


class TestAc82LoadExternalCorrectionsHelpers:
    """``_load_external_corrections`` orchestrates 4 phases via"""

    def test_load_bundled_corrections_helper_exists(self):
        """``_load_bundled_corrections`` is a callable returning a 5-tuple."""
        assert callable(_load_bundled_corrections)
        result = _load_bundled_corrections()
        assert isinstance(result, tuple)
        assert len(result) == 5
        misspellings, phrases, extra_words, loaded_any, load_errors = result
        assert isinstance(misspellings, dict)
        assert isinstance(phrases, list)
        assert isinstance(extra_words, list)
        assert isinstance(loaded_any, bool)
        assert isinstance(load_errors, list)

    def test_load_bundled_corrections_returns_data_when_file_exists(self):
        """With the real bundled corrections.json, the helper returns"""
        misspellings, _phrases, _extra, loaded_any, load_errors = _load_bundled_corrections()
        assert loaded_any is True
        assert load_errors == []
        # The bundled corrections.json has well-known entries.
        assert "infestigate" in misspellings

    def test_load_bundled_corrections_returns_empty_when_file_missing(self, monkeypatch, tmp_path):
        """When the bundled path doesn't exist, returns empty containers"""
        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        misspellings, phrases, extra_words, loaded_any, load_errors = _load_bundled_corrections()
        assert misspellings == {}
        assert phrases == []
        assert extra_words == []
        assert loaded_any is False
        assert load_errors == []  # missing bundled file is silent (no error)

    def test_load_user_corrections_helper_exists(self):
        """``_load_user_corrections`` is a callable returning an 8-tuple."""
        assert callable(_load_user_corrections)
        result = _load_user_corrections(config_dir=None, corrections_path=None)
        assert isinstance(result, tuple)
        assert len(result) == 8

    def test_load_user_corrections_no_file_returns_empty_extensions(self, tmp_path, monkeypatch):
        """When no user file exists, the extension sets are empty."""
        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        result = _load_user_corrections(config_dir=tmp_path)
        path, _m, _p, _e, roman_ctx, roman_fol, loaded_any, _errs = result
        assert path is None
        assert loaded_any is False
        assert roman_ctx == set()
        assert roman_fol == set()

    def test_load_user_corrections_returns_extensions_when_present(self, tmp_path, monkeypatch):
        """When the user file has ``roman_numeral_context_words`` /"""
        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    "roman_numeral_context_words": ["George", "EDWARD"],
                    "roman_numeral_following_words": ["Until"],
                }
            ),
            encoding="utf-8",
        )
        result = _load_user_corrections(config_dir=tmp_path)
        (
            path,
            _m,
            _p,
            _e,
            roman_ctx,
            roman_fol,
            loaded_any,
            _errs,
        ) = result
        assert path is not None
        assert loaded_any is True
        # Strings are lowercased regardless of the user's casing.
        assert roman_ctx == {"george", "edward"}
        assert roman_fol == {"until"}

    def test_load_external_corrections_signature_unchanged(self, tmp_path, monkeypatch):
        """The orchestrator still returns the 3-tuple"""
        monkeypatch.setattr(text_cleanup, "_BUNDLED_CORRECTIONS_PATH", tmp_path / "nonexistent.json")
        result = _load_external_corrections(config_dir=tmp_path)
        # No user file → returns None (silent fallback).
        assert result is None
        # With a user file → returns a 3-tuple.
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(json.dumps({"misspellings": {"teh": "the"}}), encoding="utf-8")
        result = _load_external_corrections(config_dir=tmp_path)
        assert result is not None
        assert len(result) == 3
        misspellings, phrases, extra_words = result
        assert "teh" in misspellings


class TestAc84RomanNumeralWordSetExtensibility:
    """following word sets via their corrections file. The extensions are"""

    def test_bundled_defaults_unchanged(self):
        """The bundled defaults are still present and include the"""
        assert "henry" in text_cleanup._ROMAN_NUMERAL_CONTEXT_WORDS
        assert "chapter" in text_cleanup._ROMAN_NUMERAL_CONTEXT_WORDS
        assert "through" in text_cleanup._ROMAN_NUMERAL_FOLLOWING_WORDS
        assert "iv" in text_cleanup._ROMAN_NUMERAL_FOLLOWING_WORDS

    def test_extension_state_starts_empty_after_configure(self):
        """``configure_corrections()`` (called by the autouse fixture)"""
        configure_corrections()
        assert text_cleanup._user_roman_numeral_context_extensions == set()
        assert text_cleanup._user_roman_numeral_following_extensions == set()

    def test_capitalize_pronoun_i_uses_bundled_defaults(self):
        """Sanity: with no extensions loaded, the bundled defaults"""
        configure_corrections()
        # "king henry i" → "henry" is in the bundled context set → lowercase.
        assert _capitalize_pronoun_i("king henry i") == "king henry i"
        # "i am here" → no Roman-numeral context → capitalized.
        assert _capitalize_pronoun_i("i am here") == "I am here"

    def test_user_extensions_make_i_lowercase(self, tmp_path):
        """A user-provided extension word makes a following standalone"""
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    "roman_numeral_context_words": ["george", "edward", "charles"],
                }
            ),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        # "king george i" → "george" is now an extension context word.
        assert _capitalize_pronoun_i("king george i") == "king george i"
        # "king edward i" → "edward" is now an extension context word.
        assert _capitalize_pronoun_i("king edward i") == "king edward i"
        # The extensions are ADDITIVE, the bundled "henry" still works.
        assert _capitalize_pronoun_i("king henry i") == "king henry i"

    def test_user_following_extensions_make_i_lowercase(self, tmp_path):
        """A user-provided following-word extension makes a preceding"""
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    "roman_numeral_following_words": ["until"],
                }
            ),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        # "i until v" → "until" is now an extension following word.
        assert _capitalize_pronoun_i("i until v") == "i until v"

    def test_extensions_are_case_insensitive(self, tmp_path):
        """user capitalised them in the file."""
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    "roman_numeral_context_words": ["GeOrGe"],
                }
            ),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        # The extensions are stored lowercased internally.
        assert "george" in text_cleanup._user_roman_numeral_context_extensions
        # And the lookup is case-insensitive (the prev-word helper lowercases).
        assert _capitalize_pronoun_i("king george i") == "king george i"
        assert _capitalize_pronoun_i("king GEORGE i") == "king GEORGE i"

    def test_removing_extensions_reverts_to_bundled_only(self, tmp_path):
        """behaviour to bundled-only (extensions are REPLACED, not"""
        # First load: with extensions.
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps({"roman_numeral_context_words": ["george"]}),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        assert "george" in text_cleanup._user_roman_numeral_context_extensions
        assert _capitalize_pronoun_i("king george i") == "king george i"

        user_file.write_text(json.dumps({"misspellings": {"teh": "the"}}), encoding="utf-8")
        configure_corrections(config_dir=tmp_path)
        assert text_cleanup._user_roman_numeral_context_extensions == set()
        # Now "george" is no longer a context word → 'i' is capitalized.
        assert _capitalize_pronoun_i("king george i") == "king george I"

    def test_malformed_extensions_silently_skipped(self, tmp_path):
        """When the extension keys have wrong types (not lists), they're"""
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    # Wrong type, should be skipped, not raise.
                    "roman_numeral_context_words": "george",
                    "roman_numeral_following_words": 42,
                }
            ),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        assert text_cleanup._user_roman_numeral_context_extensions == set()
        assert text_cleanup._user_roman_numeral_following_extensions == set()
        # No Roman-numeral context applies → 'i' is capitalized.
        assert _capitalize_pronoun_i("king george i") == "king george I"

    def test_non_string_items_in_extension_list_filtered(self, tmp_path):
        """Non-string items in the extension list are filtered out"""
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    "roman_numeral_context_words": ["george", 42, None, "edward"],
                }
            ),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        # Only the string items made it through.
        assert text_cleanup._user_roman_numeral_context_extensions == {
            "george",
            "edward",
        }

    def test_end_to_end_clean_transcribed_text_respects_extensions(self, tmp_path):
        """End-to-end: ``clean_transcribed_text`` honours user-provided"""
        user_file = tmp_path / "voice-typer-corrections.json"
        user_file.write_text(
            json.dumps(
                {
                    "roman_numeral_context_words": ["napoleon"],
                }
            ),
            encoding="utf-8",
        )
        configure_corrections(config_dir=tmp_path)
        out = tc_engine.clean_transcribed_text("emperor napoleon i conquered europe")
        assert "napoleon i" in out
        assert "napoleon I" not in out
