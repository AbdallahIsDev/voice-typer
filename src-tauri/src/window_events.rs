//! Window-event arm dispatch for the host's `.on_window_event` closure.
//!
//! Extracted verbatim from `main.rs`'s inline closure (pure move, no
//! behavior change) so the host entrypoint stays wiring-only (C-ARCH-1):
//! `main.rs` registers the closure, and each arm below forwards to the
//! focused module that owns the concern:
//!
//! - `CloseRequested` → `commands::sidecar_cmds::on_main_window_close`
//!   (close-to-tray semantics — see `commands/sidecar_cmds/
//!   window_close.rs`).
//! - `ThemeChanged` → `theme_icon::apply_to_window` (TR-4 theme-reactive
//!   taskbar icon, `main` window only).
//! - `Moved` → `commands::bubble::schedule_persist` (durable bubble
//!   drag-position persistence, `bubble` window only).

use tauri::{Manager, WindowEvent};

/// Route one window event to the module that owns its concern.
pub(crate) fn handle(window: &tauri::Window, event: &WindowEvent) {
    // Close-to-tray (ADR-0020 §10 + Electron parity): the main
    // window's X button hides the window (prevent_close + hide)
    // unless a deliberate shutdown is in flight; the bubble
    // window closes normally. The branch logic lives in
    // `commands::sidecar_cmds::on_main_window_close`.
    if let WindowEvent::CloseRequested { api, .. } = event {
        crate::commands::sidecar_cmds::on_main_window_close(window.app_handle(), window, api);
    }
    // TR-4 dynamic: OS theme flipped while running — swap the
    // main-window icon so the taskbar button + Alt-Tab tile keep
    // contrasting (white glyph on dark, black on light). The
    // bubble window is excluded: it is skipTaskbar, so it never
    // shows on the taskbar or in Alt-Tab. Body lives in
    // `theme_icon.rs`.
    if let WindowEvent::ThemeChanged(theme) = event {
        if window.label() == crate::theme_icon::MAIN_WINDOW_LABEL {
            crate::theme_icon::apply_to_window(window, theme);
        }
    }
    // Durable bubble drag-position persistence: observe USER drags
    // of the bubble window and write them back to the Python config
    // (debounced, fire-and-forget — see
    // `commands::bubble::persisted_position`). Programmatic moves
    // arm a suppression window so they never persist themselves.
    if let WindowEvent::Moved(position) = event {
        if window.label() == "bubble" {
            let sidecar_state = window
                .app_handle()
                .state::<std::sync::Arc<crate::state::SidecarState>>();
            crate::commands::bubble::schedule_persist(
                sidecar_state.inner(),
                position.x,
                position.y,
            );
        }
    }
}
