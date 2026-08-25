//! Export commands: history/vocabulary → JSON/CSV ( + ADR-0020 §6).

use serde_json::{json, Value};
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
use crate::error::VoiceTyperError;

//Tauri command: export_history () ─────────────────────────

//ADR-0020 §6 + : export the transcription history to a file
/// chosen by the user via `tauri-plugin-dialog`'s save dialog.
///
/// - `data` is the history payload (array of records) from the Python
///   sidecar's `export_history` command.
/// - `format` is `"json"` or `"csv"`.
/// - Returns `{"canceled": true}` if the user dismissed the dialog,
///   `{"success": true, "path": "<chosen path>"}` on success, or
///   `Err(message)` on I/O / encode failure.
///
//`window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('export_history', { data, format })` call is
/// unchanged. `require_main_window(&window)?` runs FIRST so a
/// compromised bubble renderer cannot drive the export path.
#[tauri::command]
pub async fn export_history(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    export_data(data, format, app, "voice-typer-history", "Export History").await
}

//Tauri command: export_vocabulary () ──────────────────────

//ADR-0020 §6 + : export the user's custom vocabulary to a
/// file chosen by the user. Same shape as `export_history` with a
/// different default filename + dialog title.
///
//same main-window guard as `export_history`.
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
        "Export Vocabulary",
    )
    .await
}

/// Shared helper for `export_history` + `export_vocabulary`. Opens a
/// `tauri-plugin-dialog` save-file dialog, then writes the data as
/// pretty-printed JSON or CSV to the chosen path. Returns
/// `{"canceled": true}` when the user cancels the dialog.
///
/// Misc host failures (path conversion, encoding, the blocking write)
/// surface as `VoiceTyperError::Host` — the legacy formatted strings,
/// byte-identical on the wire.
pub(crate) async fn export_data(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    default_filename: &str,
    title: &str,
) -> Result<Value, VoiceTyperError> {
    //use the async file-save pattern instead of blocking.
    // The blocking variant parks the Tokio worker thread for the entire
    // duration the user has the save dialog open; with Tauri's default
    // 2-N worker pool, that stalls concurrent ``dispatch`` calls
    // (heartbeat, status polling) queued behind the blocked worker.
    // tauri-plugin-dialog v2.7.2's ``save_file()`` is callback-based
    // (not async), so we bridge it via a oneshot channel.
    let (tx, rx) = oneshot::channel();
    app.dialog()
        .file()
        .set_title(title)
        .add_filter("JSON", &["json"])
        .add_filter("CSV", &["csv"])
        .set_file_name(default_filename)
        .save_file(move |f| {
            let _ = tx.send(f);
        });
    let file_path = rx.await.unwrap_or(None);
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
    // Use the shared `atomic_write_bytes` helper
    // (temp + fsync + rename + parent-dir fsync) instead of
    // `std::fs::write`. The user-picked destination may be on a
    // network drive, USB stick, or sync-client-watched folder
    // (Dropbox/OneDrive) — a non-atomic `std::fs::write` truncates
    // the destination first, so a crash or disk-full mid-write
    // leaves a partial CSV/JSON that opens but is missing rows.
    // `atomic_write_bytes` writes to a sibling temp file then renames
    // into place, so the destination is either the OLD file or the
    // NEW file (never a truncated half). The helper lives in
    // `crate::util` (it is a generic fs-write helper shared by the
    // migration path, the supervisor restart counter, and this export
    // path — see the migrate.rs cross-language "3 variants of
    // atomic-write" finding, which this fix consolidates on the
    // Rust side).
    //
    // The atomic-write helper is synchronous I/O (file create,
    // write, fsync, rename, parent-dir fsync). On slow destinations
    // (HDD fsync, network drives, USB sticks) a single call can
    // block for 100ms+; with Tauri's default 2-N async worker
    // pool that stalls every concurrent `dispatch` call (heartbeat,
    // status polling) queued behind the blocked worker. Wrapping in
    // `tauri::async_runtime::spawn_blocking` dispatches the closure
    // onto the Tokio blocking thread pool — the same pattern used in
    // `tray.rs` (rebuild_tray_menu) — so the worker thread stays
    // free. The closure owns `path_for_blocking` (a clone of `path`,
    // since `path` is still needed below for the success envelope)
    // and `content` (which is not used after this point).
    let path_for_blocking = path.clone();
    tauri::async_runtime::spawn_blocking(move || {
        crate::util::atomic_write_bytes(&path_for_blocking, content.as_bytes())
    })
    .await
    .map_err(|e| format!("write task join failed: {e}"))?
    .map_err(|e| format!("write failed: {e}"))?;
    Ok(json!({"success": true, "path": path.to_string_lossy().to_string()}))
}

