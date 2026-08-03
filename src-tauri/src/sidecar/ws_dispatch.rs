//! WS reader task — inbound frame dispatch (ADR-0020 §9).
//!
//! Extracted from `ws.rs` (was ~340 lines inline). The reader task is
//! the inbound message dispatcher: it parses JSON frames, fulfills
//! pending dispatch requests by `id`, and emits Tauri events for
//! server-initiated events (after passing the
//! `ws_routes::is_allowed_event_type` allowlist gate and the
//! `ws_routes::translate_event_name` bubble-lifecycle rename).
//!
//! Owned here:
//! - `spawn_reader_task` (the inbound frame loop + generation-gated
//!   cleanup block)
//!
//! Re-used from sibling modules:
//! - `ws_routes::is_allowed_event_type` / `translate_event_name`
//! - `ws::drain_pending_with_disconnect_error` / `trigger_respawn_off_thread`
//! - `ws::WsStream` (the WebSocketStream type alias)

use crate::sidecar::bubble_coalesce::bubble_coalesce_should_emit;
use crate::sidecar::ws::WsStream;
use crate::sidecar::ws_reconnect::{
    drain_pending_with_disconnect_error, trigger_respawn_off_thread,
};
use crate::sidecar::ws_routes::{is_allowed_event_type, translate_event_name};
use crate::state::lock as mutex_lock;
use crate::state::SidecarState;
use crate::util::BUBBLE_LEVEL_COALESCE_HZ;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Instant;
use futures_util::{
    stream::SplitStream,
    FutureExt, StreamExt,
};
use serde_json::{json, Value};
use tauri::Emitter;
use tokio_tungstenite::tungstenite::Message;

