//! System tray (ADR-0020 §6.5). Python owns the menu model; host rebuilds
//! the native menu and on click dispatches `tray_click` via trusted
//! `dispatch_inner` (never renderer invoke). C-BRAND-1: APP_NAME only.

const TRAY_TOOLTIP: &str = crate::branding::APP_NAME;
const TRAY_ID: &str = "voice-typer-tray";

mod events;
mod icon_cache;
mod menu;

pub(crate) use events::is_focus_main_window_event;
#[cfg(test)]
pub(crate) use icon_cache::is_allowed_icon_name;
pub(crate) use icon_cache::load_tray_icon;
pub(crate) use menu::{build_menu, empty_menu, MenuItemData, TrayMenuPayload, TrayStatePayload};

#[cfg(test)]
use tauri::tray::{MouseButton, TrayIconEvent};

// tray_click is Rust-only (absent from `allowed_commands()`) → dispatch_inner.
use crate::commands::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
use std::sync::Arc;
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Listener, Manager};

/// Create the tray, wire menu/left-click handlers, subscribe to tray_menu/tray_state.
/// C-ARCH-1: sole caller is main.rs `.setup`.
pub(crate) fn create_tray(app: &AppHandle) -> tauri::Result<()> {
    // Start with the idle icon so tray matches AppState.IDLE.
    let icon = load_tray_icon(app, "idle").or_else(|| app.default_window_icon().cloned());
    let menu = empty_menu(app)?;

    // macOS: left-click opens menu by convention; Windows/Linux: right-click.
    let show_menu_on_left_click = cfg!(target_os = "macos");

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip(TRAY_TOOLTIP)
        .menu(&menu)
        .show_menu_on_left_click(show_menu_on_left_click)
        .on_menu_event(|app, event| {
            // dispatch_inner: tray_click is Rust-only; public dispatch would
            // reject it via the renderer allowlist.
            let args = DispatchArgs {
                cmd: "tray_click".to_string(),
                data: Some(serde_json::json!({ "id": event.id().as_ref() })),
            };
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let state: tauri::State<'_, Arc<SidecarState>> = app.state();
                if let Err(e) = dispatch_inner(args, state.inner().clone()).await {
                    log::warn!("[TRAY] tray_click dispatch failed: {}", e);
                }
            });
        })
        .on_tray_icon_event(|tray, event| {
            log::debug!("[TRAY] icon click event: {:?}", event);
            // macOS: menu already opened on left-click; show+focus would steal it.
            if cfg!(target_os = "macos") {
                return;
            }
            // Left-click only → raise main window (shared raise path).
            if is_focus_main_window_event(&event) {
                crate::host_events::show_main_window(tray.app_handle());
            }
        });

    if let Some(icon) = icon {
        builder = builder.icon(icon);
    }

    // macOS menubar: template image (alpha mask, OS-tinted). No-op elsewhere.
    if cfg!(target_os = "macos") {
        builder = builder.icon_as_template(true);
    }

    let _tray = builder.build(app)?;

    // Rebuild menu when Python publishes tray_menu.
    let app_clone = app.clone();
    app.listen("tray_menu", move |event| {
        let payload: TrayMenuPayload = match serde_json::from_str(event.payload()) {
            Ok(p) => p,
            Err(e) => {
                log::warn!("[TRAY] failed to parse tray_menu payload: {}", e);
                return;
            }
        };
        let app_inner = app_clone.clone();
        // spawn_blocking: keep OS tray APIs off the event-loop thread.
        #[allow(clippy::let_underscore_future)] // fire-and-forget; body logs errors
        let _ = tauri::async_runtime::spawn_blocking(move || {
            if let Err(e) = rebuild_tray_menu(&app_inner, &payload.items) {
                log::error!("[TRAY] failed to rebuild menu: {}", e);
            }
        });
    });

    // tray_state: Python publishes icon + tooltip; host applies them.
    let app_clone_state = app.clone();
    app.listen("tray_state", move |event| {
        let payload: TrayStatePayload = match serde_json::from_str(event.payload()) {
            Ok(p) => p,
            Err(e) => {
                log::warn!("[TRAY] failed to parse tray_state payload: {}", e);
                return;
            }
        };
        let app_inner = app_clone_state.clone();
        // spawn_blocking: set_icon/set_tooltip may IPC-block the OS tray.
        #[allow(clippy::let_underscore_future)] // fire-and-forget; body logs errors
        let _ = tauri::async_runtime::spawn_blocking(move || {
            if let Some(icon_name) = &payload.icon {
                // Cache for bubble-dismiss gating (toggle = start/stop).
                {
                    let state: tauri::State<'_, Arc<SidecarState>> = app_inner.state();
                    *crate::state::lock(&state.last_tray_icon) = icon_name.clone();
                }
            }
            if let Some(tray) = app_inner.tray_by_id(TRAY_ID) {
                if let Some(icon_name) = &payload.icon {
                    if let Some(img) = load_tray_icon(&app_inner, icon_name) {
                        if let Err(e) = tray.set_icon(Some(img)) {
                            log::warn!("[TRAY] set_icon({}) failed: {}", icon_name, e);
                        }
                    } else {
                        log::warn!(
                            "[TRAY] tray_state icon {:?} not available, leaving icon unchanged",
                            icon_name
                        );
                    }
                }
                if let Some(tooltip) = &payload.tooltip {
                    if let Err(e) = tray.set_tooltip(Some(tooltip)) {
                        log::warn!("[TRAY] set_tooltip({:?}) failed: {}", tooltip, e);
                    }
                }
            } else {
                log::warn!(
                    "[TRAY] tray_by_id({}) returned None: tray not yet built?",
                    TRAY_ID
                );
            }
        });
    });

    Ok(())
}

/// Create tray and mark availability in SidecarState (C-ARCH-1 wiring from main).
/// Tray failure is non-fatal: close flow falls through to exit, not hide-to-tray.
pub(crate) fn create_tray_and_mark_state(app: &AppHandle) {
    if let Err(e) = create_tray(app) {
        log::error!("[TRAY] init failed: {}", e);
    } else {
        app.state::<Arc<SidecarState>>().mark_tray_available();
    }
}

/// Rebuild tray menu from item list and re-apply to the existing tray icon.
fn rebuild_tray_menu(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<()> {
    let menu = build_menu(app, items)?;
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        tray.set_menu(Some(menu))?;
        log::debug!(
            "[TRAY] menu rebuilt ({} item{})",
            items.len(),
            if items.len() == 1 { "" } else { "s" }
        );
    }
    Ok(())
}

// C-TEST-5: sibling test file.
#[cfg(test)]
#[path = "tray_tests.rs"]
mod tray_tests;
