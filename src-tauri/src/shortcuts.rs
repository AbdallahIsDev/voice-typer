//! System-wide global shortcut registration (MO-125, Electron parity).
//!
//! Electron registered `CommandOrControl+Shift+D` via the
//! `globalShortcut` main-process module
//! (`main/shortcuts/global-shortcuts.ts`) so the dictation bubble can
//! be dismissed from anywhere — even when no app window has focus. The
//! Tauri host ships the same binding through
//! `tauri-plugin-global-shortcut`, registered Rust-side only (the
//! renderer never touches the plugin, so no capability grant is
//! involved).
//!
//! The handler mirrors Electron's `dismissAndHideBubble` exactly:
//!
//! 1. If the bubble is mid-recording/transcribing, cancel the in-flight
//!    recording first (`toggle_dictation`, fire-and-forget — the
//!    sidecar's `status_change` event is what actually updates the UI,
//!    and a second toggle would RE-START the recording).
//! 2. Hide the bubble window through the shared
//!    [`crate::commands::bubble::hide_bubble_window`] path (the
//!    exact hide body the bubble's own '×' button uses), so the
//!    keyboard path can never drift from the click path.
//!
//! Failure policy: registration is best-effort, mirroring Electron.
//! The OS may refuse the accelerator (already taken by another
//! application); a failed registration logs a warning and the rest of
//! the app keeps working — losing the global dismiss key is graceful
//! degradation, not a hard failure. The bubble's '×' button still
//! dismisses it.
//!
//! The accelerator string lives in ONE place cross-process:
//! `voice_typer/client/src/shared/dismiss-shortcut.ts`
//! (`DISMISS_SHORTCUT.accelerator`), pinned by
//! `tests/tauri/test_global_shortcut_parity.py` so the Rust registration
//! and the Electron main process can never drift apart.

use tauri::Manager;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use crate::state::SidecarState;

/// The bubble-dismiss accelerator in Tauri/global-hotkey form.
///
/// `CmdOrCtrl+Shift+D` is the Electron spelling of the same binding
/// (`DISMISS_SHORTCUT.accelerator`); `CmdOrCtrl` maps to Ctrl on
/// Windows/Linux and ⌘ on macOS, exactly what global-hotkey's
/// `SUPER | SHIFT | KeyD`-style parse of `CommandOrControl` does.
/// Pinned against the TS shared constant by
/// `tests/tauri/test_global_shortcut_parity.py` (E7: one source of
/// truth per language, joined by a contract test).
pub(crate) const BUBBLE_DISMISS_ACCELERATOR: &str = "CmdOrCtrl+Shift+D";

/// Parse the accelerator constant once at first use so a malformed
/// binding fails loudly in tests instead of silently at a keypress.
fn parse_dismiss_shortcut() -> Result<Shortcut, String> {
    BUBBLE_DISMISS_ACCELERATOR
        .parse::<Shortcut>()
        .map_err(|e| format!("malformed accelerator {BUBBLE_DISMISS_ACCELERATOR:?}: {e}"))
}

/// The one keypress body: cancel any in-flight recording, then hide the
/// bubble. Extracted so it is unit-testable in shape (the sidecar send
/// + window hide are the same calls the bubble '×' path makes).
fn dismiss_bubble(app: &tauri::AppHandle) {
    let state: tauri::State<'_, std::sync::Arc<SidecarState>> = app.state();
    // Cancel the in-flight recording FIRST (Electron's
    // `dismissAndHideBubble` ordering): hide alone would leave the mic
    // open with no visible indicator.
    //
    // Visibility check: `bubble_hide` was the last bubble event the
    // sidecar published when the bubble is NOT on screen; rather than
    // tracking a parallel mode flag in the host (state drift), always
    // send the fire-and-forget cancel and let the hide path be
    // idempotent — hiding an already-hidden window is a no-op, and
    // `toggle_dictation` with nothing in flight simply returns the
    // current state. This matches Electron's behavior, which also
    // always toggled then hid.
    if let Err(e) = crate::commands::sidecar_cmds::dispatch_fire_and_forget(
        state.inner(),
        "toggle_dictation",
        None,
    ) {
        // NotConnected (no sidecar / WS down) is the normal idle-app
        // case; log at debug. Send failures are worth a warn.
        log::debug!("[SHORTCUTS] dismiss toggle_dictation not sent: {}", e);
    }
    if let Err(e) = crate::commands::bubble::hide_bubble_window(app) {
        log::debug!("[SHORTCUTS] dismiss hide failed (bubble may be hidden): {}", e);
    }
}

/// Register the global bubble-dismiss shortcut. Idempotent per app run
/// (called once from `main.rs`'s `setup`).
///
/// Fires on key PRESS only (`ShortcutState::Pressed`), matching
/// Electron's `globalShortcut` semantics (which registers on press, no
/// key-repeat re-entry: a held key repeats the OS-level accelerator,
/// each repeat is one event — the same dedupe the sidecar's own
/// dictation hotkey path applies).
pub(crate) fn register_bubble_dismiss(app: &tauri::AppHandle) {
    let shortcut = match parse_dismiss_shortcut() {
        Ok(s) => s,
        Err(e) => {
            log::warn!("[SHORTCUTS] bubble-dismiss shortcut not registered: {}", e);
            return;
        }
    };
    let result = app.global_shortcut().on_shortcut(
        shortcut,
        |app_handle, _shortcut, event| {
            // Only the PRESS edge dismisses; the RELEASE edge would
            // double-fire the toggle (start+stop in one keypress).
            if event.state == ShortcutState::Pressed {
                dismiss_bubble(app_handle);
            }
        },
    );
    match result {
        Ok(()) => log::info!(
            "[SHORTCUTS] global bubble-dismiss accelerator registered: {}",
            BUBBLE_DISMISS_ACCELERATOR
        ),
        Err(e) => log::warn!(
            "[SHORTCUTS] bubble-dismiss accelerator {} not registered (taken by another app?): {}",
            BUBBLE_DISMISS_ACCELERATOR,
            e
        ),
    }
}

// Sibling test module: accelerator-parse + constant pins (C-TEST-5).
#[cfg(test)]
#[path = "shortcuts_tests.rs"]
mod shortcuts_tests;
