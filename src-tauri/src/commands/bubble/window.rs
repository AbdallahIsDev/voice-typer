//! Bubble window show/hide helpers ( + ADR-0020 §9).
//!
//! [`hide_bubble_window`] is the shared emit+hide path used by both
//! `bubble_hide_complete` and `bubble_dismiss`. [`show_bubble_window`]
//! is the shared restore+show path used by the `bubble_show` command
//! and by the WS reader task when the sidecar publishes `bubble_show`
//! on recording start: a single body so the renderer-invoked and the
//! host-triggered paths can never drift (E7).

use tauri::{Emitter, Manager, PhysicalPosition};

/// Hide the bubble window, emitting `bubble:hide` FIRST so the renderer
/// can run cleanup (e.g., stop the level animation) BEFORE the window
//becomes invisible ( ordering fix).
///
/// Extracted as a helper so `bubble_hide_complete` and `bubble_dismiss`
/// share the same hide path, the two commands are semantically distinct
/// (animation-complete signal vs user-dismiss affordance) but have
/// identical hide behavior.
/// Whether a sidecar event type must show the bubble OS window.
///
/// Pure predicate so the WS-reader trigger is unit-testable without a
/// Tauri runtime: only `bubble_show` (recording started) shows it.
/// Every other event (levels, config, hide, state) leaves visibility
/// alone.
pub(crate) fn wants_bubble_show(event_type: &str) -> bool {
    event_type == "bubble_show"
}

/// Show the bubble window, restoring the durable drag position first.
///
/// Shared body for the `bubble_show` command and the WS reader's
/// recording-start trigger: when the sidecar's config carries a
/// persisted `bubble_x` / `bubble_y` pair that still lies on an
/// attached monitor, place the window there before showing (mirrors
/// the predecessor's show-time placement). Without a cached pair the window
/// keeps its last keyword-centered position.
///
/// The restore is a PROGRAMMATIC placement: suppress the debounced
/// persist around it so its own `Moved` event doesn't rewrite the
/// config with the coordinates just read from it.
pub(crate) fn show_bubble_window(app: &tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    if let Some((x, y)) = super::restore_position(app) {
        super::suppress_persist_for_window();
        let _ = window.set_position(PhysicalPosition::new(x, y));
    }
    window.show().map_err(|e| e.to_string())
}

pub(crate) fn hide_bubble_window(app: &tauri::AppHandle) -> Result<(), String> {
    //emit FIRST so the renderer's cleanup runs while the
    // window is still visible.
    app.emit_to("bubble", "bubble:hide", ())
        .map_err(|e| e.to_string())?;
    let bubble = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    bubble.hide().map_err(|e| e.to_string())
}
