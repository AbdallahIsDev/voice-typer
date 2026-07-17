//! Bubble window commands (MIG-1.2 + ADR-0020 §9).

use serde_json::{json, Value};
use tauri::{PhysicalPosition, Emitter, Manager};

// ─── Tauri commands: bubble window (MIG-1.2, ADR-0020 §9) ────────────

/// Show the bubble window (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
pub async fn bubble_show(app: tauri::AppHandle) -> Result<(), String> {
    app.get_webview_window("bubble")
        .ok_or("bubble window not found")?
        .show()
        .map_err(|e| e.to_string())
}

/// Emit `bubble:ready` to the bubble window — the bubble renderer
/// listens for this and signals back to the Python sidecar that it's
/// ready to receive `bubble_level` events (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
pub async fn bubble_signal_ready(app: tauri::AppHandle) -> Result<(), String> {
    app.emit_to("bubble", "bubble:ready", ())
        .map_err(|e| e.to_string())
}

/// Move the bubble window to `(x, y)` in physical pixels (ADR-0020 §9
/// + MIG-1.2). The TS bridge calls this with the cursor position
/// (offset by a small delta) so the bubble appears under the cursor.
#[tauri::command]
pub async fn bubble_set_position(
    x: i32,
    y: i32,
    app: tauri::AppHandle,
) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    window
        .set_position(PhysicalPosition::new(x, y))
        .map_err(|e| e.to_string())
}

/// Toggle the bubble window's draggable state (ADR-0020 §9 + MIG-1.2).
///
/// Tauri v2 does NOT expose a direct `set_draggable` on `WebviewWindow`.
/// Instead, we emit a `bubble:draggable` event to the bubble window
/// with the bool payload; the bubble renderer listens for this event
/// and calls `start_dragging()` on mouse-down (or unbinds the
/// listener when `false`). This keeps the drag logic in the renderer
/// where it can be throttled to the animation frame.
#[tauri::command]
pub async fn bubble_set_draggable(
    draggable: bool,
    app: tauri::AppHandle,
) -> Result<(), String> {
    app.emit_to("bubble", "bubble:draggable", draggable)
        .map_err(|e| e.to_string())
}

/// Move the bubble window by `(dx, dy)` physical pixels relative to
/// its current `outer_position` (ADR-0020 §9 + MIG-1.2). Returns the
/// new `{x, y}` so the TS bridge can cache it without a round-trip.
#[tauri::command]
pub async fn bubble_move_by(
    dx: i32,
    dy: i32,
    app: tauri::AppHandle,
) -> Result<Value, String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    let pos = window.outer_position().map_err(|e| e.to_string())?;
    let new_x = pos.x + dx;
    let new_y = pos.y + dy;
    window
        .set_position(PhysicalPosition::new(new_x, new_y))
        .map_err(|e| e.to_string())?;
    Ok(json!({"x": new_x, "y": new_y}))
}

/// Hide the bubble window and emit `bubble:hide_complete` so the
/// renderer can run cleanup (e.g., stop the level animation) before
/// the window becomes invisible (ADR-0020 §9 + MIG-1.2).
#[tauri::command]
pub async fn bubble_hide_complete(app: tauri::AppHandle) -> Result<(), String> {
    let window = app
        .get_webview_window("bubble")
        .ok_or("bubble window not found")?;
    window.hide().map_err(|e| e.to_string())?;
    app.emit_to("bubble", "bubble:hide_complete", ())
        .map_err(|e| e.to_string())
}
