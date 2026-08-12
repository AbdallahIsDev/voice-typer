#![allow(clippy::unreachable)] // tauri command macro expansion emits `unreachable!()` fallbacks

//! `shutdown_sidecar` cooperative-shutdown Tauri command (ADR-0020
//! §10) — extracted from the former single-file
//! `commands/sidecar_cmds.rs` (EO-35 split).

use crate::commands::require_main_window;
use crate::state::SidecarState;
// (): poison-safe Mutex helper — same rationale as the dispatch path.
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
) -> Result<(), String> {
    // only the main window may drive the cooperative-shutdown
    // path. A compromised bubble renderer must NOT be able to invoke
    // `invoke('shutdown_sidecar')` to DoS the sidecar.
    //
    // Note: there is also a programmatic (non-IPC) caller in
    // `main.rs`'s `on_window_event` handler that invokes
    // `shutdown_sidecar` directly when the main window is closed —
    // that caller passes `window.clone()` from the `"main"` arm of the
    // `window.label()` match, so this check passes for it too.
    require_main_window(&window)?;

    // Early-return guard. If a previous `shutdown_sidecar`
    // invocation already flipped `shutting_down` to true, the sidecar
    // is already being torn down (or has been). Re-entering here would
    // re-send the (idempotent) shutdown frame AND block on
    // `state.child_exit_rx` for the full `SHUTDOWN_ACK_TIMEOUT_MS`
    // (2s) — a duplicate `invoke('shutdown_sidecar')` (renderer-
    // invocable via `generate_handler!`) thus freezes the UI for 2s.
    // `swap` returns the previous value: if it was already `true`,
    // short-circuit immediately.
    if state
        .shutting_down
        .swap(true, std::sync::atomic::Ordering::SeqCst)
    {
        log::info!("[SHUTDOWN] already in progress — duplicate call short-circuited");
        return Ok(());
    }
    // Abort the in-flight heartbeat task so it doesn't keep dispatching
    // `heartbeat` frames into the dead WS for up to HEARTBEAT_MAX_MISSES
    // (~30s) after shutdown. Mirrors `shutdown_sidecar_for_exit` in
    // state.rs — both shutdown paths must abort the heartbeat so the
    // task doesn't outlive the WS connection.
    crate::sidecar::ws::abort_heartbeat(state.inner()).await;
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
    // Wait up to SHUTDOWN_ACK_TIMEOUT_MS for the sidecar to exit.
    // Use the `CommandEvent` receiver captured at spawn time to detect
    // `Terminated` and return immediately (typical sidecar acks+exits in
    // ~50ms), instead of sleeping the full deadline unconditionally.
    // Falls back to a single bounded sleep for the dev-mode path (which
    // has no event receiver).
    let deadline_dur = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let mut graceful = false;
    // Take the receiver out of the lock and drop the guard BEFORE awaiting
    // `rx.recv()`. Previously the `AsyncMutex` guard was held across the
    // up-to-2s `tokio::time::timeout` await, blocking `respawn_inner`
    // (supervisor.rs) under a tight shutdown race where the supervisor
    // tried to install a new receiver (via `*rx_guard = exit_rx;`) while
    // `shutdown_sidecar` was still holding the lock waiting for the old
    // sidecar to exit.
    //
    // `take()` leaves `None` in the slot. The supervisor's install path is
    // a full assignment (`*rx_guard = exit_rx;`) — it does not read the
    // current value — so overwriting a `None` slot is well-defined: the
    // next respawn stores the new receiver and the next `shutdown_sidecar`
    // call (if any; normally the app exits before that) sees `Some(new_rx)`.
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
                    "[SHUTDOWN] sidecar did not exit within {}ms — force-killing",
                    SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    } else {
        // Dev-mode path (tokio::process::Child) — no CommandEvent
        // receiver. Sleep once for the full deadline window before
        // falling through to the force-kill backstop.
        log::info!(
            "[SHUTDOWN] dev-mode sidecar — sleeping {}ms before force-kill",
            SHUTDOWN_ACK_TIMEOUT_MS
        );
        tokio::time::sleep(deadline_dur).await;
    }
    // Force-kill backstop. Gate on `!graceful` — if the sidecar exited
    // cooperatively, the grandchildren (native hotkey binary, model
    // subprocesses) were already reaped by the sidecar itself; we still
    // `take()` the child handle (dropping it cleanly) but skip the
    // recursive `kill_tree`. If `graceful` is false (timeout /
    // unexpected exit), `kill_tree` is the force-kill backstop that
    // also walks the process tree to reap grandchildren the sidecar
    // didn't clean up. ADR-0020 §10.
    let child_opt = mutex_lock(&state.child).take();
    if let Some(child) = child_opt {
        if !graceful {
            let _ = child.kill_tree().await;
        }
    }
    log::info!("[SHUTDOWN] sidecar kill completed (graceful={})", graceful);
    let _ = app;
    Ok(())
}
