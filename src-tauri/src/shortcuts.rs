//! System-wide global shortcut registration (bubble dismiss).
//! Accelerator is pinned against the TS shared constant by
//! `tests/tauri/test_global_shortcut_parity.py`.

use tauri::Manager;
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

use crate::state::SidecarState;

/// Bubble-dismiss accelerator (CmdOrCtrl = Ctrl on Win/Linux, ⌘ on macOS).
/// Cross-process source: `voice_typer/client/src/shared/dismiss-shortcut.ts`.
pub(crate) const BUBBLE_DISMISS_ACCELERATOR: &str = "CmdOrCtrl+Shift+D";

/// Parse once at first use so a malformed binding fails in tests.
fn parse_dismiss_shortcut() -> Result<Shortcut, String> {
    BUBBLE_DISMISS_ACCELERATOR
        .parse::<Shortcut>()
        .map_err(|e| format!("malformed accelerator {BUBBLE_DISMISS_ACCELERATOR:?}: {e}"))
}

/// Dismiss the bubble accelerator. `toggle_dictation` is start/stop —
/// firing it on an idle bubble STARTS a hidden recording. Only cancel when
/// the host last saw a recording/transcribing tray icon; otherwise just hide.
fn dismiss_bubble(app: &tauri::AppHandle) {
    let state: tauri::State<'_, std::sync::Arc<SidecarState>> = app.state();
    if state.host_knows_recording() {
        if let Err(e) = crate::commands::sidecar_cmds::dispatch_fire_and_forget(
            state.inner(),
            "toggle_dictation",
            None,
        ) {
            // NotConnected is the normal idle-app case.
            log::debug!("[SHORTCUTS] dismiss toggle_dictation not sent: {}", e);
        }
    } else {
        log::debug!("[SHORTCUTS] dismiss: idle/unknown tray state, hide only (no toggle)");
    }
    if let Err(e) = crate::commands::bubble::hide_bubble_window(app) {
        log::debug!("[SHORTCUTS] dismiss hide failed (bubble may be hidden): {}", e);
    }
}

/// Register the global bubble-dismiss shortcut (called once from `main.rs`
/// setup). Best-effort: OS may refuse (already taken); failure only costs
/// the keyboard path — the bubble '×' still works. Fires on press only.
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
            // Press edge only; RELEASE would double-fire the toggle.
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

// C-TEST-5: sibling test file (accelerator-parse + constant pins).
#[cfg(test)]
#[path = "shortcuts_tests.rs"]
mod shortcuts_tests;
