"""
Parity test for the i18n keys added by the translation pass.
Offline-only, no network calls (C-DATA-1). No task IDs in source (C-STYLE-1).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = REPO_ROOT / "voice_typer/client/src/renderer/src/i18n/translations"

ALL_LOCALES = ["en", "ar", "de", "es", "fr", "hi", "ru", "zh"]


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _flatten_keys(obj: dict, prefix: str = "") -> dict[str, str]:
    """Recursively collect all dot-separated keys → scalar values."""
    flat: dict[str, str] = {}
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_keys(v, full))
        else:
            flat[full] = str(v)
    return flat


ZU_FIX_14_NEW_KEYS: dict[str, list[str]] = {
    "ZU-2": [
        "errors.viewLogsAction",
        "errors.copyErrorAction",
    ],
    "ZU-5": [
        "models.openFolder.label",
        "models.openFolder.aria",
    ],
    "ZU-25": [
        "models.disk.freeSpace",
        # (models.disk.lowSpaceTitle / lowSpaceBody / models.status.insufficientDisk
    ],
    "ZU-26": [
        "vocabulary.noResultsDescription",
    ],
    "ZU-17": [
        "connection.respawnFailed",
        "connection.respawnFailedHint",
    ],
    "ZU-11": [
        "hotkeyPicker.capsLockSrConflictWarning",
    ],
    "ZU-37": [
        "a11y.transcriptionPasted",
    ],
    "ZU-31": [
        "analytics.noDataDescription",
    ],
    "ZU-35": [
        "dialog.pythonCrash.title",
        "dialog.pythonCrash.body",
        "dialog.pythonCrash.signalBody",
        "dialog.pythonNotFound.title",
        "dialog.pythonNotFound.body",
        "dialog.pythonStartupTimeout.title",
        "dialog.pythonStartupTimeout.body",
        "dialog.restartLoopBreak.title",
        "dialog.restartLoopBreak.body",
    ],
    "ZU-21": [
        "vocabulary.searchPlaceholderCount",
        "analytics.dayCountTooltip_one",
        "analytics.dayCountTooltip_other",
        "analytics.dayCountTooltip_zero",
        "analytics.dayCountTooltip_two",
        "analytics.dayCountTooltip_few",
        "analytics.dayCountTooltip_many",
    ],
}

# Aggregate of every key.
ALL_NEW_KEYS: list[str] = [k for keys in ZU_FIX_14_NEW_KEYS.values() for k in keys]


@pytest.fixture(scope="module")
def locale_flats() -> dict[str, dict[str, str]]:
    return {loc: _flatten_keys(_load_json(TRANSLATIONS_DIR / f"{loc}.json")) for loc in ALL_LOCALES}


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_zu_fix_14_keys_exist_in_locale(locale: str, locale_flats: dict[str, dict[str, str]]) -> None:
    """Every key must be present as a leaf in every locale file."""
    flat = locale_flats[locale]
    missing = [k for k in ALL_NEW_KEYS if k not in flat]
    assert not missing, f"{locale}.json is missing {len(missing)} keys: {missing}"


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_zu_fix_14_no_english_fallback(locale: str, locale_flats: dict[str, dict[str, str]]) -> None:
    """Non-English locales must NOT have English-fallback values for keys."""
    if locale == "en":
        pytest.skip("English is allowed to equal itself.")
    en_flat = locale_flats["en"]
    loc_flat = locale_flats[locale]
    untranslated: list[str] = []
    for key in ALL_NEW_KEYS:
        if key not in loc_flat:
            continue  # covered by the existence test
        if loc_flat[key] == en_flat.get(key, ""):
            untranslated.append(f"  {key}: {en_flat.get(key)!r}")
    assert not untranslated, (
        f"{locale}.json has {len(untranslated)} keys whose values are "
        f"identical to English (untranslated):\n" + "\n".join(untranslated)
    )


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_zu_fix_14_placeholder_parity(locale: str, locale_flats: dict[str, dict[str, str]]) -> None:
    """Every {placeholder} in an en.json value must exist in the locale's value."""
    en_flat = locale_flats["en"]
    loc_flat = locale_flats[locale]
    mismatches: list[str] = []
    for key in ALL_NEW_KEYS:
        if key not in en_flat or key not in loc_flat:
            continue
        en_ph = set(re.findall(r"\{(\w+)\}", en_flat[key]))
        loc_ph = set(re.findall(r"\{(\w+)\}", loc_flat[key]))
        if en_ph != loc_ph:
            mismatches.append(
                f"  {key}: en={sorted(en_ph)}, {locale}={sorted(loc_ph)}"
                + (f" missing={sorted(en_ph - loc_ph)}" if en_ph - loc_ph else "")
                + (f" extra={sorted(loc_ph - en_ph)}" if loc_ph - en_ph else "")
            )
    assert not mismatches, f"{locale}.json has placeholder mismatches:\n" + "\n".join(mismatches)


