//! Main-window close-requested branch body (C-ARCH-1), extracted from
//! the former single-file `commands/sidecar_cmds.rs`.

use crate::state::SidecarState;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tauri::Manager;

use super::shutdown::shutdown_sidecar;

fn should_hide_to_tray(label: &str, shutting_down: bool, tray_available: bool) -> bool {
    label == "main" && !shutting_down && tray_available
}

// Host entrypoint helper: main-window close handler (C-) ──────

pub(crate) fn on_main_window_close(
    app_handle: &tauri::AppHandle,
    window: &tauri::Window,
    api: &tauri::CloseRequestApi,
) {
    match window.label() {
        "main" => {
            let state: tauri::State<'_, Arc<SidecarState>> = app_handle.state();
            if should_hide_to_tray(
                window.label(),
                state.shutting_down.load(Ordering::SeqCst),
                state.tray_available.load(Ordering::SeqCst),
            ) {
                log::info!(
                    "[WINDOW] main window close requested: hiding to tray (sidecar stays running)"
                );
                api.prevent_close();
                if let Err(e) = window.set_skip_taskbar(true) {
                    log::warn!(
                        "[WINDOW] set_skip_taskbar(true) on close failed (best-effort): {}",
                        e
                    );
                }
                if let Err(e) = window.hide() {
                    log::warn!("[WINDOW] hide on close failed (best-effort): {}", e);
                }
                return;
            }
            // Deliberate shutdown in progress: allow the close through.
            if cfg!(target_os = "macos") {
                return;
            }
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
            // Bubble window close: no sidecar shutdown, just log.
            log::info!("[WINDOW] bubble window closed by user");
        }
        _ => {}
    }
}

// Sibling test module: tests live in `window_close_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "window_close_tests.rs"]
mod window_close_tests;
