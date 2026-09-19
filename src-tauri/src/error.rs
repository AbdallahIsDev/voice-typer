
use serde::{ser::Serializer, Serialize};
use serde_json::{json, Value};

use crate::commands::sidecar_cmds::{
    DISALLOWED_COMMAND_CODE, DISALLOWED_WINDOW_CODE, PENDING_FULL_CODE,
};


/// The `pending_full` backpressure envelope (dispatch pending-map cap).
fn pending_full_envelope() -> String {
    json!({
        "type": "error",
        "data": {
            "code": PENDING_FULL_CODE,
            "message": "Sidecar dispatch queue is full; please retry"
        }
    })
    .to_string()
}

/// The `disallowed_command` envelope (ALLOWED_COMMANDS gate rejection).
fn disallowed_command_envelope() -> String {
    json!({
        "type": "error",
        "data": {
            "code": DISALLOWED_COMMAND_CODE,
            "message": "Command not in allowlist"
        }
    })
    .to_string()
}

/// The `data_too_large` envelope (dispatch payload size-cap rejection).
fn data_too_large_envelope() -> String {
    json!({
        "type": "error",
        "data": {
            "code": "data_too_large",
            "message": "dispatch data payload exceeds size cap"
        }
    })
    .to_string()
}

fn disallowed_window_envelope(message: &str) -> String {
    json!({
        "type": "error",
        "data": {
            "code": DISALLOWED_WINDOW_CODE,
            "message": message
        }
    })
    .to_string()
}

// ─── The enum ─────────────────────────────────────────────────────────

#[derive(Debug, thiserror::Error)]
pub(crate) enum VoiceTyperError {
    /// `state.ws_tx` is `None`: the sidecar WS link is down (or the
    /// writer task exited, surfaced as `TrySendError::Closed`).
    #[error("sidecar not connected")]
    NotConnected,

    /// `state.shutting_down` is set: dispatch short-circuits so it
    /// can't orphan a pending entry in the shutdown window.
    #[error("sidecar shutting down")]
    ShuttingDown,

    #[error("dispatch timeout ({secs}s)")]
    Timeout { secs: u64 },

    /// The response oneshot sender was dropped without sending, the
    /// WS reader task exited mid-dispatch (sidecar crashed / WS closed).
    #[error("dispatch response channel closed")]
    ChannelClosed,

    /// `ws_tx.try_send` failed with the writer channel full.
    #[error("WS send failed: {message}")]
    SendFailed { message: String },

    /// The dispatch pending-map hit its capacity cap, a transient
    /// backpressure signal the renderer retries after backing off.
    #[error("{}", pending_full_envelope())]
    PendingFull,

    /// The renderer dispatched a command that is not in
    /// the Rust host allowlist (`allowlist.rs`; SEC-019
    /// defense-in-depth gate).
    #[error("{}", disallowed_command_envelope())]
    DisallowedCommand,

    /// The dispatch data payload exceeded the Rust-side size cap
    /// (bounded before the WS frame is built and enqueued).
    #[error("{}", data_too_large_envelope())]
    DataTooLarge,

    #[error("{}", disallowed_window_envelope(message))]
    DisallowedWindow {
        /// The guard-specific message carried in the envelope.
        message: &'static str,
    },

    #[error("server error [{code}]: {message}")]
    Server {
        /// Extracted `data.code` (defaults to `"unknown"`).
        code: String,
        /// Extracted `data.message` (defaults to `"server error"`).
        message: String,
        /// The envelope's `data` payload, verbatim.
        data: Value,
    },

    #[error("{0}")]
    Host(String),
}

impl VoiceTyperError {
    /// Rejection for a call whose invoking window is not the main
    /// window (`commands::require_main_window`).
    pub(crate) fn disallowed_main_window() -> Self {
        VoiceTyperError::DisallowedWindow {
            message: "command only allowed from main window",
        }
    }

    /// Rejection for a call whose invoking window is not the bubble
    /// window (`commands::require_bubble_window`).
    pub(crate) fn disallowed_bubble_window() -> Self {
        VoiceTyperError::DisallowedWindow {
            message: "command only allowed from bubble window",
        }
    }

    pub(crate) fn server_from_data(data: Value) -> Self {
        if data.is_object() {
            let code = data
                .get("code")
                .and_then(Value::as_str)
                .unwrap_or("unknown")
                .to_string();
            let message = data
                .get("message")
                .and_then(Value::as_str)
                .unwrap_or("server error")
                .to_string();
            VoiceTyperError::Server {
                code,
                message,
                data,
            }
        } else {
            VoiceTyperError::Host("server error [unknown]: server error".to_string())
        }
    }
}

impl From<String> for VoiceTyperError {
    fn from(s: String) -> Self {
        VoiceTyperError::Host(s)
    }
}

impl From<&str> for VoiceTyperError {
    fn from(s: &str) -> Self {
        VoiceTyperError::Host(s.to_string())
    }
}

// ─── Wire serialization ───────────────────────────────────────────────

impl Serialize for VoiceTyperError {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            VoiceTyperError::Server { data, .. } => {
                serializer.serialize_str(&json!({ "type": "error", "data": data }).to_string())
            }
            _ => serializer.serialize_str(&self.to_string()),
        }
    }
}
