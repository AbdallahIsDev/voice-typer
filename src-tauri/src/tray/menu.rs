//! Menu deserialization types + native menu builders.
//!
//! Split out of the former monolithic `tray.rs` so the serde payload
//! shapes (the mirror of the Python sidecar's `MenuItem` model) and
//! the recursive native-menu construction live in one focused module.
//! Re-exported from `crate::tray` so existing
//! `crate::tray::{TrayMenuPayload, TrayStatePayload, MenuItemData,
//! build_menu, empty_menu}` paths keep resolving.

use serde::Deserialize;
use tauri::menu::{
    CheckMenuItemBuilder, IsMenuItem, MenuBuilder, MenuItemBuilder, PredefinedMenuItem,
    SubmenuBuilder,
};
use tauri::AppHandle;

type R = tauri::Wry;

// Field visibility: `pub(crate)` — the sibling test module
// (`tray_tests.rs`, child of `crate::tray`) and the `create_tray`
// wiring in the parent module read the deserialized fields directly.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct MenuItemData {
    #[serde(default)]
    pub(crate) id: String,
    #[serde(default)]
    pub(crate) label: String,
    #[serde(default)]
    pub(crate) disabled: bool,
    #[serde(default)]
    pub(crate) separator: bool,
    #[serde(default)]
    pub(crate) checked: Option<bool>,
    #[serde(default)]
    pub(crate) submenu: Option<Vec<MenuItemData>>,
    // Optional keyboard accelerator (e.g. "Cmd+Q",
    // "Ctrl+Shift+R", "F5"). Populated by the Python sidecar's
    // `build_tray_menu_model` when an item has a shortcut. When
    // present, the native menu item is built with `.accelerator(...)` so
    // the OS renders the platform-correct keyboard equivalent hint next
    // to the label (e.g. "⌘Q" on macOS, "Ctrl+Q" on Windows/Linux) AND
    // wires the global shortcut so the user can trigger the item without
    // opening the tray menu.
    //
    // The string format is Tauri's accelerator grammar (NOT Qt or GTK):
    //   - Modifiers: "Control" / "Ctrl", "Shift", "Alt" / "Option",
    //     "Super" / "Cmd" / "Command"
    //   - Key: a single key name ("A", "F5", "Space", "Enter", etc.)
    //   - Joined with "+" — e.g. "Cmd+Shift+R"
    // Tauri validates the string at build time; an invalid accelerator
    // surfaces as a `tauri::Error` from `MenuItemBuilder::build`.
    #[serde(default)]
    pub(crate) accelerator: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
pub(crate) struct TrayMenuPayload {
    #[serde(default)]
    pub(crate) items: Vec<MenuItemData>,
}

//payload shape for the `tray_state` event emitted by the
/// Python sidecar. `icon` is a logical name (`"idle"`, `"recording"`,
/// `"transcribing"`, `"error"`) that the Rust host maps to a bundled
/// tray icon resource. `tooltip` is the new tooltip string (e.g.
/// "Voice Typer — Recording (12s)"). Both fields are optional — the
/// host only updates the fields present in the payload.
#[derive(Debug, Clone, Deserialize)]
pub(crate) struct TrayStatePayload {
    #[serde(default)]
    pub(crate) icon: Option<String>,
    #[serde(default)]
    pub(crate) tooltip: Option<String>,
}

/// Build the list of `IsMenuItem` boxed items for `items`. Each entry is
/// either a separator, a leaf `MenuItem` (or `CheckMenuItem` when
//`checked` is `Some` — : native checkmark, not accelerator
/// text), or a nested `Submenu`.
fn build_item_refs(
    app: &AppHandle,
    items: &[MenuItemData],
) -> tauri::Result<Vec<Box<dyn IsMenuItem<R>>>> {
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
        //use the native `CheckMenuItemBuilder` (Tauri v2) for
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
            let mut mi = CheckMenuItemBuilder::with_id(item.id.clone(), &item.label)
                .enabled(!item.disabled)
                .checked(checked);
            // Forward the optional accelerator to the
            // native menu item. `CheckMenuItemBuilder::accelerator` takes
            // `S: AsRef<str>` — `&String` satisfies that. Tauri validates
            // the accelerator string at `build` time; an invalid string
            // (e.g. "Cmd+XYZ") surfaces as a `tauri::Error` from `.build()`
            // below, which the caller (`build_menu` → `rebuild_tray_menu`)
            // logs and propagates.
            if let Some(acc) = &item.accelerator {
                mi = mi.accelerator(acc);
            }
            let mi = mi.build(app)?;
            out.push(Box::new(mi));
        } else {
            let mut mi =
                MenuItemBuilder::with_id(item.id.clone(), &item.label).enabled(!item.disabled);
            // Same accelerator forwarding as the
            // `CheckMenuItemBuilder` branch above. `MenuItemBuilder::
            // accelerator` is the same `S: AsRef<str>` signature.
            if let Some(acc) = &item.accelerator {
                mi = mi.accelerator(acc);
            }
            let mi = mi.build(app)?;
            out.push(Box::new(mi));
        }
    }
    Ok(out)
}

/// Build a (possibly nested) `Menu` from the serialized item list.
pub(crate) fn build_menu(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<tauri::menu::Menu<R>> {
    let built = build_item_refs(app, items)?;
    let refs: Vec<&dyn IsMenuItem<R>> = built.iter().map(|b| b.as_ref()).collect();
    MenuBuilder::new(app).items(&refs).build()
}

/// Build an empty (single disabled placeholder) menu so the tray always
/// has a menu handle before the first `tray_menu` event arrives.
pub(crate) fn empty_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<R>> {
    // Use `APP_NAME` instead of an inline brand literal so
    // the placeholder label stays in sync with the rest of the UI.
    let item = MenuItemBuilder::with_id("hidden_placeholder", crate::branding::APP_NAME)
        .enabled(false)
        .build(app)?;
    MenuBuilder::new(app).items(&[&item]).build()
}
