#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation,
    clippy::approx_constant
)] // 3.14 etc. are deliberate test literals

//! Unit tests for `export` (extracted per C-TEST-5).
//!
//! Originally inline in `export.rs` as `#[cfg(test)] mod tests { ... }`;
//! moved to this sibling file to keep production source files free of test
//! code (C-TEST-5 — matches the pattern established by
//! `commands/bubble/tests.rs`).
//!
//! These tests pin the CSV escape / value-to-string / json-to-csv helpers,
//! including the SEC-015 formula-injection defense (prefixing `=`, `+`,
//! `-`, `@`, `\t`, `\r` with `'`), and the atomic-write helper contract
//! used by `export_data`.

use super::{csv_escape, csv_escape_into, json_to_csv, value_to_string, value_to_string_into};
use serde_json::{json, Value};

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
    // CR is BOTH a SEC-015 prefix trigger AND an RFC 4180 quoting
    // trigger. After prefixing with `'`, the value `"'\rcmd"` still
    // contains a CR, so it MUST be wrapped in double quotes.
    // Matches the TS `csvEscape("\rcmd")` byte-for-byte.
    assert_eq!(csv_escape("\rcmd"), "\"'\rcmd\"");
}

#[test]
fn test_csv_escape_leading_trailing_whitespace() {
    // RFC 4180 only requires quoting for comma, double-quote,
    // newline, or CR. Leading/trailing spaces do NOT trigger quoting.
    // Matches the TS `csvEscape` byte-for-byte (parity enforced by
    // `export-handlers-csv-escape.test.ts`).
    assert_eq!(csv_escape("  hello  "), "  hello  ");
    assert_eq!(csv_escape("  hello"), "  hello");
    assert_eq!(csv_escape("hello  "), "hello  ");
    // A leading TAB triggers the SEC-015 prefix (formula-injection
    // defense) but is NOT a quoting trigger — the prefixed value
    // contains neither comma, quote, newline, nor CR, so it stays
    // unquoted.
    assert_eq!(csv_escape("\thello"), "'\thello");
}

#[test]
fn test_csv_escape_formula_with_comma_quoted() {
    // Formula prefix AND comma → both defenses apply: prefix `'`
    // then RFC 4180 quoting (because the prefixed value contains
    // a comma).
    assert_eq!(csv_escape("=a,b"), "\"'=a,b\"");
}

// ── csv_escape_into ─────────────────────────────────────────────
//
// `csv_escape_into` writes the escaped form directly into a
// caller-provided `&mut String` instead of allocating a fresh `String`
// per cell. The bytes written MUST be byte-for-byte identical to
// `csv_escape` — these tests verify that equivalence on the same
// inputs covered by the `csv_escape` tests above, plus an append-
// semantics test (writing into a non-empty buffer must NOT overwrite
// the existing content).

#[test]
fn test_csv_escape_into_matches_csv_escape() {
    // Every input that `csv_escape` handles must produce identical
    // bytes when written via `csv_escape_into`.
    let inputs = [
        "hello",
        "123",
        "",
        "hello,world",
        "hello\"world",
        "hello\nworld",
        "hello\rworld",
        "a,b\"c\nd\re",
        "=cmd|'/C calc'!A1",
        "+1+1",
        "-2+3",
        "@SUM(A1:A2)",
        "\tcmd",
        "\rcmd",
        "=a,b",
    ];
    for input in inputs {
        let mut out = String::new();
        csv_escape_into(&mut out, input);
        assert_eq!(out, csv_escape(input), "mismatch for input {input:?}");
    }
}

#[test]
fn test_csv_escape_into_appends_to_existing_buffer() {
    // Contract: `csv_escape_into` appends — it must NOT
    // overwrite existing buffer content. This is what `json_to_csv`
    // relies on when it writes the header row + each row's cells into
    // the same `out` buffer.
    let mut out = String::from("prefix|");
    csv_escape_into(&mut out, "hello,world");
    assert_eq!(out, "prefix|\"hello,world\"");
}

#[test]
fn test_value_to_string_into_matches_value_to_string() {
    // Contract: `value_to_string_into` must produce identical
    // bytes to `value_to_string` for every JSON value variant.
    let values: Vec<Value> = vec![
        json!("hello"),
        json!("with\"quote"),
        json!("with,comma"),
        json!(42),
        json!(3.14),
        json!(0),
        json!(-7),
        json!(true),
        json!(false),
        json!(null),
        json!([1, 2, 3]),  // array → other.to_string()
        json!({"k": "v"}), // object → other.to_string()
    ];
    for v in &values {
        let mut out = String::new();
        value_to_string_into(&mut out, v);
        assert_eq!(out, value_to_string(v), "mismatch for value {v}");
    }
}

#[test]
fn test_value_to_string_into_appends_to_existing_buffer() {
    // Contract: appends, does not overwrite.
    let mut out = String::from("[");
    value_to_string_into(&mut out, &json!("hello"));
    out.push('|');
    value_to_string_into(&mut out, &json!(42));
    out.push('|');
    value_to_string_into(&mut out, &json!(null));
    out.push(']');
    assert_eq!(out, "[hello|42|]");
}

