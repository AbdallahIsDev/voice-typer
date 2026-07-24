//! System-level window commands: open_logs, open_model_import_dialog,
//! export_templates, export_config (CR-33 + UX-008 + MODEL-IMPORT +
//! NEW-PRIV-007).
//!
//! CR-33: these 4 commands were missing from the Tauri host — the
//! renderer's `window.window_` bridge declared them as optional
//! (`openLogs?`, `openModelImportDialog?`, `exportTemplates?`,
//! `exportConfig?`) so the type system allowed them to be undefined
//! at runtime, and the Tauri bridge never wired them. The Models page
//! (`Models.tsx`) printed "import not available outside Electron"
//! under Tauri; the Settings page's "View Logs" button no-op'd; the
//! Templates page's export button silently failed. This module ports
//! the 4 Electron IPC handlers from
//! `voice_typer/client/src/main/ipc/{window-handlers,export-handlers}.ts`
//! to Tauri commands so the renderer is unchanged on both runtimes.
//!
//! Module is `pub(crate)` — only `commands::mod` and
//! `tauri::generate_handler!` in `main.rs` reference its items.

use serde_json::{json, Value};
use tauri_plugin_dialog::DialogExt;

use crate::commands::export::export_data;
use crate::platform::paths::config_dir;

// ─── Tauri command: open_logs (UX-008) ────────────────────────────────

/// UX-008: open the Voice Typer log directory in the OS file manager.
///
/// Mirrors the Electron `window:open-logs` IPC handler in
/// `voice_typer/client/src/main/ipc/window-handlers.ts:57-76` which
/// calls `shell.openPath(logDir)`. The Tauri path uses the OS-native
/// open command via `std::process::Command`:
/// - Windows: `explorer.exe <path>`
/// - macOS:   `open <path>`
/// - Linux:   `xdg-open <path>`
///
/// The path is the same `config_dir` used by the Python sidecar's
/// `_paths.config_dir()` resolution (see `platform::paths::config_dir`)
/// — under Tauri the logs live under `<config_dir>/voice-typer/` (NOT
/// the Electron `~/.voice-typer/` path the old handler used, because
/// the Tauri host writes to the platform-canonical config dir per
/// ADR-0020 §8).
///
/// Returns `{"success": true, "path": "<dir>"}` on success or
/// `{"success": false, "error": "<msg>"}` on failure — matching the
/// Electron handler's shape so `Settings.tsx`'s `viewLogs()` handler
/// is unchanged on both runtimes.
///
/// GT-83: this command opens the PARENT `<config_dir>/` (the voice-
/// typer root, which contains `logs/`, `config.json`, `history.db`,
/// `models/`, etc.). For opening the logs/ subdir specifically, see
/// `open_host_logs` below — the renderer's "View Logs" button should
/// prefer `open_host_logs` so the user lands in the logs directory
/// directly. (GT-E3-4: `app` param removed since `config_dir()` no
/// longer takes args.)
#[tauri::command]
pub async fn open_logs() -> Result<Value, String> {
    let log_dir = config_dir();
    // Best-effort mkdir — if it fails (e.g. permission denied), the
    // open command below will surface the error to the user.
    let _ = std::fs::create_dir_all(&log_dir);
    let path_str = log_dir.to_string_lossy().to_string();

    let open_result = open_path_in_file_manager(&log_dir);
    match open_result {
        Ok(()) => Ok(json!({"success": true, "path": path_str})),
        Err(e) => Ok(json!({"success": false, "error": e, "path": path_str})),
    }
}

/// GT-83: open the Rust host log directory (`<config_dir>/logs/`) in
/// the OS file manager. Distinct from `open_logs` which opens the
/// parent `<config_dir>/` root.
///
/// The actual Rust host log files (`voice-typer.log`, `.log.1`, ...)
/// live in `<config_dir>/logs/` per `platform::logging::init_file_logger`
/// (which does `config_dir.join("logs")`). The previous `open_logs`
/// command opened `<config_dir>/` — operators clicking "View Logs"
/// landed in the config root and had to navigate into `logs/` manually.
/// This command opens `logs/` directly.
///
/// Coordinate with GT-FIX-17 (owns `window-namespace.ts`) — the TS
/// bridge is being updated to route the renderer's "View Logs" button
/// to `open_host_logs` (and keep `open_logs` for "Open Config Folder").
#[tauri::command]
pub async fn open_host_logs() -> Result<Value, String> {
    let log_dir = config_dir().join("logs");
    let _ = std::fs::create_dir_all(&log_dir);
    let path_str = log_dir.to_string_lossy().to_string();

    let open_result = open_path_in_file_manager(&log_dir);
    match open_result {
        Ok(()) => Ok(json!({"success": true, "path": path_str})),
        Err(e) => Ok(json!({"success": false, "error": e, "path": path_str})),
    }
}

/// GT-35: sink for renderer-side error logs. The React UI's
/// `__tauriLog.error(...)` invokes this command so uncaught UI errors
/// land in the host-side rotating log file (`<config_dir>/logs/voice-typer.log`
/// via the existing `log::error!` global logger) for operator triage
/// without requiring DevTools to be open.
///
/// The payload is an opaque JSON value — the renderer sends
/// `{message, stack?, componentStack?, location?}`. We serialize it
/// to a single line and emit via `log::error!` with a `[RENDERER_ERROR]`
/// prefix. Returns `Ok(())` unconditionally — the renderer's promise
/// resolves so its `__tauriLog.error` call doesn't itself become an
/// unhandled rejection.
#[tauri::command]
pub async fn renderer_log_error(payload: Value, _app: tauri::AppHandle) -> Result<(), String> {
    let serialized = serde_json::to_string(&payload).unwrap_or_else(|_| "<unserializable>".to_string());
    log::error!("[RENDERER_ERROR] {}", serialized);
    Ok(())
}