@pytest.mark.parametrize(
    "review_id, expected_keys",
    [(rid, keys) for rid, keys in ZU_FIX_14_NEW_KEYS.items()],
)
def test_zu_fix_14_review_entry_keys_catalogued(review_id: str, expected_keys: list[str]) -> None:
    """Smoke test: each review entry has at least one key catalogued."""
    assert expected_keys, f"{review_id} has no keys catalogued"


def test_zu_fix_21_russian_plural_forms_present(locale_flats: dict[str, dict[str, str]]) -> None:
    """Russian must have all four CLDR plural forms (_one/_few/_many/_other)."""
    ru = locale_flats["ru"]
    for form in ("_one", "_few", "_many", "_other"):
        for family in ("analytics.dayCountTooltip",):
            key = f"{family}{form}"
            assert key in ru, f"ru.json missing Russian plural key: {key}"


def test_zu_fix_21_arabic_plural_forms_present(locale_flats: dict[str, dict[str, str]]) -> None:
    """Arabic must have all six CLDR plural forms."""
    ar = locale_flats["ar"]
    for form in ("_zero", "_one", "_two", "_few", "_many", "_other"):
        for family in ("analytics.dayCountTooltip",):
            key = f"{family}{form}"
            assert key in ar, f"ar.json missing Arabic plural key: {key}"


def test_zu_fix_22_priority_translations_completed(locale_flats: dict[str, dict[str, str]]) -> None:
    """ZU-22 priority untranslated strings are now translated in zh and ru."""
    en = locale_flats["en"]
    priority_keys = [
        "onboarding.permissionsTestLabel",
        "onboarding.permissionsTestSuccess",
        "onboarding.permissionsTestButton",
        "microphoneTest.detectedIssueCodes.high_noise",
        "microphoneTest.detectedIssueCodes.moderate_noise",
        "microphoneTest.detectedIssueCodes.clipping",
        "microphoneTest.detectedIssueCodes.volume_too_low",
        "microphoneTest.detectedIssueCodes.volume_low",
        "microphoneTest.detectedIssueCodes.no_voice",
        "microphone.loading",
        "vocabulary.loading",
        "templates.loading",
        "hotkeyValidation.holding",
    ]
    for locale in ("zh", "ru"):
        loc = locale_flats[locale]
        untranslated = [k for k in priority_keys if loc.get(k) == en.get(k)]
        assert not untranslated, (
            f"{locale}.json still has {len(untranslated)} untranslated priority ZU-22 keys: {untranslated}"
        )


def test_zu_fix_22_search_hints_translated(locale_flats: dict[str, dict[str, str]]) -> None:
    """settings.searchHints.* must be translated in de/hi/ru/zh (ZU-22)."""
    en = locale_flats["en"]
    keys = [
        "settings.searchHints.appearance",
        "settings.searchHints.general",
        "settings.searchHints.aiAudio",
        "settings.searchHints.privacy",
    ]
    for locale in ("de", "hi", "ru", "zh"):
        loc = locale_flats[locale]
        untranslated = [k for k in keys if loc.get(k) == en.get(k)]
        assert not untranslated, f"{locale}.json still has untranslated settings.searchHints.*: {untranslated}"


def _no_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict:
    """object_pairs_hook that raises on ANY duplicate sibling key."""
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key!r}")
        result[key] = value
    return result


@pytest.mark.parametrize("locale", ALL_LOCALES)
def test_no_duplicate_keys_in_any_locale(locale):
    """No translation file may contain duplicate sibling JSON keys."""
    path = TRANSLATIONS_DIR / f"{locale}.json"
    with path.open(encoding="utf-8") as f:
        try:
            json.load(f, object_pairs_hook=_no_duplicate_json_keys)
        except ValueError as exc:
            pytest.fail(f"{path.name}: {exc}")
