use serde_json::Value;

use super::dialog_titles::DialogTitle;
use super::redaction::redact_config_secrets;
use crate::commands::export::export_data;
use crate::commands::require_main_window;
use crate::error::LausuError;

#[tauri::command]
pub async fn export_templates(
    data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, LausuError> {
    require_main_window(&window)?;
    export_data(
        data,
        "json".to_string(),
        app,
        "lausu-templates",
        DialogTitle::ExportTemplates,
    )
    .await
}

#[tauri::command]
pub async fn export_config(
    mut data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, LausuError> {
    require_main_window(&window)?;
    let redaction_count = redact_config_secrets(&mut data);
    if redaction_count > 0 {
        log::warn!(
            "[REDACT-DEFENSE] export_config: redacted {} sensitive fields at the \
             Rust host (Python-side redaction should have caught these)",
            redaction_count
        );
    }
    export_data(
        data,
        "json".to_string(),
        app,
        "lausu-config",
        DialogTitle::ExportConfig,
    )
    .await
}
