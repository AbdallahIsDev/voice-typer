//! Export commands: history/vocabulary → JSON/CSV ( + ADR-0020 §6).

use serde_json::{json, Value};
use std::time::Duration;
use tauri_plugin_dialog::DialogExt;
use tokio::sync::oneshot;

//shared main-window guard ──────────────────────────────────
//
// `export_history`, `export_vocabulary`, `export_templates`,
// `export_config`, `open_logs`, and `open_model_import_dialog` are all
// `#[tauri::command]` functions that a compromised renderer could invoke
// over the IPC bridge. The bubble window is a sandboxed webview
// (ADR-0020 §7 + §9 + SEC-026) that must NEVER drive the export / open
// paths (a malicious bubble could exfiltrate history/vocabulary or
// trigger OS file-manager opens). Tauri v2's capability system only
// gates plugin commands, so user-defined commands need this runtime
// check.
//
//the canonical `require_main_window` helper now lives in
// `commands/mod.rs` (single source of truth). This module imports it
// privately for local use; downstream callers (e.g. `system_cmds.rs`)
// import directly from `crate::commands::require_main_window`.
//
// The error envelope shape mirrors the sidecar's WS error envelope
// ({"type":"error","data":{"code":...,"message":...}}) so the
// renderer's existing reject path treats this identically to a
// server-side rejection. See `commands::mod::require_main_window` for
//the  /  envelope shape contract.
use crate::commands::require_main_window;
use crate::commands::system_cmds::{localized_title_for, DialogTitle};
use crate::error::VoiceTyperError;


const JSON_AND_CSV_FILTERS: &[(&str, &[&str])] = &[("JSON", &["json"]), ("CSV", &["csv"])];

const JSON_ONLY_FILTERS: &[(&str, &[&str])] = &[("JSON", &["json"])];

pub(crate) fn export_file_filters(
    format: &str,
) -> &'static [(&'static str, &'static [&'static str])] {
    match format {
        "csv" => JSON_AND_CSV_FILTERS,
        _ => JSON_ONLY_FILTERS,
    }
}

//Tauri command: export_history () ─────────────────────────

#[tauri::command]
pub async fn export_history(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    export_data(
        data,
        format,
        app,
        "voice-typer-history",
        DialogTitle::ExportHistory,
    )
    .await
}

//Tauri command: export_vocabulary () ──────────────────────

#[tauri::command]
pub async fn export_vocabulary(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    export_data(
        data,
        format,
        app,
        "voice-typer-vocabulary",
        DialogTitle::ExportVocabulary,
    )
    .await
}

pub(crate) const DIALOG_CALLBACK_TIMEOUT_MS: u64 = 10 * 60 * 1000;

pub(crate) async fn await_dialog_bridge<T>(rx: oneshot::Receiver<Option<T>>) -> Option<T> {
    await_dialog_bridge_with_timeout(rx, Duration::from_millis(DIALOG_CALLBACK_TIMEOUT_MS)).await
}

pub(crate) async fn await_dialog_bridge_with_timeout<T>(
    rx: oneshot::Receiver<Option<T>>,
    timeout: Duration,
) -> Option<T> {
    match tokio::time::timeout(timeout, rx).await {
        Ok(Ok(value)) => value,
        Ok(Err(_recv_err)) => None,
        Err(_elapsed) => {
            log::warn!(
                "[DIALOG] dialog callback never fired within {}ms: resolving as canceled",
                timeout.as_millis()
            );
            None
        }
    }
}

pub(crate) async fn export_data(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    default_filename: &str,
    title_kind: DialogTitle,
) -> Result<Value, VoiceTyperError> {
    // Locale-aware title from the renderer-pushed host_locale
    // (English until the first `set_host_locale` push resolves).
    let title = localized_title_for(title_kind, &app);
    let (tx, rx) = oneshot::channel();
    let mut dialog = app.dialog().file().set_title(title);
    for (filter_name, extensions) in export_file_filters(&format) {
        dialog = dialog.add_filter(*filter_name, *extensions);
    }
    dialog.set_file_name(default_filename).save_file(move |f| {
        let _ = tx.send(f);
    });
    let file_path = await_dialog_bridge(rx).await;
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    let content = match format.as_str() {
        "json" => {
            serde_json::to_string_pretty(&data).map_err(|e| format!("JSON encode failed: {e}"))?
        }
        "csv" => json_to_csv(&data)?,
        other => return Err(format!("unsupported format: {}", other).into()),
    };
    let path_for_blocking = path.clone();
    tauri::async_runtime::spawn_blocking(move || {
        crate::util::atomic_write_bytes(&path_for_blocking, content.as_bytes())
    })
    .await
    .map_err(|e| format!("write task join failed: {e}"))?
    .map_err(|e| format!("write failed: {e}"))?;
    Ok(json!({"success": true, "path": path.to_string_lossy().to_string()}))
}

