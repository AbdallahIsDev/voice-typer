
use crate::state::{lock, SidecarState};
use crate::util::EXIT_SHUTDOWN_ACK_TIMEOUT_MS;
use std::sync::Arc;
use tauri_plugin_shell::process::CommandEvent;
use tokio_tungstenite::tungstenite::Message;

//app-exit sidecar teardown ────────────────────────────────

pub(crate) async fn shutdown_sidecar_for_exit(state: &Arc<SidecarState>) {
    shutdown_sidecar_for_exit_with_budget(state, EXIT_SHUTDOWN_ACK_TIMEOUT_MS).await;
}

pub(crate) async fn shutdown_sidecar_for_exit_with_budget(
    state: &Arc<SidecarState>,
    wait_budget_ms: u64,
) {
    use std::time::Duration;

    if state.begin_shutdown() {
        log::info!("[EXIT-SHUTDOWN] shutting_down already set, skipping duplicate teardown");
        return;
    }

    //abort the heartbeat task so it doesn't keep
    // dispatching `heartbeat` frames into the dead WS.
    {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        if let Some(handle) = hb_guard.take() {
            handle.abort();
            log::info!("[EXIT-SHUTDOWN] aborted in-flight heartbeat task");
        }
    }

    // Send the shutdown frame (best-effort).
    //log on failure so a stuck writer task isn't silent.
    let frame = serde_json::json!({"type": "shutdown"});
    if let Some(ws_tx) = lock(&state.ws_tx).clone() {
        if let Err(e) = ws_tx.try_send(Message::Text(frame.to_string().into())) {
            log::warn!(
                "[EXIT-SHUTDOWN] try_send of shutdown frame failed (best-effort): {}",
                e
            );
        }
    } else {
        log::info!("[EXIT-SHUTDOWN] no ws_tx, skipping cooperative shutdown frame");
    }

    let deadline = Duration::from_millis(wait_budget_ms);
    let mut graceful = false;
    let rx_opt = {
        let mut rx_guard = state.child_exit_rx.lock().await;
        rx_guard.take()
    };
    if let Some(mut rx) = rx_opt {
        match tokio::time::timeout(deadline, rx.recv()).await {
            Ok(Some(CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[EXIT-SHUTDOWN] sidecar exited gracefully (code={:?}, signal={:?})",
                    payload.code,
                    payload.signal
                );
                graceful = true;
            }
            Ok(Some(other)) => {
                log::warn!(
                    "[EXIT-SHUTDOWN] unexpected event while waiting for termination: {:?}",
                    other
                );
            }
            Ok(None) => {
                log::warn!("[EXIT-SHUTDOWN] sidecar event stream closed without Terminated");
            }
            Err(_) => {
                log::warn!(
                    "[EXIT-SHUTDOWN] sidecar did not exit within {}ms: force-killing",
                    wait_budget_ms
                );
            }
        }
    } else {
        log::info!(
            "[EXIT-SHUTDOWN] dev-mode sidecar: polling for exit (up to {}ms, 100ms interval) before force-kill",
            wait_budget_ms
        );
        let poll_step = Duration::from_millis(100);
        let poll_deadline = tokio::time::Instant::now() + deadline;
        loop {
            let reaped = {
                let mut guard = lock(&state.child);
                match guard.as_mut() {
                    Some(handle) => match handle.try_wait() {
                        Ok(Some(exited)) => exited,
                        Ok(None) => false, // ShellPlugin: no poll, wait for deadline
                        Err(_) => false,   // best-effort, don't fail the shutdown
                    },
                    None => true, // No child: already gone
                }
            };
            if reaped {
                graceful = true;
                log::info!(
                    "[EXIT-SHUTDOWN] dev-mode sidecar exited gracefully (reaped within {}ms budget)",
                    wait_budget_ms
                );
                break;
            }
            if tokio::time::Instant::now() >= poll_deadline {
                break;
            }
            let now = tokio::time::Instant::now();
            let remaining = if now < poll_deadline {
                poll_deadline - now
            } else {
                Duration::ZERO
            };
            tokio::time::sleep(std::cmp::min(remaining, poll_step)).await;
        }
    }

    let child_opt = lock(&state.child).take();
    if let Some(child) = child_opt {
        if !graceful {
            if let Err(e) = child.kill_tree().await {
                log::warn!("[EXIT-SHUTDOWN] kill_tree failed (best-effort): {}", e);
            }
        }
    }

    //final summary line.
    log::info!(
        "[EXIT-SHUTDOWN] sidecar teardown complete graceful={}",
        graceful
    );
}

pub(crate) fn send_fire_and_forget_frame(
    state: &Arc<SidecarState>,
    frame_type: &str,
) -> Option<u64> {
    use std::sync::atomic::Ordering;
    let ws_tx = lock(&state.ws_tx).clone()?;
    let id = state.next_id.fetch_add(1, Ordering::Relaxed);
    let frame = serde_json::json!({
        "type": frame_type,
        "data": {},
        "id": id,
    });
    match ws_tx.try_send(Message::Text(frame.to_string().into())) {
        Ok(_) => log::info!("[WS] {} frame sent (id={})", frame_type, id),
        Err(e) => log::warn!(
            "[WS] failed to send {} frame (id={}): {}, peer will wait for its timeout",
            frame_type,
            id,
            e
        ),
    }
    Some(id)
}
