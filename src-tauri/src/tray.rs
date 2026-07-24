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
//! back to the sidecar via the shared `dispatch_frame` helper (CR-14:
//! previously the click was forwarded by emitting a Tauri event named
//! `"dispatch"` that had no listener — `app.emit("dispatch", payload)`
//! was dead code, so the click was silently dropped). Left-click (no
//! item) focuses the main window.

use serde::Deserialize;
use std::sync::Arc;
use tauri::menu::{
    CheckMenuItemBuilder, IsMenuItem, MenuBuilder, MenuItemBuilder, PredefinedMenuItem,
    SubmenuBuilder,
};
use tauri::tray::{TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Listener, Manager};
use tauri::image::Image;

// CR-5: use `dispatch_inner` (no allowlist gate — `tray_click` is a
// Rust-only command not in the renderer `ALLOWED_COMMANDS` set) which
// internally delegates to session-2's shared `dispatch_frame` helper
// (CR-14). This combines both sessions' fixes for the dropped-tray-click
// bug: session-1 added the typed `dispatch_inner`/`DispatchArgs` path
// for trusted Rust callers; session-2 extracted the WS-send body into
// `dispatch_frame` so the public `dispatch` command and the tray
// handler share one implementation.
use crate::commands::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;

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

/// CR-6: payload shape for the `tray_state` event emitted by the
/// Python sidecar. `icon` is a logical name (`"idle"`, `"recording"`,
/// `"transcribing"`, `"error"`) that the Rust host maps to a bundled
/// tray icon resource. `tooltip` is the new tooltip string (e.g.
/// "Voice Typer — Recording (12s)"). Both fields are optional — the
/// host only updates the field(s) present in the payload.
#[derive(Debug, Clone, Deserialize)]
struct TrayStatePayload {
    #[serde(default)]
    icon: Option<String>,
    #[serde(default)]
    tooltip: Option<String>,
}

const TRAY_TOOLTIP: &str = "Voice Typer";
const TRAY_ID: &str = "voice-typer-tray";

/// CR-6: map a logical icon name (`"idle"`, `"recording"`,
/// `"transcribing"`, `"error"`) emitted by the Python sidecar to a
/// bundled Tauri image resource. Returns `None` if the name is unknown
/// (caller logs and skips the icon update — non-fatal).
///
/// The icon files live under `src-tauri/icons/tray/` and are declared
/// in `bundle.resources` of the per-arch Tauri config overrides so
/// they're shipped with the bundle. At runtime they're resolved via
/// `app.path().resource_dir()` → `tray/<name>.png`.
///
/// If the icon file is missing on disk (e.g. a fresh dev checkout that
/// hasn't run the icon-generation script), the call returns `None` —
/// the tray icon is left unchanged so the app still runs.
fn load_tray_icon(app: &AppHandle, name: &str) -> Option<Image<'static>> {
    // Whitelist the logical names — never load an arbitrary path from
    // the sidecar (defense against a compromised sidecar trying to read
    // an arbitrary file via the tray icon path).
    let allowed = match name {
        "idle" | "recording" | "transcribing" | "error" => name,
        _ => {
            log::warn!("[TRAY] ignoring unknown tray_state icon name: {:?}", name);
            return None;
        }
    };
    let resource_dir = app.path().resource_dir().ok()?;
    let path = resource_dir.join("tray").join(format!("{}.png", allowed));
    let bytes = std::fs::read(&path).ok()?;
    match Image::from_path(&path) {
        Ok(img) => Some(img),
        Err(e) => {
            log::warn!(
                "[TRAY] failed to decode tray icon {:?} ({} bytes from {}): {}",
                allowed,
                bytes.len(),
                path.display(),
                e
            );
            None
        }
    }
}

