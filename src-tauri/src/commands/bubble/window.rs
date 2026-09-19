
use tauri::{Emitter, Manager, PhysicalPosition};

pub(crate) fn wants_bubble_show(event_type: &str) -> bool {
    event_type == "bubble_show"
}

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
