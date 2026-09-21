//! Unit tests for the `renderer_log_error` bounded-payload serializer
//! (`cap_and_serialize_renderer_payload`).
//!
//! Pins the 8 KiB cap contract: byte-identical output to the former
//! serialize-then-truncate implementation, the `...[truncated]` marker
//! on overflow, NO marker on an exact-fit payload, and the
//! UTF-8 char-boundary floor that replaced the old `String::truncate`
//! panic on multi-byte payloads straddling the cap.

#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable
)]

use super::{
    cap_and_serialize_renderer_payload, format_renderer_log_line, parse_renderer_level,
    MAX_RENDERER_ERROR_PAYLOAD_BYTES,
};
use serde_json::{json, Value};

const CAP: usize = MAX_RENDERER_ERROR_PAYLOAD_BYTES;
const TRUNCATION_MARKER: &str = "...[truncated]";

/// A payload well under the cap serializes to its exact JSON form with
/// no marker and no truncation.
#[test]
fn test_small_payload_serializes_exactly_without_marker() {
    let payload = json!({"message": "boom", "stack": "at fn (a.ts:1)"});
    let out = cap_and_serialize_renderer_payload(&payload);
    assert!(!out.contains(TRUNCATION_MARKER));
    assert_eq!(out, serde_json::to_string(&payload).unwrap());
    assert!(out.len() < CAP);
}

/// A serialized payload of EXACTLY 8 KiB bytes must NOT be truncated —
/// the original code truncated only when `len > cap`, and the bounded
/// writer must agree (an off-by-one here would append a marker to
/// perfectly-sized payloads).
#[test]
fn test_exact_cap_payload_not_truncated() {
    // A bare JSON string serializes to 2 quote bytes + content, so
    // 8190 `a` chars → exactly 8192 serialized bytes.
    assert_eq!(CAP, 8192);
    let payload = Value::String("a".repeat(CAP - 2));
    let full = serde_json::to_string(&payload).unwrap();
    assert_eq!(
        full.len(),
        CAP,
        "test setup: payload must serialize to exactly the cap"
    );
    let out = cap_and_serialize_renderer_payload(&payload);
    assert_eq!(out, full, "exact-cap payload must pass through untouched");
    assert!(!out.contains(TRUNCATION_MARKER));
}

/// One byte over the cap → truncated to the cap + marker. The
/// truncated prefix must equal the first `cap` bytes of the full
/// serialization (byte-identical to the former truncate-then-mark
/// output).
#[test]
fn test_over_cap_payload_truncated_with_marker() {
    // 8191 `a` chars → 8193 serialized bytes (1 over the cap).
    let payload = Value::String("a".repeat(CAP - 1));
    let full = serde_json::to_string(&payload).unwrap();
    assert_eq!(full.len(), CAP + 1);
    let out = cap_and_serialize_renderer_payload(&payload);
    assert!(out.ends_with(TRUNCATION_MARKER));
    assert_eq!(out.len(), CAP + TRUNCATION_MARKER.len());
    // Truncated prefix == first CAP bytes of the full serialization.
    assert_eq!(&out[..CAP], &full[..CAP]);
    assert_eq!(&out[..CAP], &format!("\"{}", "a".repeat(CAP - 1)));
}

/// A payload far over the cap (nested object) is truncated + marked;
/// the buffered writer never allocates beyond the cap-sized prefix.
#[test]
fn test_far_over_cap_nested_payload_truncated() {
    let payload = json!({
        "message": "x".repeat(100_000),
        "stack": "at fn (a.ts:1)",
        "componentStack": "in App"
    });
    let full = serde_json::to_string(&payload).unwrap();
    assert!(
        full.len() > CAP * 2,
        "test setup: payload must far exceed the cap"
    );
    let out = cap_and_serialize_renderer_payload(&payload);
    assert!(out.ends_with(TRUNCATION_MARKER));
    // 100_000-char run also trips the 20+ alnum catch-all inside
    // redact_pii downstream, but THIS layer only caps, verify the
    // prefix is the raw serialization (redaction happens in the logger).
    assert_eq!(&out[..CAP], &full[..CAP]);
}

