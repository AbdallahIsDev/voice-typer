//! Main-window close-requested branch body (C-ARCH-1) — extracted from
//! the former single-file `commands/sidecar_cmds.rs` (EO-35 split).

use crate::state::SidecarState;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tauri::Manager;

use super::shutdown::shutdown_sidecar;

/// Close-to-tray predicate: the main window hides instead of closing
/// when the app is NOT shutting down AND a tray actually exists
/// (mirrors Electron `main-window.ts` — `if (!app.isQuitting &&
/// !isLinuxWaylandWithoutSni()) { event.preventDefault();
/// window.hide(); }`):
///
/// - `shutting_down` set (deliberate quit — tray Quit → `quit_app`
///   event → `state::on_quit_app`): the close is allowed through so the
///   last-window-close → `RunEvent::Exit` teardown can complete.
/// - `tray_available` false (the host's `create_tray` failed — e.g.
///   Linux Wayland without StatusNotifierItem): hiding the last window
///   would strand the user (no tray icon, no Dock entry, no
///   second-instance path to bring it back), so the close flows
///   through to app exit instead. This is the Tauri equivalent of
///   Electron's `isLinuxWaylandWithoutSni()` guard.
///
/// Extracted as a pure predicate so the close-to-tray semantics are
/// unit-testable without a live `tauri::Window` (mirrors the
/// `is_focus_main_window_event` / `is_allowed_icon_name` test
/// conventions in `tray.rs`).
fn should_hide_to_tray(label: &str, shutting_down: bool, tray_available: bool) -> bool {
    label == "main" && !shutting_down && tray_available
}

// Host entrypoint helper: main-window close handler (C-) ──────

/// `on_window_event` close-requested branch body, extracted from
/// `main.rs`'s inline closure so the host entrypoint stays wiring-only
/// (C-).
///
/// Close-to-tray (Electron parity): the X button HIDES the main window;
/// the process (tray icon, Python backend, bubble) stays alive. Full
/// quit only happens via the tray "Quit" menu item → `quit_app` event
/// → `state::on_quit_app` → host exit.
///
/// When a deliberate shutdown IS in flight (`shutting_down` set — e.g.
/// the tray-Quit teardown is already running), the close is allowed
/// through so the last-window-close → `RunEvent::Exit` → `on_host_exit`
/// teardown can complete.
///
/// on macOS the app stays alive when the last window
/// closes (standard macOS app lifecycle — the tray / Dock keeps the
/// process running). The sidecar is not killed by the window-close path
/// on macOS: the host-exit teardown (`RunEvent::Exit` →
/// `on_host_exit` → `shutdown_sidecar_for_exit`) is what reaps it when
/// the user actually quits via the tray.
///
/// The actual shutdown runs in a spawned async task so the event loop
/// is not blocked on the cooperative-shutdown wait (up to
/// `SHUTDOWN_ACK_TIMEOUT_MS` = 2s).
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
                // Close-to-tray (Electron parity): prevent the close and
                // hide the window. The sidecar keeps running — nothing is
                // torn down, and the hidden window can be re-shown via the
                // tray left-click / Dock / second-instance handlers.
                log::info!(
                    "[WINDOW] main window close requested — hiding to tray (sidecar stays running)"
                );
                api.prevent_close();
                if let Err(e) = window.hide() {
                    log::warn!("[WINDOW] hide on close failed (best-effort): {}", e);
                }
                return;
            }
            // Deliberate shutdown in progress — allow the close through.
            if cfg!(target_os = "macos") {
                // macOS keeps the app alive when the last window closes
                // (standard macOS app lifecycle — the tray / Dock keeps
                // the process running). The sidecar teardown runs via the
                // host-exit path (`RunEvent::Exit` → `on_host_exit`) when
                // the user actually quits.
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

// Sibling test module — tests live in `window_close_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "window_close_tests.rs"]
mod window_close_tests;
