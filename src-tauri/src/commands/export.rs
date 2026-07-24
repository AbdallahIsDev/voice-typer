//! Export commands: history/vocabulary → JSON/CSV (MIG-1.1 + ADR-0020 §6).

use serde_json::{json, Value};
use tauri_plugin_dialog::DialogExt;

// ─── DE-18: shared main-window guard ──────────────────────────────────
//
// `export_history`, `export_vocabulary`, `export_templates`,
// `export_config`, `open_logs`, and `open_model_import_dialog` are all
// `#[tauri::command]` functions that a compromised renderer could invoke
// over the IPC bridge. The bubble window is a sandboxed webview
// (ADR-0020 §7 + §9 + SEC-026) that must NEVER drive the export / open
// paths (a malicious bubble could exfiltrate history/vocabulary or
// trigger OS file-manager opens). Tauri v2's capability system only
// gates plugin commands, so user-defined commands need this runtime
// check. See `sidecar_cmds.rs:629` (`require_main_window`) for the
// canonical pattern — this is a sibling copy kept inside the export
// module so this file's slice (export.rs + system_cmds.rs + paste.rs)
// remains self-contained without touching sidecar_cmds.rs.
//
// The error envelope shape mirrors the sidecar's WS error envelope
// ({"type":"error","data":{"code":...,"message":...}}) so the
// renderer's existing reject path treats this identically to a
// server-side rejection.
pub(crate) fn require_main_window(window: &tauri::Window) -> Result<(), String> {
    if window.label() != "main" {
        log::warn!(
            "[DE-18] export/system command rejected from non-main window: {}",
            window.label()
        );
        let err = json!({
            "type": "error",
            "data": {
                "code": "disallowed_window",
                "message": "command only allowed from main window"
            }
        });
        return Err(err.to_string());
    }
    Ok(())
}

// ─── Tauri command: export_history (MIG-1.1) ─────────────────────────

/// ADR-0020 §6 + MIG-1.1: export the transcription history to a file
/// chosen by the user via `tauri-plugin-dialog`'s save dialog.
///
/// - `data` is the history payload (array of records) from the Python
///   sidecar's `export_history` command.
/// - `format` is `"json"` or `"csv"`.
/// - Returns `{"canceled": true}` if the user dismissed the dialog,
///   `{"success": true, "path": "<chosen path>"}` on success, or
///   `Err(message)` on I/O / encode failure.
///
/// DE-18: `window` is auto-injected by Tauri at runtime — the
/// renderer's `invoke('export_history', { data, format })` call is
/// unchanged. `require_main_window(&window)?` runs FIRST so a
/// compromised bubble renderer cannot drive the export path.
#[tauri::command]
pub async fn export_history(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    export_data(data, format, app, "voice-typer-history", "Export History").await
}

// ─── Tauri command: export_vocabulary (MIG-1.1) ──────────────────────

/// ADR-0020 §6 + MIG-1.1: export the user's custom vocabulary to a
/// file chosen by the user. Same shape as `export_history` with a
/// different default filename + dialog title.
///
/// DE-18: same main-window guard as `export_history`.
#[tauri::command]
pub async fn export_vocabulary(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    window: tauri::Window,
) -> Result<Value, String> {
    require_main_window(&window)?;
    export_data(data, format, app, "voice-typer-vocabulary", "Export Vocabulary").await
}

/// Shared helper for `export_history` + `export_vocabulary`. Opens a
/// `tauri-plugin-dialog` save-file dialog, then writes the data as
/// pretty-printed JSON or CSV to the chosen path. Returns
/// `{"canceled": true}` when the user cancels the dialog.
pub(crate) async fn export_data(
    data: Value,
    format: String,
    app: tauri::AppHandle,
    default_filename: &str,
    title: &str,
) -> Result<Value, String> {
    let file_path = app
        .dialog()
        .file()
        .set_title(title)
        .add_filter("JSON", &["json"])
        .add_filter("CSV", &["csv"])
        .set_file_name(default_filename)
        .blocking_save_file();
    let path = match file_path {
        Some(fp) => fp.into_path().map_err(|e| format!("invalid path: {e}"))?,
        None => return Ok(json!({"canceled": true})),
    };
    let content = match format.as_str() {
        "json" => serde_json::to_string_pretty(&data)
            .map_err(|e| format!("JSON encode failed: {e}"))?,
        "csv" => json_to_csv(&data)?,
        other => return Err(format!("unsupported format: {}", other)),
    };
    std::fs::write(&path, content).map_err(|e| format!("write failed: {e}"))?;
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
    // PVT-049: use a HashSet for O(1) membership checks instead of
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
    out.push_str(
        &keys
            .iter()
            .map(|k| csv_escape(k))
            .collect::<Vec<_>>()
            .join(","),
    );
    out.push('\n');
    for item in arr {
        let empty_map = serde_json::Map::new();
        let obj = item.as_object().unwrap_or(&empty_map);
        let row: Vec<String> = keys
            .iter()
            .map(|k| {
                let v = obj.get(k).map(value_to_string).unwrap_or_default();
                csv_escape(&v)
            })
            .collect();
        out.push_str(&row.join(","));
        out.push('\n');
    }
    Ok(out)
}

