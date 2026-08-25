//! Unified error type for the Tauri host's `#[tauri::command]` surface.
//!
//! Every Tauri command the renderer can `invoke` rejects with a
//! [`VoiceTyperError`]. The type has two deliberately different
//! representations:
//!
//! - **`Display`** — the human/log-facing string. It preserves the
//!   exact strings the host emitted before this enum existed
//!   (`"sidecar not connected"`, `"dispatch timeout (120s)"`,
//!   `"server error [<code>]: <message>"`, and the JSON envelope
//!   strings for the envelope-shaped rejections), so log consumers
//!   (the tray menu handler, the WS heartbeat task, the bubble
//!   position persister — all of which only ever `{}`-format the
//!   error) see byte-identical lines.
//! - **`Serialize`** — the renderer-facing wire payload. Tauri v2's
//!   `InvokeError` serializes the rejection value with
//!   `serde_json::to_value`, and the renderer's `usePython.ts`
//!   normalizes ONLY `typeof err === "string"` rejections (anything
//!   else collapses to `"unknown IPC error"`). The impl therefore
//!   emits `serialize_str` — a JSON *string* whose contents are
//!   either a plain message or the serialized
//!   `{"type":"error","data":{...}}` envelope, matching what the
//!   former `Err(json!(...).to_string())` sites put on the wire.
//!
//! Envelope rejections are built with the same `json!(...).to_string()`
//! construction the former inline sites used, so the wire bytes are
//! identical by construction (serde_json without `preserve_order`
//! emits map keys in sorted order — `"data"` before `"type"` — and
//! both the old and new paths share that ordering).
//!
//! Envelope codes are single-sourced from the `allowlist.rs` constants
//! (`DISALLOWED_COMMAND_CODE`, `DISALLOWED_WINDOW_CODE`,
//! `PENDING_FULL_CODE`) — no inline code literals here.
//!
//! The `Server` variant is the error-envelope passthrough: when the
//! Python sidecar answers a dispatch with `{"type":"error","data":{...}}`,
//! the host re-emits the envelope VERBATIM (only wrapping it in
//! `{"type":"error","data":<data>}` at serialization) so structured
//! payload fields the renderer branches on — `data.errors[]`,
//! `consent_field`, `engine_name`, `model_id` — survive the hop.
//! Before this variant existed the host flattened the envelope to
//! `"server error [<code>]: <message>"`, destroying those fields.

use serde::{ser::Serializer, Serialize};
use serde_json::{json, Value};

use crate::commands::sidecar_cmds::{
    DISALLOWED_COMMAND_CODE, DISALLOWED_WINDOW_CODE, PENDING_FULL_CODE,
};

// ─── Envelope builders ────────────────────────────────────────────────
//
// Private helpers that construct the error-envelope JSON strings. They
// are shared between `Display` (via the `thiserror` trailing format
// args) and `Serialize` (which routes non-`Server` variants through
// `Display`), so each envelope exists in exactly ONE place.

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

/// The `disallowed_window` envelope (window-origin guard rejection).
/// `message` distinguishes the main-window guard from the bubble-window
/// guard — both share the `disallowed_window` code.
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

/// Host-side error for every `#[tauri::command]` the renderer can invoke.
///
/// See the module docs for the `Display` vs `Serialize` split. Variants:
/// - plain host conditions (`NotConnected`, `ShuttingDown`, `Timeout`,
///   `ChannelClosed`, `SendFailed`) — `Display` and wire string are the
///   same plain message the host emitted pre-enum.
/// - envelope rejections (`PendingFull`, `DisallowedCommand`,
///   `DataTooLarge`, `DisallowedWindow`) — both `Display` and wire
///   string are the envelope JSON (log lines and renderer payload stay
///   identical to the former `Err(err.to_string())` sites).
/// - `Server` — the sidecar error-envelope passthrough (see module
///   docs); `Display` is the log-facing `"server error [code]: message"`
///   while the wire payload re-wraps the envelope verbatim.
/// - `Host` — catch-all for legacy formatted-string errors that have no
///   more specific variant (kept byte-identical for the renderer).
#[derive(Debug, thiserror::Error)]
pub(crate) enum VoiceTyperError {
    /// `state.ws_tx` is `None` — the sidecar WS link is down (or the
    /// writer task exited, surfaced as `TrySendError::Closed`).
    #[error("sidecar not connected")]
    NotConnected,

