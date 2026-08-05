"""Whisper language-code validation.

Extracted from the original monolithic ``config_validators.py`` (package
split).

Previously ``_validate_language`` was just ``_make_str_validator(max_len=16)``,
which accepted any string up to 16 chars.  A typo like ``"english"`` or
``"zzzzz"`` would pass validation, persist to ``config.json``, and surface as
a cryptic Whisper load error at transcription time.  This module layers a
Whisper language-code allowlist on top of the existing string validator so
typos are caught at ``set_config`` time with a clear, actionable error.

The allowlist source is ``whisper.tokenizer.LANGUAGES`` (a dict of 2-letter
ISO 639-1 code → language name) if the ``whisper`` package is importable at
module init.  Otherwise a hardcoded fallback covering the same 99 codes from
openai-whisper's tokenizer.py (as of v20231117) is used.  When whisper IS
importable we use the live dict so any new languages added upstream are
picked up automatically.
"""

from __future__ import annotations

import logging

from voice_typer.server.config_validators.scalar import _make_str_validator

_LOGGER = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────
# recognized Whisper language codes.
#
# Previously ``_VALIDATOR_LANGUAGE`` was just ``_make_str_validator(max_len=16)``,
# which accepted any string up to 16 chars.  A typo like ``"english"`` or
# ``"zzzzz"`` would pass validation, persist to config.json, and surface as
# a cryptic Whisper load error at transcription time.  This allowlist
# catches such typos at ``set_config`` time with a clear, actionable error.
#
# Source: ``whisper.tokenizer.LANGUAGES`` (a dict of 2-letter ISO 639-1
# code → language name) if the ``whisper`` package is importable at module
# init.  Otherwise a hardcoded fallback covering the same 99 codes from
# openai-whisper's tokenizer.py (as of v20231117).  When whisper IS
# importable we use the live dict so any new languages added upstream are
# picked up automatically.
# ──────────────────────────────────────────────────────────────────────────


def _try_load_whisper_languages() -> tuple[frozenset[str], str] | None:
    """Attempt to load the language-code set from ``whisper.tokenizer``.

    Returns ``(frozenset, source_label)`` on success, or ``None`` if the
    ``whisper`` package is not importable OR the imported ``LANGUAGES``
    dict is suspiciously small (fewer than 50 entries — Whisper's
    upstream dict has 99, so anything under 50 is almost certainly a
    broken / stubbed / partially-initialized import).  Returning
    ``None`` triggers the hardcoded fallback in
    :func:`_build_allowed_languages`, so a broken whisper install
    degrades gracefully instead of rejecting every language code.
    Keeping the import in a dedicated helper makes the fallback path
    explicit and easy to test.
    """
    try:
        from whisper.tokenizer import LANGUAGES as _whisper_languages  # type: ignore[import-not-found]  # noqa: N811
    except ImportError:
        return None

    # Whisper's upstream LANGUAGES dict has 99 entries (as of
    # v20231117).  A dict with fewer than 50 entries is almost
    # certainly broken (stubbed / monkeypatched / partial import);
    # using it as the allowlist would reject every language code and
    # break the whole app.  Fall back to the hardcoded list and log a
    # WARNING so the operator can investigate the whisper install.
    if len(_whisper_languages) < 50:
        _LOGGER.warning(
            "whisper.tokenizer.LANGUAGES has only %d entries (expected ~99); "
            "falling back to the hardcoded language-code list",
            len(_whisper_languages),
        )
        return None

    return frozenset(_whisper_languages.keys()), "whisper.tokenizer.LANGUAGES"


def _hardcoded_language_codes() -> frozenset[str]:
    """Hardcoded fallback — the 99 codes from openai-whisper's tokenizer.py.

    Kept in sync with the upstream list.  When whisper IS importable we
    use the live dict (above) so new languages are picked up automatically.
    """
    return frozenset(
        {
            "en",
            "zh",
            "de",
            "es",
            "ru",
            "ko",
            "fr",
            "ja",
            "pt",
            "tr",
            "pl",
            "ca",
            "nl",
            "ar",
            "sv",
            "it",
            "id",
            "hi",
            "fi",
            "vi",
            "he",
            "uk",
            "el",
            "ms",
            "cs",
            "ro",
            "da",
            "hu",
            "ta",
            "no",
            "th",
            "ur",
            "hr",
            "bg",
            "lt",
            "la",
            "mi",
            "ml",
            "cy",
            "sk",
            "te",
            "fa",
            "lv",
            "bn",
            "sr",
            "az",
            "sl",
            "kn",
            "et",
            "mk",
            "br",
            "eu",
            "is",
            "hy",
            "ne",
            "mn",
            "bs",
            "kk",
            "sq",
            "sw",
            "gl",
            "mr",
            "pa",
            "si",
            "km",
            "sn",
            "yo",
            "so",
            "af",
            "oc",
            "ka",
            "be",
            "tg",
            "sd",
            "gu",
            "am",
            "yi",
            "lo",
            "uz",
            "fo",
            "ht",
            "ps",
            "tk",
            "nn",
            "mt",
            "sa",
            "lb",
            "my",
            "bo",
            "tl",
            "mg",
            "as",
            "tt",
            "haw",
            "ln",
            "ha",
            "ba",
            "jw",
            "su",
            "yue",
        }
    )