pub(crate) fn json_to_csv(data: &Value) -> Result<String, String> {
    let arr = data
        .as_array()
        .ok_or_else(|| "CSV export requires an array of objects".to_string())?;
    if arr.is_empty() {
        return Ok(String::new());
    }
    let mut keys: Vec<String> = Vec::new();
    let mut seen: std::collections::HashSet<String> = std::collections::HashSet::new();
    for item in arr {
        if let Some(obj) = item.as_object() {
            for k in obj.keys() {
                if seen.insert(k.clone()) {
                    keys.push(k.clone());
                }
            }
        }
    }
    let mut out = String::new();
    out.reserve(arr.len().saturating_mul(64));
    for (i, k) in keys.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        csv_escape_into(&mut out, k);
    }
    out.push('\n');
    for item in arr {
        let empty_map = serde_json::Map::new();
        let obj = item.as_object().unwrap_or(&empty_map);
        for (i, k) in keys.iter().enumerate() {
            if i > 0 {
                out.push(',');
            }
            let cell = obj.get(k).map(value_to_string).unwrap_or_default();
            csv_escape_into(&mut out, &cell);
        }
        out.push('\n');
    }
    Ok(out)
}

/// Render a JSON value as a single CSV cell (no quoting).
pub(crate) fn value_to_string(v: &Value) -> String {
    let mut out = String::new();
    value_to_string_into(&mut out, v);
    out
}

pub(crate) fn value_to_string_into(out: &mut String, v: &Value) {
    use std::fmt::Write as _;
    match v {
        Value::String(s) => out.push_str(s),
        Value::Number(n) => {
            let _ = write!(out, "{}", n);
        }
        Value::Bool(b) => {
            let _ = write!(out, "{}", b);
        }
        Value::Null => {}
        other => {
            let _ = write!(out, "{}", other);
        }
    }
}

/// RFC 4180 CSV cell escaping: wrap in double quotes if the cell
/// contains a comma, double-quote, newline, or carriage return; double
/// any embedded double-quotes.
///
/// SEC-015 CSV formula-injection defense.
/// Cells starting with `=`, `+`, `-`, `@`, `\t`, or `\r` are prefixed
/// with a single quote `'` before quoting so spreadsheet apps (Excel,
/// LibreOffice) treat them as text rather than executing them as
/// formulas. Without this defense, a user who dictates `=cmd|'/C calc'!A1`
/// and then exports history to CSV would be vulnerable to formula
/// injection when opening the file in a spreadsheet.
///
/// Mirrors the predecessor-side `csvEscape` in
/// `voice_typer/client/src/main/ipc/export-handlers.ts`: the two
/// implementations produce byte-identical output for the same input
/// (enforced by the TS parity test `export-handlers-csv-escape.test.ts`
/// and by the CSV-escape cases in `export_tests.rs`).
///
/// Writes the escaped form of `s` directly into `out`, appending to
/// any existing content (never overwriting) and allocating no per-cell
/// `String`: [`json_to_csv`] relies on both properties to reuse one
/// output buffer across every header cell + data cell of an export.
pub(crate) fn csv_escape_into(out: &mut String, s: &str) {
    // SEC-015: prefix formula-injection-prone cells with a single quote.
    let needs_prefix = s.starts_with('=')
        || s.starts_with('+')
        || s.starts_with('-')
        || s.starts_with('@')
        || s.starts_with('\t')
        || s.starts_with('\r');
    let needs_quote = s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r');
    if needs_quote {
        out.push('"');
        if needs_prefix {
            out.push('\'');
        }
        for ch in s.chars() {
            if ch == '"' {
                out.push('"');
                out.push('"');
            } else {
                out.push(ch);
            }
        }
        out.push('"');
    } else if needs_prefix {
        out.push('\'');
        out.push_str(s);
    } else {
        out.push_str(s);
    }
}

// Unit tests for `csv_escape_into`, `value_to_string`,
// `value_to_string_into`, `json_to_csv`, and the `atomic_write_bytes`
// contract live in the sibling `export_tests.rs` file (C-TEST-5, keeps
// production source free of inline test code, matching the
// `commands/bubble/tests.rs` pattern). The module is wired as a child of
// `export` so the test file can use `use super::{...}` to access
// `pub(crate)` items.
#[cfg(test)]
#[path = "export_tests.rs"]
mod export_tests;
