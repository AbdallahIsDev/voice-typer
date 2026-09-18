//! Main-window bootstrap (the `tauri::Builder::setup` stage).
//!
//! Extracted verbatim from `main.rs`'s `.setup` closure (pure move, no
//! behavior change) so the host entrypoint stays wiring-only
//! (C-ARCH-1): the window-config lookup, the platform-conditional
//! frame setup, the startup theme icon, and the VT_START_HIDDEN
//! background-launch handling are window-construction logic, not
//! builder wiring.
//!
//! Wiring: `main.rs` calls [`bootstrap_main_window`] once inside
//! `.setup`. The window itself is declared in `tauri.conf.json` with
//! `"create": false` and is built HERE from that config so the FRAME
//! can be platform-conditional (macOS keeps native decorations,
//! Windows/Linux go frameless).

/// Build the `main` window from its `tauri.conf.json` config entry,
/// apply the platform frame + startup icon, and honor VT_START_HIDDEN.
///
/// Custom-title-bar window construction (the Electron host that this
/// layout originally mirrored was removed 2026-09-17). The window is NOT
/// auto-created: `tauri.conf.json` declares it with `"create": false`
/// and it is built here from that config so the FRAME can be
/// platform-conditional:
///   - macOS: keep native decorations + the traffic lights
///     (`titleBarStyle: Overlay` + `trafficLightPosition` from
///     config). The renderer
///     omits its custom window buttons on macOS and reserves the
///     traffic-light gutter.
///   - Windows/Linux: fully frameless (`decorations: false`), the
///     renderer draws the custom title bar + window controls.
///
/// Panics (via `expect`, mirroring the pre-extraction `main.rs`
/// invariants) only on config-level defects: a missing `main` window
/// entry in `tauri.conf.json` or an invalid window config. Those are
/// build-time constants: they cannot vary at runtime.
///
/// Takes an `&AppHandle` (rather than `&App`) so the SAME builder can run
/// from `host_events::show_main_window` when the window no longer exists
/// (macOS: the last window can be closed while the process stays alive,
/// and the Dock-activate path must bring it back; MO-112). `main.rs`
/// passes `app.handle()` at startup.
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
    // TR-4 dynamic: match the main-window icon to the current OS theme
    // (white glyph on dark, black on light). Body lives in
    // `theme_icon.rs`.
    crate::theme_icon::apply_startup(&main_window);
    // Respect VT_START_HIDDEN=1 (set by autostart_launcher when
    // launched with --hidden). Tauri honors it here by hiding the
    // window immediately + skip_taskbar, otherwise a background
    // autostart would briefly flash the window and the renderer's
    // persisted "microphone" page would activate the mic indicator
    // while hidden.
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
