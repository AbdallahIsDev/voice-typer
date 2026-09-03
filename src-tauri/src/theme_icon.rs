//! Theme-reactive main-window icon (TR-4 dynamic follow-up).
//!
//! Windows has no per-theme window-class icon: the .exe-embedded icon
//! (`icons/icon.ico`, solid white) is static — it cannot flip with the
//! OS theme, so pinned shortcuts and the closed-app taskbar tile always
//! show white. While the app RUNS, the host instead tracks the OS theme
//! and swaps the main window's icon at runtime (dark OS → white glyph,
//! light OS → black glyph), which is what the running taskbar button
//! and the Alt-Tab tile render.
//!
//! Wiring (both in `main.rs`, which stays wiring-only):
//!   - `.setup` → [`apply_startup`] once for the initial OS theme.
//!   - `.on_window_event` → `WindowEvent::ThemeChanged` arm → [`apply_to_window`].
//! (There is deliberately NO `RunEvent` arm: `ThemeChanged` is a
//! per-window event in Tauri v2 — it has no `RunEvent` counterpart.)
//! The bubble window is excluded on purpose: it is `skipTaskbar` +
//! transparent, so it never appears on the taskbar or in Alt-Tab.
//!
//! Assets: white bytes are `icons/icon.png` itself (the committed white
//! brand mark — no duplication); black bytes are
//! `theme-icons/icon-black-512.png`, the exact RGB inverse of that file
//! (same glyph, same 512×512 RGBA container). That dir lives OUTSIDE
//! `icons/` on purpose: `scripts/build/generate_tauri_icons.py::prune`
//! deletes everything under `icons/` except the `bundle.icon` keep-set.

use tauri::{image::Image, Theme};

/// Label of the window whose icon follows the OS theme (`tauri.conf.json`).
pub(crate) const MAIN_WINDOW_LABEL: &str = "main";

/// White-glyph PNG bytes (== the committed `icons/icon.png`).
const ICON_WHITE_PNG: &[u8] = include_bytes!("../icons/icon.png");

/// Black-glyph PNG bytes (RGB inverse of the white file, same container).
const ICON_BLACK_PNG: &[u8] = include_bytes!("../theme-icons/icon-black-512.png");

/// PNG bytes for `theme`: dark OS → white glyph, light OS → black glyph.
///
/// Pure mapping (no I/O, no window handle) so it stays unit-testable.
pub(crate) fn png_for_theme(theme: &Theme) -> &'static [u8] {
    match theme {
        Theme::Dark => ICON_WHITE_PNG,
        // `Theme` is `#[non_exhaustive]` — a future variant must fall back
        // to *a* glyph, and black-on-light is the legacy default.
        _ => ICON_BLACK_PNG,
    }
}

/// Decode [`png_for_theme`] into a runtime [`Image`] for `set_icon`.
pub(crate) fn image_for_theme(theme: &Theme) -> tauri::Result<Image<'static>> {
    Image::from_bytes(png_for_theme(theme))
}

/// Apply the OS-theme icon to `window`. Never panics: a rejected icon
/// is a warn log — the window keeps its embedded default icon.
pub(crate) fn apply_to_window(window: &tauri::Window, theme: &Theme) {
    match image_for_theme(theme) {
        Ok(img) => {
            if let Err(e) = window.set_icon(img) {
                log::warn!("[THEME-ICON] set_icon({theme:?}) failed: {e}");
            } else {
                log::info!("[THEME-ICON] main window icon set for {theme:?} OS theme");
            }
        }
        Err(e) => log::warn!("[THEME-ICON] icon decode failed: {e}"),
    }
}

/// Read the main window's current OS theme and apply the matching icon.
/// `.setup`-time entry point; same never-panics contract as above.
pub(crate) fn apply_startup(window: &tauri::WebviewWindow) {
    match window.theme() {
        Ok(theme) => match image_for_theme(&theme) {
            Ok(img) => {
                if let Err(e) = window.set_icon(img) {
                    log::warn!("[THEME-ICON] set_icon({theme:?}) failed: {e}");
                } else {
                    log::info!("[THEME-ICON] main window icon set for {theme:?} OS theme");
                }
            }
            Err(e) => log::warn!("[THEME-ICON] icon decode failed: {e}"),
        },
        Err(e) => log::warn!("[THEME-ICON] could not read OS theme: {e}"),
    }
}
