"""Lightweight cleanup for raw speech-to-text output.

Compatibility facade: the original single-module ``text_cleanup.py`` is
now a package split by concern:

- :mod:`._corrections_data` — corrections loaders + the Roman-numeral /
  pronoun-I cluster (writer + reader share one module because the
  user-extension state is rebound via ``global``).
- :mod:`._casing`          — sentence capitalization + file-extension repair.
- :mod:`._engine`          — active-corrections state, entry points,
                             regex constants, and the cleaning rules.

Every public AND private top-level name of the old module is
re-exported here so existing importers (including tests that patch or
poke module attributes through this namespace) keep working unchanged.
NOTE: tests that REPLACE mutable module state (e.g.
``_active_phrases``, ``_phrases_re_cache``, ``_BUNDLED_CORRECTIONS_PATH``)
must target the owning leaf (``_engine`` / ``_corrections_data``) for
the change to be visible to the reading functions.
"""

from __future__ import annotations

import logging

from ._casing import (  # noqa: F401  # facade re-export
    _KNOWN_EXTENSIONS,
    _RE_FILE_EXT,
    _capitalize_sentences,
    _fix_file_extensions,
)
from ._corrections_data import (  # noqa: F401  # facade re-export
    _BUNDLED_CORRECTIONS_PATH,
    _INTENTIONAL_REPEAT_WORDS,
    _PRONOUN_I_RE,
    _QUESTION_OPENERS,
    _ROMAN_NUMERAL_CONTEXT_WORDS,
    _ROMAN_NUMERAL_FOLLOWING_WORDS,
    CorrectionsLoadError,
    _active_corrections,
    _capitalize_pronoun_i,
    _filter_corrections_by_length,
    _load_bundled_corrections,
    _load_external_corrections,
    _load_user_corrections,
    _next_word_starting_at,
    _prev_word_ending_at,
    _truncate_corrections,
    _user_roman_numeral_context_extensions,
    _user_roman_numeral_following_extensions,
)
from ._engine import (  # noqa: F401  # facade re-export
    _MIN_WORDS_FOR_TERMINAL_PUNCTUATION,
    _NO_PUNCTUATION_PATTERNS,
    _RE_MISSPELL_WRAP,
    _RE_SENTENCE_SPLIT,
    _RE_SPACING_PUNCT_AFTER,
    _RE_SPACING_PUNCT_BEFORE,
    _RE_SPACING_WS,
    _RE_TOKEN_KEY,
    _RE_WORD_CHARS,
    _active_extra_words,
    _active_misspellings,
    _active_phrases,
    _active_state_lock,
    _add_safe_terminal_punctuation,
    _apply_case_preserving_replacement,
    _apply_phrase_substitutions,
    _build_phrases_regex,
    _clean_self_corrections,
    _clean_self_corrections_tokens,
    _correct_whisper_phrases,
    _duplicate_phrase_length,
    _extra_words_re_cache,
    _fix_common_misspellings,
    _fix_common_misspellings_tokens,
    _get_extra_words_regex,
    _get_phrases_regex,
    _looks_like_question,
    _normalize_spacing,
    _phrases_re_cache,
    _remove_adjacent_duplicate_phrases,
    _remove_adjacent_duplicate_phrases_tokens,
    _remove_extra_words,
    _remove_near_duplicate_words,
    _remove_near_duplicate_words_tokens,
    _token_key,
    clean_transcribed_text,
    configure_corrections,
)

log = logging.getLogger(__name__)
