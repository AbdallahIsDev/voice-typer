#![allow(clippy::unreachable)] // tauri command macro expansion emits `unreachable!()` fallbacks


use crate::commands::require_main_window;
use crate::error::VoiceTyperError;
use crate::state::SidecarState;
// state::lock (aliased `mutex_lock`): poison-safe Mutex helper, same rationale as the dispatch path.
use crate::state::lock as mutex_lock;
use crate::util::SHUTDOWN_ACK_TIMEOUT_MS;
use serde_json::json;
use std::sync::Arc;
use std::time::Duration;
use tauri_plugin_shell::process::CommandEvent;
use tokio_tungstenite::tungstenite::Message;

// ─── Tauri command: cooperative shutdown (ADR-0020 §10) ───────────────

#[tauri::command]
pub async fn shutdown_sidecar(
    app: tauri::AppHandle,
    state: tauri::State<'_, Arc<SidecarState>>,
    window: tauri::Window,
) -> Result<(), VoiceTyperError> {
    require_main_window(&window)?;
    let _ = app;
    shutdown_sidecar_inner(state.inner()).await
}

pub(super) async fn shutdown_sidecar_inner(
    state: &Arc<SidecarState>,
) -> Result<(), VoiceTyperError> {
    if state.begin_shutdown() {
        log::info!("[SHUTDOWN] already in progress, duplicate call short-circuited");
        return Ok(());
    }
    crate::sidecar::ws::abort_heartbeat(state).await;
    // Send the shutdown frame.
    let frame = json!({"type": "shutdown"});
    if let Some(ws_tx) = mutex_lock(&state.ws_tx).clone() {
        if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string().into())) {
            log::warn!(
                "[SHUTDOWN] try_send of shutdown frame failed (best-effort): {}",
                e
            );
        }
    }
    let deadline_dur = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    let rx_opt = {
        let mut rx_guard = state.child_exit_rx.lock().await;
        rx_guard.take()
    };
    if let Some(mut rx) = rx_opt {
        match tokio::time::timeout(deadline_dur, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
                );
                graceful = true;
            }
            Ok(Some(other)) => {
                log::warn!(
                    "[SHUTDOWN] unexpected event while waiting for termination: {:?}",
                    other
                );
            }
            Ok(None) => {
                log::warn!("[SHUTDOWN] sidecar event stream closed without Terminated");
            }
            Err(_) => {
                log::warn!(
                    "[SHUTDOWN] sidecar did not exit within {}ms: force-killing",
                    SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        log::info!(
            "[SHUTDOWN] dev-mode sidecar: sleeping {}ms before force-kill",
            SHUTDOWN_ACK_TIMEOUT_MS
        );
        tokio::time::sleep(deadline_dur).await;
    }
    let child_opt = mutex_lock(&state.child).take();
    if let Some(child) = child_opt {
        if !graceful {
            let _ = child.kill_tree().await;
        }
    }
    log::info!("[SHUTDOWN] sidecar kill completed (graceful={})", graceful);
    Ok(())
}