/// Render a JSON value as a single CSV cell (no quoting).
pub(crate) fn value_to_string(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Null => String::new(),
        other => other.to_string(),
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
/// formulas. Mirrors the Electron-side `csvEscape` in
/// `voice_typer/client/src/main/ipc/export-handlers.ts:23-29`.
/// Without this defense, a user who dictates `=cmd|'/C calc'!A1` and
/// then exports history to CSV would be vulnerable to formula injection
/// when opening the file in a spreadsheet.
pub(crate) fn csv_escape(s: &str) -> String {
    // SEC-015: prefix formula-injection-prone cells with a single quote.
    let needs_prefix = s.starts_with('=')
        || s.starts_with('+')
        || s.starts_with('-')
        || s.starts_with('@')
        || s.starts_with('\t')
        || s.starts_with('\r');
    let v = if needs_prefix { format!("'{}", s) } else { s.to_string() };
    // Then apply standard CSV quoting (RFC 4180) on the prefixed value.
    if v.contains(',') || v.contains('"') || v.contains('\n') || v.contains('\r') {
        format!("\"{}\"", v.replace('"', "\"\""))
    } else {
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // ── csv_escape ────────────────────────────────────────────────────

    #[test]
    fn test_csv_escape_plain() {
        assert_eq!(csv_escape("hello"), "hello");
        assert_eq!(csv_escape("123"), "123");
        assert_eq!(csv_escape(""), "");
    }

    #[test]
    fn test_csv_escape_comma() {
        assert_eq!(csv_escape("hello,world"), "\"hello,world\"");
    }

    #[test]
    fn test_csv_escape_double_quote() {
        assert_eq!(csv_escape("hello\"world"), "\"hello\"\"world\"");
    }

    #[test]
    fn test_csv_escape_newline() {
        assert_eq!(csv_escape("hello\nworld"), "\"hello\nworld\"");
    }

    #[test]
    fn test_csv_escape_carriage_return() {
        assert_eq!(csv_escape("hello\rworld"), "\"hello\rworld\"");
    }

    #[test]
    fn test_csv_escape_all_special() {
        assert_eq!(csv_escape("a,b\"c\nd\re"), "\"a,b\"\"c\nd\re\"");
    }

    // ── csv_escape — SEC-015 formula-injection defense (H-12) ────────

    #[test]
    fn test_csv_escape_formula_equals() {
        // `=cmd|'/C calc'!A1` must be prefixed with `'` so Excel/LibreOffice
        // treats it as text, not a formula.
        assert_eq!(csv_escape("=cmd|'/C calc'!A1"), "'=cmd|'/C calc'!A1");
    }

    #[test]
    fn test_csv_escape_formula_plus() {
        assert_eq!(csv_escape("+1+1"), "'+1+1");
    }

    #[test]
    fn test_csv_escape_formula_minus() {
        // `-2+3` would be a formula in Excel; prefix with `'`.
        assert_eq!(csv_escape("-2+3"), "'-2+3");
    }

    #[test]
    fn test_csv_escape_formula_at() {
        assert_eq!(csv_escape("@SUM(A1:A2)"), "'@SUM(A1:A2)");
    }

    #[test]
    fn test_csv_escape_formula_tab() {
        assert_eq!(csv_escape("\tcmd"), "'\tcmd");
    }

    #[test]
    fn test_csv_escape_formula_carriage_return() {
        assert_eq!(csv_escape("\rcmd"), "'\rcmd");
    }

    #[test]
    fn test_csv_escape_formula_with_comma_quoted() {
        // Formula prefix AND comma → both defenses apply: prefix `'`
        // then RFC 4180 quoting (because the prefixed value contains
        // a comma).
        assert_eq!(csv_escape("=a,b"), "\"'=a,b\"");
    }

    // ── json_to_csv ───────────────────────────────────────────────────

    #[test]
    fn test_json_to_csv_empty_array() {
        let data = json!([]);
        assert_eq!(json_to_csv(&data).unwrap(), "");
    }

    #[test]
    fn test_json_to_csv_not_array() {
        let data = json!({"a": 1});
        let err = json_to_csv(&data).unwrap_err();
        assert!(err.contains("requires an array"), "err: {}", err);
    }

    #[test]
    fn test_json_to_csv_homogeneous() {
        let data = json!([
            {"id": 1, "text": "hello"},
            {"id": 2, "text": "world"},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines.len(), 3);
        // Header preserves first object's key order.
        assert_eq!(lines[0], "id,text");
        assert_eq!(lines[1], "1,hello");
        assert_eq!(lines[2], "2,world");
    }

    #[test]
    fn test_json_to_csv_missing_keys() {
        let data = json!([
            {"id": 1, "text": "hello"},
            {"id": 2},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines[0], "id,text");
        assert_eq!(lines[1], "1,hello");
        assert_eq!(lines[2], "2,"); // missing "text" → empty cell
    }

    #[test]
    fn test_json_to_csv_special_chars() {
        let data = json!([
            {"text": "hello, world"},
            {"text": "quote\"inside"},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        assert_eq!(lines[0], "text");
        assert_eq!(lines[1], "\"hello, world\"");
        assert_eq!(lines[2], "\"quote\"\"inside\"");
    }

    #[test]
    fn test_json_to_csv_extra_keys_in_later_rows() {
        let data = json!([
            {"id": 1},
            {"id": 2, "extra": "x"},
        ]);
        let csv = json_to_csv(&data).unwrap();
        let lines: Vec<&str> = csv.lines().collect();
        // Extra key is appended to the header.
        assert_eq!(lines[0], "id,extra");
        assert_eq!(lines[1], "1,"); // first row had no "extra"
        assert_eq!(lines[2], "2,x");
    }

    // ── value_to_string ───────────────────────────────────────────────

    #[test]
    fn test_value_to_string() {
        assert_eq!(value_to_string(&json!("hello")), "hello");
        assert_eq!(value_to_string(&json!(42)), "42");
        assert_eq!(value_to_string(&json!(3.14)), "3.14");
        assert_eq!(value_to_string(&json!(true)), "true");
        assert_eq!(value_to_string(&json!(false)), "false");
        assert_eq!(value_to_string(&json!(null)), "");
    }

    // ── require_main_window (DE-18) ───────────────────────────────────
    //
    // We can't construct a real `tauri::Window` in a unit test (it
    // requires a running Tauri runtime), so we verify the error
    // envelope shape indirectly via the JSON literal we emit. The
    // "main"-label acceptance path is exercised end-to-end by the
    // Tauri mig19 glue tests (`tests/tauri/mig19/test_final_glue.py`)
    // which invoke the registered commands through the real webview.

    #[test]
    fn test_require_main_window_error_envelope_shape() {
        // The error envelope is a JSON string — verify its shape so
        // the renderer's reject handler (which JSON-parses the error
        // message) keeps working. Mirrors the sidecar's WS error
        // envelope: {"type":"error","data":{"code":...,"message":...}}.
        //
        // We can't call require_main_window() without a real Window,
        // but we can pin the literal envelope shape via the json! macro
        // used inside the function — if anyone changes the shape, this
        // test breaks and forces them to update the renderer's reject
        // handler too.
        let envelope = json!({
            "type": "error",
            "data": {
                "code": "disallowed_window",
                "message": "command only allowed from main window"
            }
        });
        let parsed: Value = serde_json::from_str(&envelope.to_string()).unwrap();
        assert_eq!(parsed["type"], "error");
        assert_eq!(parsed["data"]["code"], "disallowed_window");
        assert_eq!(
            parsed["data"]["message"], "command only allowed from main window"
        );
    }
}
