//! Theme-reactive main-window icon (TR-4 dynamic follow-up).
//!
//! Windows has no per-theme window-class icon: the .exe-embedded icon
//! (`icons/icon.ico`) is static — it cannot flip with the OS theme, so
//! pinned shortcuts and the closed-app taskbar tile always show it.
//! While the app RUNS, the host instead tracks the OS theme and swaps
//! the main window's icon at runtime between the LIGHT and DARK
//! variants — what the running taskbar button and the Alt-Tab tile
//! render.
//!
//! Both variants today carry the SAME mark — the #1a1b1e chip + white
//! glyph + brand-red dot (user decision 2026-09: the chip is the dark
//! #1a1b1e in light AND dark OS themes). They stay two separate assets
//! (and the theme swap stays live) so the light/dark chrome can be
//! diverged later by editing the LIGHT_CHIP / DARK_CHIP constants in
//! `voice_typer/client/scripts/generate-icons.mjs` — no mode must be
//! re-added from scratch.
//!
//! Wiring (both in `main.rs`, which stays wiring-only):
//!   - `.setup` → [`apply_startup`] once for the initial OS theme.
//!   - `.on_window_event` → `WindowEvent::ThemeChanged` arm → [`apply_to_window`].
//! (There is deliberately NO `RunEvent` arm: `ThemeChanged` is a
//! per-window event in Tauri v2 — it has no `RunEvent` counterpart.)
//! The bubble window is excluded on purpose: it is `skipTaskbar` +
//! transparent, so it never appears on the taskbar or in Alt-Tab.
//!
//! Assets: the LIGHT variant is `icons/icon.png` itself (the committed
//! brand mark — no duplication); the DARK variant is
//! `theme-icons/icon-dark-512.png`. Both are 512×512 RGBA and both
//! carry the same #1a1b1e chip + white glyph + brand-red dot (NOT an
//! RGB-inverse pair — the red dot must stay red). Both are emitted by
//! `voice_typer/client/scripts/generate-icons.mjs`. The
//! theme-icons dir lives OUTSIDE `icons/` on purpose:
//! `scripts/build/generate_tauri_icons.py::prune` deletes everything
//! under `icons/` except the `bundle.icon` keep-set.

use tauri::{image::Image, Theme};

/// Label of the window whose icon follows the OS theme (`tauri.conf.json`).
pub(crate) const MAIN_WINDOW_LABEL: &str = "main";

/// Light-variant PNG bytes (#1a1b1e chip + white glyph — == the
/// committed `icons/icon.png`, the static default).
const ICON_LIGHT_PNG: &[u8] = include_bytes!("../icons/icon.png");

/// Dark-variant PNG bytes (#1a1b1e chip + white glyph — identical to
/// the light variant today; separate asset so the chrome looks can
/// diverge later).
const ICON_DARK_PNG: &[u8] = include_bytes!("../theme-icons/icon-dark-512.png");

/// PNG bytes for `theme`: dark OS → the DARK variant, light OS → the
/// LIGHT variant (visually identical today — #1a1b1e chip + white glyph).
///
/// Pure mapping (no I/O, no window handle) so it stays unit-testable.
pub(crate) fn png_for_theme(theme: &Theme) -> &'static [u8] {
    match theme {
        Theme::Dark => ICON_DARK_PNG,
        // `Theme` is `#[non_exhaustive]` — a future variant must fall back
        // to *a* mark, and the light variant is the legacy default.
        _ => ICON_LIGHT_PNG,
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
