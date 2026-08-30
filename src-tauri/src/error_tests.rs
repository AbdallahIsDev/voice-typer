//! Unit tests for the unified Tauri-host command error enum
//! (`crate::error::VoiceTyperError`).
//!
//! Two properties are golden-pinned for every variant:
//!
//! 1. **`Display`** — the log-facing string must be byte-identical to
//!    what the former `Result<_, String>` sites produced (the tray
//!    menu handler, the WS heartbeat task, and the bubble position
//!    persister `{}`-format the error into `log::warn!` lines — any
//!    drift changes the rotating-log format those consumers parse).
//! 2. **`Serialize`** — the renderer-facing wire payload must be a
//!    JSON STRING (`serde_json::to_value` → `Value::String`), because
//!    the renderer's `usePython.ts` normalizes ONLY
//!    `typeof err === "string"` rejections; an object payload would
//!    collapse to `"unknown IPC error"`. For envelope variants the
//!    string is the envelope JSON; for the `Server` passthrough the
//!    envelope is re-wrapped with the sidecar's `data` VERBATIM so
//!    structured fields (`errors[]`, `consent_field`, `engine_name`,
//!    `model_id`) reach the webview.
//!
//! The golden strings below pin serde_json's map-key ordering
//! (no `preserve_order` feature ⇒ keys sort alphabetically:
//! `"data"` before `"type"`) and the exact envelope payloads the
//! pre-enum inline `json!(...).to_string()` sites emitted.

use serde_json::{json, Value};

use crate::commands::sidecar_cmds::{
    DISALLOWED_COMMAND_CODE, DISALLOWED_WINDOW_CODE, PENDING_FULL_CODE,
};
use crate::error::VoiceTyperError;

/// Serialize the error the way Tauri's `InvokeError` does and assert
/// the result is a JSON string with the expected contents.
fn wire_string(err: &VoiceTyperError) -> String {
    let value = serde_json::to_value(err).expect("VoiceTyperError must serialize");
    match value {
        Value::String(s) => s,
        other => panic!(
            "VoiceTyperError must serialize to a JSON string (usePython.ts only \
             normalizes `typeof err === \"string\"` rejections); got: {other}"
        ),
    }
}

// ─── Plain host-condition variants: Display == wire string ────────────

#[test]
fn test_not_connected_display_and_wire_string() {
    let err = VoiceTyperError::NotConnected;
    assert_eq!(err.to_string(), "sidecar not connected");
    assert_eq!(wire_string(&err), "sidecar not connected");
}

#[test]
fn test_shutting_down_display_and_wire_string() {
    let err = VoiceTyperError::ShuttingDown;
    assert_eq!(err.to_string(), "sidecar shutting down");
    assert_eq!(wire_string(&err), "sidecar shutting down");
}

#[test]
fn test_timeout_display_and_wire_string() {
    // The 120s value is the model-lifecycle dispatch timeout
    // (`DISPATCH_TIMEOUT_SECS`); 15s is the short timeout. Both render
    // through the same format the former
    // `format!("dispatch timeout ({}s)", timeout_secs)` produced.
    let long = VoiceTyperError::Timeout { secs: 120 };
    assert_eq!(long.to_string(), "dispatch timeout (120s)");
    assert_eq!(wire_string(&long), "dispatch timeout (120s)");

    let short = VoiceTyperError::Timeout { secs: 15 };
    assert_eq!(short.to_string(), "dispatch timeout (15s)");
    assert_eq!(wire_string(&short), "dispatch timeout (15s)");
}

#[test]
fn test_channel_closed_display_and_wire_string() {
    let err = VoiceTyperError::ChannelClosed;
    assert_eq!(err.to_string(), "dispatch response channel closed");
    assert_eq!(wire_string(&err), "dispatch response channel closed");
}

#[test]
fn test_send_failed_display_and_wire_string() {
    // `message` is the `TrySendError`'s Display — the former inline
    // `format!("WS send failed: {e}")` concatenation.
    let err = VoiceTyperError::SendFailed {
        message: "channel is full".to_string(),
    };
    assert_eq!(err.to_string(), "WS send failed: channel is full");
    assert_eq!(wire_string(&err), "WS send failed: channel is full");
}

#[test]
fn test_host_variant_passes_string_through_verbatim() {
    let err = VoiceTyperError::Host("bubble window not found".to_string());
    assert_eq!(err.to_string(), "bubble window not found");
    assert_eq!(wire_string(&err), "bubble window not found");
}