/// UTF-8 safety: when the 8 KiB boundary lands inside a multi-byte
/// char, the old `String::truncate(cap)` PANICKED. The bounded writer
/// must floor the prefix to the nearest char boundary instead, valid
/// UTF-8 out, marker appended, no panic.
#[test]
fn test_multibyte_char_straddling_cap_floors_to_char_boundary() {
    // Serialization: `"` + 8190 `a` + `é`(2 bytes) + `é`(2 bytes) + `"`.
    // The 8 KiB boundary (8192) lands INSIDE the second `é` (bytes
    // 8191..8193): exactly the input that panicked the old code.
    let payload = Value::String(format!("{}éé", "a".repeat(CAP - 2)));
    let full = serde_json::to_string(&payload).unwrap();
    assert_eq!(full.len(), CAP + 4, "test setup: prefix math");
    assert!(
        !full.is_char_boundary(CAP),
        "test setup: boundary must split a char"
    );

    let out = cap_and_serialize_renderer_payload(&payload); // must not panic
    assert!(out.ends_with(TRUNCATION_MARKER));
    assert!(
        out.len() < CAP + TRUNCATION_MARKER.len(),
        "prefix floored below the cap"
    );
    // Floored prefix = `"` + 8190 `a` + first `é` = 1 + 8190 + 2 bytes.
    assert_eq!(out.len(), (CAP - 1) + TRUNCATION_MARKER.len());
    assert_eq!(&out[..CAP - 1], &full[..CAP - 1]);
    // The output is a strict prefix of the full serialization.
    assert!(full.starts_with(&out[..out.len() - TRUNCATION_MARKER.len()]));
}

/// A multibyte payload that fits under the cap passes through
/// byte-exact (non-ASCII is emitted unescaped by serde_json).
#[test]
fn test_multibyte_payload_under_cap_passes_through() {
    let payload = Value::String("héllo wörld — 日本語 🎙".to_string());
    let out = cap_and_serialize_renderer_payload(&payload);
    assert_eq!(out, serde_json::to_string(&payload).unwrap());
    assert!(!out.contains(TRUNCATION_MARKER));
}

/// Empty object / null payloads: tiny edge inputs stay byte-exact.
#[test]
fn test_empty_and_null_payloads() {
    assert_eq!(cap_and_serialize_renderer_payload(&json!({})), "{}");
    assert_eq!(cap_and_serialize_renderer_payload(&Value::Null), "null");
}

// ── canonical line formatting ──────────────────────────

/// An error payload with a message + structured location renders as the
/// canonical, grep-able line: tag, message, `(src=file:line:col)`,
/// scope. No JSON braces, no `[RENDERER_ERROR]` blob.
#[test]
fn test_format_error_payload_is_canonical_line() {
    let payload = json!({
        "level": "error",
        "scope": "react-error-boundary",
        "message": "Cannot read properties of undefined",
        "location": { "file": "App.tsx", "line": 42, "column": 7 },
        "stack": "Error: boom\n    at App (App.tsx:42:7)\n    at render"
    });
    let (level, line) = format_renderer_log_line(&payload);
    assert_eq!(level, log::Level::Error);
    assert!(line.starts_with("[renderer-error] Cannot read properties of undefined"));
    assert!(line.contains("(src=App.tsx:42:7)"), "line: {line}");
    assert!(line.contains("scope=react-error-boundary"), "line: {line}");
    assert!(line.contains("stack=Error: boom at App (App.tsx:42:7)"));
    assert!(!line.contains('{'), "no JSON blob: {line}");
    assert!(!line.contains('\n'), "single line: {line}");
}