/// Open a filesystem path in the OS-native file manager. Best-effort:
/// returns an error string on failure (the caller surfaces it to the
/// UI). Mirrors Electron's `shell.openPath()` semantics.
fn open_path_in_file_manager(path: &std::path::Path) -> Result<(), String> {
    #[cfg(target_os = "windows")]
    {
        std::process::Command::new("explorer.exe")
            .arg(path)
            .spawn()
            .map_err(|e| format!("explorer.exe spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(target_os = "macos")]
    {
        std::process::Command::new("open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("open spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(target_os = "linux")]
    {
        std::process::Command::new("xdg-open")
            .arg(path)
            .spawn()
            .map_err(|e| format!("xdg-open spawn failed: {e}"))?;
        return Ok(());
    }
    #[cfg(not(any(target_os = "windows", target_os = "macos", target_os = "linux")))]
    {
        let _ = path;
        Err("unsupported platform: open_logs is only implemented for Windows / macOS / Linux".to_string())
    }
}

// ─── Tauri command: open_model_import_dialog (MODEL-IMPORT) ───────────

/// MODEL-IMPORT: open a native folder picker for importing HuggingFace
/// model cache directories. The user picks a folder; the renderer then
/// passes the path to `dispatch({cmd:'import_model', data:{path}})` to
/// trigger the Python sidecar's import flow.
///
/// Mirrors the Electron `model:import-dialog` IPC handler in
/// `voice_typer/client/src/main/ipc/window-handlers.ts:81-90` which
/// calls `dialog.showOpenDialog({properties: ["openDirectory"]})`.
/// The Tauri path uses `tauri-plugin-dialog`'s folder-picker API.
///
/// Returns `{"canceled": true}` if the user dismissed the dialog, or
/// `{"canceled": false, "path": "<folder>"}` on success. Matches the
/// Electron handler's shape so `Models.tsx`'s import handler is
/// unchanged on both runtimes.
#[tauri::command]
pub async fn open_model_import_dialog(
    app: tauri::AppHandle,
) -> Result<Value, String> {
    let file_path = app
        .dialog()
        .file()
        .set_title("Select Model Folder")
        .blocking_pick_folder();
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    Ok(json!({
        "canceled": false,
        "path": path.to_string_lossy().to_string(),
    }))
}

// ─── Tauri commands: export_templates / export_config (NEW-PRIV-007) ──

/// NEW-PRIV-007: GDPR right-to-export for templates. Opens a save-file
/// dialog (JSON only — no CSV shape for templates) and writes the data
/// as pretty-printed JSON. Mirrors the Electron `templates:export` IPC
/// handler in `voice_typer/client/src/main/ipc/export-handlers.ts`.
///
/// Returns the same `{success, path?, canceled?, error?}` shape as
/// `export_history` / `export_vocabulary` so the renderer's mapping
/// (Tauri `canceled:true` → Electron `{success:false}` parity) works
/// identically.
#[tauri::command]
pub async fn export_templates(
    data: Value,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    // Templates are always JSON (no tabular CSV shape). Pass "json"
    // explicitly so the shared helper's format-validation accepts.
    export_data(data, "json".to_string(), app, "voice-typer-templates", "Export Templates").await
}

/// NEW-PRIV-007: GDPR right-to-export for the full app config. The
/// Python sidecar is responsible for redacting API keys BEFORE the
/// data reaches this command — the Rust host just writes whatever
/// JSON the renderer passed. Mirrors the Electron `config:export` IPC
/// handler.
///
/// Same return shape as `export_templates`.
#[tauri::command]
pub async fn export_config(
    data: Value,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    export_data(data, "json".to_string(), app, "voice-typer-config", "Export Config").await
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── open_path_in_file_manager (pure spawn, no side effects on the
    //    test process — spawn fires-and-forgets, the child explorer /
    //    open / xdg-open may or may not actually open in a headless
    //    sandbox). We only assert the spawn doesn't panic. ──────────

    #[cfg(target_os = "linux")]
    #[test]
    fn test_open_path_in_file_manager_does_not_panic_on_existing_dir() {
        // /tmp always exists on Linux — xdg-open may or may not be
        // installed in the sandbox, but `spawn()` only fails if the
        // binary itself can't be launched (it doesn't wait for exit).
        // We accept either Ok or Err (sandbox may lack xdg-open) — the
        // test only asserts the function doesn't panic.
        let _ = open_path_in_file_manager(std::path::Path::new("/tmp"));
    }

    #[test]
    fn test_open_path_in_file_manager_handles_nonexistent_path_gracefully() {
        // A nonexistent path doesn't make `spawn()` fail (the file
        // manager would open and then show an error, but that's the
        // user's problem, not the host's). We just assert no panic.
        let _ = open_path_in_file_manager(std::path::Path::new("/nonexistent/path/that/does/not/exist/voice-typer-test"));
    }
}