#[test]
fn test_from_string_wraps_into_host_variant() {
    // Command fns rely on `?` + `From<String>` to convert the legacy
    // helper errors (`json_to_csv`, `resolve_cursor_monitor`, …) — the
    // renderer-visible string must survive the wrap byte-identically.
    let err: VoiceTyperError = "CSV export requires an array of objects".to_string().into();
    assert_eq!(wire_string(&err), "CSV export requires an array of objects");

    let err: VoiceTyperError = "bubble window not found".into();
    assert_eq!(wire_string(&err), "bubble window not found");
}

// ─── Envelope variants: Display == wire string == envelope JSON ───────

#[test]
fn test_pending_full_envelope_golden() {
    let err = VoiceTyperError::PendingFull;
    let expected = json!({
        "type": "error",
        "data": {
            "code": PENDING_FULL_CODE,
            "message": "Sidecar dispatch queue is full; please retry"
        }
    })
    .to_string();
    // Golden-pinned literal (keys sorted: "data" before "type" —
    // serde_json without `preserve_order` uses a BTreeMap).
    assert_eq!(
        expected,
        "{\"data\":{\"code\":\"pending_full\",\"message\":\
         \"Sidecar dispatch queue is full; please retry\"},\"type\":\"error\"}"
    );
    // Both the log-facing Display AND the wire payload are the envelope.
    assert_eq!(err.to_string(), expected);
    assert_eq!(wire_string(&err), expected);
    // The renderer's reject path JSON-parses this string and branches
    // on `code === "pending_full"` — verify the parsed shape too.
    let parsed: Value =
        serde_json::from_str(&wire_string(&err)).expect("envelope must be valid JSON");
    assert_eq!(parsed["type"], "error");
    assert_eq!(parsed["data"]["code"], PENDING_FULL_CODE);
    assert_eq!(
        parsed["data"]["message"],
        "Sidecar dispatch queue is full; please retry"
    );
}

#[test]
fn test_disallowed_command_envelope_golden() {
    let err = VoiceTyperError::DisallowedCommand;
    let expected = json!({
        "type": "error",
        "data": {
            "code": DISALLOWED_COMMAND_CODE,
            "message": "Command not in allowlist"
        }
    })
    .to_string();
    assert_eq!(
        expected,
        "{\"data\":{\"code\":\"disallowed_command\",\
         \"message\":\"Command not in allowlist\"},\"type\":\"error\"}"
    );
    assert_eq!(err.to_string(), expected);
    assert_eq!(wire_string(&err), expected);
}

#[test]
fn test_data_too_large_envelope_golden() {
    let err = VoiceTyperError::DataTooLarge;
    let expected = json!({
        "type": "error",
        "data": {
            "code": "data_too_large",
            "message": "dispatch data payload exceeds size cap"
        }
    })
    .to_string();
    assert_eq!(
        expected,
        "{\"data\":{\"code\":\"data_too_large\",\
         \"message\":\"dispatch data payload exceeds size cap\"},\"type\":\"error\"}"
    );
    assert_eq!(err.to_string(), expected);
    assert_eq!(wire_string(&err), expected);
}

#[test]
fn test_disallowed_window_envelope_golden_main_window_guard() {
    let err = VoiceTyperError::disallowed_main_window();
    let expected = json!({
        "type": "error",
        "data": {
            "code": DISALLOWED_WINDOW_CODE,
            "message": "command only allowed from main window"
        }
    })
    .to_string();
    assert_eq!(
        expected,
        "{\"data\":{\"code\":\"disallowed_window\",\
         \"message\":\"command only allowed from main window\"},\"type\":\"error\"}"
    );
    assert_eq!(err.to_string(), expected);
    assert_eq!(wire_string(&err), expected);
}

#[test]
fn test_disallowed_window_envelope_golden_bubble_window_guard() {
    let err = VoiceTyperError::disallowed_bubble_window();
    let expected = json!({
        "type": "error",
        "data": {
            "code": DISALLOWED_WINDOW_CODE,
            "message": "command only allowed from bubble window"
        }
    })
    .to_string();
    assert_eq!(
        expected,
        "{\"data\":{\"code\":\"disallowed_window\",\
         \"message\":\"command only allowed from bubble window\"},\"type\":\"error\"}"
    );
    assert_eq!(err.to_string(), expected);
    assert_eq!(wire_string(&err), expected);
}

// ─── Server variant: envelope passthrough vs log-facing Display ──────

