//! Defense-in-depth config-secret scrubbing library.
//!
//! The Python sidecar is contractually responsible for redacting API
//! keys / secrets / tokens BEFORE the config payload reaches the Rust
//! `export_config` command (see
//! `voice_typer/server/credential_store/_redact.py` + `_redact_sensitive`).
//! This module adds a Rust-side redaction pass as defense-in-depth: if a
//! future sidecar refactor or a custom build of the renderer forgets to
//! redact, the Rust host still scrubs obvious secret-shaped keys before
//! writing the JSON to disk. Without this, any regression in the Python
//! redaction path silently leaks API keys into the user's chosen export
//! file (a GDPR / security incident).
//!
//! The key-matching pattern is `(?i)(api[_-]?key|secret|token|password|
//! passwd|pwd|credential|auth)` — the same shape as the Python
//! `_secrets._KEY_PATTERNS` allowlist, applied case-insensitively as a
//! substring match (the regex is unanchored, so `my_api_key_v2` and
//! `X-Auth-Token` both match). It is implemented without the `regex`
//! crate (substring check on the lowercased key) to avoid pulling a new
//! dependency into the Tauri host — the pattern is simple enough that
//! hand-rolling the matcher is cleaner than adding `regex` to
//! `Cargo.toml`.
//!
//! When a redaction fires, this module logs at `warn` so it lands in the
//! rotating log file for post-mortem diagnosis (a redaction firing at
//! this layer means the Python sidecar's redaction FAILED — that's a
//! bug worth investigating).
//!
//! Consumers: `super::export::export_config` (the only production
//! caller) and the sibling `system_cmds_tests.rs`.

use serde_json::Value;

/// Marker value substituted in place of redacted secrets. Matches the
/// Python sidecar's `_REDACTED` literal shape so the exported JSON is
/// consistent regardless of which layer did the redaction.
pub(crate) const REDACTED_MARKER: &str = "***REDACTED***";

/// Return `true` if `key` matches the redaction pattern
/// `(?i)(api[_-]?key|secret|token|password|passwd|pwd|credential|auth)`
/// (case-insensitive substring match — the regex is unanchored).
pub(crate) fn is_sensitive_key(key: &str) -> bool {
    let k = key.to_ascii_lowercase();
    // `api[_-]?key` → "api_key", "api-key", "apikey" (and any
    // key containing those as a substring, e.g. "openai_api_key").
    // We check all three spellings because the regex `[_-]?` makes
    // the separator optional.
    if k.contains("api_key") || k.contains("api-key") || k.contains("apikey") {
        return true;
    }
    // The remaining alternatives are plain substring checks. Using
    // `contains` (not `==`) so keys like "auth_token" or
    // "client_secret_v2" still match — same semantics as the
    // unanchored regex.
    k.contains("secret")
        || k.contains("token")
        || k.contains("password")
        || k.contains("passwd")
        || k.contains("pwd")
        || k.contains("credential")
        || k.contains("auth")
}

/// Walk a JSON `Value` recursively and replace every value whose key
/// matches [`is_sensitive_key`] with the [`REDACTED_MARKER`] literal.
/// Mutates `value` in place. Returns the count of redactions performed
/// so the caller can log a single summary line (and so tests can assert
/// the count).
///
/// Recurses into objects and arrays. Non-container values (strings,
/// numbers, bools, null) are leaves — they're only redacted if their
/// PARENT key is sensitive (handled by the parent's iteration).
pub(crate) fn redact_config_secrets(value: &mut Value) -> usize {
    let mut count = 0usize;
    redact_config_secrets_inner(value, None, &mut count);
    count
}

/// Recursive helper. `parent_key` is the key under which `value`
/// lives (None at the root) — used to decide whether to replace
/// `value` wholesale (if the parent key is sensitive) or to recurse
/// into it.
fn redact_config_secrets_inner(value: &mut Value, parent_key: Option<&str>, count: &mut usize) {
    // If the parent key is sensitive, replace this value wholesale
    // with the redaction marker (regardless of type — a secret could
    // be a string, number, bool, or even a nested object the sidecar
    // failed to scrub).
    if let Some(key) = parent_key {
        if is_sensitive_key(key) {
            // Only redact non-null values — redacting `null` would
            // be a false positive (a sensitive key with no value is
            // not a leak). This also avoids logging a `warn` line
            // for empty secrets, which would be noise.
            if !value.is_null() {
                log::warn!(
                    "[REDACT-DEFENSE] redacted sensitive config key {:?} (value type: {}) — \
                     Python-side redaction may have missed this",
                    key,
                    match value {
                        Value::String(_) => "string",
                        Value::Number(_) => "number",
                        Value::Bool(_) => "bool",
                        Value::Array(_) => "array",
                        Value::Object(_) => "object",
                        Value::Null => "null",
                    }
                );
                *value = Value::String(REDACTED_MARKER.to_string());
                *count += 1;
            }
            return;
        }
    }

    // Otherwise recurse into containers.
    match value {
        Value::Object(map) => {
            // Collect keys first to avoid borrow issues while mutating.
            let keys: Vec<String> = map.keys().cloned().collect();
            for key in keys {
                if let Some(child) = map.get_mut(&key) {
                    redact_config_secrets_inner(child, Some(&key), count);
                }
            }
        }
        Value::Array(arr) => {
            // Array elements have no key — pass None so they're only
            // redacted if their value happens to be an object whose
            // OWN keys are sensitive (handled by the Object arm above
            // on the next recursion).
            for child in arr.iter_mut() {
                redact_config_secrets_inner(child, None, count);
            }
        }
        // Leaves: nothing to do (the parent-key check above already
        // handled redaction).
        _ => {}
    }
}
