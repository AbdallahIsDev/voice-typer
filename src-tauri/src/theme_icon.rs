//! Theme-reactive main-window icon. Windows has no per-theme window-class
//! icon; while the app runs the host swaps the main window's icon with the
//! OS theme (taskbar / Alt-Tab). Both variants currently share the same
//! mark (dark chip + white glyph + brand-red dot) so light/dark chrome can
//! diverge later via `generate-icons.mjs` without re-adding the mode.
//! Bubble window is excluded (skipTaskbar + transparent).

use tauri::{image::Image, Theme};

/// Window label whose icon follows the OS theme (`tauri.conf.json`).
pub(crate) const MAIN_WINDOW_LABEL: &str = "main";

/// Light-variant PNG (same mark as the committed `icons/icon.png`).
const ICON_LIGHT_PNG: &[u8] = include_bytes!("../icons/icon.png");

/// Dark-variant PNG (`theme-icons/`; outside `icons/` so the prune script
/// that clears `icons/` cannot delete it).
const ICON_DARK_PNG: &[u8] = include_bytes!("../theme-icons/icon-dark-512.png");

/// Pure theme→PNG mapping (unit-testable, no I/O).
pub(crate) fn png_for_theme(theme: &Theme) -> &'static [u8] {
    match theme {
        Theme::Dark => ICON_DARK_PNG,
        // `Theme` is non_exhaustive; light is the legacy default fallback.
        _ => ICON_LIGHT_PNG,
    }
}

/// Decode [`png_for_theme`] into a runtime [`Image`] for `set_icon`.
pub(crate) fn image_for_theme(theme: &Theme) -> tauri::Result<Image<'static>> {
    Image::from_bytes(png_for_theme(theme))
}

/// Apply the OS-theme icon to `window`. Never panics: rejected icon logs
/// a warning; the window keeps its embedded default.
pub(crate) fn apply_to_window(window: &tauri::Window, theme: &Theme) {
    apply_theme_icon(theme, |img| window.set_icon(img))
}

/// `.setup`-time entry: read the main window's current OS theme and apply.
pub(crate) fn apply_startup(window: &tauri::WebviewWindow) {
    match window.theme() {
        Ok(theme) => apply_theme_icon(&theme, |img| window.set_icon(img)),
        Err(e) => log::warn!("[THEME-ICON] could not read OS theme: {e}"),
    }
}

/// Shared body: decode + set, mapping every failure to a warn log.
/// `set_icon` is injected because `Window` and `WebviewWindow` expose no
/// common set_icon interface.
fn apply_theme_icon(theme: &Theme, set_icon: impl FnOnce(Image<'static>) -> tauri::Result<()>) {
    match image_for_theme(theme) {
        Ok(img) => {
            if let Err(e) = set_icon(img) {
                log::warn!("[THEME-ICON] set_icon({theme:?}) failed: {e}");
            } else {
                log::info!("[THEME-ICON] main window icon set for {theme:?} OS theme");
            }
        }
        Err(e) => log::warn!("[THEME-ICON] icon decode failed: {e}"),
    }
}