def _build_allowed_languages() -> tuple[frozenset[str], str]:
    """Build the canonical language-code allowlist + source label.

    Prefers ``whisper.tokenizer.LANGUAGES`` when importable so new
    languages added upstream are picked up automatically; falls back to
    the hardcoded list otherwise.  Splitting the build into a dedicated
    helper (vs. inline module-level code) makes the import-or-fallback
    decision explicit and unit-testable.
    """
    whisper_loaded = _try_load_whisper_languages()
    if whisper_loaded is not None:
        return whisper_loaded
    return _hardcoded_language_codes(), "hardcoded fallback (whisper not importable)"


_ALLOWED_LANGUAGES: frozenset[str]
_ALLOWED_LANGUAGES_SOURCE: str
_ALLOWED_LANGUAGES, _ALLOWED_LANGUAGES_SOURCE = _build_allowed_languages()


# Reuse the existing string validator for the basic shape checks (type,
# length, control characters).  The language-code allowlist is layered on
# top so the existing ``test_str_validator_via_ipc_rejects_nul_in_language``
# regression test (which expects the error to contain the word "control")
# continues to pass.
_LANGUAGE_BASE_VALIDATOR = _make_str_validator(max_len=16)


def _validate_language_shape(value: object) -> str | None:
    """Stage 1: type / length / control-character checks via the base validator.

    Returning ``None`` here means ``value`` is a string of length ≤ 16 with
    no C0/DEL/C1 control characters.  The caller can safely ``assert
    isinstance(value, str)`` afterwards.
    """
    return _LANGUAGE_BASE_VALIDATOR(value)


def _format_invalid_language_error(value: str) -> str:
    """Format the error string for a non-allowlisted language code.

    Pulled into its own helper so future tweaks to the wording (e.g.
    listing valid codes when the set is small) live in one place.
    """
    return f"Invalid language code {value!r} — expected a 2-letter ISO 639-1 code like 'en', 'zh', 'ja'"


def _check_language_membership(value: str) -> str | None:
    """Stage 2: allowlist membership check.

    Returns ``None`` if ``value`` is ``""`` (interpreted as "auto-detect")
    or a known 2-letter code; otherwise returns the formatted error string.
    """
    # Empty string is interpreted as "auto-detect" — accept it.
    if value == "":
        return None
    if value not in _ALLOWED_LANGUAGES:
        return _format_invalid_language_error(value)
    return None


def _validate_language(value: object) -> str | None:
    """Validate a Whisper language code.

    previously ``_VALIDATOR_LANGUAGE`` was just
        ``_make_str_validator(max_len=16)`` which accepted any string up to
        16 chars.  A typo like ``"english"`` or ``"zzzzz"`` would pass
        validation, persist to config.json, and surface as a cryptic Whisper
        load error at transcription time.

        This validator:

        1. Reuses :data:`_LANGUAGE_BASE_VALIDATOR` for type / length /
           control-character checks (so the existing
           ``test_str_validator_via_ipc_rejects_nul_in_language`` regression
           test still passes — the error must contain the word "control").
        2. Accepts the empty string as valid (interpreted as "auto-detect" —
           the renderer's ``value={config.language || "auto"}`` fallback relies
           on this).
        3. Rejects any non-empty string that is not a 2-letter ISO 639-1 code
           in :data:`_ALLOWED_LANGUAGES` with a clear, actionable error.
    """
    err = _validate_language_shape(value)
    if err is not None:
        return err
    # ``err is None`` implies ``value`` is a str (per _make_str_validator).
    assert isinstance(value, str)
    return _check_language_membership(value)


__all__ = [
    "_ALLOWED_LANGUAGES",
    "_ALLOWED_LANGUAGES_SOURCE",
    "_LANGUAGE_BASE_VALIDATOR",
    "_try_load_whisper_languages",
    "_hardcoded_language_codes",
    "_build_allowed_languages",
    "_validate_language_shape",
    "_format_invalid_language_error",
    "_check_language_membership",
    "_validate_language",
]
