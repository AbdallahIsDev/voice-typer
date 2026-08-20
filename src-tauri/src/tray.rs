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
//! back to the sidecar via the shared `dispatch_frame` helper (:
//! previously the click was forwarded by emitting a Tauri event named
//! `"dispatch"` that had no listener — `app.emit("dispatch", payload)`
//! was dead code, so the click was silently dropped). Left-click (no
//! item) focuses the main window.

use serde::Deserialize;
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};
use tauri::image::Image;
use tauri::menu::{
    CheckMenuItemBuilder, IsMenuItem, MenuBuilder, MenuItemBuilder, PredefinedMenuItem,
    SubmenuBuilder,
};
use tauri::tray::{MouseButton, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Listener, Manager};

//use `dispatch_inner` (no allowlist gate — `tray_click` is a
// Rust-only command not in the renderer `ALLOWED_COMMANDS` set) which
// internally delegates to session-2's shared `dispatch_frame` helper
//(). This combines both sessions' fixes for the dropped-tray-click
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
    // Optional keyboard accelerator (e.g. "Cmd+Q",
    // "Ctrl+Shift+R", "F5"). Populated by the Python sidecar's
    // `build_tray_menu_model` (Fix-6) when an item has a shortcut. When
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
    accelerator: Option<String>,
}

#[derive(Debug, Clone, Deserialize)]
struct TrayMenuPayload {
    #[serde(default)]
    items: Vec<MenuItemData>,
}

//payload shape for the `tray_state` event emitted by the
/// Python sidecar. `icon` is a logical name (`"idle"`, `"recording"`,
/// `"transcribing"`, `"error"`) that the Rust host maps to a bundled
/// tray icon resource. `tooltip` is the new tooltip string (e.g.
/// "Voice Typer — Recording (12s)"). Both fields are optional — the
/// host only updates the fields present in the payload.
#[derive(Debug, Clone, Deserialize)]
struct TrayStatePayload {
    #[serde(default)]
    icon: Option<String>,
    #[serde(default)]
    tooltip: Option<String>,
}

// Tray tooltip + placeholder label use the cross-language
// `APP_NAME` constant from `branding.rs` (mirrors
// `voice_typer/server/branding.py::APP_NAME` and
// `voice_typer/client/src/main/branding.ts::APP_NAME`). Replaces two
// inline brand literals that were drift hazards.
const TRAY_TOOLTIP: &str = crate::branding::APP_NAME;
const TRAY_ID: &str = "voice-typer-tray";

//Process-wide cache of decoded tray icons, keyed by the logical
// name (`"idle"` / `"recording"` / `"transcribing"` / `"error"`).
//
// Without this cache, every `tray_state` event re-read the PNG from
// disk (`resource_dir/icons/tray/<name>.png`) AND re-decoded it via
// `Image::from_path` (which allocates an RGBA buffer + runs the PNG
// decoder). For a typical session the icon flips between `idle` ↔
// `recording` ↔ `transcribing` dozens of times — each flip paid the
// disk-read + decode cost. The cache holds the decoded `Image<'static>`
// (an `Arc`-backed `Cow<[u8]>` internally, so `.clone()` is a single
// atomic increment) and serves subsequent lookups from memory.
//
// `OnceLock<Mutex<HashMap<...>>>` is used instead of a plain
// `OnceLock<HashMap<...>>` because `OnceLock::get_or_init` returns an
// immutable `&T` — we need interior mutability to insert cache-miss
// entries after init. `Mutex` (not `RwLock`) is fine here because the
// cache is read+written under a single short critical section (no I/O
// under the lock — disk read + decode happen BEFORE the lock is taken
// on a cache miss, and the lock is only held for the `HashMap::get` /
// `HashMap::insert`). Tray-state events are low-frequency (a handful
// per session), so even if two threads raced a cache miss on the same
// icon name, both would decode + one `insert` would win — the loser's
// decoded `Image` is dropped (cheap, just an `Arc` decrement).
static TRAY_ICON_CACHE: OnceLock<Mutex<HashMap<String, Image<'static>>>> = OnceLock::new();

