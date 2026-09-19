//! Tray-icon event predicates (re-exported from `crate::tray`).

use tauri::tray::{MouseButton, TrayIconEvent};

/// True only for left-click on the tray icon; OS/Tauri handle the rest
/// (right-click opens the bound menu, etc.).
pub(crate) fn is_focus_main_window_event(event: &TrayIconEvent) -> bool {
    matches!(
        event,
        TrayIconEvent::Click {
            button: MouseButton::Left,
            ..
        }
    )
}
