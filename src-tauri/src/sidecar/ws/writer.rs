//! WS writer task (ADR-0020 §1 + §9 + §10).
//!
//! Extracted from the parent `ws.rs` module-split pipeline. Holds:
//! - `spawn_writer_task` — drains the bounded WS writer channel into
//!   the tungstenite write half, wrapped in
//!   `AssertUnwindSafe(...).catch_unwind()` with the symmetric
//!   cleanup block (clear ws_tx + drain pending + emit
//!   `supervisor_relaunching` + trigger supervisor respawn).
//!
//! Visibility contract:
//! - `spawn_writer_task` is `pub(super)` — visible to the parent
//!   `ws` module (single call site in `reconnect_ws`), mirroring
//!   `heartbeat::spawn_heartbeat_task`.
//! - Shared helpers stay in `ws.rs` (`WsStream` alias,
//!   `drain_pending_with_disconnect_error`) and are reached via
//!   `super::`; the respawn trigger lives in the sibling
//!   `respawn_scheduler` submodule.

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

/// (was inline in `reconnect_ws`): spawn the WS writer task.
///
/// Drains `ws_rx` into `write.send`. The body is wrapped in
/// `AssertUnwindSafe(...).catch_unwind()` () so a panic inside
/// `write.send()` (e.g. a tungstenite internal invariant violation)
/// doesn't tear down the task without cleanup. The writer has no
/// post-panic cleanup beyond dropping `write` (which `catch_unwind`
/// does automatically as the wrapped future unwinds), but the
/// `catch_unwind` future still resolves normally so the outer
/// `tokio::spawn` future completes cleanly instead of propagating the
/// panic to the runtime.
pub(super) fn spawn_writer_task(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    write: SplitSink<WsStream, Message>,
    mut ws_rx: mpsc::Receiver<Message>,
    // generation captured at reconnect time so the cleanup block
    // can skip clearing `ws_tx` if a newer reconnect has already stored
    // its own sender (race — see `SidecarState::ws_generation`).
    my_generation: u64,
) {
    // clone handles for the cleanup block, mirroring
    // ``spawn_reader_task``'s pattern. The originals are moved into
    // the ``AssertUnwindSafe`` body; the cleanup clones are used
    // AFTER ``catch_unwind`` so the cleanup runs even if the body
    // panics. Pre-fix, the writer task had NO cleanup block — when
    // ``write.send()`` returned ``Err`` (write half broken), the
    // task just ``break``ed, leaving ``state.ws_tx`` pointing at the
    // dead sender and ``state.pending`` un-drained. Subsequent
    // ``dispatch`` calls queued onto the dead channel and waited up
    // to 30s for the heartbeat to detect the failure and trigger
    // respawn. With this cleanup block, the writer now clears
    // ``ws_tx`` + drains pending + emits ``supervisor_relaunching`` +
    // triggers respawn — symmetric with the reader's cleanup.
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
                "[WS-WRITER] writer task panicked during body — task exiting \
                 (write half dropped, WS connection will close)"
            );
        }
        // symmetric cleanup block — clear ws_tx + drain
        // pending + trigger supervisor respawn (gated on
        // ``!shutting_down`` so a graceful shutdown doesn't fire a
        // spurious respawn). Mirrors ``spawn_reader_task``'s
        // post-catch_unwind cleanup block at lines ~1086-1128.
        //
        // only clear `ws_tx` if the current
        // generation matches `my_generation`. If a newer reconnect has
        // bumped the generation (i.e. `state.ws_generation` > my_generation),
        // the stored `ws_tx` belongs to the NEW connection — clearing it
        // would clobber the new sender and force a flap loop. The drain
        // and respawn trigger still run unconditionally: draining our
        // pending entries is safe (they're keyed by id, not by ws_tx),
        // and the respawn trigger is idempotent (the supervisor's
        // `respawn_in_progress` compare_exchange will no-op if a newer
        // reconnect is already in flight).
        {
            let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
            let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx);
            if current_generation == my_generation {
                *ws_tx_guard = None;
            } else {
                log::info!(
                    "[WS-WRITER] cleanup skipping ws_tx clear — generation mismatch \
                     (mine={}, current={}); a newer reconnect owns ws_tx ()",
                    my_generation,
                    current_generation
                );
            }
        }
        // Gate the drain and respawn trigger on the same generation
        // check as the ws_tx clear above. The writer cleanup previously
        // did an UNCONDITIONAL drain + respawn trigger — if a newer
        // reconnect had bumped the generation and added new in-flight
        // dispatches, the old writer's unconditional drain would reject
        // the NEW connection's dispatches with sidecar_disconnected
        // errors and emit a spurious supervisor_relaunching banner.
        // Mirror the reader cleanup block (ws.rs:840-892) which already
        // gates all three side effects on the generation check.
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
                    "[WS-WRITER] cleanup skipping drain — generation mismatch \
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
                log::warn!("[WS-WRITER] write half closed — triggering supervisor respawn");
                // pass `Some(my_generation)`: this decision is made
                // synchronously but EXECUTED asynchronously (the
                // supervisor dequeues later). If a newer reconnect went
                // live in between, the dequeue-time generation re-check
                // inside the supervisor loop drops this request instead
                // of killing the healthy newer connection.
                trigger_respawn_off_thread(
                    app_for_cleanup.clone(),
                    state_for_cleanup.clone(),
                    Some(my_generation),
                );
            } else {
                log::info!(
                    "[WS-WRITER] cleanup skipping respawn trigger — generation mismatch \
                     (mine={}, current={})",
                    my_generation,
                    current_generation
                );
            }
        }
    });
}
