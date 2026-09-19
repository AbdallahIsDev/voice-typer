"""Whisper language-code validation."""

from __future__ import annotations

import logging

from voice_typer.server.config_validators.scalar import _make_str_validator

_LOGGER = logging.getLogger(__name__)

# recognized Whisper language codes.


def _try_load_whisper_languages() -> tuple[frozenset[str], str] | None:
    """Attempt to load the language-code set from ``whisper.tokenizer``.

    Returns ``(frozenset, source_label)`` on success, or ``None`` if the
    """
    try:
        from whisper.tokenizer import LANGUAGES as _whisper_languages  # type: ignore[import-not-found]  # noqa: N811
    except ImportError:
        return None

    # Whisper's upstream LANGUAGES dict has 99 entries (as of
    if len(_whisper_languages) < 50:
        _LOGGER.warning(
            "whisper.tokenizer.LANGUAGES has only %d entries (expected ~99); "
            "falling back to the hardcoded language-code list",
            len(_whisper_languages),
        )
        return None

    return frozenset(_whisper_languages.keys()), "whisper.tokenizer.LANGUAGES"


def _hardcoded_language_codes() -> frozenset[str]:
    """Hardcoded fallback, the 99 codes from openai-whisper's tokenizer.py."""
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
    """Build the canonical language-code allowlist + source label."""
    whisper_loaded = _try_load_whisper_languages()
    if whisper_loaded is not None:
        return whisper_loaded
    return _hardcoded_language_codes(), "hardcoded fallback (whisper not importable)"


_ALLOWED_LANGUAGES: frozenset[str]
_ALLOWED_LANGUAGES_SOURCE: str
_ALLOWED_LANGUAGES, _ALLOWED_LANGUAGES_SOURCE = _build_allowed_languages()


# Reuse the existing string validator for the basic shape checks (type,
_LANGUAGE_BASE_VALIDATOR = _make_str_validator(max_len=16)


def _validate_language_shape(value: object) -> str | None:
    """Stage 1: type / length / control-character checks via the base validator."""
    return _LANGUAGE_BASE_VALIDATOR(value)


def _format_invalid_language_error(value: str) -> str:
    """Format the error string for a non-allowlisted language code."""
    return f"Invalid language code {value!r}, expected a 2-letter ISO 639-1 code like 'en', 'zh', 'ja'"


def _check_language_membership(value: str) -> str | None:
    """Stage 2: allowlist membership check.

    Returns ``None`` if ``value`` is ``""`` (interpreted as "auto-detect")
    """
    # Empty string is interpreted as "auto-detect", accept it.
    if value == "":
        return None
    if value not in _ALLOWED_LANGUAGES:
        return _format_invalid_language_error(value)
    return None


def _validate_language(value: object) -> str | None:
    """Validate a Whisper language code."""
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