/// (was inline in `reconnect_ws`): spawn the WS reader task.
///
/// Parses incoming frames, fulfills pending dispatch requests by id,
/// emits Tauri events for server-initiated events. The body is wrapped
/// in `AssertUnwindSafe(...).catch_unwind()` so a panic
/// inside `read.next()` / `serde_json::from_str` / `app.emit()` /
/// `bubble_coalesce_should_emit()` / the `last_bubble_payload.take()`
/// line doesn't tear down the task without running cleanup. Without
/// this wrapper, a panic would propagate to the tokio runtime, which
/// logs the panic and drops the task — leaving `ws_tx` set (new
/// dispatch calls would queue onto a dead channel forever) and
/// pending dispatch requests hanging until their 120s timeout. With
/// the wrapper, the panic is caught, logged at ERROR, and the
/// cleanup block below runs identically to the normal-exit path
/// (drain pending, clear ws_tx, emit `supervisor_relaunching`, spawn
/// supervisor respawn).
pub(crate) fn spawn_reader_task(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    mut read: SplitStream<WsStream>,
    // generation captured at reconnect time so the cleanup block
    // can skip clearing `ws_tx` if a newer reconnect has already stored
    // its own sender (race — see `SidecarState::ws_generation`).
    my_generation: u64,
) {
    let app_for_reader = app.clone();
    let state_for_reader = state.clone();
    // clone handles for the cleanup block, which runs OUTSIDE
    // the `catch_unwind` wrapper so it runs even if the reader body
    // panics. The originals are moved INTO the `AssertUnwindSafe`
    // body and consumed by the inner async block.
    let app_for_cleanup = app_for_reader.clone();
    let state_for_cleanup = state_for_reader.clone();
    tokio::spawn(async move {
        let result = AssertUnwindSafe(async move {
            let mut last_bubble_level: Option<Instant> = None;
            #[allow(unused_assignments)]
            let mut last_bubble_payload: Option<Value> = None;
            // per-task counters for the flood-prone warning
            // sites (invalid JSON + dropped unknown events + non-numeric
            // id fields). A misbehaving sidecar (or an attacker who has
            // compromised it) could otherwise log-spam at the ~60 Hz
            // frame rate. Log the first occurrence and every 100th
            // thereafter; the counter is also emitted so operators can
            // see the true flood volume.
            let mut invalid_json_count: u32 = 0;
            let mut unknown_event_count: u32 = 0;
            let mut non_numeric_id_count: u32 = 0;
            while let Some(msg) = read.next().await {
                match msg {
                    Ok(Message::Text(text)) => {
                        let v: Value = match serde_json::from_str(&text) {
                            Ok(v) => v,
                            Err(_) => {
                                invalid_json_count = invalid_json_count.saturating_add(1);
                                if invalid_json_count == 1 || invalid_json_count % 100 == 0 {
                                    log::warn!(
                                        "[WS-READER] invalid JSON frame (count={}): {}",
                                        invalid_json_count,
                                        text
                                    );
                                }
                                continue;
                            }
                        };
                        // If the frame has an `id`, it's a dispatch
                        // response — fulfill the pending oneshot.
                        //
                        // take the sender out of the map under the
                        // lock, then send OUTSIDE the lock. `oneshot::send`
                        // is non-blocking (returns Err immediately if the
                        // receiver was already dropped), but holding the
                        // AsyncMutex across the send is an anti-pattern —
                        // a concurrent dispatch path's `pending.insert(...)`
                        // (or a concurrent reader-exit drain) would be
                        // stalled behind the send. Bounding the lock hold
                        // time to the HashMap lookup keeps the dispatch
                        // path's `lock().await` contention minimal.
                        if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
                            let tx_opt = {
                                let mut pending = state_for_reader.pending.lock().await;
                                pending.remove(&id)
                            };
                            if let Some(tx) = tx_opt {
                                let _ = tx.send(v);
                            }
                            continue;
                        }
                        // T3-06: a frame that HAS an `id` field but where
                        // `as_u64()` returns None (e.g. the field is a
                        // string, float, bool, array, or object) is
                        // malformed — previously it fell through to the
                        // server-event emit path below, where it would be
                        // emitted to the renderer as a bogus event with
                        // `type: "unknown"`. Log + skip instead so we
                        // neither fulfill a dispatch that never matched a
                        // pending id nor emit garbage to the UI.
                        else if v.get("id").is_some() {
                            non_numeric_id_count = non_numeric_id_count.saturating_add(1);
                            if non_numeric_id_count == 1 || non_numeric_id_count % 100 == 0 {
                                log::warn!(
                                    "[WS-READER] frame has non-numeric id field, ignoring (count={}): {}",
                                    non_numeric_id_count,
                                    text
                                );
                            }
                            continue;
                        }
                        // Otherwise it's a server-initiated event
                        // (channel 2). Emit it as a Tauri event.
                        let event_type = v.get("type").and_then(|t| t.as_str()).unwrap_or("unknown");
                        let payload = v.get("data").cloned().unwrap_or(json!({}));

                        // drop unknown event types BEFORE
                        // emitting (defense-in-depth against a
                        // compromised sidecar process injecting
                        // arbitrary event names that the renderer's
                        // `usePythonEvent(type, ...)` listeners might
                        // be tricked into handling). The allowlist
                        // `ALLOWED_EVENT_TYPES` lives in `ws_routes.rs`
                        // and covers all event types the Python sidecar
                        // is known to publish today.
                        if !is_allowed_event_type(event_type) {
                            unknown_event_count = unknown_event_count.saturating_add(1);
                            if unknown_event_count == 1 || unknown_event_count % 100 == 0 {
                                log::warn!(
                                    "[WS-READER] dropping unknown event type (count={}): {}",
                                    unknown_event_count,
                                    event_type
                                );
                            }
                            continue;
                        }

                        // ADR-0020 §9: coalesce bubble_level from ~60 Hz
                        // to ≤30 Hz.
                        if event_type == "bubble_level" {
                            last_bubble_payload = Some(payload);
                            let now = Instant::now();
                            if bubble_coalesce_should_emit(last_bubble_level, now, BUBBLE_LEVEL_COALESCE_HZ) {
                                last_bubble_level = Some(now);
                                // previously
                                // `last_bubble_payload.take().unwrap()` —
                                // a panic if `take()` returned None. While
                                // in the current control flow `take()` is
                                // always Some at this point (we just
                                // assigned it above on this same iteration),
                                // the `.unwrap()` is brittle to future
                                // refactors that change the assignment
                                // ordering. Use `if let Some(p) = ...` so
                                // the emit is a no-op on the (currently
                                // unreachable) None path instead of a panic.
                                if let Some(p) = last_bubble_payload.take() {
                                    // ADR-0020 "Sidecar→UI Event Table" (channel 2):
                                    // emit BOTH the specific event (for direct
                                    // listeners like the bubble window) AND the
                                    // generic `python-event` (for the usePython
                                    // hook's onEvent catch-all, matching the
                                    // Electron path's ipcRenderer.on("python-event")).
                                    let _ = app_for_reader.emit("bubble_level", p.clone());
                                    let _ = app_for_reader.emit("python-event", json!({"type": "bubble_level", "data": p}));
                                }
                            }
                            continue;
                        }

                        // extracted the event-name translation
                        // into `translate_event_name` (in `ws_routes.rs`)
                        // so it can be unit-tested without a Tauri runtime,
                        // and so additional bubble-related event renames can
                        // be added in one place (the Python sidecar still
                        // publishes some events under snake_case names that
                        // the renderer expects as `bubble:*` kebab-case).
                        let emit_name = translate_event_name(event_type);
                        // ADR-0020 "Sidecar→UI Event Table" (channel 2):
                        // emit BOTH the specific event (for direct listeners)
                        // AND the generic `python-event` (for the usePython
                        // hook's onEvent catch-all).
                        let _ = app_for_reader.emit(emit_name, payload.clone());
                        let _ = app_for_reader.emit("python-event", json!({"type": emit_name, "data": payload}));

                        // the legacy `electron_notification` →
                        // `notification` alias block was REMOVED. The
                        // Python sidecar now publishes `notification`
                        // directly (and `electron_notification` is no
                        // longer in `ALLOWED_EVENT_TYPES`, so legacy
                        // frames are dropped earlier with a `[WS-READER]
                        // dropping unknown event type:` log line).
                    }
                    Ok(Message::Close(_)) => {
                        log::info!("[WS-READER] sidecar closed the WS");
                        break;
                    }
                    Ok(_) => {} // binary/ping/pong — ignore
                    Err(e) => {
                        log::warn!("[WS-READER] error: {}", e);
                        break;
                    }
                }
            }
            // log the silent stream-end explicitly. `read.next()`
            // returning `None` (vs an `Err`) means the WS stream ended
            // cleanly with no error frame — without this log line the
            // transition from "reader active" to "reader cleanup running"
            // was invisible in diagnostics. This line also fires on the
            // `break` paths above (Close frame, Err), but those arms log
            // their own specific reason first, so this serves as a
            // confirming "loop has exited" marker in all cases.
            log::info!("[WS-READER] stream ended (read.next() returned None)");
        })
        .catch_unwind()
        .await;

        if let Err(_panic_payload) = &result {
            log::error!(
                "[WS-READER] reader task panicked during body — running \
                 cleanup (drain pending, clear ws_tx, emit supervisor_relaunching, \
                 trigger supervisor respawn)"
            );
        }

        // WS reader exited (normally or via caught panic) — drain
        // pending dispatch requests + clear ws_tx so new dispatch
        // calls fail fast instead of queueing onto a dead channel
        // (CR-Finding 1 + 3). Then trigger supervisor respawn (unless we're
        // shutting down). This cleanup block runs UNCONDITIONALLY on
        // reader exit — even if the body panicked — because it uses the
        // cloned `state_for_cleanup` / `app_for_cleanup` handles (not
        // the originals, which were moved into the `AssertUnwindSafe`
        // body and may have been partially consumed before the panic).
        // The THREE side effects inside (ws_tx clear, drain pending,
        // respawn trigger) are individually gated on the generation
        // check below — see the next comment block for the race
        // rationale.
        //
        // only clear `ws_tx` / drain pending / trigger respawn if the
        // current generation matches `my_generation`. The race window is:
        // supervisor kills old sidecar → old reader's `read.next()`
        // returns None → meanwhile `reconnect_ws` runs and stores a
        // NEW `ws_tx` (bumping the generation) → old reader's cleanup
        // runs `*state.ws_tx = None` / drains `pending` / triggers a
        // respawn. Without the generation guard the old reader's cleanup
        // wipes the NEW connection's pending dispatches and arms a
        // spurious supervisor respawn on top of the new reconnect's
        // own recovery. The generation check makes the old reader's
        // cleanup a no-op across ALL three side effects — the new
        // reconnect owns recovery and handles its own drain/respawn if
        // it later fails.
        let current_generation = state_for_cleanup.ws_generation.load(Ordering::SeqCst);
        if current_generation == my_generation {
            {
                // Clear ws_tx first so new dispatch calls return
                // "sidecar not connected" immediately.
                // poison-safe lock helper.
                let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx);
                *ws_tx_guard = None;
            }
            {
                // drain pending requests via the shared
                // `drain_pending_with_disconnect_error` helper (collect out
                // of the lock first, then send outside the lock). Reject
                // each with a `sidecar_disconnected` error so callers don't
                // wait the full 120s timeout.
                let count = drain_pending_with_disconnect_error(&state_for_cleanup).await;
                if count > 0 {
                    log::warn!("[WS-READER] drained {} pending dispatch requests", count);
                }
            }
            if !state_for_cleanup.shutting_down.load(Ordering::SeqCst) {
                // (ADR-0020 §10): emit `supervisor_relaunching` IMMEDIATELY
                // at disconnect start so the UI can show a "reconnecting…"
                // banner before the backoff schedule runs. The eventual
                // `supervisor_reconnected` (on success) or second `supervisor_relaunching`
                // (on exhaustion) supersedes this event.
                let _ = app_for_cleanup.emit(
                    "supervisor_relaunching",
                    json!({"reason": "disconnected"}),
                );
                log::warn!("[WS-READER] unexpected close — triggering supervisor");
                // spawn supervisor respawn on a separate thread via the
                // shared `trigger_respawn_off_thread` helper. The thread
                // + `block_on` bridge is required because `respawn`
                // awaits `reconnect_ws`, whose future is `!Send`
                // (tokio-tungstenite holds a `!Send` across an await), and
                // `tokio::spawn` requires `Send` futures.
                // documents the failed attempt to use a direct
                // `tokio::spawn` here. See the helper's doc comment for
                // the full rationale.
                trigger_respawn_off_thread(
                    app_for_cleanup.clone(),
                    state_for_cleanup.clone(),
                );
            }
        } else {
            log::info!(
                "[WS-READER] cleanup skipping drain + respawn trigger — generation mismatch \
                 (mine={}, current={}); a newer reconnect owns recovery",
                my_generation,
                current_generation
            );
        }
    });
}

