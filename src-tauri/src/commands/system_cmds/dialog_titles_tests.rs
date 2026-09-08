//! Unit tests for `system_cmds::dialog_titles` (C-TEST-5 sibling
//! file).
//!
//! Pins the host-side locale→title lookup that consumes
//! `SidecarState::host_locale` at the native dialog title sites:
//!
//! - every non-English supported language yields a genuinely
//!   localized (non-English) title for EVERY title kind;
//! - `None` (locale never pushed), empty, unrecognized, and
//!   unknown-region locales fall back to English;
//! - the pushed locale is normalized (primary subtag,
//!   case-insensitive, `-` and `_` separators);
//! - the Rust table stays byte-identical to the Electron
//!   main-process locale files (`voice_typer/client/src/main/i18n/
//!   locales/*.json` keys `dialog.selectModelFolder.title` +
//!   `dialog.export.*`) — the cross-runtime parity guard.

use super::{localized_title, DialogTitle, SUPPORTED_LANGUAGES};
use serde_json::Value;

/// All title kinds covered by the lookup — every test below iterates
/// this list so adding a new dialog title site without extending the
/// lookup (or the tests) fails loudly.
const ALL_KINDS: [DialogTitle; 5] = [
    DialogTitle::SelectModelFolder,
    DialogTitle::ExportHistory,
    DialogTitle::ExportVocabulary,
    DialogTitle::ExportTemplates,
    DialogTitle::ExportConfig,
];

/// The 7 non-English supported languages (English is the fallback,
/// handled separately).
const NON_ENGLISH_LANGUAGES: [&str; 7] = ["ar", "de", "es", "fr", "hi", "ru", "zh"];

// ── per-kind localization ────────────────────────────────────────

#[test]
fn test_non_english_locales_yield_non_english_titles_for_every_kind() {
    for kind in ALL_KINDS {
        let english = localized_title(kind, Some("en"));
        for lang in NON_ENGLISH_LANGUAGES {
            let title = localized_title(kind, Some(lang));
            assert_ne!(
                title, english,
                "title for kind {kind:?} under locale '{lang}' must be localized, \
                 not the English string '{english}'"
            );
            assert!(
                !title.is_empty(),
                "title for kind {kind:?} under locale '{lang}' must not be empty"
            );
        }
    }
}

#[test]
fn test_supported_language_list_is_the_eight_locale_set() {
    // The lookup must cover exactly the app's 8-locale set (mirrors
    // the renderer's SUPPORTED_LOCALES). A missing language here
    // means one of the app's locales silently gets English titles.
    assert_eq!(
        SUPPORTED_LANGUAGES,
        ["en", "ar", "de", "es", "fr", "hi", "ru", "zh"]
    );
    // Every non-English supported language resolves to its own arm
    // (spot-check distinctness across languages for one kind so a
    // copy-paste arm mistake surfaces).
    let history_titles: Vec<&str> = NON_ENGLISH_LANGUAGES
        .iter()
        .map(|lang| localized_title(DialogTitle::ExportHistory, Some(lang)))
        .collect();
    let unique: std::collections::HashSet<&str> = history_titles.iter().copied().collect();
    assert_eq!(
        unique.len(),
        NON_ENGLISH_LANGUAGES.len(),
        "each non-English language must have its own Export History title"
    );
}

// ── fallbacks ────────────────────────────────────────────────────

#[test]
fn test_none_and_unknown_locales_fall_back_to_english() {
    let fallback_inputs: [Option<&str>; 5] = [
        None,          // locale never pushed
        Some(""),      // empty push (defensive)
        Some("xx"),    // unrecognized language
        Some("pt-BR"), // real language, not supported by the app
        Some("  "),    // whitespace-only (defensive)
    ];
    for kind in ALL_KINDS {
        let english = localized_title(kind, Some("en"));
        for locale in fallback_inputs {
            assert_eq!(
                localized_title(kind, locale),
                english,
                "kind {kind:?} with locale {locale:?} must fall back to English"
            );
        }
    }
}

