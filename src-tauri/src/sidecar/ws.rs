//! WebSocket reconnect + reader/writer tasks (ADR-0020 §1 + §9 + §10).

use crate::state::SidecarState;
use crate::sidecar::ft1::{bubble_coalesce_should_emit, ft1_respawn};
use crate::util::{BUBBLE_LEVEL_COALESCE_HZ, MAX_FRAME_BYTES};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Instant;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tauri::Emitter;
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async_with_config, tungstenite::Message};

pub(crate) async fn reconnect_ws(
    _app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    port: u16,
    token: &str,
) -> Result<(), String> {
    let url = format!("ws://127.0.0.1:{}", port);
    // ADR-0020 §10: enforce 1 MiB WS frame cap.
    let ws_config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig {
        max_message_size: Some(MAX_FRAME_BYTES),
        max_frame_size: Some(MAX_FRAME_BYTES),
        ..Default::default()
    };
    let (ws, _) = connect_async_with_config(&url, Some(ws_config), false)
        .await
        .map_err(|e| format!("WS reconnect failed: {e}"))?;
    let (write, mut read) = ws.split();

    // Set up the WS writer channel + reader task.
    let (ws_tx, mut ws_rx) = mpsc::unbounded_channel::<Message>();
    // Send the auth frame via the channel so the writer task sends it.
    let auth = json!({"type": "auth", "token": token});
    ws_tx
        .send(Message::Text(auth.to_string()))
        .map_err(|_| "failed to queue auth frame".to_string())?;
    // Drop the MutexGuard before spawning tasks (MutexGuard is !Send).
    {
        let mut ws_tx_guard = state.ws_tx.lock().unwrap();
        *ws_tx_guard = Some(ws_tx);
    }
    let state_clone = state.clone();
    let app_handle = _app.clone();

    // Writer task: drain ws_rx → write.send.
    tokio::spawn(async move {
        let mut write = write;
        while let Some(msg) = ws_rx.recv().await {
            if write.send(msg).await.is_err() {
                break;
            }
        }
    });

    // Reader task: parse incoming frames, fulfill pending dispatch
    // requests by id, emit Tauri events for server-initiated events.
    let app_for_reader = app_handle.clone();
    let state_for_reader = state_clone.clone();
    tokio::spawn(async move {
        let mut last_bubble_level: Option<Instant> = None;
        #[allow(unused_assignments)]
        let mut last_bubble_payload: Option<Value> = None;
        while let Some(msg) = read.next().await {
            match msg {
                Ok(Message::Text(text)) => {
                    let v: Value = match serde_json::from_str(&text) {
                        Ok(v) => v,
                        Err(_) => {
                            log::warn!("[WS-READER] invalid JSON frame: {}", text);
                            continue;
                        }
                    };
                    // If the frame has an `id`, it's a dispatch
                    // response — fulfill the pending oneshot.
                    if let Some(id) = v.get("id").and_then(|i| i.as_u64()) {
                        let mut pending = state_for_reader.pending.lock().await;
                        if let Some(tx) = pending.remove(&id) {
                            let _ = tx.send(v);
                        }
                        continue;
                    }
                    // Otherwise it's a server-initiated event
                    // (channel 2). Emit it as a Tauri event.
                    let event_type = v.get("type").and_then(|t| t.as_str()).unwrap_or("unknown");
                    let payload = v.get("data").cloned().unwrap_or(json!({}));

                    // ADR-0020 §9: coalesce bubble_level from ~60 Hz
                    // to ≤30 Hz.
                    if event_type == "bubble_level" {
                        last_bubble_payload = Some(payload);
                        let now = Instant::now();
                        if bubble_coalesce_should_emit(last_bubble_level, now, BUBBLE_LEVEL_COALESCE_HZ) {
                            last_bubble_level = Some(now);
                            let p = last_bubble_payload.take().unwrap();
                            // ADR-0020 §6.3: emit BOTH the specific event
                            // (for direct listeners like the bubble window)
                            // AND the generic `python-event` (for the
                            // usePython hook's onEvent catch-all, matching
                            // the Electron path's ipcRenderer.on("python-event")).
                            let _ = app_for_reader.emit("bubble_level", p.clone());
                            let _ = app_for_reader.emit("python-event", json!({"type": "bubble_level", "data": p}));
                        }
                        continue;
                    }

                    // ADR-0020 §6.1: rename `relaunch_electron` →
                    // `relaunch_app` (Tauri's `app.restart()` API).
                    //
                    // CR-8 (this change): the `electron_notification` →
                    // `notification` rename was REMOVED from this match
                    // arm — the Python sidecar now publishes the event
                    // under the platform-agnostic `notification` name
                    // directly (see `system_handlers.py` +
                    // `startup_sequence.py`), so it passes through
                    // unchanged via the `other => other` arm. A
                    // backward-compat alias BELOW handles old Python
                    // sidecars that still emit `electron_notification`
                    // during a rolling upgrade — emit BOTH names for
                    // one release cycle, then drop the alias.
                    let emit_name = match event_type {
                        "relaunch_electron" => "relaunch_app",
                        other => other,
                    };
                    // ADR-0020 §6.3: emit BOTH the specific event (for
                    // direct listeners) AND the generic `python-event`
                    // (for the usePython hook's onEvent catch-all).
                    let _ = app_for_reader.emit(emit_name, payload.clone());
                    let _ = app_for_reader.emit("python-event", json!({"type": emit_name, "data": payload}));

                    // CR-8 backward-compat alias: if an older Python
                    // sidecar still emits the legacy `electron_notification`
                    // event name (rolling upgrade), also emit it under
                    // the new canonical `notification` name so new UI
                    // code subscribing to `notification` keeps working.
                    // The legacy `electron_notification` emit above
                    // (via the `other => other` pass-through) keeps any
                    // old UI listeners working too. Drop this alias
                    // after one release cycle once all sidecars are
                    // upgraded to emit `notification` directly.
                    if event_type == "electron_notification" {
                        let _ = app_for_reader.emit("notification", payload.clone());
                    }
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
        // WS reader exited — drain pending dispatch requests + clear
        // ws_tx so new dispatch calls fail fast instead of queueing
        // onto a dead channel (CR-Finding 1 + 3). Then trigger FT-1
        // respawn (unless we're shutting down).
        {
            // Clear ws_tx first so new dispatch calls return
            // "sidecar not connected" immediately.
            let mut ws_tx_guard = state_for_reader.ws_tx.lock().unwrap();
            *ws_tx_guard = None;
        }
        {
            // Drain pending requests — reject each with an error so
            // callers don't wait the full 120s timeout.
            let mut pending = state_for_reader.pending.lock().await;
            let count = pending.len();
            for (_id, tx) in pending.drain() {
                let _ = tx.send(json!({
                    "type": "error",
                    "data": {
                        "code": "sidecar_disconnected",
                        "message": "sidecar WS disconnected (FT-1 respawn in progress)"
                    }
                }));
            }
            if count > 0 {
                log::warn!("[WS-READER] drained {} pending dispatch requests", count);
            }
        }
        if !state_for_reader.shutting_down.load(Ordering::SeqCst) {
            // CR-5 (ADR-0020 §10): emit `ft1_relaunching` IMMEDIATELY
            // at disconnect start so the UI can show a "reconnecting…"
            // banner before the backoff schedule runs. The eventual
            // `ft1_reconnected` (on success) or second `ft1_relaunching`
            // (on exhaustion) supersedes this event.
            let _ = app_for_reader.emit(
                "ft1_relaunching",
                json!({"reason": "disconnected"}),
            );
            log::warn!("[WS-READER] unexpected close — triggering FT-1");
            // Spawn FT-1 on a separate thread via std::thread::spawn +
            // a block_on, so the non-Send WS stream half doesn't
            // poison the tokio::spawn Send requirement. The FT-1
            // supervisor itself uses tokio::spawn internally for the
            // respawn attempts, so this is just a bridge.
            let app_clone = app_for_reader.clone();
            let state_clone = state_for_reader.clone();
            std::thread::spawn(move || {
                tauri::async_runtime::block_on(async move {
                    let _ = ft1_respawn(&app_clone, &state_clone).await;
                });
            });
        }
    });

    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::PendingMap;
    use std::collections::HashMap;
    use std::time::Duration;
    use tokio::sync::{oneshot, Mutex as AsyncMutex};

    // ── CR-13: pending-dispatch map (ADR-0020 §7) ────────────────────

    #[tokio::test]
    async fn test_pending_dispatch_map_fulfill_by_id() {
        // ADR-0020 §7: the WS reader task fulfills pending dispatch
        // requests by id. Insert a pending request with id=42, then
        // fulfill it with a response carrying id=42 — the oneshot
        // receiver must resolve with that exact response, and the map
        // must be empty afterwards.
        let pending: PendingMap = Arc::new(AsyncMutex::new(HashMap::new()));
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
        // A response with the wrong id must NOT fulfill a pending request.
        // The dispatch caller will time out (DISPATCH_TIMEOUT_SECS) and
        // remove the entry itself (see `dispatch` command).
        let pending: PendingMap = Arc::new(AsyncMutex::new(HashMap::new()));
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