/// The renderers send the predecessor-era `kind` field name (both
/// `globalErrorHandler` and the console capture). It MUST render as the
/// `scope=` fragment instead of being dropped as an unknown field, and
/// the explicit `level` must still select the tag, this is the exact
/// payload shape `console.warn` produces under Tauri.
#[test]
fn test_renderer_kind_field_aliases_scope() {
    let payload = json!({
        "level": "warn",
        "kind": "console.warn",
        "message": "Some UI problem"
    });
    let (level, line) = format_renderer_log_line(&payload);
    assert_eq!(level, log::Level::Warn);
    assert_eq!(line, "[renderer-warn] Some UI problem scope=console.warn");

    // An explicit `scope` still wins nothing / changes nothing: both
    // spellings map to the same rendered fragment.
    let with_scope = json!({"scope": "console.error", "message": "boom"});
    assert_eq!(
        format_renderer_log_line(&with_scope).1,
        "[renderer-error] boom scope=console.error"
    );
}

/// `warn` renders at WARN level with the `[renderer-warn]` tag; any
/// other / absent value stays ERROR (fail-loud).
#[test]
fn test_format_level_routing() {
    let warn = json!({"level": "warn", "message": "deprecated path"});
    assert_eq!(format_renderer_log_line(&warn).0, log::Level::Warn);
    assert!(format_renderer_log_line(&warn).1.starts_with("[renderer-warn] "));
    let weird = json!({"level": "verbose", "message": "x"});
    assert_eq!(format_renderer_log_line(&weird).0, log::Level::Error);
    let absent = json!({"message": "x"});
    assert_eq!(format_renderer_log_line(&absent).0, log::Level::Error);
    assert_eq!(parse_renderer_level(Some(" WARNING ")), log::Level::Warn);
    assert_eq!(parse_renderer_level(Some("Error")), log::Level::Error);
    assert_eq!(parse_renderer_level(None), log::Level::Error);
}

/// Multi-line messages and string locations are collapsed to one line
/// (the canonical template is one record per line).
#[test]
fn test_format_collapses_multiline_message_and_text_location() {
    let payload = json!({
        "message": "line one\n\tline two   line three",
        "location": "bundle.js:1:99"
    });
    let (_, line) = format_renderer_log_line(&payload);
    assert_eq!(
        line,
        "[renderer-error] line one line two line three (src=bundle.js:1:99)"
    );
}

/// A payload without a `message` field (unexpected shape) still lands,
/// via the bounded JSON fallback, so nothing is silently dropped.
#[test]
fn test_format_payload_without_message_falls_back_to_json() {
    let payload = json!({"detail": {"code": 7}, "extra": [1, 2]});
    let (level, line) = format_renderer_log_line(&payload);
    assert_eq!(level, log::Level::Error);
    assert_eq!(line, serde_json::to_string(&payload).unwrap());
}

/// The 8 KiB ceiling survives the switch from serialized JSON to a
/// formatted line: huge messages are truncated with the marker and the
/// result never exceeds the cap (marker included).
#[test]
fn test_format_caps_oversized_line_with_marker() {
    let payload = json!({
        "message": "m".repeat(50_000),
        "stack": "s".repeat(50_000)
    });
    let (_, line) = format_renderer_log_line(&payload);
    assert!(line.ends_with(TRUNCATION_MARKER), "marker expected");
    assert!(
        line.len() <= CAP,
        "capped line must never exceed the cap: {} > {}",
        line.len(),
        CAP
    );
}

/// UTF-8 safety at the cap boundary: a multi-byte char straddling the
/// truncation point must not panic and must stay valid UTF-8.
#[test]
fn test_format_cap_floors_to_char_boundary() {
    let payload = json!({ "message": format!("{}éé", "a".repeat(CAP)) });
    let (_, line) = format_renderer_log_line(&payload);
    assert!(line.ends_with(TRUNCATION_MARKER));
    assert!(line.len() <= CAP);
    assert!(line.is_char_boundary(line.len()));
}

/// The `renderer_log_error` Tauri command itself needs a live window to
/// construct (auto-injected params), so its decision surface is pinned
/// through the pure core above; the `require_main_window` guard is the
/// shared canonical helper already covered by
/// `system_cmds_tests.rs::test_set_host_locale_window_gate_uses_main_label_predicate`.
#[test]
fn test_cap_constant_matches_contract() {
    // 8 KiB: matches the doc contract (rich error report size).
    assert_eq!(MAX_RENDERER_ERROR_PAYLOAD_BYTES, 8 * 1024);
}