/// Predicate that returns `true` iff `name` is one of the four
/// whitelisted tray icon logical names (`idle`, `recording`,
/// `transcribing`, `error`). Used by `load_tray_icon` to defend against
/// a compromised sidecar trying to read an arbitrary file via the tray
/// icon path (e.g. `icon: "../../../etc/passwd"` would otherwise be
/// joined to `resource_dir/icons/tray/../../../etc/passwd.png` and read).
///
/// Extracted from `load_tray_icon` so the whitelist is unit-testable in
/// isolation (calling `load_tray_icon` directly would require a live
/// `AppHandle` + the bundled resource dir — both unavailable in `cargo
/// test`). The test module asserts `is_allowed_icon_name` agrees with
/// the `ALLOWED_ICON_NAMES` test constant below.
///
/// The four names here MUST exactly match the filenames
/// emitted by `voice_typer/client/scripts/generate-icons.mjs` under
/// `src-tauri/icons/tray/` (the icon-generation script writes
/// `{idle,recording,transcribing,error}.png`). A mismatch surfaces as a
/// "tray_state icon not available" warning at runtime — non-fatal but
/// the tray icon stops updating.
fn is_allowed_icon_name(name: &str) -> bool {
    matches!(name, "idle" | "recording" | "transcribing" | "error")
}