/// Convert a JSON array of flat objects to CSV. The first object's keys
/// (in insertion order) form the header row; missing keys in later
/// rows are emitted as empty cells. Nested values are serialized as
/// their `to_string()` form (no recursion).
pub(crate) fn json_to_csv(data: &Value) -> Result<String, String> {
    let arr = data
        .as_array()
        .ok_or_else(|| "CSV export requires an array of objects".to_string())?;
    if arr.is_empty() {
        return Ok(String::new());
    }
    // Collect all keys (preserve insertion order from the first object
    // that contains them; subsequent objects may add new keys at the
    // end, which keeps the header stable for the common case of
    // homogeneous records).
    //use a HashSet for O(1) membership checks instead of
    // Vec::contains (O(n) per key). Previous code was O(R·K²) for R
    // records with K distinct keys — 4M string comparisons on a 10k-row
    // history export with 20 keys.
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
    // Pre-allocate the output buffer to avoid repeated grow() calls
    // during the per-cell push_str/write! below. For a 10K-row export with
    // ~22 columns, the average cell is ~12 bytes (timestamps, short text,
    // model names) so ~2.6MB is a reasonable starting capacity — the
    // String will still grow if needed, but most exports will fit without
    // a single reallocation.
    out.reserve(arr.len().saturating_mul(64));
    // Write each header cell directly to the buffer instead of collecting
    // into a `Vec<String>` and joining — for a 10k-row export with 20
    // columns, the previous `collect()` + `join(",")` pattern allocated
    // ~10,020 throwaway `Vec`s and ~10,020 join `String`s. Direct
    // `push_str` emits the same bytes with no per-row heap traffic.
    for (i, k) in keys.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        // Write the escaped cell directly into `out` instead of
        // calling `csv_escape(k)` which allocates a per-cell String that
        // is immediately discarded after `push_str` copies its bytes.
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
            // Same direct-write optimization as the header loop.
            // `value_to_string` still allocates a small intermediate String
            // for the cell value (kept for clarity + because the Value →
            // String rendering is serde_json's job), but `csv_escape_into`
            // writes the escaped form directly into `out`'s reusable buffer.
            // For a 10K-row × 22-col export this eliminates ~220K per-cell
            // String allocations that the previous `csv_escape(&v)` call
            // produced.
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

/// In-place variant of [`value_to_string`] that writes the
/// rendered value directly into ``out`` without allocating an intermediate
/// ``String``. Used by [`json_to_csv`] to avoid ~220K per-cell allocations
/// on a 10K-row export.
pub(crate) fn value_to_string_into(out: &mut String, v: &Value) {
    use std::fmt::Write as _;
    match v {
        Value::String(s) => out.push_str(s),
        // `write!` into a `String` (via `std::fmt::Write`) writes directly
        // into the buffer's spare capacity — no intermediate `String`
        // allocation the way `n.to_string()` + `push_str` would.
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
/// H-12 (IMPROVE-2026-07-19): SEC-015 CSV formula-injection defense.
/// Cells starting with `=`, `+`, `-`, `@`, `\t`, or `\r` are prefixed
/// with a single quote `'` before quoting so spreadsheet apps (Excel,
/// LibreOffice) treat them as text rather than executing them as
/// formulas. Without this defense, a user who dictates `=cmd|'/C calc'!A1`
/// and then exports history to CSV would be vulnerable to formula
/// injection when opening the file in a spreadsheet.
///
/// Mirrors the Electron-side `csvEscape` in
/// `voice_typer/client/src/main/ipc/export-handlers.ts` — the two
/// implementations produce byte-identical output for the same input
/// (enforced by the TS parity test `export-handlers-csv-escape.test.ts`
/// and by the `test_csv_escape_*` cases in this module).
#[allow(dead_code)] // test-only: production path uses csv_escape_into (see doc above)
pub(crate) fn csv_escape(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    csv_escape_into(&mut out, s);
    out
}

/// In-place variant of [`csv_escape`] that writes the escaped
/// cell directly into ``out`` without allocating a per-cell ``String``.
/// Used by [`json_to_csv`] to avoid ~220K per-cell allocations on a 10K-row
/// export (one per cell × ~22 columns × 10K rows).
///
/// The bytes written are byte-for-byte identical to [`csv_escape`]; the
/// only difference is that the result is appended to ``out`` rather than
/// returned as a fresh ``String``.
pub(crate) fn csv_escape_into(out: &mut String, s: &str) {
    // SEC-015: prefix formula-injection-prone cells with a single quote.
    let needs_prefix = s.starts_with('=')
        || s.starts_with('+')
        || s.starts_with('-')
        || s.starts_with('@')
        || s.starts_with('\t')
        || s.starts_with('\r');
    // RFC 4180 quoting is required if the cell (after the optional prefix
    // is applied) contains a comma, double-quote, newline, or carriage
    // return. The prefix `'` is not itself a quoting trigger, so we check
    // the raw source string — equivalent to checking the prefixed value.
    let needs_quote = s.contains(',') || s.contains('"') || s.contains('\n') || s.contains('\r');
    if needs_quote {
        out.push('"');
        if needs_prefix {
            out.push('\'');
        }
        // Double any embedded double-quotes (RFC 4180 §2.7). Iterate
        // char-by-char to avoid the intermediate `String` that
        // `s.replace('"', "\"\"")` would allocate.
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

// Unit tests for `csv_escape`, `csv_escape_into`, `value_to_string`,
// `value_to_string_into`, `json_to_csv`, and the `atomic_write_bytes`
// contract live in the sibling `export_tests.rs` file (C-TEST-5 — keeps
// production source free of inline test code, matching the
// `commands/bubble/tests.rs` pattern). The module is wired as a child of
// `export` so the test file can use `use super::{...}` to access
// `pub(crate)` items.
#[cfg(test)]
#[path = "export_tests.rs"]
mod export_tests;
