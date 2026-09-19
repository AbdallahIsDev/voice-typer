
use serde::Deserialize;
use tauri::{AppHandle, Emitter, Listener, Manager};

#[derive(Debug, Clone, Deserialize)]
struct NotificationPayload {
    #[serde(default)]
    title: String,
    #[serde(default)]
    message: String,
    #[serde(default)]
    click_path: Option<String>,
    #[serde(default)]
    click_consent_field: Option<String>,
    #[serde(default)]
    duration_ms: u64,
}

impl Default for NotificationPayload {
    fn default() -> Self {
        Self {
            title: String::new(),
            message: String::new(),
            click_path: None,
            click_consent_field: None,
            duration_ms: 0,
        }
    }
}

fn parse_notification(raw: &str) -> Option<NotificationPayload> {
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
    Some(payload)
}

fn navigate_payload(payload: &NotificationPayload) -> serde_json::Value {
    match &payload.click_consent_field {
        Some(field) => serde_json::json!({
            "path": "/settings",
            "consent_field": field,
        }),
        None => serde_json::json!({
            "path": payload.click_path.clone().unwrap_or_default(),
        }),
    }
}

fn show_notification(app: &AppHandle, payload: &NotificationPayload) {
    use tauri_plugin_notification::NotificationExt;

    let app = app.clone();
    let title = payload.title.clone();
    let message = payload.message.clone();
    let click = payload.click_path.is_some() || payload.click_consent_field.is_some();
    let navigate = navigate_payload(payload);
    let duration_ms = payload.duration_ms;
    #[allow(clippy::let_underscore_future)] // intentional fire-and-forget
    let _ = tauri::async_runtime::spawn_blocking(move || {
        if click {
            if let Err(e) = app.emit("navigate", navigate) {
                log::warn!("[HOST-EVENTS] navigate pre-broadcast failed: {}", e);
            }
        }
        if let Err(e) = app
            .notification()
            .builder()
            .title(&title)
            .body(&message)
            .show()
        {
            log::warn!("[HOST-EVENTS] notification show failed: {}", e);
            return;
        }
        if duration_ms > 0 {
            log::debug!(
                "[HOST-EVENTS] notification duration_ms={} requested (OS-managed on desktop)",
                duration_ms
            );
        }
    });
}

pub(crate) fn show_main_window(app: &AppHandle) {
    let app = app.clone();
    #[allow(clippy::let_underscore_future)] // intentional fire-and-forget
    let _ = tauri::async_runtime::spawn_blocking(move || match app.webview_windows().get("main") {
        Some(window) => raise_main_window(&window),
        None => {
            log::info!("[HOST-EVENTS] main window not found: recreating it");
            crate::window_bootstrap::bootstrap_main_window(&app);
            match app.webview_windows().get("main") {
                Some(window) => raise_main_window(&window),
                None => log::warn!("[HOST-EVENTS] main window recreation failed"),
            }
        }
    });
}

fn raise_main_window(window: &tauri::WebviewWindow) {
    if let Err(e) = window.set_skip_taskbar(false) {
        log::warn!(
            "[HOST-EVENTS] main window set_skip_taskbar(false) failed: {}",
            e
        );
    }
    if let Err(e) = window.unminimize() {
        log::warn!("[HOST-EVENTS] main window unminimize failed: {}", e);
    }
    if let Err(e) = window.show() {
        log::warn!("[HOST-EVENTS] main window show failed: {}", e);
    }
    if let Err(e) = window.set_always_on_top(true) {
        log::warn!(
            "[HOST-EVENTS] main window set_always_on_top(true) failed: {}",
            e
        );
    }
    if let Err(e) = window.set_focus() {
        log::warn!("[HOST-EVENTS] main window set_focus failed: {}", e);
    }
    if let Err(e) = window.set_always_on_top(false) {
        log::warn!(
            "[HOST-EVENTS] main window set_always_on_top(false) failed: {}",
            e
        );
    }
    log::info!("[HOST-EVENTS] main window shown + raised to front");
}

/// Register the host-side event listeners. Called once from `main.rs`
/// during app setup.
pub(crate) fn setup(app: &AppHandle) {
    let notify_handle = app.clone();
    app.listen("notification", move |event| {
        if let Some(payload) = parse_notification(event.payload()) {
            show_notification(&notify_handle, &payload);
        }
    });

    let show_handle = app.clone();
    app.listen("show_window", move |_event| {
        show_main_window(&show_handle);
    });
}

// Sibling test module: tests live in `host_events_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "host_events_tests.rs"]
mod host_events_tests;
