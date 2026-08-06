//! Unit tests for `system_cmds` (extracted per C-TEST-5).
//!
//! Originally inline in `system_cmds.rs` as `#[cfg(test)] mod tests { ... }`;
//! moved to this sibling file to keep production source files free of test
//! code (C-TEST-5 — matches the pattern established by
//! `commands/bubble/tests.rs`).
//!
//! These tests pin the defense-in-depth config-redaction logic
//! ([`is_sensitive_key`] + [`redact_config_secrets`]) that scrubs obvious
//! secret-shaped keys before the Rust host writes the JSON to disk, in
//! case the Python sidecar's own redaction path regresses.

use super::{is_sensitive_key, redact_config_secrets, REDACTED_MARKER};
use serde_json::json;

//is_sensitive_key ───────────────────────────────────────
//
// Pins the key-matching pattern: `(?i)(api[_-]?key|secret|token|
// password|passwd|pwd|credential|auth)` as a case-insensitive
// substring match.

#[test]
fn test_is_sensitive_key_api_key_variants() {
    // `api[_-]?key` matches all three spellings (and any key
    // containing them as a substring).
    assert!(is_sensitive_key("api_key"));
    assert!(is_sensitive_key("api-key"));
    assert!(is_sensitive_key("apikey"));
    assert!(is_sensitive_key("openai_api_key"));
    assert!(is_sensitive_key("OPENAI_API_KEY"));
    assert!(is_sensitive_key("X-Api-Key"));
    assert!(is_sensitive_key("provider_apikey_v2"));
}

#[test]
fn test_is_sensitive_key_secret_token_password() {
    assert!(is_sensitive_key("secret"));
    assert!(is_sensitive_key("client_secret"));
    assert!(is_sensitive_key("CLIENT_SECRET"));
    assert!(is_sensitive_key("token"));
    assert!(is_sensitive_key("auth_token"));
    assert!(is_sensitive_key("bearer_token"));
    assert!(is_sensitive_key("password"));
    assert!(is_sensitive_key("PASSWORD"));
    assert!(is_sensitive_key("user_password"));
    assert!(is_sensitive_key("passwd"));
    assert!(is_sensitive_key("pwd"));
    assert!(is_sensitive_key("credential"));
    assert!(is_sensitive_key("auth"));
    assert!(is_sensitive_key("authorization"));
    assert!(is_sensitive_key("X-Auth-Token"));
}

#[test]
fn test_is_sensitive_key_negatives() {
    // Keys that look sensitive but aren't (no substring match).
    assert!(!is_sensitive_key("api"));
    assert!(!is_sensitive_key("model"));
    assert!(!is_sensitive_key("language"));
    assert!(!is_sensitive_key("vocabulary"));
    assert!(!is_sensitive_key("backend"));
    assert!(!is_sensitive_key("history"));
    assert!(!is_sensitive_key("auto_punctuate"));
    assert!(!is_sensitive_key("tray_icon"));
    assert!(!is_sensitive_key(""));
}

//redact_config_secrets ──────────────────────────────────

#[test]
fn test_redact_config_secrets_flat_object() {
    let mut v = json!({
        "model": "small.en",
        "api_key": "sk-abc123",
        "language": "en",
        "password": "hunter2"
    });
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 2);
    assert_eq!(v["model"], "small.en");
    assert_eq!(v["api_key"], REDACTED_MARKER);
    assert_eq!(v["language"], "en");
    assert_eq!(v["password"], REDACTED_MARKER);
}

#[test]
fn test_redact_config_secrets_nested_object() {
    let mut v = json!({
        "providers": {
            "openai": {
                "api_key": "sk-abc123",
                "model": "gpt-4"
            },
            "anthropic": {
                "api_key": "sk-ant-xyz",
                "auth_token": "Bearer abc"
            }
        },
        "version": 2
    });
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 3);
    assert_eq!(v["providers"]["openai"]["api_key"], REDACTED_MARKER);
    assert_eq!(v["providers"]["openai"]["model"], "gpt-4");
    assert_eq!(v["providers"]["anthropic"]["api_key"], REDACTED_MARKER);
    assert_eq!(v["providers"]["anthropic"]["auth_token"], REDACTED_MARKER);
    assert_eq!(v["version"], 2);
}

#[test]
fn test_redact_config_secrets_array_of_objects() {
    let mut v = json!([
        {"id": 1, "api_key": "sk-1"},
        {"id": 2, "token": "tok-2"},
        {"id": 3, "name": "no secret here"}
    ]);
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 2);
    assert_eq!(v[0]["id"], 1);
    assert_eq!(v[0]["api_key"], REDACTED_MARKER);
    assert_eq!(v[1]["id"], 2);
    assert_eq!(v[1]["token"], REDACTED_MARKER);
    assert_eq!(v[2]["name"], "no secret here");
}

#[test]
fn test_redact_config_secrets_skips_null_values() {
    // A sensitive key with a null value is not a leak — don't
    // redact (and don't log a warn).
    let mut v = json!({
        "api_key": null,
        "secret": null,
        "model": "small.en"
    });
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 0, "null values should not be redacted");
    assert!(v["api_key"].is_null());
    assert!(v["secret"].is_null());
}

#[test]
fn test_redact_config_secrets_redacts_non_string_values() {
    // A secret could be a number (e.g. a numeric PIN) or bool —
    // redact regardless of type.
    let mut v = json!({
        "password": 12345,
        "pwd": true,
        "credential": ["nested", "array"]
    });
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 3);
    assert_eq!(v["password"], REDACTED_MARKER);
    assert_eq!(v["pwd"], REDACTED_MARKER);
    assert_eq!(v["credential"], REDACTED_MARKER);
}

#[test]
fn test_redact_config_secrets_case_insensitive_keys() {
    let mut v = json!({
        "API_KEY": "sk-1",
        "ApiKey": "sk-2",
        "PASSWORD": "pw"
    });
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 3);
    assert_eq!(v["API_KEY"], REDACTED_MARKER);
    assert_eq!(v["ApiKey"], REDACTED_MARKER);
    assert_eq!(v["PASSWORD"], REDACTED_MARKER);
}

#[test]
fn test_redact_config_secrets_empty_object() {
    let mut v = json!({});
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 0);
}

#[test]
fn test_redact_config_secrets_no_secrets() {
    let mut v = json!({
        "model": "small.en",
        "language": "en",
        "vocabulary": ["hello", "world"]
    });
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 0);
    // Verify the data is untouched.
    assert_eq!(v["model"], "small.en");
    assert_eq!(v["vocabulary"][0], "hello");
}

#[test]
fn test_redact_config_secrets_non_object_root() {
    // The root itself is not under any key — redaction only
    // applies to values whose parent KEY is sensitive. A bare
    // scalar root has nothing to redact.
    let mut v = json!("just a string");
    let count = redact_config_secrets(&mut v);
    assert_eq!(count, 0);
    assert_eq!(v, "just a string");
}
