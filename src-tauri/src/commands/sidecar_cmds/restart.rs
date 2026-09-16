//! Sidecar-restart Tauri command (`restart_sidecar`): the renderer's
//! "Lost connection → Retry" escalation (MO-120).
//!
//! Under Electron this is the `backend:restart` IPC channel
//! (`main/ipc/backend-restart-handler.ts` → `python/restart-backend.ts`):
//! process-control only, no TCP command is sent, because the backend is
//! dead by definition when the user clicks Retry after a failed probe.
//! The Tauri equivalent is this command, which delegates to the
//! supervisor's `respawn` path: it kills the old child handle (including
//! its process tree), rotates the bearer token, spawns a fresh sidecar
//! and reconnects the WS, emitting the SAME `supervisor_reconnected` /
//! `supervisor_relaunching` events the automatic crash-recovery path
//! already publishes. The renderer therefore needs no new event wiring.
//!
//! Return envelope mirrors Electron's `{ok, reason?}` so
//! `useConnection.ts`'s existing escalation branch works unchanged on
//! both runtimes. It resolves (never rejects) for domain failures: a
//! rejected promise would surface as an unhandled rejection in the
//! renderer's Retry handler, which already treats `ok: false` as "the
//! host cannot recover this for you" and shows the relaunch hint.

use serde_json::{json, Value};
use std::sync::Arc;

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::sidecar::supervisor;
use crate::state::SidecarState;

/// Pure gate: while a deliberate shutdown is in flight a restart must not
/// spawn a fresh sidecar. The supervisor checks the same flag internally,
/// but its check SILENTLY returns `Ok(())`, which would report `ok: true`
/// to the renderer for a restart that never happened; gating here lets
/// the renderer show the relaunch hint instead. Extracted so the decision
/// is unit-testable without a Tauri runtime.
fn restart_blocked_envelope(shutting_down: bool) -> Option<Value> {
    shutting_down.then(|| json!({"ok": false, "reason": "shutting-down"}))
}

/// Pure gate: in adopted-backend mode (the backend is our PARENT, MO-110)
/// a restart must refuse, mirroring Electron's
/// `restart-backend.ts` adopted check (`{ok: false, reason: "adopted"}`).
/// The supervisor's adopted guard silently no-ops, which would misreport
/// success; this gate gives the renderer the same explicit envelope
/// Electron's users get.
fn adopted_blocked_envelope(adopted: bool) -> Option<Value> {
    adopted.then(|| json!({"ok": false, "reason": "adopted"}))
}

/// Decision core for [`restart_sidecar`]: run one supervisor respawn and
/// map its result onto Electron's `{ok, reason?}` envelope. Split out so
/// the mapping is documented in one place (the command itself only adds
/// the window guard).
async fn restart_sidecar_inner(app: &tauri::AppHandle, state: &Arc<SidecarState>) -> Value {
    let shutting_down = state.shutting_down.load(std::sync::atomic::Ordering::SeqCst);
    if let Some(blocked) = restart_blocked_envelope(shutting_down) {
        log::info!("[RESTART] restart refused: host is shutting down");
        return blocked;
    }
    let adopted = *state.adopted_backend.lock().await;
    if let Some(blocked) = adopted_blocked_envelope(adopted) {
        log::info!(
            "[RESTART] restart refused: adopted-backend mode (backend is our parent)"
        );
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

/// Tauri command: restart the Python sidecar process (Electron
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
) -> Result<Value, VoiceTyperError> {
    require_main_window(&window)?;
    Ok(restart_sidecar_inner(&app, state.inner()).await)
}

// Sibling test module: tests live in `restart_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "restart_tests.rs"]
mod restart_tests;
