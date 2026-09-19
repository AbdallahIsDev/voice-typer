
use std::sync::Arc;

use crate::state::{lock, SidecarState};

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
    ExportStatsImage,
}

#[cfg(test)]
pub(crate) const SUPPORTED_LANGUAGES: [&str; 8] = ["en", "ar", "de", "es", "fr", "hi", "ru", "zh"];

pub(crate) fn host_locale(app: &tauri::AppHandle) -> Option<String> {
    use tauri::Manager;
    let state = app.state::<Arc<SidecarState>>();
    let locale = lock(&state.host_locale).clone();
    locale
}

pub(crate) fn localized_title_for(kind: DialogTitle, app: &tauri::AppHandle) -> &'static str {
    localized_title(kind, host_locale(app).as_deref())
}

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
        DialogTitle::ExportStatsImage => match lang.as_str() {
            "ar" => "تصدير صورة الإحصائيات",
            "de" => "Statistikbild exportieren",
            "es" => "Exportar imagen de estadísticas",
            "fr" => "Exporter l'image des statistiques",
            "hi" => "आँकड़े छवि निर्यात करें",
            "ru" => "Экспортировать изображение статистики",
            "zh" => "导出统计图像",
            _ => "Export Stats Image",
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

fn primary_language(locale: Option<&str>) -> String {
    locale
        .and_then(|l| l.split(['-', '_']).next())
        .unwrap_or_default()
        .to_ascii_lowercase()
}

// Unit tests for the lookup (all locales per kind, fallbacks,
// normalization, and byte-parity with the predecessor main-process
// locale files) live in the sibling `dialog_titles_tests.rs` file
// (C-TEST-5: keeps production source free of inline test code,
// matching the `commands/bubble/tests.rs` pattern). The module is
// wired as a child of `dialog_titles` so the test file can use
// `use super::{...}` to reach the `pub(crate)` items directly.
#[cfg(test)]
#[path = "dialog_titles_tests.rs"]
mod dialog_titles_tests;
