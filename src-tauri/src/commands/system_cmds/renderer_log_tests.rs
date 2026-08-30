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

use super::{cap_and_serialize_renderer_payload, MAX_RENDERER_ERROR_PAYLOAD_BYTES};
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
    // redact_pii downstream, but THIS layer only caps — verify the
    // prefix is the raw serialization (redaction happens in the logger).
    assert_eq!(&out[..CAP], &full[..CAP]);
}

/// UTF-8 safety: when the 8 KiB boundary lands inside a multi-byte
/// char, the old `String::truncate(cap)` PANICKED. The bounded writer
/// must floor the prefix to the nearest char boundary instead — valid
/// UTF-8 out, marker appended, no panic.
#[test]
fn test_multibyte_char_straddling_cap_floors_to_char_boundary() {
    // Serialization: `"` + 8190 `a` + `é`(2 bytes) + `é`(2 bytes) + `"`.
    // The 8 KiB boundary (8192) lands INSIDE the second `é` (bytes
    // 8191..8193) — exactly the input that panicked the old code.
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

/// The `renderer_log_error` Tauri command itself needs a live window to
/// construct (auto-injected params), so its decision surface is pinned
/// through the pure core above; the `require_main_window` guard is the
/// shared canonical helper already covered by
/// `system_cmds_tests.rs::test_set_host_locale_window_gate_uses_main_label_predicate`.
#[test]
fn test_cap_constant_matches_contract() {
    // 8 KiB — matches the doc contract (rich error report size).
    assert_eq!(MAX_RENDERER_ERROR_PAYLOAD_BYTES, 8 * 1024);
}
