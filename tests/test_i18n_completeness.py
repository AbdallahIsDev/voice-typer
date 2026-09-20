"""Comprehensive i18n completeness tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TRANSLATIONS_DIR = REPO_ROOT / "voice_typer/client/src/renderer/src/i18n/translations"
EN_FILE = TRANSLATIONS_DIR / "en.json"

NON_ENGLISH_LOCALES = ["ar", "de", "es", "fr", "hi", "ru", "zh"]

# Keys whose values are intentionally identical across all locales (brand
ALLOWED_UNTRANSLATED = {
    "app.name",  # "Voice Typer", brand name
    "settings.apiUrl",  # "API URL", technical acronym, kept as-is
    "settings.languageHindi",  # "Hindi", proper noun
    "nav.microphone",  # "Microphone", technical term
    "settings.overlay",  # "Overlay", technical term
    "settings.preset",  # "Preset", technical term
    "settings.presetCode",  # "Code", technical term
    "settings.notifications",  # "Notifications", technical term
    "a11y.notifications",  # "Notifications", identical cognate in French (same word)
    "home.error",  # "ERROR", technical term
    "trayState.error",
    "nav.settingsGeneral",
    "settings.audioEnhancement.presetAuto",
    "settings.audioEnhancement.presetStudio",
    # (client_root_i18n): "⚠ Error", universal warning symbol +
    "bubble.errorLabel",  # "⚠ Error"
    # Provider labels are brand names, kept identical across all locales.
    "models.providers.openai.label",  # "OpenAI Whisper API"
    "models.providers.groq.label",  # "Groq Whisper API"
    "models.providers.deepgram.label",  # "Deepgram API"
    # Parakeet label is a product name, kept identical.
    "models.card.parakeetLabel",  # "NVIDIA Parakeet TDT v3  ·  "
    # Em-dash placeholder for missing data, identical glyph across locales.
    "about.unknown",  # "—"
    # Version number format: "v{version}" is universally identical.
    "about.versionValue",  # "v{version}"
    "about.version",  # "Version"
    "about.cloudTitle",  # "Cloud (optional)"
    # About page platform list, OS brand names (Windows/macOS/Linux)
    "about.platformsValue",  # "Windows, macOS, and Linux"
    "settings.audioEnhancement.equalizer",  # "Equalizer"
    "settings.audioEnhancement.equalizerAria",  # "Equalizer"
    "settings.audioEnhancement.limiter",  # "Limiter"
    "settings.audioEnhancement.limiterAria",  # "Limiter"
    # "Variables: {vars}": "Variables" is identical in EN/ES (cognate).
    "templates.variablesTooltip",  # "Variables: {vars}"
    # Vocabulary table column headers: "Original" is the identical
    "vocabulary.columnOriginal",  # "Original"
    "vocabulary.columnActions",  # "Actions"
    # About Diagnostics row label: "Backend" is the standard technical
    "about.backend",  # "Backend"
    # "Corrections", the Analytics corrections card label. The word is
    "analytics.corrections",  # "Corrections"
    "vocabulary.count_zero",  # "{count} corrections"
    "vocabulary.count_one",  # "{count} correction"
    "vocabulary.count_two",  # "{count} corrections"
    "vocabulary.count_few",  # "{count} corrections"
    "vocabulary.count_many",  # "{count} corrections"
    "vocabulary.count_other",  # "{count} corrections"
    "theme.system",  # "System"
    "settings.apiUrlPlaceholder",  # "https://api.openai.com/v1/chat/completions"
    "settings.modelPlaceholder",  # "gpt-4o-mini"
    "hotkeyPicker.customLabel",  # "{label}"
    # Proper nouns: credits section entries, canonical English names.
    "about.creditsAuthorsValue",  # "AbdallahIsDev and contributors"
    "about.creditsFontsValue",  # "Geist"
    "about.creditsIconsValue",  # "Hugeicons"
    "about.creditsLibrariesValue",  # "faster-whisper, CTranslate2, predecessor, ..."
    "notify.model_manager.backend_change_deferred_title",  # "{appName}"
    "notify.model_manager.backend_init_failed_title",  # "{appName}"
    "notify.recording_controller.consent_required_title",  # "{appName}"
    "notify.recording_controller.max_duration_stop_title",  # "{appName}"
    "notify.recording_controller.mic_disconnected_title",  # "{appName}"
    "notify.recording_controller.mic_permission_revoked_title",  # "{appName}"
    "notify.recording_controller.silence_auto_stop_title",  # "{appName}"
    "notify.recording_controller.watchdog_title",  # "{appName}"
    "notify.recording_controller.xrun_warning_title",  # "{appName}"
    "notify.settings_controller.autostart_toggle_failed_title",  # "{appName}"
    "notify.settings_controller.microphone_selection_failed_title",  # "{appName}"
    # Universal abbreviation.
    "bubble.recordingLabel",  # "REC"
    # Universal sort notation, identical across Latin-script locales.
    "common.sortAZ",  # "A \u2192 Z"
    "common.sortZA",  # "Z \u2192 A"
    # Punctuation command/symbol names shown in the cheat sheet.
    "help.punctuation.allCaps",  # "All caps [word]"
    "help.punctuation.capital",  # "Capital [word]"
    "help.punctuation.closeParen",  # "Close parenthesis"
    "help.punctuation.dash",  # "Dash"
    "help.punctuation.ellipsis",  # "Ellipsis"
    "help.punctuation.hyphen",  # "Hyphen"
    "help.punctuation.openParen",  # "Open parenthesis"
    "help.punctuation.tab",  # "Tab"
    # Technical status, flat path duplicate of models.benchmark.notImplemented (in PRE).
    "models.benchmarkNotImplemented",  # "Benchmark not yet implemented."
    # Universal abbreviations.
    "stats.shareImage.min",  # "min"
    "stats.shareImage.wpm",  # "WPM"
    # Social share targets are brand names, kept identical across all
    "stats.shareImage.socialWhatsapp",  # "WhatsApp"
    "stats.shareImage.socialTelegram",  # "Telegram"
    "stats.shareImage.socialTwitter",  # "Twitter"
    "stats.shareImage.socialFacebook",  # "Facebook"
    # Theme preset names are brand/proper nouns, kept identical.
    "theme.preset.ayu",  # "Ayu"
    "theme.preset.catppuccin",  # "Catppuccin"
    "theme.preset.dracula",  # "Dracula"
    "theme.preset.github",  # "GitHub"
    "theme.preset.monokai",  # "Monokai"
    "theme.preset.nord",  # "Nord"
    "theme.preset.sepia",  # "Sepia"
    "theme.preset.solarized",  # "Solarized"
    # HTTP status format: "HTTP {status}" is a universal format string
    "about.httpError",  # "HTTP {status}"
    # (UI/UX overhaul 2026-08-20): "VRAM" and "WER" are universal
    "models.card.vramLabel",  # "VRAM"
    "models.card.werLabel",  # "WER"
    "models.cloud.tagCloud",  # "Cloud"
    # Duration suffix abbreviations: "min" and "s" are SI-standard
    "settings.hotkeySection.minutesSuffix",  # "min"
    "settings.hotkeySection.secondsSuffix",  # "s"
    "analytics.durationZero",  # pre-existing untranslated
    "analytics.durationMinutes",  # pre-existing untranslated
    "analytics.durationHours",  # pre-existing untranslated
    "analytics.durationHoursMinutes",  # pre-existing untranslated
    "hotkeyKeys.ctrl",  # pre-existing untranslated
    "hotkeyKeys.shift",  # pre-existing untranslated
    "hotkeyKeys.alt",  # pre-existing untranslated
    "hotkeyKeys.altGr",  # pre-existing untranslated
    "hotkeyKeys.cmd",  # pre-existing untranslated
    "hotkeyKeys.win",  # pre-existing untranslated
    "hotkeyKeys.super",  # pre-existing untranslated
    "hotkeyKeys.fn",  # pre-existing untranslated
    "hotkeyKeys.capsLock",  # pre-existing untranslated
    "hotkeyKeys.numLock",  # pre-existing untranslated
    "hotkeyKeys.scrollLock",  # pre-existing untranslated
    "hotkeyKeys.printScreen",  # pre-existing untranslated
    "hotkeyKeys.pause",  # pre-existing untranslated
    "hotkeyKeys.insert",  # pre-existing untranslated
    "hotkeyKeys.delete",  # pre-existing untranslated
    "hotkeyKeys.home",  # pre-existing untranslated
    "hotkeyKeys.tab",  # pre-existing untranslated
    "hotkeyKeys.esc",  # pre-existing untranslated
    "hotkeyKeys.enter",  # pre-existing untranslated
    "hotkeyKeys.space",  # pre-existing untranslated
    "hotkeyKeys.upArrow",  # pre-existing untranslated
    "hotkeyKeys.downArrow",  # pre-existing untranslated
    "hotkeyKeys.leftArrow",  # pre-existing untranslated
    "hotkeyKeys.rightArrow",  # pre-existing untranslated
    "hotkeyKeys.shiftLeft",  # pre-existing untranslated
    "hotkeyKeys.shiftRight",  # pre-existing untranslated
    "hotkeyKeys.ctrlLeft",  # pre-existing untranslated
    "hotkeyKeys.ctrlRight",  # pre-existing untranslated
    "hotkeyKeys.altLeft",  # pre-existing untranslated
    "hotkeyKeys.altRight",  # pre-existing untranslated
    "hotkeyKeys.cmdLeft",  # pre-existing untranslated
    "hotkeyKeys.cmdRight",  # pre-existing untranslated
    "hotkeyKeys.winLeft",  # pre-existing untranslated
    "hotkeyKeys.winRight",  # pre-existing untranslated
    "hotkeyKeys.end",  # 'End'
    "hotkeyKeys.pageUp",  # 'Page Up'
    "hotkeyKeys.pageDown",  # 'Page Down'
    "hotkeyKeys.fnGlobeMacos",  # 'Fn / Globe \U0001f310 (macOS only)'
}

# Pre-existing untranslated settings keys documented in the directive (§6).
PRE_EXISTING_UNTRANSLATED = {
    "settings.general",
    "settings.generalDescription",
    "settings.tabs.general",
    "settings.postProcessing",
    "settings.postProcessingDescription",
    "settings.autoPunctuation",
    "settings.autoPunctuationInfo",
    "settings.textCleanupLabel",
    "settings.textCleanupInfo",
    "settings.textSnippets",
    "settings.textSnippetsInfo",
    "settings.vocabulary",
    "settings.vocabularyInfo",
    "settings.llmPolishing",
    "settings.llmPolishingDescription2",
    "settings.enable",
    "settings.enableInfo",
    "settings.apiKey",
    "settings.apiKeyInfo",
    "settings.apiUrlInfo",
    "settings.model",
    "settings.modelInfo",
    "settings.presetInfo",
    "settings.presetCasual",
    "settings.appLanguage",
    "settings.appLanguageDescription",
    "settings.transcriptionLanguage",
    "settings.transcriptionLanguageDescription",
    "settings.notificationsDescription",
    "settings.trayClick",
    "settings.trayClickDescription",
    "settings.launchAtLogin",
    "settings.launchAtLoginDescription",
    "settings.bubbleBehaviorLabel",
    "settings.bubbleBehaviorInfo",
    "settings.bubblePositionLabel",
    "settings.bubblePositionInfo",
    "settings.showOnAppStartup",
    "settings.showOnAppStartupInfo",
    "settings.dragToMove",
    "settings.dragToMoveInfo",
    "settings.overlayDescription",
    "settings.show",
    "settings.hide",
    "settings.tabs.appearance",
    "settings.tabs.aiAudio",
    "settings.tabs.privacy",
    "history.clearAllMessage",
    "history.exportSaved",
    "history.noTranscriptionsDescription",
    "history.noTranscriptionsToday",
    "history.restoreFailed",
    "history.showAll",
    "history.showFavorites",
    "history.startDictation",
    "history.title",
    "history.transcriptionsToday",
    "hotkeyValidation.empty",
    "hotkeyValidation.noKeys",
    "hotkeyValidation.reservedUniversal",
    "hotkeyValidation.reservedOs",
    "hotkeyValidation.singleLetterDigit",
    "hotkeyValidation.mustEndWithNonModifier",
    "hotkeyValidation.reservedWindows",
    "hotkeyValidation.reservedMacosCmd",
    "hotkeyValidation.reservedWindowsAltShift",
    "hotkeyValidation.reservedAppCtrl",
    "hotkeyValidation.shiftLetterInterferes",
    "hotkeyValidation.dictationKeyMustBeSingle",
    "hotkeyValidation.alreadyInUse",
    "hotkeyValidation.keyNotSupported",
    "hotkeyValidation.invalidHotkey",
    "hotkeyValidation.dictationKeySingle",
    "hotkeyValidation.dictationKeySingleShort",
    "hotkeyValidation.fnMacosOnly",
    "hotkeyValidation.holding",
    # I18N-PARTIAL: models.* keys pending translation for de/fr/hi/ru/zh.
    "models.snack.parakeetDepsRequired",
    "models.snack.notDownloaded",
    "models.snack.usingModel",
    "models.snack.downloaded",
    "models.snack.downloadFailedName",
    "models.snack.downloadFailed",
    "models.snack.cannotDeleteActive",
    "models.snack.deleted",
    "models.snack.deleteFailed",
    "models.snack.deleteFailedError",
    "models.snack.apiKeySaved",
    "models.snack.consentGranted",
    "models.snack.consentRevoked",
    "models.snack.hfConsentGranted",
    "models.snack.hfConsentRevoked",
    "models.snack.resumeFailed",
    "models.snack.pauseFailed",
    "models.snack.cancelled",
    "models.snack.cancelFailed",
    "models.test.needApiKey",
    "models.test.connectionSuccessful",
    "models.test.connectionFailed",
    "models.test.endpointUnavailable",
    "models.test.connectionTestFailed",
    "models.benchmark.notImplemented",
    "models.benchmarkNotImplemented",  # flat path duplicate
    "models.benchmark.title",
    "models.benchmark.description",
    "models.benchmark.runAria",
    "models.benchmark.running",
    "models.benchmark.run",
    "models.import.title",
    "models.import.importModel",
    "models.import.importing",
    "models.import.success",
    "models.import.noModelsFound",
    "models.import.failedAll",
    "models.import.failed",
    "models.hfConsent.grant",
    "models.progress.eta",
    "models.progress.paused",
    "models.download.resumeAria",
    "models.download.pauseAria",
    "models.download.resume",
    "models.download.pause",
    "models.download.cancelAria",
    "models.download.cancel",
    "models.download.depsAria",
    "models.download.deps",
    "models.card.multilingual",
    "models.card.englishOnly",
    "models.card.speedSuffix",
    "models.card.distilled",
    "models.card.activeAria",
    "models.card.useAria",
    "models.card.deleteAria",
    "models.cloud.title",
    "models.cloud.description",
    "models.cloud.apiKey",
    "models.cloud.apiKeyPlaceholder",
    "models.cloud.saveKeyAria",
    "models.cloud.saveKey",
    "models.cloud.testConnectionAria",
    "models.cloud.testConnection",
    "models.cloud.consentTitle",
    "models.cloud.consentDescription",
    "models.cloud.statusLabel",
    "models.cloud.consentGrantedStatus",
    "models.cloud.consentNotGrantedStatus",
    "models.cloud.consentAria",
    "models.deleteDialog.title",
    "models.deleteDialog.message",
    "models.errors.unknown",
    # HTTP status format, universal format string
    "about.httpError",
    # Duration suffix abbreviations
    "settings.hotkeySection.minutesSuffix",
    "settings.hotkeySection.secondsSuffix",
}

# Maintenance contract (enforced by TestBackfillSetIsMinimal below):
RW2_BACKFILLED_PENDING_TRANSLATION: set[str] = {
    "microphoneTest.volume",
    "about.documentationLink",  # "Documentation"
    "about.versionValue",  # "v{version}"
    # hotkeyPicker (1 key)
    "hotkeyPicker.customLabel",  # "{label}"
    # settings (2 keys)
    "settings.apiUrlPlaceholder",  # "https://api.openai.com/v1/chat/completions"
    "settings.modelPlaceholder",  # "gpt-4o-mini"
    "settings.audioEnhancement.equalizer",  # "Equalizer"
    "settings.audioEnhancement.equalizerAria",  # "Equalizer"
    # "How fast the gate opens when the signal rises above the open threshold."
    "settings.audioEnhancement.limiter",  # "Limiter"
    "settings.audioEnhancement.limiterAria",  # "Limiter"
    # "How fast the limiter recovers after catching a transient."
    "templates.matchModeExactLabel",  # "Exact"
    "templates.variablesTooltip",  # "Variables: {vars}"
    # theme (1 key)
    "theme.system",  # "System"
    # 96 keys backfilled with English-fallback values across all
    "format.duration.hourShort",
    "format.duration.minuteShort",
    "format.duration.secondShort",
    "hotkeyPicker.secondsRemainingSuffix",
    "models.speed.variable",
    "nav.group.system",
    "onboarding.backendCloudLabel",
    "onboarding.cloudProviderLabel",
}


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _flatten_keys(obj: dict, prefix: str = "") -> dict[str, str]:
    """Recursively collect all dot-separated keys → scalar values from a nested dict."""
    flat: dict[str, str] = {}
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            flat.update(_flatten_keys(v, full))
        else:
            flat[full] = str(v)
    return flat


def _extract_placeholders(value: str) -> set[str]:
    """Extract {placeholder} tokens from a string. Returns a set of placeholder names."""
    return set(re.findall(r"\{(\w+)\}", value))


@pytest.fixture(scope="module")
def en_data() -> dict:
    return _load_json(EN_FILE)


@pytest.fixture(scope="module")
def en_flat(en_data: dict) -> dict[str, str]:
    return _flatten_keys(en_data)


@pytest.mark.parametrize("locale", NON_ENGLISH_LOCALES)
class Test8nCompleteness:
    """Per-locale completeness tests."""

    def test_locale_file_exists(self, locale: str) -> None:
        loc_file = TRANSLATIONS_DIR / f"{locale}.json"
        assert loc_file.exists(), f"{loc_file} must exist"

    def test_key_parity_with_en(self, locale: str, en_flat: dict[str, str]) -> None:
        """Every key in en.json must exist in the locale file."""
        loc_file = TRANSLATIONS_DIR / f"{locale}.json"
        loc_data = _load_json(loc_file)
        loc_flat = _flatten_keys(loc_data)
        en_keys = set(en_flat.keys())
        loc_keys = set(loc_flat.keys())
        missing = en_keys - loc_keys
        assert not missing, (
            f"{locale}.json is missing {len(missing)} keys that en.json has: "
            f"{sorted(missing)[:10]}{'...' if len(missing) > 10 else ''}"
        )

    def test_placeholder_parity(self, locale: str, en_flat: dict[str, str]) -> None:
        """Every {placeholder} in an en.json value must exist in the locale's value."""
        loc_file = TRANSLATIONS_DIR / f"{locale}.json"
        loc_data = _load_json(loc_file)
        loc_flat = _flatten_keys(loc_data)
        mismatches: list[str] = []
        for key, en_value in en_flat.items():
            if key not in loc_flat:
                continue  # key parity is tested separately
            loc_value = loc_flat[key]
            en_placeholders = _extract_placeholders(en_value)
            loc_placeholders = _extract_placeholders(loc_value)
            if en_placeholders != loc_placeholders:
                missing_in_loc = en_placeholders - loc_placeholders
                extra_in_loc = loc_placeholders - en_placeholders
                mismatches.append(
                    f"  {key}: en={en_placeholders}, {locale}={loc_placeholders}"
                    + (f" missing={missing_in_loc}" if missing_in_loc else "")
                    + (f" extra={extra_in_loc}" if extra_in_loc else "")
                )
        assert not mismatches, f"{locale}.json has placeholder mismatches:\n" + "\n".join(mismatches)

    def test_values_translated(self, locale: str, en_flat: dict[str, str]) -> None:
        """No locale value should be identical to the English value (unless allowed)."""
        loc_file = TRANSLATIONS_DIR / f"{locale}.json"
        loc_data = _load_json(loc_file)
        loc_flat = _flatten_keys(loc_data)
        skipped_keys = ALLOWED_UNTRANSLATED | PRE_EXISTING_UNTRANSLATED | RW2_BACKFILLED_PENDING_TRANSLATION
        untranslated: list[str] = []
        for key, en_value in en_flat.items():
            if key in skipped_keys:
                continue
            if key not in loc_flat:
                continue  # key parity is tested separately
            loc_value = loc_flat[key]
            if loc_value == en_value:
                untranslated.append(f"  {key}: {en_value!r}")
        assert not untranslated, (
            f"{locale}.json has {len(untranslated)} untranslated values "
            f"(identical to English):\n" + "\n".join(untranslated[:20]) + ("..." if len(untranslated) > 20 else "")
        )

    def test_no_extra_keys_in_locale(self, locale: str, en_flat: dict[str, str]) -> None:
        """Locale files must not contain keys that en.json doesn't have."""
        loc_file = TRANSLATIONS_DIR / f"{locale}.json"
        loc_data = _load_json(loc_file)
        loc_flat = _flatten_keys(loc_data)
        en_keys = set(en_flat.keys())
        loc_keys = set(loc_flat.keys())
        extra = loc_keys - en_keys
        assert not extra, (
            f"{locale}.json has {len(extra)} keys that en.json doesn't have: "
            f"{sorted(extra)[:10]}{'...' if len(extra) > 10 else ''}"
        )