/// Map a logical icon name (`"idle"`, `"recording"`,
/// `"transcribing"`, `"error"`) emitted by the Python sidecar to a
/// bundled Tauri image resource. Returns `None` if the name is unknown
/// (caller logs and skips the icon update — non-fatal).
///
/// The icon files live under `src-tauri/icons/tray/` and are declared
/// in `bundle.resources` (string entry `"icons/tray/"` — Tauri
/// preserves the relative path, so the files land at
/// `$RESOURCE/icons/tray/<name>.png`, mirroring the source tree) of
/// the base + per-arch Tauri configs so they're shipped with the
/// bundle. At runtime they're resolved via `app.path().resource_dir()`
/// → `icons/tray/<name>.png`.
///
/// If the icon file is missing on disk (e.g. a fresh dev checkout that
/// hasn't run the icon-generation script), the call returns `None` —
/// the tray icon is left unchanged so the app still runs.
///
/// On a cache miss, the PNG is read + decoded OUTSIDE the cache lock
/// (so a slow disk read doesn't block other threads' cache hits), then
/// inserted under a brief lock. On a cache hit, the cached
/// `Image<'static>` is cloned (cheap — `Arc`-backed `Cow<[u8]>`).
fn load_tray_icon(app: &AppHandle, name: &str) -> Option<Image<'static>> {
    // Whitelist the logical names — never load an arbitrary path from
    // the sidecar (defense against a compromised sidecar trying to read
    // an arbitrary file via the tray icon path).
    if !is_allowed_icon_name(name) {
        log::warn!("[TRAY] ignoring unknown tray_state icon name: {:?}", name);
        return None;
    }
    let allowed = name;

    // Fast path: cache hit. The cache is initialized on first use and
    // populated lazily as each icon name is requested for the first
    // time. `Mutex::lock` is a fast user-space lock (no syscall on the
    // uncontended path); the critical section is a single `HashMap::get`.
    let cache = TRAY_ICON_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(guard) = cache.lock() {
        if let Some(img) = guard.get(allowed) {
            return Some(img.clone());
        }
    } else {
        // Poisoned lock — fall through to the disk-read path so the
        // tray still updates. The cache is best-effort, not a correctness
        // requirement.
        log::warn!(
            "[TRAY] icon cache lock poisoned — bypassing cache for {:?}",
            allowed
        );
    }

    // Slow path: read + decode from disk. Done OUTSIDE the cache lock
    // so a slow disk doesn't block other threads' cache hits.
    let resource_dir = app.path().resource_dir().ok()?;
    let path = resource_dir
        .join("icons")
        .join("tray")
        .join(format!("{}.png", allowed));
    let bytes = std::fs::read(&path).ok()?;
    let img = match Image::from_path(&path) {
        Ok(img) => img,
        Err(e) => {
            log::warn!(
                "[TRAY] failed to decode tray icon {:?} ({} bytes from {}): {}",
                allowed,
                bytes.len(),
                path.display(),
                e
            );
            return None;
        }
    };

    // Insert into the cache. If another thread raced us and inserted
    // first, our insert is a no-op overwrite with an equivalent value
    // (same logical name → same file → same decoded bytes). The
    // `Mutex` guard is held only for the `HashMap::insert`, not the
    // disk read above.
    if let Ok(mut guard) = cache.lock() {
        guard.insert(allowed.to_string(), img.clone());
    }
    Some(img)
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
fn build_menu(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<tauri::menu::Menu<R>> {
    let built = build_item_refs(app, items)?;
    let refs: Vec<&dyn IsMenuItem<R>> = built.iter().map(|b| b.as_ref()).collect();
    MenuBuilder::new(app).items(&refs).build()
}

/// Build an empty (single disabled placeholder) menu so the tray always
/// has a menu handle before the first `tray_menu` event arrives.
fn empty_menu(app: &AppHandle) -> tauri::Result<tauri::menu::Menu<R>> {
    // Use `APP_NAME` instead of an inline brand literal so
    // the placeholder label stays in sync with the rest of the UI.
    let item = MenuItemBuilder::with_id("hidden_placeholder", crate::branding::APP_NAME)
        .enabled(false)
        .build(app)?;
    MenuBuilder::new(app).items(&[&item]).build()
}

//predicate that decides whether a tray icon event should
/// trigger the show + focus main-window path. Extracted from the
/// `on_tray_icon_event` closure so the button filter is unit-testable
/// (constructing a `TrayIconEvent` and asserting on the predicate is
/// much simpler than spinning up a real Tauri app + tray in a test).
///
/// Returns `true` ONLY for `TrayIconEvent::Click` with
/// `button == MouseButton::Left`. Right-click, middle-click, double-
/// click, mouse-enter, mouse-move, and mouse-leave all return `false`
/// — the OS / Tauri handles those (right-click opens the bound
/// `.menu(...)`, etc.).
fn is_focus_main_window_event(event: &TrayIconEvent) -> bool {
    matches!(
        event,
        TrayIconEvent::Click {
            button: MouseButton::Left,
            ..
        }
    )
}

/// Create the tray icon, attach the initial empty menu, set tooltip +
/// icon, and wire menu-click + left-click handlers. Also subscribes to
/// the `tray_menu` event to rebuild the menu on demand.
///
/// Visibility: `pub(crate)` — the only caller is `main.rs` at app
/// startup. Demoted from `pub` (which would expose the symbol on the
/// crate's public surface) because no external crate links against
/// `voice-typer` (it's a binary crate, not a library), and a tighter
/// visibility surfaces unintended cross-module couplings at compile
/// time rather than letting them slip through as silent API growth.
pub(crate) fn create_tray(app: &AppHandle) -> tauri::Result<()> {
    // Initial icon: the `idle` state icon (gray bars) so the tray
    // starts in the same visual state the Python sidecar starts in
    // (AppState.IDLE). Falls back to the default window icon when the
    // tray resources aren't available (e.g. a checkout that predates
    // the tray PNGs) — never a bare `None`, so the tray always shows a
    // real icon from the first frame. On macOS `icon_as_template(true)`
    // (set below) renders either as the menubar-colored bar shape.
    let icon = load_tray_icon(app, "idle").or_else(|| app.default_window_icon().cloned());
    let menu = empty_menu(app)?;

    // macOS opens the tray menu on LEFT-click by
    // convention (the menubar is the primary interaction surface —
    // right-click has no standard meaning). Windows/Linux use RIGHT-click
    // to open the context menu and reserve LEFT-click for our
    // show+focus-main-window handler (see `on_tray_icon_event` below).
    // `cfg!(target_os = ...)` returns a `const bool` so the branch is
    // resolved at compile time — no runtime cost on either platform.
    let show_menu_on_left_click = cfg!(target_os = "macos");

    let mut builder = TrayIconBuilder::with_id(TRAY_ID)
        .tooltip(TRAY_TOOLTIP)
        .menu(&menu)
        .show_menu_on_left_click(show_menu_on_left_click)
        .on_menu_event(|app, event| {
            //invoke the `tray_click` command on the Python
            // sidecar DIRECTLY via `dispatch_inner` — the previous
            // implementation emitted a Tauri event named `dispatch`
            // that nobody listened to (events ≠ commands in Tauri).
            // The click silently dropped.
            //
            // `tray_click` is a Rust-only command — the renderer never
            // invokes it — so it is NOT in the renderer-side
            // `ALLOWED_COMMANDS` allowlist. The public `dispatch`
            //Tauri command () enforces the allowlist and would
            // reject `tray_click`. We therefore call `dispatch_inner`
            // directly, which is the WS-send path WITHOUT the
            // allowlist gate (callers are trusted Rust code).
            let id = event.id().as_ref().to_string();
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                //+  (combined): previously emitted a Tauri event
                // named "dispatch" via `app.emit("dispatch", payload)` — but
                // no listener was registered for that event (the renderer
                // invokes the `dispatch` *command* via `invoke('dispatch',
                // ...)`, not by listening to a "dispatch" event). The emit
                // was dead code, so tray clicks were silently dropped. Now
                //call `dispatch_inner` () which delegates to the shared
                //`dispatch_frame` helper () — the same WS-send path
                // the renderer's `invoke('dispatch', ...)` takes, but
                //without the ALLOWED_COMMANDS gate () since
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
            //log the raw event at debug so a future regression
            // in tray click handling surfaces in the rotating log.
            log::debug!("[TRAY] icon click event: {:?}", event);
            // On macOS, `show_menu_on_left_click(true)` is
            // set above, so the OS opens the menu on left-click. If we
            // ALSO showed+focused the main window here, the window would
            // steal focus from the just-opened menu (the menu would flash
            // and disappear). We therefore skip the show+focus path
            // entirely on macOS — macOS users open the menu by clicking
            // the tray icon and focus the main window via the dock or
            // cmd+tab (the conventional macOS flow). On Windows/Linux,
            // `show_menu_on_left_click(false)` means left-click does NOT
            // open the menu, so we own the left-click behavior — the
            // existing show+focus path runs as before.
            //
            // `cfg!(target_os = "macos")` is a `const bool`, so the
            // branch is compile-time-resolved and the dead arm is elided
            // by the optimizer — zero runtime cost on either platform.
            if cfg!(target_os = "macos") {
                return;
            }
            //fix: only show + focus the main window on LEFT
            // click. The previous `TrayIconEvent::Click { .. }` pattern
            // matched left, right, AND middle click without filtering,
            // so right-clicking the tray icon (which the OS uses to open
            // the context menu on Windows/Linux) would race with menu
            // display — the main window stole focus from the menu, and
            // on some WMs the menu flashed and disappeared. Middle
            // click is intentionally ignored too (no binding for it).
            //
            // Tauri v2's `TrayIconEvent::Click` carries `button:
            // MouseButton` + `button_state: MouseButtonState`; we
            // delegate to the `is_focus_main_window_event` predicate
            // (extracted for unit-testability) so the show/focus path
            // only fires for left-clicks. Right-click falls through to
            // the OS default (Tauri v2 opens the bound `.menu(...)`
            // automatically on right-click on Windows + Linux).
            if is_focus_main_window_event(&event) {
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

    // On macOS, mark the tray icon as a [template
    // image](https://developer.apple.com/documentation/appkit/nsimage/1520017-template)
    // so the OS renders it as a single-color alpha mask — black on the
    // light menubar, white on the dark menubar. This is the conventional
    // macOS behavior for menubar icons: full-color icons look out of
    // place next to the system's monochrome SF Symbol-style icons. The
    // per-state colors (idle=gray, recording=green, transcribing=blue,
    // error=red) emitted by `generate-icons.mjs` are only visible on
    // Windows/Linux; on macOS the state is communicated via the tooltip
    // ("Voice Typer — Recording") and the bar SHAPE (which is identical
    // across states — only the alpha mask matters).
    //
    // `TrayIconBuilder::icon_as_template` is a no-op on Windows/Linux
    // (the underlying `set_icon_as_template` call is `#[cfg(target_os =
    // "macos")]` in Tauri's source), but we gate the call with
    // `cfg!(target_os = "macos")` anyway so the builder chain reads as
    // macOS-specific at a glance (and to avoid relying on the no-op
    // behavior in case Tauri ever changes it).
    if cfg!(target_os = "macos") {
        builder = builder.icon_as_template(true);
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
        //`rebuild_tray_menu` is fully synchronous (no `.await`
        // points), so wrapping it in `tauri::async_runtime::spawn(async
        // move { ... })` paid Tokio task-scheduler overhead for no async
        // benefit. The previous `std::thread::spawn` paid a per-event
        // OS-thread-creation cost (~50µs) — fine at low frequency, but
        // it allocated a fresh thread for every `tray_menu` publish.
        // `tauri::async_runtime::spawn_blocking` is the cached
        // equivalent: it dispatches the closure onto the Tokio blocking
        // thread pool, which is lazily grown and reused across calls.
        // The listener closure (which runs on the Tauri event-loop
        // thread) returns immediately; the blocking pool absorbs the
        // work without per-event thread allocation. The returned
        // `JoinHandle` is intentionally dropped (fire-and-forget) — the
        // body logs its own errors and returns `()`.
        #[allow(clippy::let_underscore_future)] // intentional fire-and-forget (comment above)
        let _ = tauri::async_runtime::spawn_blocking(move || {
            if let Err(e) = rebuild_tray_menu(&app_inner, &payload.items) {
                log::error!("[TRAY] failed to rebuild menu: {}", e);
            }
        });
    });

    //(Rust side): listen for `tray_state` events from the Python
    // sidecar and update the tray icon + tooltip.
    //
    //(option b — preferred): the Python-side publish path
    // (`tray.py::set_state` emitting `tray_state` via the WS bridge) is
    //NOT YET WIRED — it's owned by Fix-E ( owns `tray.py`).
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
        //the body below is fully synchronous (no `.await`s —
        // `tray_by_id`, `load_tray_icon`, `tray.set_icon`, and
        // `tray.set_tooltip` are all blocking Tauri APIs). The previous
        // `std::thread::spawn` allocated a fresh OS thread per event
        // (~50µs per allocation). With the `TRAY_ICON_CACHE` in place,
        // the icon-load path is a HashMap lookup + `Arc` clone on a
        // cache hit — the body is now ~µs-scale CPU work, but the OS
        // tray APIs (`set_icon` / `set_tooltip`) can still block on
        // sync IPC to the OS tray subsystem on some platforms, so we
        // keep the work OFF the event-loop thread.
        // `tauri::async_runtime::spawn_blocking` dispatches the closure
        // onto the cached Tokio blocking thread pool (lazily grown,
        // reused across calls), avoiding the per-event thread-creation
        // cost while still keeping the event-loop thread free. The
        // returned `JoinHandle` is intentionally dropped
        // (fire-and-forget) — the body logs its own errors and
        // returns `()`.
        #[allow(clippy::let_underscore_future)] // intentional fire-and-forget (comment above)
        let _ = tauri::async_runtime::spawn_blocking(move || {
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
                log::warn!(
                    "[TRAY] tray_by_id({}) returned None — tray not yet built?",
                    TRAY_ID
                );
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

// Sibling test module — tests live in `tray_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "tray_tests.rs"]
mod tray_tests;