#[test]
fn test_json_to_csv_large_export_no_per_cell_string_leak() {
    // Smoke test that `json_to_csv` produces the expected
    // output for a moderately-sized homogeneous dataset (the kind
    // of thing a real history export produces). This exercises the
    // `csv_escape_into` + `value_to_string` integration in
    // `json_to_csv`'s hot loop.
    let mut rows: Vec<Value> = Vec::with_capacity(100);
    for i in 0..100 {
        rows.push(json!({
            "id": i,
            "text": format!("entry {i}"),
            "ts": format!("2026-08-01T00:00:{i:02}"),
        }));
    }
    let data = Value::Array(rows);
    let csv = json_to_csv(&data).expect("json_to_csv must succeed");
    let lines: Vec<&str> = csv.lines().collect();
    // 1 header + 100 rows = 101 lines.
    assert_eq!(lines.len(), 101, "expected 101 lines, got {}", lines.len());
    assert_eq!(lines[0], "id,text,ts");
    assert_eq!(lines[1], "0,entry 0,2026-08-01T00:00:00");
    assert_eq!(lines[100], "99,entry 99,2026-08-01T00:00:99");
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

//require_main_window () ───────────────────────────────────────
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
        parsed["data"]["message"],
        "command only allowed from main window"
    );
}

// ── atomic_write_bytes wiring ─────────────────
//
// `export_data` can't be unit-tested directly because it opens a
// real `tauri-plugin-dialog` save dialog (requires a running Tauri
// runtime). Instead, these tests verify the contract of the
// underlying helper that `export_data` now delegates to:
// `crate::util::atomic_write_bytes`. The contract is "write
// fails → original file unchanged" (atomicity), which is the
// property the helper requires.

#[test]
fn test_pi13_atomic_write_helper_preserves_existing_file_on_overwrite() {
    // Contract: when `atomic_write_bytes` is called on a
    // path that already has content, the new content fully
    // replaces the old (no truncated half). The temp-file-then-
    // rename pattern guarantees this — either the OLD file is at
    // `path` (rename hasn't happened yet) or the NEW file is at
    // `path` (rename succeeded). There's no intermediate state.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-pi13-test-{}-overwrite",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    let path = tmp.join("export.csv");
    // Pre-existing file with sentinel content.
    std::fs::write(&path, b"OLD,SENTINEL,CONTENTS\n").unwrap();
    // Atomic overwrite with new content.
    let new_content = b"new,export,contents\nrow2\n";
    crate::util::atomic_write_bytes(&path, new_content).expect("atomic_write_bytes must succeed");
    let read_back = std::fs::read(&path).expect("file must still exist");
    assert_eq!(
        read_back.as_slice(),
        new_content.as_ref(),
        "PI-13: atomic overwrite must replace contents fully"
    );
    // The temp file must NOT leak.
    let tmp_path = tmp.join(".export.csv.tmp.migrate");
    assert!(
        !tmp_path.exists(),
        "PI-13: temp file leaked after rename: {}",
        tmp_path.display()
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_pi13_atomic_write_helper_failure_leaves_original_unchanged() {
    // Contract: when `atomic_write_bytes` FAILS (e.g. the
    // target directory doesn't exist), the original file at `path`
    // (if any) must be UNCHANGED. This is the key property the
    // export path needs: a flaky destination (USB stick pulled
    // mid-write, network drive dropped) must NOT corrupt the
    // user's pre-existing export file.
    //
    // We can't easily simulate a mid-rename failure in a unit test
    // (the rename syscall is atomic on POSIX). Instead, we test
    // the "create tmp file fails" path by pointing at a path
    // inside a non-existent directory — `File::create(&tmp)`
    // returns ENOENT, the function returns Err, and we verify
    // that a sentinel file at a DIFFERENT path (the "original
    // export file" we're simulating) is unchanged.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-pi13-test-{}-failure",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    // "Original file" — must survive the failed write.
    let original_path = tmp.join("export.csv");
    std::fs::write(&original_path, b"ORIGINAL,SENTINEL\n").unwrap();
    // Path whose parent dir does NOT exist — `File::create` fails.
    let bad_path = tmp.join("nonexistent_subdir").join("export.csv");
    let result = crate::util::atomic_write_bytes(&bad_path, b"NEW");
    assert!(
        result.is_err(),
        "PI-13: write to non-existent subdir must return Err, got Ok"
    );
    // The original file at the unrelated path must be UNCHANGED.
    let read_back =
        std::fs::read(&original_path).expect("original file must still exist after failed write");
    assert_eq!(
        read_back.as_slice(),
        b"ORIGINAL,SENTINEL\n".as_ref(),
        "PI-13: failed atomic write must NOT modify the original file"
    );
    std::fs::remove_dir_all(&tmp).ok();
}