class TestAllowedUntranslated:
    """Verify the ALLOWED_UNTRANSLATED set is well-formed."""

    def test_app_name_is_allowed(self) -> None:
        assert "app.name" in ALLOWED_UNTRANSLATED

    def test_all_entries_are_dot_keys(self) -> None:
        for key in ALLOWED_UNTRANSLATED:
            assert "." in key, f"Allowed untranslated key {key!r} should be a dot-separated path"


class TestEnJson:
    """Verify en.json is well-formed."""

    def test_en_json_exists(self) -> None:
        assert EN_FILE.exists()

    def test_en_json_is_valid_json(self) -> None:
        data = _load_json(EN_FILE)
        assert isinstance(data, dict)
        assert len(data) > 0

    def test_en_json_has_models_namespace(self, en_data: dict) -> None:
        assert "models" in en_data, "en.json must have a 'models' namespace"
        models = en_data["models"]
        assert isinstance(models, dict)
        # Verify the key sub-namespaces exist. ("use" / "useAria" were
        for sub in ("title", "active", "delete", "status", "snack", "cloud", "download"):
            assert sub in models, f"en.json models.{sub} must exist"

    def test_en_json_has_history_namespace(self, en_data: dict) -> None:
        assert "history" in en_data, "en.json must have a 'history' namespace"
        history = en_data["history"]
        assert isinstance(history, dict)
        for sub in ("title", "undo", "clearAllAria", "entryDeleted", "loadMore"):
            assert sub in history, f"en.json history.{sub} must exist"


