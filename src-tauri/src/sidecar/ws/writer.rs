
use crate::state::lock as mutex_lock;
use crate::state::SidecarState;
use futures_util::{stream::SplitSink, FutureExt, SinkExt};
use serde_json::json;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tauri::Emitter;
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::Message;

use super::drain_pending_with_disconnect_error;
use super::respawn_scheduler::trigger_respawn_off_thread;
use super::WsStream;

pub(super) fn spawn_writer_task(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    write: SplitSink<WsStream, Message>,
    mut ws_rx: mpsc::Receiver<Message>,
    my_generation: u64,
) {
    let app_for_cleanup = app.clone();
    let state_for_cleanup = state.clone();
    tokio::spawn(async move {
        let result = AssertUnwindSafe(async move {
            let mut write = write;
            while let Some(msg) = ws_rx.recv().await {
                if write.send(msg).await.is_err() {
                    break;
                }
            }
        })
        .catch_unwind()
        .await;
        if let Err(_panic_payload) = &result {
            log::error!(
                "[WS-WRITER] writer task panicked during body: task exiting \
                 (write half dropped, WS connection will close)"
            );
        }
        {
            let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
            let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx);
            if current_generation == my_generation {
                *ws_tx_guard = None;
            } else {
                log::info!(
                    "[WS-WRITER] cleanup skipping ws_tx clear: generation mismatch \
                     (mine={}, current={}); a newer reconnect owns ws_tx ()",
                    my_generation,
                    current_generation
                );
            }
        }
        {
            let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
            if current_generation == my_generation {
                let count = drain_pending_with_disconnect_error(&state_for_cleanup).await;
                if count > 0 {
                    log::warn!(
                        "[WS-WRITER] drained {} pending dispatch requests on write-half failure",
                        count
                    );
                }
            } else {
                log::info!(
                    "[WS-WRITER] cleanup skipping drain: generation mismatch \
                     (mine={}, current={})",
                    my_generation,
                    current_generation
                );
            }
        }
        if !state_for_cleanup.shutting_down.load(Ordering::SeqCst) {
            let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
            if current_generation == my_generation {
                if let Err(e) = app_for_cleanup.emit(
                    "supervisor_relaunching",
                    json!({"reason": "writer_half_closed"}),
                ) {
                    log::warn!("[WS-WRITER] failed to emit supervisor_relaunching: {}", e);
                }
                log::warn!("[WS-WRITER] write half closed, triggering supervisor respawn");
                trigger_respawn_off_thread(
                    app_for_cleanup.clone(),
                    state_for_cleanup.clone(),
                    Some(my_generation),
                );
            } else {
                log::info!(
                    "[WS-WRITER] cleanup skipping respawn trigger: generation mismatch \
                     (mine={}, current={})",
                    my_generation,
                    current_generation
                );
            }
        }
    });
}
