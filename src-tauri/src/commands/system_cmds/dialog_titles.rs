//! Rust-side locale→title lookup for the host's native OS dialogs.
//!
//! The renderer pushes its current locale to the host via the
//! `set_host_locale` command (stored in `SidecarState::host_locale` —
//! see `system_cmds/locale.rs`). This module is the consumer of that
//! pushed locale at the host's native-dialog title sites:
//!
//! - `open_model_import_dialog`'s folder picker
//!   ([`DialogTitle::SelectModelFolder`] — `system_cmds/dialogs.rs`),
//! - the export save-file dialogs ([`DialogTitle::ExportHistory`] /
//!   [`ExportVocabulary`] via `commands::export::export_data`,
//!   [`ExportTemplates`] / [`ExportConfig`] via
//!   `system_cmds/export.rs`).
//!
//! This mirrors what Electron's main process does with `mainT()` +
//! the `i18n:set-locale` push: the main process keeps the pushed
//! locale and uses it to localize its own native surfaces. The
//! translation strings are byte-mirrors of the Electron main-process
//! locale files (`voice_typer/client/src/main/i18n/locales/*.json`,
//! keys `dialog.selectModelFolder.title` and `dialog.export.*`) so
//! the two runtimes present identical native dialog titles — the
//! sibling test module pins that parity (see
//! `dialog_titles_tests.rs`).
//!
//! The lookup is fully self-contained in the Rust host: no new IPC
//! round-trip, no renderer involvement, and the dialog commands keep
//! their exact signatures (only the title string they pass to the OS
//! dialog builder becomes locale-aware).

use std::sync::Arc;

use crate::state::{lock, SidecarState};

/// A native OS dialog title the Rust host presents to the user.
/// Each variant corresponds to exactly one `set_title` call site in
/// the host's command layer; the localized string is produced by
/// [`localized_title`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum DialogTitle {
    /// `open_model_import_dialog`'s native folder picker
    /// (HuggingFace model-cache import).
    SelectModelFolder,
    /// `export_history`'s save-file dialog.
    ExportHistory,
    /// `export_vocabulary`'s save-file dialog.
    ExportVocabulary,
    /// `export_templates`'s save-file dialog.
    ExportTemplates,
    /// `export_config`'s save-file dialog.
    ExportConfig,
}

/// The app's supported UI languages (mirrors `SUPPORTED_LOCALES` in
/// the renderer's `i18n/locale.ts`: en, ar, de, es, fr, hi, ru, zh).
/// Test-support constant: the sibling test module uses it to verify
/// every supported language has a genuinely translated title, so it
/// only exists in test builds (the lookup itself matches on the
/// primary language subtag directly).
#[cfg(test)]
pub(crate) const SUPPORTED_LANGUAGES: [&str; 8] = ["en", "ar", "de", "es", "fr", "hi", "ru", "zh"];

/// Read the renderer-pushed locale from the managed
/// [`SidecarState`]. Returns `None` until the first
/// `set_host_locale` push arrives (English titles are used in that
/// case — see [`localized_title`]).
///
/// Reads through the process-wide managed state via the
/// `tauri::AppHandle` the dialog commands already receive
/// (auto-injected by Tauri at runtime), so the command signatures
/// stay exactly as they were — no renderer-visible change.
pub(crate) fn host_locale(app: &tauri::AppHandle) -> Option<String> {
    use tauri::Manager;
    let state = app.state::<Arc<SidecarState>>();
    // Bind through a local so the `MutexGuard` temporary is dropped
    // BEFORE `state` (tail-expression temporaries outlive block
    // locals in drop order, which would borrow `state` after its
    // own drop).
    let locale = lock(&state.host_locale).clone();
    locale
}

/// Convenience wrapper used by the native-dialog call sites: resolve
/// the localized title for `kind` using the locale currently pushed
/// by the renderer ([`host_locale`]). Command-layer call sites use
/// this so the locale read + title resolution live in exactly one
/// place per host, while the pure [`localized_title`] stays
/// unit-testable without a Tauri runtime.
pub(crate) fn localized_title_for(kind: DialogTitle, app: &tauri::AppHandle) -> &'static str {
    localized_title(kind, host_locale(app).as_deref())
}

