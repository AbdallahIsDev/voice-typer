use serde_json::{json, Value};
use std::sync::Arc;

use crate::commands::require_main_window;
use crate::error::LausuError;
use crate::sidecar::supervisor;
use crate::state::SidecarState;

fn restart_blocked_envelope(shutting_down: bool) -> Option<Value> {
    shutting_down.then(|| json!({"ok": false, "reason": "shutting-down"}))
}

fn adopted_blocked_envelope(adopted: bool) -> Option<Value> {
    adopted.then(|| json!({"ok": false, "reason": "adopted"}))
}

async fn restart_sidecar_inner(app: &tauri::AppHandle, state: &Arc<SidecarState>) -> Value {
    let shutting_down = state
        .shutting_down
        .load(std::sync::atomic::Ordering::SeqCst);
    if let Some(blocked) = restart_blocked_envelope(shutting_down) {
        log::info!("[RESTART] restart refused: host is shutting down");
        return blocked;
    }
    let adopted = *state.adopted_backend.lock().await;
    if let Some(blocked) = adopted_blocked_envelope(adopted) {
        log::info!("[RESTART] restart refused: adopted-backend mode (backend is our parent)");
        return blocked;
    }
    match supervisor::respawn(app, state).await {
        Ok(()) => {
            log::info!("[RESTART] sidecar restart requested by renderer: respawn completed");
            json!({"ok": true})
        }
        Err(e) => {
            log::warn!("[RESTART] sidecar restart failed: {}", e);
            json!({"ok": false, "reason": "respawn-failed"})
        }
    }
}

/// Tauri command: restart the Python sidecar process (predecessor
/// `backend:restart` parity).
///
/// Main-window-only: the bubble renderer is a sandboxed webview (SEC-026)
/// and must never be able to drive the backend lifecycle. `window` is
/// auto-injected by Tauri, so the renderer's
/// `invoke('restart_sidecar')` call needs no arguments.
#[tauri::command]
pub async fn restart_sidecar(
    app: tauri::AppHandle,
    window: tauri::Window,
    state: tauri::State<'_, Arc<SidecarState>>,
) -> Result<Value, LausuError> {
    require_main_window(&window)?;
    Ok(restart_sidecar_inner(&app, state.inner()).await)
}

// Sibling test module: tests live in `restart_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "restart_tests.rs"]
mod restart_tests;
