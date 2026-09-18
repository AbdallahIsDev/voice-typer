//! Renderer heartbeat command (`renderer_heartbeat`).
//!
//! The renderer posts a bare heartbeat while its window is VISIBLE
//! (`useRendererHeartbeat`); the host's watchdog
//! (`platform::renderer_watchdog`) compares the newest beat against a
//! stall threshold while the main window is visible and un-minimized, and
//! logs an ERROR when the webview stops executing timers.
//!
//! This is the Tauri equivalent of the predecessor's `child-process-gone`
//! telemetry (review.md MO-113): no Tauri/wry platform surfaces a
//! renderer/GPU crash event, but "the visible renderer stopped running"
//! is exactly the blank-window condition support needs in the log.
//!
//! The command is deliberately tiny: no payload, no return value, no
//! processing. It exists purely to timestamp liveness.

use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::platform::renderer_watchdog::HeartbeatState;
use std::sync::Arc;

/// Tauri command: record one renderer heartbeat.
///
/// Main-window-only (`SEC-026`): the bubble renderer is a sandboxed
/// webview and must never be able to feed the host's liveness state.
#[tauri::command]
pub async fn renderer_heartbeat(
    window: tauri::Window,
    state: tauri::State<'_, Arc<HeartbeatState>>,
) -> Result<(), VoiceTyperError> {
    require_main_window(&window)?;
    state.inner().record();
    Ok(())
}