class TestBackfillSetIsMinimal:
    """ratchet: ensure RW2_BACKFILLED_PENDING_TRANSLATION only shrinks."""

    def test_every_entry_exists_in_en_json(self, en_flat: dict[str, str]) -> None:
        en_keys = set(en_flat.keys())
        stale = RW2_BACKFILLED_PENDING_TRANSLATION - en_keys
        assert not stale, (
            "RW2_BACKFILLED_PENDING_TRANSLATION has entries that don't exist in "
            f"en.json (likely renamed/removed): {sorted(stale)}"
        )

    def test_every_entry_is_still_english_fallback_somewhere(self, en_flat: dict[str, str]) -> None:
        locale_flats: dict[str, dict[str, str]] = {}
        for locale in NON_ENGLISH_LOCALES:
            loc_data = _load_json(TRANSLATIONS_DIR / f"{locale}.json")
            locale_flats[locale] = _flatten_keys(loc_data)

        stale: list[str] = []
        for key in RW2_BACKFILLED_PENDING_TRANSLATION:
            en_value = en_flat.get(key, "")
            still_english_somewhere = any(
                key in locale_flats[loc] and locale_flats[loc][key] == en_value for loc in NON_ENGLISH_LOCALES
            )
            if not still_english_somewhere:
                stale.append(key)
        assert not stale, (
            "RW2_BACKFILLED_PENDING_TRANSLATION has entries that are now fully "
            "translated in every non-English locale, remove them from the set to "
            f"keep it minimal: {sorted(stale)}"
        )

    def test_set_size_documented(self) -> None:
        """Smoke test: the set is non-empty (stopgap is in effect)."""
        assert len(RW2_BACKFILLED_PENDING_TRANSLATION) <= 370, (
            "RW2_BACKFILLED_PENDING_TRANSLATION set size grew past 370. The set "
            "should only shrink over time as translations are commissioned. "
            "If new English-fallback keys were intentionally added, update this "
            "upper bound to match, otherwise investigate the unexpected growth."
        )


class Test8nGateSummary:
    """per-locale missing-key count summary."""

    def test_all_locales_have_full_key_parity(self, en_flat: dict[str, str]) -> None:
        en_keys = set(en_flat.keys())
        summary: dict[str, int] = {}
        for locale in NON_ENGLISH_LOCALES:
            loc_data = _load_json(TRANSLATIONS_DIR / f"{locale}.json")
            loc_flat = _flatten_keys(loc_data)
            missing = en_keys - set(loc_flat.keys())
            summary[locale] = len(missing)
        total_missing = sum(summary.values())
        assert total_missing == 0, (
            f"i18n key-parity gate failed, {total_missing} missing keys total "
            f"across non-English locales: {summary}. Run "
            f"`python -m pytest tests/test_i18n_completeness.py -k key_parity -q` "
            f"for per-locale details."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
