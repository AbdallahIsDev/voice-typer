//! Bubble window hide helper ( + ADR-0020 §9).
//!
//! [`hide_bubble_window`] is the shared emit+hide path used by both
//! `bubble_hide_complete` and `bubble_dismiss`.

use tauri::{Emitter, Manager};

/// Hide the bubble window, emitting `bubble:hide` FIRST so the renderer
/// can run cleanup (e.g., stop the level animation) BEFORE the window
//becomes invisible ( ordering fix).
///
/// Extracted as a helper so `bubble_hide_complete` and `bubble_dismiss`
/// share the same hide path — the two commands are semantically distinct
/// (animation-complete signal vs user-dismiss affordance) but have
/// identical hide behavior.
pub(super) fn hide_bubble_window(app: &tauri::AppHandle) -> Result<(), String> {
    //emit FIRST so the renderer's cleanup runs while the
    // window is still visible.
    app.emit_to("bubble", "bubble:hide", ())
        .map_err(|e| e.to_string())?;
    let bubble = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    bubble.hide().map_err(|e| e.to_string())
}
