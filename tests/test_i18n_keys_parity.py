"""Parity test for the i18n keys added by the translation pass.

This test asserts that every key introduced by the fix (covering
review entries ZU-2, ZU-5, ZU-17, ZU-21, ZU-22, ZU-25, ZU-26, ZU-31, ZU-35,
ZU-37, ZU-11) exists as a leaf in ALL 8 locale files (en/ar/de/es/fr/hi/ru/zh).

It complements ``tests/test_i18n_completeness.py`` (which enforces structural
parity against en.json as a whole) by pinning the specific additions
so that future regressions (e.g. a locale file getting truncated) are caught
with a focused failure message naming the missing key + locale.

Offline-only — no network calls (C-DATA-1). No task IDs in source (C-STYLE-1).
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


# ---------------------------------------------------------------------------
# Catalog of every key added by , grouped by review entry.
# ---------------------------------------------------------------------------

ZU_FIX_14_NEW_KEYS: dict[str, list[str]] = {
    # error toast action labels
    "ZU-2": [
        "errors.viewLogsAction",
        "errors.copyErrorAction",
    ],
    # open models folder button (replaces the flat models.openFolder string)
    "ZU-5": [
        "models.openFolder.label",
        "models.openFolder.aria",
    ],
    # low-disk-space banner interpolation
    "ZU-25": [
        "models.disk.freeSpace",
        # (models.disk.lowSpaceTitle / lowSpaceBody / models.status.insufficientDisk
        # already existed in every locale before  and are therefore not
        # asserted here — they are covered by the broader structural-parity test.)
    ],
    # vocabulary no-results empty-state description
    "ZU-26": [
        "vocabulary.noResultsDescription",
    ],
    # backend respawn-exhausted connection screen
    "ZU-17": [
        "connection.respawnFailed",
        "connection.respawnFailedHint",
    ],
    # caps-lock / screen-reader conflict warning
    "ZU-11": [
        "hotkeyPicker.capsLockSrConflictWarning",
    ],
    # aria-live announcement of pasted transcription
    "ZU-37": [
        "a11y.transcriptionPasted",
    ],
    # dashboard no-data description (now interpolates {hotkey})
    "ZU-31": [
        "analytics.noDataDescription",
    ],
    # main-process fatal dialog strings
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
    # ICU-style plural forms for 4 key families.
    # All 6 CLDR plural suffixes are present in every locale so that key-parity
    # is satisfied (en/de/es/fr/zh/hi only USE _one/_other at runtime, but the
    # unused _zero/_two/_few/_many leaves are kept in lockstep so the broader
    # structural-parity test stays green).
    # NOTE: vocabulary.importSuccess_* / templates.importSuccess_* are NOT
    # catalogued here: the import-success keys use the Singular/Plural suffix
    # convention (importSuccessSingular/importSuccessPlural) in en.json and the
    # renderer, so the ICU plural forms for those two families do not exist.
    "ZU-21": [
        "vocabulary.entryCount_one",
        "vocabulary.entryCount_other",
        "vocabulary.entryCount_zero",
        "vocabulary.entryCount_two",
        "vocabulary.entryCount_few",
        "vocabulary.entryCount_many",
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
    """Non-English locales must NOT have English-fallback values for keys.

    English is allowed to equal itself. This catches the case where a new key
    was added to en.json but the locale file was propagated with the English
    placeholder value and never translated.
    """
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
    """Russian must have all four CLDR plural forms (_one/_few/_many/_other).

    Russian uses 4 plural categories per CLDR. The 2-form locales (en/de/es/fr/zh/hi)
    have _one + _other semantically; the unused _zero/_two/_few/_many leaves are
    kept in lockstep to satisfy structural parity but are not picked at runtime.
    """
    ru = locale_flats["ru"]
    for form in ("_one", "_few", "_many", "_other"):
        for family in (
            "vocabulary.entryCount",
            "analytics.dayCountTooltip",
        ):
            key = f"{family}{form}"
            assert key in ru, f"ru.json missing Russian plural key: {key}"


def test_zu_fix_21_arabic_plural_forms_present(locale_flats: dict[str, dict[str, str]]) -> None:
    """Arabic must have all six CLDR plural forms.

    Arabic uses 6 plural categories per CLDR (_zero/_one/_two/_few/_many/_other).
    """
    ar = locale_flats["ar"]
    for form in ("_zero", "_one", "_two", "_few", "_many", "_other"):
        for family in (
            "vocabulary.entryCount",
            "analytics.dayCountTooltip",
        ):
            key = f"{family}{form}"
            assert key in ar, f"ar.json missing Arabic plural key: {key}"


def test_zu_fix_22_priority_translations_completed(locale_flats: dict[str, dict[str, str]]) -> None:
    """ZU-22 priority untranslated strings are now translated in zh and ru.

    Specifically: onboarding.permissions* (21 keys), microphoneTest.detectedIssueCodes.*
    (6 keys), hotkeyValidation.holding, microphone.loading, vocabulary.loading,
    templates.loading. These were the priority items called out in the orchestrator
    brief for the zh/ru locales.
    """
    en = locale_flats["en"]
    priority_keys = [
        # onboarding.permissions* (21 keys)
        "onboarding.permissionsTitle",
        "onboarding.permissionsDescription",
        "onboarding.permissionsLoading",
        "onboarding.permissionsNeeded",
        "onboarding.permissionsOk",
        "onboarding.permissionsNoneNeeded",
        "onboarding.permissionsTestLabel",
        "onboarding.permissionsTestSuccess",
        "onboarding.permissionsTestFailure",
        "onboarding.permissionsTestFailureBlocked",
        "onboarding.permissionsTestButton",
        "onboarding.permissionsRefresh",
        "onboarding.permissionsRefreshAria",
        "onboarding.permissionsInstructionsMacosTitle",
        "onboarding.permissionsInstructionsMacosStep1",
        "onboarding.permissionsInstructionsMacosStep2",
        "onboarding.permissionsInstructionsMacosStep3",
        "onboarding.permissionsInstructionsLinuxTitle",
        "onboarding.permissionsInstructionsLinuxStep1",
        "onboarding.permissionsInstructionsLinuxStep2",
        "onboarding.permissionsInstructionsLinuxStep3",
        # microphoneTest.detectedIssueCodes.* (6 keys)
        "microphoneTest.detectedIssueCodes.high_noise",
        "microphoneTest.detectedIssueCodes.moderate_noise",
        "microphoneTest.detectedIssueCodes.clipping",
        "microphoneTest.detectedIssueCodes.volume_too_low",
        "microphoneTest.detectedIssueCodes.volume_low",
        "microphoneTest.detectedIssueCodes.no_voice",
        # loading states (3 keys)
        "microphone.loading",
        "vocabulary.loading",
        "templates.loading",
        # hotkeyValidation.holding
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