/// Resolve the localized title for a native dialog.
///
/// `locale` is the raw pushed value from `SidecarState::host_locale`
/// (`None` before the first push). Language matching uses the
/// PRIMARY subtag only and is case-insensitive, so `"ar"`,
/// `"ar-EG"`, `"ar_EG"` and `"AR"` all resolve to Arabic. `None`,
/// an empty value, or an unrecognized language falls back to
/// English — matching the renderer's own i18n fallback behavior and
/// guaranteeing a usable title on every code path.
///
/// Non-English strings are genuine translations (byte-mirrors of
/// the Electron main-process locale files), never English pasted
/// into a non-English locale.
pub(crate) fn localized_title(kind: DialogTitle, locale: Option<&str>) -> &'static str {
    let lang = primary_language(locale);
    match kind {
        DialogTitle::SelectModelFolder => match lang.as_str() {
            "ar" => "اختيار مجلد النماذج",
            "de" => "Modellordner auswählen",
            "es" => "Seleccionar carpeta de modelos",
            "fr" => "Sélectionner le dossier de modèles",
            "hi" => "मॉडल फ़ोल्डर चुनें",
            "ru" => "Выберите папку моделей",
            "zh" => "选择模型文件夹",
            // "en" and every unrecognized language: English fallback.
            _ => "Select Model Folder",
        },
        DialogTitle::ExportHistory => match lang.as_str() {
            "ar" => "تصدير السجل",
            "de" => "Verlauf exportieren",
            "es" => "Exportar historial",
            "fr" => "Exporter l'historique",
            "hi" => "इतिहास निर्यात करें",
            "ru" => "Экспортировать историю",
            "zh" => "导出历史记录",
            _ => "Export History",
        },
        DialogTitle::ExportVocabulary => match lang.as_str() {
            "ar" => "تصدير المفردات",
            "de" => "Vokabular exportieren",
            "es" => "Exportar vocabulario",
            "fr" => "Exporter le vocabulaire",
            "hi" => "शब्दावली निर्यात करें",
            "ru" => "Экспортировать словарь",
            "zh" => "导出词汇表",
            _ => "Export Vocabulary",
        },
        DialogTitle::ExportTemplates => match lang.as_str() {
            "ar" => "تصدير القوالب",
            "de" => "Vorlagen exportieren",
            "es" => "Exportar plantillas",
            "fr" => "Exporter les modèles",
            "hi" => "टेम्पलेट निर्यात करें",
            "ru" => "Экспортировать шаблоны",
            "zh" => "导出模板",
            _ => "Export Templates",
        },
        DialogTitle::ExportConfig => match lang.as_str() {
            "ar" => "تصدير الإعدادات",
            "de" => "Konfiguration exportieren",
            "es" => "Exportar configuración",
            "fr" => "Exporter la configuration",
            "hi" => "कॉन्फ़िगरेशन निर्यात करें",
            "ru" => "Экспортировать конфигурацию",
            "zh" => "导出配置",
            _ => "Export Configuration",
        },
    }
}

/// Normalize a pushed locale to its lowercase primary language
/// subtag: `"de-DE"` / `"de_DE"` / `"DE"` → `"de"`. Returns an empty
/// string for `None` / empty input (which matches no language arm
/// in [`localized_title`] and therefore falls back to English).
fn primary_language(locale: Option<&str>) -> String {
    locale
        .and_then(|l| l.split(['-', '_']).next())
        .unwrap_or_default()
        .to_ascii_lowercase()
}

// Unit tests for the lookup (all locales per kind, fallbacks,
// normalization, and byte-parity with the Electron main-process
// locale files) live in the sibling `dialog_titles_tests.rs` file
// (C-TEST-5 — keeps production source free of inline test code,
// matching the `commands/bubble/tests.rs` pattern). The module is
// wired as a child of `dialog_titles` so the test file can use
// `use super::{...}` to reach the `pub(crate)` items directly.
#[cfg(test)]
#[path = "dialog_titles_tests.rs"]
mod dialog_titles_tests;