    /// `state.shutting_down` is set — dispatch short-circuits so it
    /// can't orphan a pending entry in the shutdown window.
    #[error("sidecar shutting down")]
    ShuttingDown,

    /// The per-command dispatch deadline elapsed before the sidecar
    /// answered; the pending entry was removed.
    #[error("dispatch timeout ({secs}s)")]
    Timeout { secs: u64 },

    /// The response oneshot sender was dropped without sending — the
    /// WS reader task exited mid-dispatch (sidecar crashed / WS closed).
    #[error("dispatch response channel closed")]
    ChannelClosed,

    /// `ws_tx.try_send` failed with the writer channel full.
    #[error("WS send failed: {message}")]
    SendFailed { message: String },

    /// The dispatch pending-map hit its capacity cap — a transient
    /// backpressure signal the renderer retries after backing off.
    #[error("{}", pending_full_envelope())]
    PendingFull,

    /// The renderer dispatched a command that is not in
    /// `ALLOWED_COMMANDS` (defense-in-depth gate, mirrors the Electron
    /// renderer-side allowlist).
    #[error("{}", disallowed_command_envelope())]
    DisallowedCommand,

    /// The dispatch data payload exceeded the Rust-side size cap
    /// (bounded before the WS frame is built and enqueued).
    #[error("{}", data_too_large_envelope())]
    DataTooLarge,

    /// A window-origin guard rejected the call — the invoking window's
    /// label is not the one the command is restricted to. `message` is
    /// the guard's exact human message ("command only allowed from
    /// main window" / "command only allowed from bubble window").
    #[error("{}", disallowed_window_envelope(message))]
    DisallowedWindow {
        /// The guard-specific message carried in the envelope.
        message: &'static str,
    },

    /// The Python sidecar answered a dispatch with a `type:"error"`
    /// envelope. `data` is that envelope's `data` payload, passed
    /// through VERBATIM at serialization; `code` and `message` are
    /// extracted copies for the log-facing `Display`.
    #[error("server error [{code}]: {message}")]
    Server {
        /// Extracted `data.code` (defaults to `"unknown"`).
        code: String,
        /// Extracted `data.message` (defaults to `"server error"`).
        message: String,
        /// The envelope's `data` payload, verbatim.
        data: Value,
    },

    /// Catch-all for miscellanous host-side failures (I/O, encoding,
    /// dialog paths, join errors). The string is the exact renderer-
    /// visible message the former `Result<_, String>` site produced.
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

    /// Wrap the `data` payload of a sidecar `type:"error"` response.
    ///
    /// When `data` is a JSON object the result is the `Server` variant
    /// (envelope passthrough — every field in `data` reaches the
    /// renderer). When the sidecar violated the envelope contract
    /// (`data` missing / not an object), the result degrades to the
    /// legacy flat string `"server error [unknown]: server error"` —
    /// byte-identical to the host's pre-enum fallback so even the
    /// contract-violation path keeps its wire string.
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
            VoiceTyperError::Server { code, message, data }
        } else {
            VoiceTyperError::Host("server error [unknown]: server error".to_string())
        }
    }
}

// `?` conversion from the legacy `String` errors the un-migrated helper
// functions still return (`json_to_csv`, `resolve_cursor_monitor`,
// `hide_bubble_window`, geometry math, `.map_err(|e| e.to_string())`
// sites). Wrapping in `Host` keeps the renderer-visible string
// byte-identical.
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
    /// Serialize as a STRING payload (never a struct/object).
    ///
    /// Tauri's `InvokeError` runs `serde_json::to_value` on the
    /// rejection; `serialize_str` makes that value a `Value::String`,
    /// so the renderer's `invoke` promise rejects with a string — the
    /// only shape `usePython.ts` normalizes (`typeof err === "string"`
    /// is parsed as an error envelope; everything else becomes
    /// `"unknown IPC error"`).
    ///
    /// - `Server` → `{"type":"error","data":<data verbatim>}` — the
    ///   passthrough envelope, re-wrapped. All structured fields
    ///   (`errors[]`, `consent_field`, `engine_name`, `model_id`, …)
    ///   ride along untouched.
    /// - every other variant → its `Display` string (plain message or
    ///   envelope JSON — both already wire-shaped).
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        match self {
            VoiceTyperError::Server { data, .. } => serializer
                .serialize_str(&json!({ "type": "error", "data": data }).to_string()),
            _ => serializer.serialize_str(&self.to_string()),
        }
    }
}
