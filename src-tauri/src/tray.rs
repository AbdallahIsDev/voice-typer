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
//! back to the sidecar via the trusted `dispatch_inner` path
//! (previously the click was forwarded by emitting a Tauri event named
//! `"dispatch"` that had no listener — `app.emit("dispatch", payload)`
//! was dead code, so the click was silently dropped). Left-click (no
//! item) focuses the main window.
//!
//! # Module layout
//!
//! This file is the top-level wiring surface
//! (`create_tray_and_mark_state` → `create_tray` + the menu-rebuild
//! listener glue). The three extractable concerns live in
//! focused submodules, mirroring the `commands/bubble/*` decomposition
//! pattern, and are re-exported from here so every existing
//! `crate::tray::X` path keeps resolving:
//!
//! - [`icon_cache`] — the process-wide decoded-icon cache + whitelisted
//!   icon loader (state + I/O).
//! - [`menu`] — serde payload types (`MenuItemData`, `TrayMenuPayload`,
//!   `TrayStatePayload`) + native menu construction.
//! - [`events`] — tray-icon event predicates (left-click → show+focus).

// Tray tooltip + placeholder label use the cross-language
// `APP_NAME` constant from `branding.rs` (mirrors
// `voice_typer/server/branding.py::APP_NAME` and
// `voice_typer/client/src/main/branding.ts::APP_NAME`). Replaces two
// inline brand literals that were drift hazards.
const TRAY_TOOLTIP: &str = crate::branding::APP_NAME;
const TRAY_ID: &str = "voice-typer-tray";

mod events;
mod icon_cache;
mod menu;

// Re-exports — keep every pre-split `crate::tray::<name>` path (and the
// sibling `tray_tests.rs` glob) resolving unchanged. `is_allowed_icon_name`
// is re-exported TEST-ONLY: its production caller lives inside
// `icon_cache` (`load_tray_icon`'s whitelist gate), so a non-test
// re-export would be an unused import.
pub(crate) use events::is_focus_main_window_event;
#[cfg(test)]
pub(crate) use icon_cache::is_allowed_icon_name;
pub(crate) use icon_cache::load_tray_icon;
pub(crate) use menu::{build_menu, empty_menu, MenuItemData, TrayMenuPayload, TrayStatePayload};

// Event-construction types for the sibling test module (which builds
// synthetic `TrayIconEvent::Click` variants via `use super::*;`) — the
// parent imports stay the single resolution path for those names.
#[cfg(test)]
use tauri::tray::{MouseButton, TrayIconEvent};

// Use `dispatch_inner` (no allowlist gate — `tray_click` is a
// Rust-only command not in the renderer `ALLOWED_COMMANDS` set),
// which internally delegates to the shared `dispatch_frame` helper.
// Two coordinated changes fixed the dropped-tray-click bug: the typed
// `dispatch_inner`/`DispatchArgs` path was added for trusted Rust
// callers, and the WS-send body was extracted into `dispatch_frame` so
// the public `dispatch` command and the tray handler share one
// implementation.
use crate::commands::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
use std::sync::Arc;
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Listener, Manager};

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
    // Initial icon: the `idle` state icon (gray logo glyph) so the
    // tray starts in the same visual state the Python sidecar starts
    // in (AppState.IDLE). Falls back to the default window icon when
    // the tray resources aren't available (e.g. a checkout that
    // predates the tray PNGs) — never a bare `None`, so the tray
    // always shows a real icon from the first frame. On macOS
    // `icon_as_template(true)` (set below) renders either as the
    // menubar-colored glyph shape.
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
            // Invoke the `tray_click` command on the Python
            // sidecar DIRECTLY via `dispatch_inner` — the previous
            // implementation emitted a Tauri event named `dispatch`
            // that nobody listened to (events ≠ commands in Tauri).
            // The click silently dropped.
            //
            // `tray_click` is a Rust-only command — the renderer never
            // invokes it — so it is NOT in the renderer-side
            // `ALLOWED_COMMANDS` allowlist. The public `dispatch`
            // Tauri command enforces the allowlist and would
            // reject `tray_click`. We therefore call `dispatch_inner`
            // directly, which is the WS-send path WITHOUT the
            // allowlist gate (callers are trusted Rust code).
            //
            // The dispatch payload is built HERE on the event-loop
            // thread (borrowing the clicked item's id straight from
            // the event) and the owned `args` are MOVED into the
            // spawned task. This keeps the per-click heap footprint
            // down to the payload itself: previously the id was first
            // copied into a standalone `String` and THEN serialized a
            // second time by `json!`'s `to_value` — two allocations
            // for the same text. Borrowing `event.id().as_ref()`
            // directly into the JSON value allocates it exactly once.
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
            // Log the raw event at debug so a future regression
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
            // Only show + focus the main window on LEFT
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
    // ("Voice Typer — Recording") and the glyph SHAPE (which is
    // identical across states — only the alpha mask matters).
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
        // `rebuild_tray_menu` is fully synchronous (no `.await`
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

    // (Rust side): listen for `tray_state` events from the Python
    // sidecar and update the tray icon + tooltip.
    //
    // The Python publish path is WIRED: `tray.py::_publish_tray_state`
    // → `tray_publish.publish_tray_state` (deduped under `_publish_lock`)
    // → `tray_menu.publish_tray_state` → `event_bus.publish` → the WS
    // bridge forwards the frame (allowlisted in
    // `sidecar/ws/event_protocol.rs::ALLOWED_EVENT_TYPES`) as a Tauri
    // `tray_state` event, and the listener below consumes it to move
    // the icon + tooltip. A parse failure or a missing tray is logged
    // and skipped — the tray keeps its previous icon/tooltip.
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
        // The body below is fully synchronous (no `.await`s —
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

/// Create the tray and record its availability in `SidecarState`.
///
/// The single wiring call from `main.rs`'s `.setup` (C-ARCH-1 — the
/// error log + `tray_available` marking moved out of the entry file as
/// tray-init detail, not builder wiring). Tray failure is non-fatal:
/// the app still runs without a tray, and the then-unmarked
/// `tray_available` makes the main-window close handler let the close
/// flow through to app exit instead of hide-to-tray (no stranded hidden
/// window — see `state.rs`'s `tray_available` field docs for the full
/// rationale).
pub(crate) fn create_tray_and_mark_state(app: &AppHandle) {
    if let Err(e) = create_tray(app) {
        log::error!("[TRAY] init failed: {}", e);
    } else {
        app.state::<Arc<SidecarState>>().mark_tray_available();
    }
}

/// Rebuild the tray menu from the item list and re-apply it to the
/// existing tray icon (matched by id `TRAY_ID`).
fn rebuild_tray_menu(app: &AppHandle, items: &[MenuItemData]) -> tauri::Result<()> {
    let menu = build_menu(app, items)?;
    if let Some(tray) = app.tray_by_id(TRAY_ID) {
        tray.set_menu(Some(menu))?;
        // DEBUG observability for the "menu missing after respawn" class
        // of report: pairs with the Python-side "host ready — tray menu +
        // state re-published" line so a placeholder menu can be bisected
        // (no event vs. event-but-no-rebuild).
        log::debug!(
            "[TRAY] menu rebuilt ({} item{})",
            items.len(),
            if items.len() == 1 { "" } else { "s" }
        );
    }
    Ok(())
}

// Sibling test module — tests live in `tray_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "tray_tests.rs"]
mod tray_tests;