#[test]
fn test_server_variant_display_is_flat_log_string() {
    // The log-facing Display keeps the pre-enum flat concat so the
    // tray / heartbeat `log::warn!("... {}", e)` lines don't shift.
    let err = VoiceTyperError::server_from_data(json!({
        "code": "server.internal_error",
        "message": "internal error"
    }));
    assert_eq!(
        err.to_string(),
        "server error [server.internal_error]: internal error"
    );
}

#[test]
fn test_server_variant_wire_is_envelope_passthrough() {
    // The wire payload re-wraps the sidecar's `data` VERBATIM — the
    // `code` + `message` survive AND any sibling fields ride along.
    let err = VoiceTyperError::server_from_data(json!({
        "code": "client.invalid_field",
        "message": "invalid field",
        "errors": ["sample_rate must be > 0", "channels must be 1 or 2"]
    }));
    let wire = wire_string(&err);
    let parsed: Value = serde_json::from_str(&wire).expect("passthrough must be valid JSON");
    assert_eq!(parsed["type"], "error");
    assert_eq!(parsed["data"]["code"], "client.invalid_field");
    assert_eq!(parsed["data"]["message"], "invalid field");
    assert_eq!(
        parsed["data"]["errors"],
        json!(["sample_rate must be > 0", "channels must be 1 or 2"])
    );
}

#[test]
fn test_server_variant_preserves_consent_fields_verbatim() {
    // The consent-gated engine path: `client.consent_required`
    // envelopes carry `consent_field` / `engine_name` / `model_id` so
    // the renderer can deep-link to the exact Settings toggle. The
    // former flat `"server error [code]: msg"` concat destroyed them.
    let err = VoiceTyperError::server_from_data(json!({
        "code": "client.consent_required",
        "message": "consent required",
        "consent_field": "voice_biometric_consent",
        "engine_name": "whisper",
        "model_id": null
    }));
    let parsed: Value =
        serde_json::from_str(&wire_string(&err)).expect("passthrough must be valid JSON");
    assert_eq!(parsed["data"]["consent_field"], "voice_biometric_consent");
    assert_eq!(parsed["data"]["engine_name"], "whisper");
    // `model_id: null` rides along as JSON null — the renderer maps
    // null → undefined (same normalization as the Electron path).
    assert_eq!(parsed["data"]["model_id"], Value::Null);
    // Display is still the flat log string.
    assert_eq!(
        err.to_string(),
        "server error [client.consent_required]: consent required"
    );
}

#[test]
fn test_server_variant_defaults_code_and_message_when_missing() {
    let err = VoiceTyperError::server_from_data(json!({}));
    assert_eq!(err.to_string(), "server error [unknown]: server error");
    let parsed: Value = serde_json::from_str(&wire_string(&err)).unwrap();
    assert_eq!(parsed["type"], "error");
    assert_eq!(parsed["data"], json!({}));
}

#[test]
fn test_server_from_data_non_object_falls_back_to_legacy_flat_string() {
    // Contract-violation edge: the sidecar answered `type:"error"`
    // with a missing / non-object `data`. The pre-enum code produced
    // the flat `"server error [unknown]: server error"` string — the
    // fallback keeps that byte-identical instead of emitting a
    // `{"type":"error","data":null}` envelope the renderer would
    // surface as raw JSON text.
    assert_eq!(
        VoiceTyperError::server_from_data(Value::Null).to_string(),
        "server error [unknown]: server error"
    );
    assert_eq!(
        wire_string(&VoiceTyperError::server_from_data(Value::Null)),
        "server error [unknown]: server error"
    );
    assert_eq!(
        wire_string(&VoiceTyperError::server_from_data(json!("boom"))),
        "server error [unknown]: server error"
    );
    // Non-string code / message values don't panic — the extraction
    // falls back to the defaults.
    assert_eq!(
        VoiceTyperError::server_from_data(json!({"code": 42, "message": true})).to_string(),
        "server error [unknown]: server error"
    );
}

#[test]
fn test_server_variant_wire_matches_direct_envelope_construction() {
    // Byte-identity guard: the passthrough wire string must equal
    // `json!({"type":"error","data":<data>}).to_string()` for the same
    // `data` — i.e. the re-wrap adds nothing, reorders nothing, and
    // drops nothing (the whole point of the fix).
    let data = json!({
        "code": "server.handler_error",
        "message": "handler failed",
        "extra": {"nested": [1, 2, 3]}
    });
    let err = VoiceTyperError::server_from_data(data.clone());
    assert_eq!(
        wire_string(&err),
        json!({ "type": "error", "data": data }).to_string()
    );
}
