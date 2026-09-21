
use serde_json::{json, Value};
use tauri_plugin_dialog::DialogExt;
use tokio::sync::oneshot;

use super::dialog_titles::{localized_title_for, DialogTitle};
use crate::commands::export::await_dialog_bridge;
use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::platform::open_path::{
    open_external_url, open_path_in_file_manager, reveal_path_in_file_manager,
};
use crate::platform::paths::config_dir;

pub(crate) fn logs_dir_path(config_dir: &std::path::Path) -> std::path::PathBuf {
    config_dir.join("logs")
}

#[tauri::command]
pub async fn open_logs(
    _app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    let log_dir = logs_dir_path(&config_dir());
    let blocking_result = tauri::async_runtime::spawn_blocking(move || {
        if let Err(e) = std::fs::create_dir_all(&log_dir) {
            return Err(format!(
                "create_dir_all({}) failed: {}",
                log_dir.display(),
                e
            ));
        }
        open_path_in_file_manager(&log_dir)
    })
    .await;
    match blocking_result {
        Ok(Ok(())) => Ok(json!({"success": true})),
        Ok(Err(e)) => Ok(json!({"success": false, "error": e})),
        Err(join_err) => Ok(json!({
            "success": false,
            "error": format!("open_logs blocking task failed: {join_err}")
        })),
    }
}

/// Open an https URL in the user's default browser.
///
/// Replaces the predecessor's `shell.openExternal` route (see
/// `platform::open_path::open_external_url` for the https-only rationale
/// and the per-OS mechanics). The renderer's `ExternalLink` component /
/// `openExternalUrl` helper invoke this instead of `window.open`, which
/// the Tauri webview either blocks or traps inside the app (CSP
/// `default-src 'self'`, `plugins.shell.open = false` per C-TAURI-2).
///
/// Returns the same `{success, error?}` envelope shape as `open_logs` so
/// the renderer's existing handling is reused unchanged. `window` is
/// auto-injected by Tauri; `require_main_window` runs FIRST so the
/// sandboxed bubble renderer cannot launch a browser.
#[tauri::command]
pub async fn open_external_url_command(
    url: String,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    // Blocking work (argv build + OS handler spawn) goes to the blocking
    // pool, mirroring `open_logs`.
    let result = tauri::async_runtime::spawn_blocking(move || open_external_url(&url)).await;
    match result {
        Ok(Ok(())) => Ok(json!({"success": true})),
        Ok(Err(e)) => Ok(json!({"success": false, "error": e})),
        Err(join_err) => Ok(json!({
            "success": false,
            "error": format!("open_external_url blocking task failed: {join_err}")
        })),
    }
}

#[tauri::command]
pub async fn reveal_path_command(
    path: String,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    let path_buf = std::path::PathBuf::from(path);
    let result =
        tauri::async_runtime::spawn_blocking(move || reveal_path_in_file_manager(&path_buf)).await;
    match result {
        Ok(Ok(())) => Ok(json!({"success": true})),
        Ok(Err(e)) => Ok(json!({"success": false, "error": e})),
        Err(join_err) => Ok(json!({
            "success": false,
            "error": format!("reveal_path blocking task failed: {join_err}")
        })),
    }
}

#[tauri::command]
pub async fn open_model_import_dialog(
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    // Locale-aware title from the renderer-pushed host_locale
    // (English until the first `set_host_locale` push resolves).
    let title = localized_title_for(DialogTitle::SelectModelFolder, &app);
    let (tx, rx) = oneshot::channel();
    app.dialog().file().set_title(title).pick_folder(move |f| {
        let _ = tx.send(f);
    });
    let file_path = await_dialog_bridge(rx).await;
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    Ok(json!({
        "canceled": false,
        "path": path.to_string_lossy().to_string(),
    }))
}

// Unit tests for the `open_logs` target-directory helper live in the
// sibling `dialogs_tests.rs` file (C-TEST-5: keeps production source
// free of inline test code, matching the `commands/bubble/tests.rs`
// pattern). The module is wired as a child of `dialogs` so the test
// file can use `use super::logs_dir_path` directly.
#[cfg(test)]
#[path = "dialogs_tests.rs"]
mod dialogs_tests;
