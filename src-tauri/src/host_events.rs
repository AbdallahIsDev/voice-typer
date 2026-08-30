//! Host-side handlers for sidecar events that have no renderer
//! consumer (ADR-0020 §6.5 / Electron parity).
//!
//! The WS reader forwards every allowlisted server event to the
//! renderer as a Tauri event, but some events are consumed by the
//! ELECTRON MAIN process today (`handle-message.ts`) and had no Tauri
//! counterpart:
//!
//! - `show_window` — published by `tray_window.py` when the tray's
//!   "Open App" action (or a left-click focus redirect) fires. Under
//!   Electron the main process calls `showMainWindow()`; under Tauri
//!   nobody listened, so the tray's "Open App" entry was a no-op
//!   whenever the Win32 focus fallback could not reach the window.
//! - `notification` — published by `system_handlers.py` /
//!   `model_manager.py` / `parakeet_engine.py` /
//!   `tray_notifications.py` (the Tauri runtime never creates a pystray
//!   icon, so its toasts must surface here). Under Electron the main
//!   process shows a native `Notification`; under Tauri the plugin was
//!   registered but no listener existed, so toasts were silently
//!   dropped.
//!
//! Both listener bodies are kept off the hot path: the payloads are
//! tiny and low-frequency. Window/notification OS calls run on the
//! blocking pool via `spawn_blocking`, mirroring `tray.rs`.

use serde::Deserialize;
use tauri::{AppHandle, Listener, Manager};

/// Payload of the server `notification` event
/// (`{"type":"notification","data":{"title":...,"message":...}}`).
/// Extra fields (`duration_ms`, `critical`, `click_path`,
/// `click_consent_field`) are intentionally ignored here — native toast
/// click-routing is a follow-up; the fields this module consumes are the
/// only ones the OS toast needs.
#[derive(Debug, Clone, Deserialize)]
struct NotificationPayload {
    #[serde(default)]
    title: String,
    #[serde(default)]
    message: String,
}

/// Extract `(title, message)` from a raw `notification` event payload.
///
/// Pure function so the JSON-shape contract is unit-testable without a
/// Tauri runtime. Returns `None` for malformed JSON, non-object
/// payloads, or payloads where BOTH fields are empty (an empty toast is
/// worse than no toast).
fn parse_notification(raw: &str) -> Option<(String, String)> {
    let payload: NotificationPayload = match serde_json::from_str(raw) {
        Ok(p) => p,
        Err(e) => {
            log::warn!("[HOST-EVENTS] failed to parse notification payload: {}", e);
            return None;
        }
    };
    if payload.title.is_empty() && payload.message.is_empty() {
        return None;
    }
    Some((payload.title, payload.message))
}

/// Show a native desktop notification with the parsed title/body.
fn show_notification(app: &AppHandle, title: &str, message: &str) {
    use tauri_plugin_notification::NotificationExt;

    let app = app.clone();
    let title = title.to_string();
    let message = message.to_string();
    // OS toast APIs can block on platform notification services; keep
    // them off the event-loop thread (mirrors tray.rs' spawn_blocking
    // pattern).
    #[allow(clippy::let_underscore_future)] // intentional fire-and-forget
    let _ = tauri::async_runtime::spawn_blocking(move || {
        if let Err(e) = app
            .notification()
            .builder()
            .title(&title)
            .body(&message)
            .show()
        {
            log::warn!("[HOST-EVENTS] notification show failed: {}", e);
        }
    });
}

/// Show + focus the main window (tray "Open App" / focus redirect).
fn show_main_window(app: &AppHandle) {
    let app = app.clone();
    #[allow(clippy::let_underscore_future)] // intentional fire-and-forget
    let _ = tauri::async_runtime::spawn_blocking(move || match app.webview_windows().get("main")
    {
        Some(window) => {
            // Clear skip_taskbar if the window was started hidden
            // (VT_START_HIDDEN=1). Without this the window would show
            // but leave no taskbar entry.
            if let Err(e) = window.set_skip_taskbar(false) {
                log::warn!("[HOST-EVENTS] main window set_skip_taskbar(false) failed: {}", e);
            }
            // RAISE-TO-FRONT: `set_focus` alone is subject to the OS
            // foreground lock (Windows refuses SetForegroundWindow from
            // a background process and only flashes the taskbar), so the
            // dashboard stayed buried behind other apps' windows. The
            // momentary always-on-top raise below mirrors Electron's
            // `showMainWindow()` (main-window.ts) — lift, focus, drop.
            if let Err(e) = window.unminimize() {
                log::warn!("[HOST-EVENTS] main window unminimize failed: {}", e);
            }
            if let Err(e) = window.show() {
                log::warn!("[HOST-EVENTS] main window show failed: {}", e);
            }
            if let Err(e) = window.set_always_on_top(true) {
                log::warn!("[HOST-EVENTS] main window set_always_on_top(true) failed: {}", e);
            }
            if let Err(e) = window.set_focus() {
                log::warn!("[HOST-EVENTS] main window set_focus failed: {}", e);
            }
            if let Err(e) = window.set_always_on_top(false) {
                log::warn!("[HOST-EVENTS] main window set_always_on_top(false) failed: {}", e);
            }
            log::info!("[HOST-EVENTS] main window shown + raised to front (show_window request)");
        }
        None => log::warn!("[HOST-EVENTS] show_window: main window not found"),
    });
}

/// Register the host-side event listeners. Called once from `main.rs`
/// during app setup.
pub(crate) fn setup(app: &AppHandle) {
    let notify_handle = app.clone();
    app.listen("notification", move |event| {
        if let Some((title, message)) = parse_notification(event.payload()) {
            show_notification(&notify_handle, &title, &message);
        }
    });

    let show_handle = app.clone();
    app.listen("show_window", move |_event| {
        show_main_window(&show_handle);
    });
}

// Sibling test module — tests live in `host_events_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "host_events_tests.rs"]
mod host_events_tests;
