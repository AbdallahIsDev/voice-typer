//! Tauri command handler modules (ADR-0020 §6 + §7 + §10).

pub(crate) mod bubble;
pub(crate) mod export;
pub(crate) mod sidecar_cmds;
pub(crate) mod system_cmds;
// The former `paste` / `paste_text` command was dead production code;
// the Python sidecar owns the paste path end-to-end.

// `dispatch_inner` + `DispatchArgs` are crate-visible (not Tauri
// commands): the tray menu click handler uses them via
// `crate::commands::{...}`.
pub(crate) use sidecar_cmds::{dispatch_inner, DispatchArgs};

// Unified command error type for every `#[tauri::command]` in this tree.
use crate::error::VoiceTyperError;

// SEC-026: bubble webview is sandboxed and must never drive the sidecar
// WS, export path, or host-only surfaces. Tauri capabilities only gate
// plugin commands, so user-defined commands need this runtime check.
// Error envelope matches the sidecar WS error shape so the renderer
// reject path is shared.

/// Pure main-window label predicate (`"main"`).
pub(crate) fn main_window_label_check(label: &str) -> bool {
    label == "main"
}

/// Gate a command on the calling window being the main window
/// (SEC-026). Non-main windows get the canonical
/// `disallowed_window` error envelope.
pub(crate) fn require_main_window(window: &tauri::Window) -> Result<(), VoiceTyperError> {
    if !main_window_label_check(window.label()) {
        log::warn!(
            "[window-guard] command rejected from non-main window: {}",
            window.label()
        );
        return Err(VoiceTyperError::disallowed_main_window());
    }
    Ok(())
}

/// Gate a command on the calling window being the bubble window
/// (SEC-026). Same envelope shape as `require_main_window`.
pub(crate) fn require_bubble_window(window: &tauri::Window) -> Result<(), VoiceTyperError> {
    if window.label() != "bubble" {
        log::warn!(
            "[window-guard] command rejected from non-bubble window: {}",
            window.label()
        );
        return Err(VoiceTyperError::disallowed_bubble_window());
    }
    Ok(())
}
