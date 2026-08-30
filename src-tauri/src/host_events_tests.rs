//! Tests for `host_events.rs` — pure payload-parsing contracts (per
//! C-TEST-5, sibling test file; no Tauri runtime required).

use super::parse_notification;

#[test]
fn test_parses_title_and_message() {
    let raw = r#"{"title":"Model loaded","message":"Whisper small.en ready"}"#;
    let parsed = parse_notification(raw);
    assert_eq!(
        parsed,
        Some((
            "Model loaded".to_string(),
            "Whisper small.en ready".to_string()
        ))
    );
}

#[test]
fn test_ignores_extra_fields() {
    let raw =
        r#"{"title":"T","message":"M","duration_ms":5000,"critical":true,"click_path":"/models"}"#;
    let parsed = parse_notification(raw);
    assert_eq!(parsed, Some(("T".to_string(), "M".to_string())));
}

#[test]
fn test_missing_message_defaults_empty() {
    let raw = r#"{"title":"Only title"}"#;
    let parsed = parse_notification(raw);
    assert_eq!(parsed, Some(("Only title".to_string(), String::new())));
}

#[test]
fn test_both_fields_empty_is_rejected() {
    let raw = r#"{"title":"","message":""}"#;
    assert_eq!(parse_notification(raw), None);
}

#[test]
fn test_malformed_json_is_rejected() {
    assert_eq!(parse_notification("not json"), None);
    assert_eq!(parse_notification("[1,2,3]"), None);
    assert_eq!(parse_notification(""), None);
}
