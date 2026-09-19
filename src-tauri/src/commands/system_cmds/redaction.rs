
use serde_json::Value;

pub(crate) const REDACTED_MARKER: &str = "***REDACTED***";

pub(crate) fn is_sensitive_key(key: &str) -> bool {
    let k = key.to_ascii_lowercase();
    if k.contains("api_key") || k.contains("api-key") || k.contains("apikey") {
        return true;
    }
    k.contains("secret")
        || k.contains("token")
        || k.contains("password")
        || k.contains("passwd")
        || k.contains("pwd")
        || k.contains("credential")
        || k.contains("auth")
}

pub(crate) fn redact_config_secrets(value: &mut Value) -> usize {
    let mut count = 0usize;
    redact_config_secrets_inner(value, None, &mut count);
    count
}

fn redact_config_secrets_inner(value: &mut Value, parent_key: Option<&str>, count: &mut usize) {
    if let Some(key) = parent_key {
        if is_sensitive_key(key) {
            if !value.is_null() {
                log::warn!(
                    "[REDACT-DEFENSE] redacted sensitive config key {:?} (value type: {}), \
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
            for child in arr.iter_mut() {
                redact_config_secrets_inner(child, None, count);
            }
        }
        // Leaves: nothing to do (the parent-key check above already
        // handled redaction).
        _ => {}
    }
}