/// Build the list of `IsMenuItem` boxed items for `items`. Each entry is
/// either a separator, a leaf `MenuItem` (or `CheckMenuItem` when
/// `checked` is `Some` — PVT-16: native checkmark, not accelerator
/// text), or a nested `Submenu`.
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
        // PVT-16: use the native `CheckMenuItemBuilder` (Tauri v2) for
        // items with a `checked` state instead of faking the checkmark
        // via `.accelerator("✓")` (accelerators are keyboard shortcuts,
        // not visual state — the old hack rendered as a literal "✓"
        // keyboard-equivalent on macOS and as no-op accelerator text on
        // Windows/Linux, never as a real native checkmark).
        //
        // `CheckMenuItem` is a distinct type from `MenuItem`, but both
        // implement `IsMenuItem<R>` so they share the `Box<dyn
        // IsMenuItem<R>>` slot. The `on_menu_event` handler reads
        // `event.id()`, which is identical for both kinds, so click
        // dispatch is unchanged.
        if let Some(checked) = item.checked {
            let mi = CheckMenuItemBuilder::with_id(item.id.clone(), &item.label)
                .enabled(!item.disabled)
                .checked(checked)
                .build(app)?;
            out.push(Box::new(mi));
        } else {
            let mi = MenuItemBuilder::with_id(item.id.clone(), &item.label)
                .enabled(!item.disabled)
                .build(app)?;
            out.push(Box::new(mi));
        }
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
            // CR-5: invoke the `tray_click` command on the Python
            // sidecar DIRECTLY via `dispatch_inner` — the previous
            // implementation emitted a Tauri event named `dispatch`
            // that nobody listened to (events ≠ commands in Tauri).
            // The click silently dropped.
            //
            // `tray_click` is a Rust-only command — the renderer never
            // invokes it — so it is NOT in the renderer-side
            // `ALLOWED_COMMANDS` allowlist. The public `dispatch`
            // Tauri command (CR-4) enforces the allowlist and would
            // reject `tray_click`. We therefore call `dispatch_inner`
            // directly, which is the WS-send path WITHOUT the
            // allowlist gate (callers are trusted Rust code).
            let id = event.id().as_ref().to_string();
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                // CR-5 + CR-14 (combined): previously emitted a Tauri event
                // named "dispatch" via `app.emit("dispatch", payload)` — but
                // no listener was registered for that event (the renderer
                // invokes the `dispatch` *command* via `invoke('dispatch',
                // ...)`, not by listening to a "dispatch" event). The emit
                // was dead code, so tray clicks were silently dropped. Now
                // call `dispatch_inner` (CR-5) which delegates to the shared
                // `dispatch_frame` helper (CR-14) — the same WS-send path
                // the renderer's `invoke('dispatch', ...)` takes, but
                // without the ALLOWED_COMMANDS gate (CR-4) since
                // `tray_click` is a Rust-only command.
                let state: tauri::State<'_, Arc<SidecarState>> = app.state();
                let args = DispatchArgs {
                    cmd: "tray_click".to_string(),
                    data: Some(serde_json::json!({ "id": id })),
                };
                if let Err(e) = dispatch_inner(args, state.inner().clone()).await {
                    log::warn!("[TRAY] tray_click dispatch failed: {}", e);
                }
            });
        })
        .on_tray_icon_event(|tray, event| {
            // GT-B4-7: log the raw event at debug so a future regression
            // in tray click handling surfaces in the rotating log.
            log::debug!("[TRAY] icon click event: {:?}", event);
            if let TrayIconEvent::Click { .. } = event {
                if let Some(window) = tray.app_handle().get_webview_window("main") {
                    if let Err(e) = window.show() {
                        log::warn!("[TRAY] show failed: {}", e);
                    }
                    if let Err(e) = window.set_focus() {
                        log::warn!("[TRAY] set_focus failed: {}", e);
                    }
                } else {
                    log::warn!("[TRAY] main window not found on tray click");
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

    // CR-6 (Rust side): listen for `tray_state` events from the Python
    // sidecar and update the tray icon + tooltip.
    //
    // GT-E3-7 (option b — preferred): the Python-side publish path
    // (`tray.py::set_state` emitting `tray_state` via the WS bridge) is
    // NOT YET WIRED — it's owned by Fix-E (GT-FIX-13 owns `tray.py`).
    // Until Fix-E lands, this listener is a no-op but is kept DEFENSIVELY
    // so the moment Fix-E adds the publish path, the Rust side starts
    // moving the icon + tooltip with no further host changes. The
    // alternative (option a — delete the listener + `TrayStatePayload` +
    // `load_tray_icon` + the 5 payload tests) was rejected because it
    // would create a coordinated two-PR landing requirement.
    //
    // Defensive — Python publish path pending Fix-E.
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
        tauri::async_runtime::spawn(async move {
            if let Some(tray) = app_inner.tray_by_id(TRAY_ID) {
                if let Some(icon_name) = &payload.icon {
                    if let Some(img) = load_tray_icon(&app_inner, icon_name) {
                        if let Err(e) = tray.set_icon(Some(img)) {
                            log::warn!("[TRAY] set_icon({}) failed: {}", icon_name, e);
                        }
                    } else {
                        log::warn!(
                            "[TRAY] tray_state icon {:?} not available — leaving icon unchanged",
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
                log::warn!("[TRAY] tray_by_id({}) returned None — tray not yet built?", TRAY_ID);
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


#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // ── CR-6: TrayStatePayload parsing ───────────────────────────────

    #[test]
    fn test_tray_state_payload_parses_icon_only() {
        let p: TrayStatePayload =
            serde_json::from_str(r#"{"icon":"recording"}"#).expect("parse");
        assert_eq!(p.icon.as_deref(), Some("recording"));
        assert!(p.tooltip.is_none());
    }

    #[test]
    fn test_tray_state_payload_parses_tooltip_only() {
        let p: TrayStatePayload =
            serde_json::from_str(r#"{"tooltip":"Voice Typer — Recording"}"#).expect("parse");
        assert!(p.icon.is_none());
        assert_eq!(p.tooltip.as_deref(), Some("Voice Typer — Recording"));
    }

    #[test]
    fn test_tray_state_payload_parses_both_fields() {
        let p: TrayStatePayload =
            serde_json::from_str(r#"{"icon":"error","tooltip":"Voice Typer — Error"}"#)
                .expect("parse");
        assert_eq!(p.icon.as_deref(), Some("error"));
        assert_eq!(p.tooltip.as_deref(), Some("Voice Typer — Error"));
    }

    #[test]
    fn test_tray_state_payload_parses_empty_object() {
        let p: TrayStatePayload = serde_json::from_str(r#"{}"#).expect("parse");
        assert!(p.icon.is_none());
        assert!(p.tooltip.is_none());
    }

    #[test]
    fn test_tray_state_payload_ignores_unknown_fields() {
        let p: TrayStatePayload =
            serde_json::from_str(r#"{"icon":"idle","tooltip":"ok","future_field":42}"#)
                .expect("parse");
        assert_eq!(p.icon.as_deref(), Some("idle"));
        assert_eq!(p.tooltip.as_deref(), Some("ok"));
    }

    // ── CR-6: TrayMenuPayload still parses (regression guard) ────────

    #[test]
    fn test_tray_menu_payload_parses_items() {
        let p: TrayMenuPayload =
            serde_json::from_str(r#"{"items":[{"id":"quit","label":"Quit"}]}"#).expect("parse");
        assert_eq!(p.items.len(), 1);
        assert_eq!(p.items[0].id, "quit");
        assert_eq!(p.items[0].label, "Quit");
    }

    #[test]
    fn test_tray_menu_payload_parses_empty_items() {
        let p: TrayMenuPayload = serde_json::from_str(r#"{"items":[]}"#).expect("parse");
        assert!(p.items.is_empty());
    }

    #[test]
    fn test_tray_menu_payload_parses_missing_items_default_empty() {
        let p: TrayMenuPayload = serde_json::from_str(r#"{}"#).expect("parse");
        assert!(p.items.is_empty(), "items defaults to empty vec");
    }

    #[test]
    fn test_tray_menu_payload_parses_separator() {
        let p: TrayMenuPayload = serde_json::from_str(
            r#"{"items":[{"id":"a","label":"A"},{"separator":true},{"id":"b","label":"B"}]}"#,
        )
        .expect("parse");
        assert_eq!(p.items.len(), 3);
        assert!(!p.items[0].separator);
        assert!(p.items[1].separator);
        assert!(!p.items[2].separator);
    }

    #[test]
    fn test_tray_menu_payload_parses_checked_state() {
        let p: TrayMenuPayload = serde_json::from_str(
            r#"{"items":[{"id":"x","label":"X","checked":true},{"id":"y","label":"Y","checked":false}]}"#,
        )
        .expect("parse");
        assert_eq!(p.items[0].checked, Some(true));
        assert_eq!(p.items[1].checked, Some(false));
    }

    #[test]
    fn test_tray_menu_payload_parses_submenu() {
        let p: TrayMenuPayload = serde_json::from_str(
            r#"{"items":[{"id":"models","label":"Models","submenu":[{"id":"m1","label":"M1"}]}]}"#,
        )
        .expect("parse");
        assert_eq!(p.items.len(), 1);
        let sub = p.items[0].submenu.as_ref().expect("submenu present");
        assert_eq!(sub.len(), 1);
        assert_eq!(sub[0].id, "m1");
    }

    // ── CR-6: load_tray_icon name whitelist (defense in depth) ──────

    const ALLOWED_ICON_NAMES: &[&str] = &["idle", "recording", "transcribing", "error"];

    #[test]
    fn test_allowed_icon_names_are_stable() {
        assert_eq!(
            ALLOWED_ICON_NAMES,
            &["idle", "recording", "transcribing", "error"],
            "ALLOWED_ICON_NAMES changed — update src-tauri/icons/tray/ + bundle.resources too"
        );
    }

    #[test]
    fn test_allowed_icon_names_rejects_arbitrary_path() {
        let bad_names = [
            "",
            ".",
            "..",
            "../etc/passwd",
            "/etc/passwd",
            "idle.png",
            "IDLE",
            "recording ",
            "recording\x00.png",
            "arbitrary_name",
        ];
        for bad in bad_names {
            assert!(
                !ALLOWED_ICON_NAMES.contains(&bad),
                "sentinel {:?} should NOT be in ALLOWED_ICON_NAMES",
                bad
            );
        }
    }

    // ── CR-5: DispatchArgs construction shape (regression guard) ────

    #[test]
    fn test_dispatch_args_tray_click_shape() {
        let args = DispatchArgs {
            cmd: "tray_click".to_string(),
            data: Some(json!({ "id": "toggle_dictation" })),
        };
        assert_eq!(args.cmd, "tray_click");
        assert_eq!(
            args.data,
            Some(json!({ "id": "toggle_dictation" }))
        );
    }

    #[test]
    fn test_dispatch_args_tray_click_shape_with_empty_id() {
        let args = DispatchArgs {
            cmd: "tray_click".to_string(),
            data: Some(json!({ "id": "" })),
        };
        let serialized = serde_json::to_string(&args).expect("serialize");
        assert!(serialized.contains("\"cmd\":\"tray_click\""));
        assert!(serialized.contains("\"id\":\"\""));
    }
}
