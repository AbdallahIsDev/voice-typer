//! Tray-icon event predicates.
//!
//! Split out of the former monolithic `tray.rs` so the click-button
//! filter (the decision "should this tray-icon event show + focus the
//! main window?") lives in its own focused module and stays
//! unit-testable without a live Tauri app. Re-exported from
//! `crate::tray` so the existing `crate::tray::is_focus_main_window_event`
//! path (used by the sibling `tray_tests.rs`) keeps resolving.

use tauri::tray::{MouseButton, TrayIconEvent};

//predicate that decides whether a tray icon event should
/// trigger the show + focus main-window path. Extracted from the
/// `on_tray_icon_event` closure so the button filter is unit-testable
/// (constructing a `TrayIconEvent` and asserting on the predicate is
/// much simpler than spinning up a real Tauri app + tray in a test).
///
/// Returns `true` ONLY for `TrayIconEvent::Click` with
/// `button == MouseButton::Left`. Right-click, middle-click, double-
/// click, mouse-enter, mouse-move, and mouse-leave all return `false`
/// — the OS / Tauri handles those (right-click opens the bound
/// `.menu(...)`, etc.).
pub(crate) fn is_focus_main_window_event(event: &TrayIconEvent) -> bool {
    matches!(
        event,
        TrayIconEvent::Click {
            button: MouseButton::Left,
            ..
        }
    )
}
