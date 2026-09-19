//! Main-window bootstrap (Tauri `Builder::setup` stage).
//! Logic lives here so `main.rs` stays wiring-only (C-ARCH-1).

/// Build the `main` window from `tauri.conf.json` (`"create": false`),
/// apply the platform frame + startup icon, honor VT_START_HIDDEN.
///
/// Platform frame: macOS keeps native decorations (traffic lights);
/// Windows/Linux are frameless (renderer draws the custom title bar).
/// Takes `&AppHandle` so the same builder can rebuild a closed window
/// (macOS Dock activation). Config-level defects panic (build-time constants).
/// VT_START_HIDDEN: hide + skip_taskbar so background autostart does not
/// flash the window or activate the mic indicator (C-BG-1).
pub(crate) fn bootstrap_main_window(app: &tauri::AppHandle) {
    let main_window_config = app
        .config()
        .app
        .windows
        .iter()
        .find(|w| w.label == "main")
        .unwrap_or_else(|| panic!("[SETUP] main window missing from tauri.conf.json"));
    let main_window_builder = tauri::WebviewWindowBuilder::from_config(app, main_window_config)
        .expect("[SETUP] main window config is valid");
    #[cfg(not(target_os = "macos"))]
    let main_window_builder = main_window_builder.decorations(false);
    let main_window = main_window_builder
        .build()
        .expect("[SETUP] main window build failed");
    // Match the main-window icon to the OS theme (body in `theme_icon.rs`).
    crate::theme_icon::apply_startup(&main_window);
    if std::env::var("VT_START_HIDDEN").as_deref() == Ok("1") {
        if let Err(e) = main_window.hide() {
            log::warn!("[SETUP] hide main window for VT_START_HIDDEN failed: {}", e);
        }
        if let Err(e) = main_window.set_skip_taskbar(true) {
            log::warn!("[SETUP] set_skip_taskbar for VT_START_HIDDEN failed: {}", e);
        }
        log::info!("[SETUP] started hidden (VT_START_HIDDEN=1), window hidden, skip_taskbar=true");
    }
}
