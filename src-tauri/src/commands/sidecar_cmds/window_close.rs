//! Main-window close-requested branch body (C-ARCH-1) — extracted from
//! the former single-file `commands/sidecar_cmds.rs` (EO-35 split).

use crate::state::SidecarState;
use std::sync::Arc;
use tauri::Manager;

use super::shutdown::shutdown_sidecar;

// Host entrypoint helper: main-window close handler (C-) ──────

/// `on_window_event` close-requested branch body, extracted from
/// `main.rs`'s inline closure so the host entrypoint stays wiring-only
/// (C-).
///
/// ADR-0020 §10: on main window close, shutdown the sidecar. :
/// also handle the bubble window's close so a user dismissing the
/// bubble doesn't leave the sidecar running against a closed webview
/// (just log — no sidecar shutdown for the bubble).
///
/// on macOS the app stays alive when the last window
/// closes (standard macOS app lifecycle — the tray / Dock keeps the
/// process running). Killing the sidecar here would orphan the
/// dictation engine while the app is still alive in the menu bar.
/// Only kill the sidecar on Windows/Linux where app exit is bound to
/// last-window-close.
///
/// The actual shutdown runs in a spawned async task so the event loop
/// is not blocked on the cooperative-shutdown wait (up to
/// `SHUTDOWN_ACK_TIMEOUT_MS` = 2s).
pub(crate) fn on_main_window_close(app_handle: &tauri::AppHandle, window: &tauri::Window) {
    match window.label() {
        "main" => {
            if cfg!(target_os = "macos") {
                return;
            }
            // `shutdown_sidecar` takes a `window: tauri::Window`
            // parameter ( main-window guard). Clone the main
            // window handle here so the spawned task can pass it through
            // the guard (the label is "main" so the check passes).
            let main_window = window.clone();
            let app_clone = app_handle.clone();
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app_clone.state();
                if let Err(e) = shutdown_sidecar(app_clone.clone(), state, main_window).await {
                    log::warn!("[WINDOW] shutdown_sidecar on close failed: {}", e);
                }
            });
        }
        "bubble" => {
            // Bubble window close — no sidecar shutdown, just log.
            log::info!("[WINDOW] bubble window closed by user");
        }
        _ => {}
    }
}