#[cfg(test)]
mod tests {
    use crate::state::PendingMap;
    use serde_json::{json, Value};
    use std::collections::HashMap;
    use std::time::Duration;
    use tokio::sync::{oneshot, Mutex as AsyncMutex};

    // pending-dispatch map (ADR-0020 §7) ────────────────────

    #[tokio::test]
    async fn test_pending_dispatch_map_fulfill_by_id() {
        // PendingMap no longer wrapped in outer Arc —
        // construct directly via `AsyncMutex::new(HashMap::new())`.
        let pending: PendingMap = AsyncMutex::new(HashMap::new());
        let id = 42u64;

        // Insert the pending request.
        let (tx, rx) = oneshot::channel::<Value>();
        pending.lock().await.insert(id, tx);
        assert_eq!(pending.lock().await.len(), 1, "pending map should have 1 entry after insert");

        // Simulate the WS reader fulfilling the request by id.
        let response = json!({"id": 42, "type": "result", "data": {"ok": true}});
        {
            let mut map = pending.lock().await;
            if let Some(sender) = map.remove(&id) {
                let _ = sender.send(response.clone());
            }
        }

        // The oneshot must resolve with the response (within a generous
        // 1s timeout — should be near-instant since the send already
        // happened).
        let received = tokio::time::timeout(Duration::from_secs(1), rx)
            .await
            .expect("oneshot did not resolve within 1s — sender was never invoked")
            .expect("oneshot sender was dropped without sending");
        assert_eq!(received, response, "received response must match the sent payload");

        // Map must be empty after fulfillment.
        assert_eq!(
            pending.lock().await.len(),
            0,
            "pending map should be empty after fulfillment"
        );
    }

    #[tokio::test]
    async fn test_pending_dispatch_map_unfulfilled_id_leaves_entry() {
        // PendingMap no longer wrapped in outer Arc.
        let pending: PendingMap = AsyncMutex::new(HashMap::new());
        let id = 99u64;
        let (tx, _rx) = oneshot::channel::<Value>();
        pending.lock().await.insert(id, tx);

        // Try to fulfill with the WRONG id (id=100, not 99).
        let wrong_response = json!({"id": 100, "type": "result"});
        {
            let mut map = pending.lock().await;
            // The reader task does `pending.remove(&id_from_frame)`, so
            // a mismatched id simply doesn't find an entry.
            if let Some(sender) = map.remove(&100u64) {
                let _ = sender.send(wrong_response.clone());
            }
        }

        // Entry for id=99 must still be present (the wrong-id fulfill
        // was a no-op from this map's perspective).
        assert_eq!(
            pending.lock().await.len(),
            1,
            "pending entry for id=99 must remain when a wrong-id response arrives"
        );
        assert!(
            pending.lock().await.contains_key(&id),
            "pending map must still contain id=99"
        );
    }
}
