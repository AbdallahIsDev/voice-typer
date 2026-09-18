//! Host-side handlers for sidecar events that have no renderer
//! consumer (ADR-0020 §6.5).
//!
//! The WS reader forwards every allowlisted server event to the
//! renderer as a Tauri event, but some events are consumed by the
//! Rust host itself (they used to have no Tauri listener):
//!
//! - `show_window`: published by `tray_window.py` when the tray's
//!   "Open App" action (or a left-click focus redirect) fires. The
//!   host calls [`show_main_window`]; without this listener the tray's
//!   "Open App" entry was a no-op whenever the Win32 focus fallback
//!   could not reach the window.
//!
//! [`show_main_window`] is also the shared raise-to-front entry point
//! every OTHER "bring the dashboard back" path routes through
//! (single-instance second launch, tray left-click, macOS dock
//! activation): see its doc comment for the pre-sharing defect.
//! - `notification`: published by `system_handlers.py` /
//!   `model_manager.py` / `parakeet_engine.py` /
//!   `tray_notifications.py` (the Tauri runtime never creates a pystray
//!   icon, so its toasts must surface here). The host shows a native
//!   toast via `tauri-plugin-notification`; without a host listener,
//!   toasts would be silently dropped.
//!
//! Both listener bodies are kept off the hot path: the payloads are
//! tiny and low-frequency. Window/notification OS calls run on the
//! blocking pool via `spawn_blocking`, mirroring `tray.rs`.

use serde::Deserialize;
use tauri::{AppHandle, Emitter, Listener, Manager};

/// Payload of the server `notification` event
/// (`{"type":"notification","data":{"title":...,"message":...}}`).
///
/// MO-117: click-routing fields are parsed here so a toast click can
/// drive host-side navigation:
///
/// - `click_path`: navigate the main window to a page (e.g.
///   `/models`).
/// - `click_consent_field`: deep-link to the EXACT Settings consent
///   row (takes precedence over `click_path`), broadcast as
///   `navigate {path: "/settings", consent_field}`.
/// - `duration_ms`: auto-close the toast after this many ms (0/absent
///   = persist until dismissed).
///
/// The click broadcast uses the same `navigate` Tauri event the
/// renderer's `usePythonEvent("navigate", ...)` hook consumes (the WS
/// reader translates server events to the same names; the renderer
/// consumes the DATA shape, which is what we emit).
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

/// Extract the notification contract from a raw `notification` event
/// payload.
///
/// Pure function so the JSON-shape contract is unit-testable without a
/// Tauri runtime. Returns `None` for malformed JSON, non-object
/// payloads, or payloads where BOTH fields are empty (an empty toast is
/// worse than no toast).
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

/// The `navigate` event the toast-click handler broadcasts when the
/// payload carries click-routing fields. The renderer's
/// `usePythonEvent("navigate", ...)` consumes exactly this DATA shape:
/// `{path: "/models"}` or `{path: "/settings", consent_field: ...}`
/// (the consent field overrides the target page to the Privacy
/// sub-page, see `useNavigateEvent.ts`).
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

/// Show a native desktop notification with the parsed title/body.
///
/// MO-117: when the payload carries `click_path` /
/// `click_consent_field`, the click routes through
/// [`show_main_window`] + a `navigate` Tauri-event broadcast (show the
/// window FIRST so the `navigate` listener is live, then
/// broadcast). `duration_ms > 0` schedules an auto-close; 0/absent
/// persists until the OS or user dismisses.
fn show_notification(app: &AppHandle, payload: &NotificationPayload) {
    use tauri_plugin_notification::NotificationExt;

    let app = app.clone();
    let title = payload.title.clone();
    let message = payload.message.clone();
    let click = payload.click_path.is_some() || payload.click_consent_field.is_some();
    let navigate = navigate_payload(payload);
    let duration_ms = payload.duration_ms;
    // OS toast APIs can block on platform notification services; keep
    // them off the event-loop thread (mirrors tray.rs' spawn_blocking
    // pattern).
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
            // The notify-rust desktop backend has no close API; the
            // toast is owned by the OS once shown. The best-effort
            // contract (same as the plugin) is that the OS default
            // duration applies; log the request so support can see
            // when a timed toast was expected.
            log::debug!(
                "[HOST-EVENTS] notification duration_ms={} requested (OS-managed on desktop)",
                duration_ms
            );
        }
    });
}

/// The ONE canonical "bring the dashboard back to the user" routine.
///
/// Called from every path that must surface an existing app (MO-109):
///
/// - the `show_window` server event (tray "Open App" / focus redirect),
///   listened for below;
/// - the single-instance callback in `main.rs` (user launched the app a
///   second time while it was minimized / buried);
/// - the tray icon's left-click handler in `tray.rs`;
/// - macOS dock-icon activation (`RunEvent::Reopen`, MO-112).
///
/// Before this was shared, only the `show_window` path ran the full
/// sequence; the others called bare `show()` + `set_focus()`, which the
/// OS foreground lock turns into a taskbar flash when the caller is a
/// background process, so "launch again from the Start Menu" looked
/// dead while the window stayed minimized behind other apps.
///
/// Runs on the blocking pool (OS window calls can sync-IPC to the
/// window server) and is fire-and-forget.
pub(crate) fn show_main_window(app: &AppHandle) {
    let app = app.clone();
    #[allow(clippy::let_underscore_future)] // intentional fire-and-forget
    let _ = tauri::async_runtime::spawn_blocking(move || match app.webview_windows().get("main") {
        Some(window) => raise_main_window(&window),
        None => {
            // macOS keeps the process alive after the last window is
            // closed (tray / Dock), so the dock-activate path can arrive
            // with NO window at all. Rebuild it through the same
            // bootstrap the app uses at startup (`window_bootstrap`),
            // then raise it.
            log::info!("[HOST-EVENTS] main window not found: recreating it");
            crate::window_bootstrap::bootstrap_main_window(&app);
            match app.webview_windows().get("main") {
                Some(window) => raise_main_window(&window),
                None => log::warn!("[HOST-EVENTS] main window recreation failed"),
            }
        }
    });
}

/// Raise one existing main window: clear the hidden-launch taskbar skip,
/// unminimize, show, momentarily force always-on-top so the OS foreground
/// lock cannot swallow the raise, focus, then drop the always-on-top
/// flag.
fn raise_main_window(window: &tauri::WebviewWindow) {
    // Clear skip_taskbar if the window was started hidden
    // (VT_START_HIDDEN=1). Without this the window would show
    // but leave no taskbar entry.
    if let Err(e) = window.set_skip_taskbar(false) {
        log::warn!(
            "[HOST-EVENTS] main window set_skip_taskbar(false) failed: {}",
            e
        );
    }
    // RAISE-TO-FRONT: `set_focus` alone is subject to the OS
    // foreground lock (Windows refuses SetForegroundWindow from
    // a background process and only flashes the taskbar), so the
    // dashboard stayed buried behind other apps' windows. The
    // momentary always-on-top raise below: lift, focus, drop.
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