#[test]
fn test_uppercase_language_falls_back_to_english_without_normalization_bug() {
    // "EN" (uppercase English) also lands on the English fallback
    // via the `_` arm after lowercasing — pinned so the normalization
    // never accidentally invents a missing arm.
    for kind in ALL_KINDS {
        assert_eq!(
            localized_title(kind, Some("EN")),
            localized_title(kind, Some("en"))
        );
    }
}

// ── locale normalization ─────────────────────────────────────────

#[test]
fn test_region_qualified_locale_resolves_primary_language() {
    assert_eq!(
        localized_title(DialogTitle::ExportHistory, Some("de-DE")),
        "Verlauf exportieren",
        "'de-DE' must resolve to the German title via its primary subtag"
    );
    assert_eq!(
        localized_title(DialogTitle::ExportHistory, Some("ar-EG")),
        "تصدير السجل",
        "'ar-EG' must resolve to the Arabic title via its primary subtag"
    );
    assert_eq!(
        localized_title(DialogTitle::SelectModelFolder, Some("zh-Hans")),
        "选择模型文件夹",
        "'zh-Hans' must resolve to the Chinese title via its primary subtag"
    );
    // Unknown region qualifier on a known language still resolves
    // via the primary subtag (locale tags are language[-script][-region]).
    assert_eq!(
        localized_title(DialogTitle::ExportConfig, Some("de-xx-unknown")),
        "Konfiguration exportieren"
    );
}

#[test]
fn test_underscore_separator_and_uppercase_locale_resolve_primary_language() {
    assert_eq!(
        localized_title(DialogTitle::ExportVocabulary, Some("es_MX")),
        "Exportar vocabulario",
        "'es_MX' (underscore form) must resolve to the Spanish title"
    );
    assert_eq!(
        localized_title(DialogTitle::SelectModelFolder, Some("AR")),
        "اختيار مجلد النماذج",
        "'AR' (uppercase) must resolve to the Arabic title"
    );
}

// ── cross-runtime parity with the Electron main-process locales ──
//
// The Rust table mirrors the Electron main process's `mainT()`
// dialog strings byte-for-byte. These tests parse the actual locale
// JSON files shipped in the client tree and compare every (kind,
// language) pair, so a future edit to either side that breaks
// parity fails here instead of shipping silently.

const EN_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/en.json");
const AR_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/ar.json");
const DE_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/de.json");
const ES_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/es.json");
const FR_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/fr.json");
const HI_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/hi.json");
const RU_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/ru.json");
const ZH_MAIN_JSON: &str =
    include_str!("../../../../voice_typer/client/src/main/i18n/locales/zh.json");

#[test]
fn test_dialog_titles_are_byte_identical_to_electron_main_locale_files() {
    let files: [(&str, &str); 8] = [
        ("en", EN_MAIN_JSON),
        ("ar", AR_MAIN_JSON),
        ("de", DE_MAIN_JSON),
        ("es", ES_MAIN_JSON),
        ("fr", FR_MAIN_JSON),
        ("hi", HI_MAIN_JSON),
        ("ru", RU_MAIN_JSON),
        ("zh", ZH_MAIN_JSON),
    ];
    for (lang, raw) in files {
        let parsed: Value = serde_json::from_str(raw)
            .unwrap_or_else(|e| panic!("main locale file for '{lang}' must parse: {e}"));
        let cases: [(DialogTitle, &str); 5] = [
            (
                DialogTitle::SelectModelFolder,
                "dialog.selectModelFolder.title",
            ),
            (DialogTitle::ExportHistory, "dialog.export.history"),
            (DialogTitle::ExportVocabulary, "dialog.export.vocabulary"),
            (DialogTitle::ExportTemplates, "dialog.export.templates"),
            (DialogTitle::ExportConfig, "dialog.export.config"),
        ];
        for (kind, key) in cases {
            let expected = parsed[key].as_str().unwrap_or_else(|| {
                panic!("main locale file for '{lang}' must contain key '{key}'")
            });
            let actual = localized_title(kind, Some(lang));
            assert_eq!(
                actual, expected,
                "Rust title for {kind:?} under '{lang}' diverged from the Electron \
                 main locale file key '{key}'"
            );
        }
    }
}
