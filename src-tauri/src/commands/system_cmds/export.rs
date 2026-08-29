//! GDPR right-to-export commands: `export_templates` and
//! `export_config`. Both are thin wrappers over the shared
//! `crate::commands::export::export_data` helper (save dialog +
//! pretty-printed JSON write); `export_config` additionally runs the
//! Rust-side defense-in-depth secret scrub from [`super::redaction`].

use serde_json::Value;

use super::redaction::redact_config_secrets;
use crate::commands::export::export_data;
use crate::commands::require_main_window;
use crate::error::VoiceTyperError;

/// GDPR right-to-export for templates. Opens a save-file dialog (JSON
/// only — no CSV shape for templates) and writes the data as
/// pretty-printed JSON. Mirrors the Electron `templates:export` IPC
/// handler in `voice_typer/client/src/main/ipc/export-handlers.ts`.
///
/// Returns the same `{success, path?, canceled?, error?}` shape as
/// `export_history` / `export_vocabulary` so the renderer's mapping
/// (Tauri `canceled:true` → Electron `{success:false}` parity) works
/// identically.
///
/// `window` is auto-injected by Tauri at runtime — the renderer's
/// `invoke('export_templates', { data })` call is unchanged.
/// `require_main_window(&window)?` runs FIRST.
#[tauri::command]
pub async fn export_templates(
    data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    // Templates are always JSON (no tabular CSV shape). Pass "json"
    // explicitly so the shared helper's format-validation accepts.
    export_data(
        data,
        "json".to_string(),
        app,
        "voice-typer-templates",
        "Export Templates",
    )
    .await
}

/// GDPR right-to-export for the full app config. The Python sidecar is
/// contractually responsible for redacting API keys BEFORE the data
/// reaches this command; this command adds a Rust-side
/// defense-in-depth redaction pass via
/// [`redact_config_secrets`] (see `super::redaction`) — if the Python
/// path regresses, the Rust host still scrubs obvious secret-shaped
/// keys (api_key / secret / token / password / passwd / pwd /
/// credential / auth, case-insensitive substring match) before writing
/// the JSON to disk. Mirrors the Electron `config:export` IPC handler.
///
/// Same return shape as `export_templates`.
///
/// `window` is auto-injected by Tauri at runtime — the renderer's
/// `invoke('export_config', { data })` call is unchanged.
/// `require_main_window(&window)?` runs FIRST.
#[tauri::command]
pub async fn export_config(
    mut data: Value,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    // Defense-in-depth redaction. Walk the JSON tree and replace any
    // value whose key matches the sensitive-key pattern with
    // `***REDACTED***`. Logs a `warn` per redaction (emitted from
    // `redact_config_secrets` in `redaction.rs`) plus this summary line —
    // BOTH are intentional: the per-key lines identify what leaked, the
    // `[REDACT-DEFENSE]` summary is the greppable anchor for regressions.
    // Do not "deduplicate" them away.
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
        "voice-typer-config",
        "Export Config",
    )
    .await
}
