//! GT-21: structured error type for Tauri command handlers.
//!
//! Replaces the pervasive `Result<T, String>` shape with a typed
//! `Result<T, VoiceTyperError>` so the renderer can pattern-match on
//! error variants (e.g. retry on `SidecarDisconnected`, surface
//! `DialogCanceled` silently) instead of string-matching on the
//! error message.
//!
//! # Migration status (incremental)
//!
//! This module is the canonical error type going forward. The migration
//! is INCREMENTAL — only `bubble_show` + `bubble_signal_ready` are
//! migrated in this session as a proof-of-concept. The remaining ~38
//! command sites still return `Result<T, String>`; they are tagged
//! `// TODO(GT-21): migrate to VoiceTyperError` at the call site.
//! Future sessions can migrate them one module at a time without
//! breaking the renderer (Tauri's IPC layer serializes both
//! `Result<T, String>` and `Result<T, VoiceTyperError>` to the same
//! JSON shape — `{"error": "<Display impl>"}` — so the renderer's
//! reject path is identical for both).
//!
//! # Serialization
//!
//! `VoiceTyperError` derives `serde::Serialize` so Tauri's
//! `#[tauri::command]` macro accepts `Result<T, VoiceTyperError>` as
//! a return type. The serialized form is the variant name (e.g.
//! `"WindowNotFound"`, `"OsError"`) — the renderer can pattern-match
//! on the variant to drive UI behavior. The `Display` impl (via
//! `thiserror`) is used for `log::error!` and operator-facing
//! messages.

use std::io;
use thiserror::Error;

/// The structured error type for Voice Typer Tauri commands.
///
/// See the module-level docstring for the migration plan + rationale.
#[allow(dead_code)]
#[derive(Debug, Error, serde::Serialize)]
#[serde(tag = "kind", content = "message")]
pub enum VoiceTyperError {
    /// A Tauri webview window lookup returned `None` — the window
    /// label doesn't match any registered window (e.g. `bubble` was
    /// never created, or was already destroyed when the command ran).
    /// The string is the window label that was looked up.
    #[error("window not found: {0}")]
    WindowNotFound(String),

    /// An OS-level I/O or windowing error from `tauri::Error` /
    /// `std::io::Error`. Covers `.show()`, `.hide()`, `.set_position()`,
    /// `.set_focus()` failures, file I/O, etc.
    #[error("OS error: {0}")]
    OsError(String),

    /// The clipboard write or read failed (tauri-plugin-clipboard-manager
    /// or OS clipboard API returned an error). Used by `paste_text`'s
    /// clipboard-fallback path.
    #[error("clipboard operation failed: {0}")]
    ClipboardFailed(String),

    /// The user dismissed a save-file / open-folder dialog. NOT a
    /// real error — the renderer should treat this as a no-op rather
    /// than showing an error toast. Distinct from `OsError` so the
    /// renderer can pattern-match on it.
    #[error("dialog canceled")]
    DialogCanceled,

    /// The WS bridge to the Python sidecar is not connected (the
    /// `ws_tx` slot in `SidecarState` is `None`, or `try_send` failed
    /// because the channel is closed / full). Used by `dispatch` and
    /// `paste_text` when they can't forward to the sidecar.
    #[error("sidecar disconnected: {0}")]
    SidecarDisconnected(String),

    /// Catch-all for error strings that don't fit any of the above
    /// variants. Used during the incremental migration — legacy
    /// `.map_err(|e| e.to_string())` sites can be mechanically
    /// converted to `.map_err(VoiceTyperError::Other)` without
    /// classifying the underlying error.
    #[error("{0}")]
    Other(String),
}

// ─── Conversions from common error types ────────────────────────────────

impl From<io::Error> for VoiceTyperError {
    fn from(e: io::Error) -> Self {
        VoiceTyperError::OsError(e.to_string())
    }
}

impl From<String> for VoiceTyperError {
    /// Mechanical conversion from the legacy `String` error type —
    /// used by `.map_err(VoiceTyperError::from)` on sites that haven't
    /// been classified yet. The string is preserved verbatim in the
    /// `Other` variant so no diagnostic information is lost during
    /// the incremental migration.
    fn from(s: String) -> Self {
        VoiceTyperError::Other(s)
    }
}

impl From<&str> for VoiceTyperError {
    fn from(s: &str) -> Self {
        VoiceTyperError::Other(s.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_window_not_found_display_includes_label() {
        let e = VoiceTyperError::WindowNotFound("bubble".to_string());
        assert!(e.to_string().contains("bubble"));
        assert!(e.to_string().contains("window not found"));
    }

    #[test]
    fn test_os_error_preserves_underlying_message() {
        let io_err = io::Error::new(io::ErrorKind::PermissionDenied, "denied");
        let e: VoiceTyperError = io_err.into();
        assert!(matches!(e, VoiceTyperError::OsError(_)));
        assert!(e.to_string().contains("denied"));
    }

    #[test]
    fn test_dialog_canceled_has_no_message_field() {
        // DialogCanceled is a unit variant — its Display is fixed.
        let e = VoiceTyperError::DialogCanceled;
        assert_eq!(e.to_string(), "dialog canceled");
    }

    #[test]
    fn test_string_conversion_preserves_message() {
        let e: VoiceTyperError = "sidecar WS send failed: channel closed".to_string().into();
        assert!(matches!(e, VoiceTyperError::Other(_)));
        assert!(e.to_string().contains("channel closed"));
    }

    #[test]
    fn test_sidecar_disconnected_includes_detail() {
        let e = VoiceTyperError::SidecarDisconnected("ws_tx is None".to_string());
        assert!(e.to_string().contains("sidecar disconnected"));
        assert!(e.to_string().contains("ws_tx is None"));
    }

    #[test]
    fn test_serde_serializes_with_tag_and_content() {
        // The #[serde(tag = "kind", content = "message")] attribute
        // produces JSON like {"kind":"WindowNotFound","message":"bubble"}.
        // Pin the shape so the renderer's pattern-match stays correct.
        let e = VoiceTyperError::WindowNotFound("bubble".to_string());
        let json = serde_json::to_string(&e).expect("serialize");
        assert!(json.contains("\"kind\":\"WindowNotFound\""), "json: {}", json);
        assert!(json.contains("\"message\":\"bubble\""), "json: {}", json);

        // Unit variant DialogCanceled serializes with kind only (no
        // message field — the variant has no data).
        let e2 = VoiceTyperError::DialogCanceled;
        let json2 = serde_json::to_string(&e2).expect("serialize");
        assert!(json2.contains("\"kind\":\"DialogCanceled\""), "json: {}", json2);
        assert!(!json2.contains("\"message\""), "unit variant must not have message field: {}", json2);
    }
}
