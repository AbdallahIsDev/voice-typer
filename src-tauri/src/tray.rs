//! System tray (ADR-0020 §6.5 / Task 2).
//!
//! The Python sidecar owns the tray menu model (locale, dynamic items,
//! checkboxes) and publishes it under the `tray_menu` event
//! (`{"type":"tray_menu","data":{"items":[...]}}`). The existing WS
//! re-emit path in `sidecar::ws` forwards every server event as a Tauri
//! event, so we only listen for `tray_menu` here and rebuild the native
//! menu from `data.items`.
//!
//! Menu item shape (mirrors the Python `MenuItem`):
//! ```json
//! { "id": str, "label": str, "disabled": bool,
//!   "separator": bool, "checked": Optional[bool],
//!   "submenu": Optional[list] }
//! ```
//!
//! On item click we dispatch `{"cmd":"tray_click","data":{"id": <id>}}`
//! back to the sidecar via the generic `dispatch` command (emitted as a
//! Tauri event that the WS bridge picks up). Left-click (no item)
//! focuses the main window.

use serde::Deserialize;
use tauri::menu::{IsMenuItem, MenuBuilder, MenuItemBuilder, PredefinedMenuItem, SubmenuBuilder};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Emitter, Listener, Manager};

type R = tauri::Wry;

#[derive(Debug, Clone, Deserialize)]
struct MenuItemData {
    #[serde(default)]
    id: String,
    #[serde(default)]
    label: String,
    #[serde(default)]
    disabled: bool,
    #[serde(default)]
    separator: bool,
    #[serde(default)]
    checked: Option<bool>,
    #[serde(default)]
    submenu: Option<Vec<MenuItemData>>,
}

#[derive(Debug, Clone, Deserialize)]
struct TrayMenuPayload {
    #[serde(default)]
    items: Vec<MenuItemData>,
}

const TRAY_TOOLTIP: &str = "Voice Typer";
const TRAY_ID: &str = "voice-typer-tray";

/// Build the list of `IsMenuItem` boxed items for `items`. Each entry is
/// either a separator, a leaf `MenuItem` (with optional checkmark via
/// the accelerator text), or a nested `Submenu`.
fn build_item_refs(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<Vec<Box<dyn IsMenuItem<R>>>> {
    let mut out: Vec<Box<dyn IsMenuItem<R>>> = Vec::with_capacity(items.len());
    for item in items {
        if item.separator {
            let sep = PredefinedMenuItem::separator(app)?;
            out.push(Box::new(sep));
            continue;
        }
        if let Some(sub) = &item.submenu {
            let children = build_item_refs(app, sub)?;
            let refs: Vec<&dyn IsMenuItem<R>> = children.iter().map(|b| b.as_ref()).collect();
            let submenu = SubmenuBuilder::new(app, &item.label)
                .enabled(!item.disabled)
                .items(&refs)
                .build()?;
            out.push(Box::new(submenu));
            continue;
        }
        let check: Option<&str> = item.checked.map(|c| if c { "✓" } else { "" });
        let mut b = MenuItemBuilder::with_id(item.id.clone(), &item.label).enabled(!item.disabled);
        if let Some(acc) = check {
            b = b.accelerator(acc);
        }
        let mi = b.build(app)?;
        out.push(Box::new(mi));
    }
    Ok(out)
}

/// Build a (possibly nested) `Menu` from the serialized item list.
fn build_menu(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<tauri::menu::Menu<R>> {
    let built = build_item_refs(app, items)?;
    let refs: Vec<&dyn IsMenuItem<R>> = built.iter().map(|b| b.as_ref()).collect();
    MenuBuilder::new(app).items(&refs).build()
}

/// Build an empty (single disabled placeholder) menu so the tray always
/// has a menu handle before the first `tray_menu` event arrives.
fn empty_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<R>> {
    let item = MenuItemBuilder::with_id("hidden_placeholder", "Voice Typer")
        .enabled(false)
        .build(app)?;
    MenuBuilder::new(app).items(&[&item]).build()
}

/// Create the tray icon, attach the initial empty menu, set tooltip +
/// icon, and wire menu-click + left-click handlers. Also subscribes to
/// the `tray_menu` event to rebuild the menu on demand.
pub fn create_tray(app: &AppHandle) -> tauri::Result<()> {
    let icon = app.default_window_icon().cloned();
    let menu = empty_menu(app)?;

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip(TRAY_TOOLTIP)
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| {
            // `event.id()` is the string id we assigned via `with_id`.
            let id = event.id().as_ref().to_string();
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                let payload = serde_json::json!({
                    "cmd": "tray_click",
                    "data": { "id": id }
                });
                // Forward through the existing generic `dispatch` path.
                let _ = app.emit("dispatch", payload);
            });
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click { .. } = event {
                if let Some(window) = tray.app_handle().get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        });

    if let Some(icon) = icon {
        builder = builder.icon(icon);
    }

    let _tray = builder.build(app)?;

    // Rebuild the menu whenever the Python sidecar publishes `tray_menu`.
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
        tauri::async_runtime::spawn(async move {
            if let Err(e) = rebuild_tray_menu(&app_inner, &payload.items) {
                log::error!("[TRAY] failed to rebuild menu: {}", e);
            }
        });
    });

    Ok(())
}

/// Rebuild the tray menu from the item list and re-apply it to the
/// existing tray icon (matched by id `TRAY_ID`).
fn rebuild_tray_menu(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<()> {
    let menu = build_menu(app, items)?;
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        tray.set_menu(Some(menu))?;
    }
    Ok(())
}
