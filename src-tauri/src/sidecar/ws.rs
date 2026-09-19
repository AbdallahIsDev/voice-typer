//! WebSocket reconnect + reader/writer tasks (ADR-0020 §1 + §9 + §10).
//! C-WS-1 handshake order / C-WS-2 TEXT frames / C-WS-3 generation.
//! NOTE: see docs/code-notes/tauri-host.md#ws-handshake-order-c-ws-1

mod event_protocol;
mod heartbeat;
mod reader;
mod respawn_scheduler;
mod writer;

pub(crate) use event_protocol::translate_event_name;
pub(crate) use heartbeat::abort_heartbeat;

// Re-export for ws_tests.rs (private submodule visibility).
pub(super) use event_protocol::{
    is_allowed_event_type, is_high_rate_event_type, python_event_envelope,
};
use heartbeat::spawn_heartbeat_task;
use reader::spawn_reader_task;
use respawn_scheduler::cleanup_and_trigger_respawn;
use writer::spawn_writer_task;

use crate::state::SidecarState;
use crate::state::lock as mutex_lock;
use crate::util::MAX_FRAME_BYTES;
use futures_util::{
    stream::{SplitSink, SplitStream},
    FutureExt, StreamExt,
};
use serde_json::{json, Value};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use tauri::Emitter;
use tokio::sync::{mpsc, oneshot};
use tokio_tungstenite::{
    connect_async_with_config, tungstenite::Message, MaybeTlsStream, WebSocketStream,
};

type WsStream = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;

// Bound WS connect so a hung handshake cannot stall the supervisor.
const WS_CONNECT_TIMEOUT_SECS: u64 = 5;

// Bounded writer channel (64 × 1 MiB worst-case = 64 MiB max).
// Callers use try_send and handle Full/Closed.
pub(crate) const WS_WRITER_CHANNEL_CAPACITY: usize = 64;

// IPC protocol version; bump in lockstep with Python PROTOCOL_VERSION.
const EXPECTED_PROTOCOL_VERSION: u64 = 1;

// Bound wait for auth_ok/ready so a dead sidecar cannot stall reconnect.
const WS_AUTH_OK_TIMEOUT_SECS: u64 = 3;

// Cap WS frame text logged at flood sites (HU-31: frames may carry PII).
const MAX_LOGGED_FRAME_TEXT_BYTES: usize = 256;

/// Truncate WS frame text for logging (HU-31): char-boundary safe,
/// `...[truncated]` marker when cut. `pub(super)` for ws_tests.rs.
pub(super) fn truncate_frame_text(text: &str) -> String {
    if text.len() <= MAX_LOGGED_FRAME_TEXT_BYTES {
        return text.to_string();
    }
    let mut end = MAX_LOGGED_FRAME_TEXT_BYTES;
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    let mut out = String::with_capacity(end + "[truncated]".len() + 3);
    out.push_str(&text[..end]);
    out.push_str("...[truncated]");
    out
}

/// Drain pending dispatches with sidecar_disconnected so callers don't
/// wait the full dispatch timeout. Collect out of lock, send outside.
pub(super) async fn drain_pending_with_disconnect_error(state: &Arc<SidecarState>) -> usize {
    let entries: Vec<(u64, oneshot::Sender<Value>)> = {
        let mut pending = state.pending.lock().await;
        pending.drain().collect()
    };
    let count = entries.len();
    for (_id, tx) in entries {
        let _ = tx.send(json!({
            "type": "error",
            "data": {
                "code": "sidecar_disconnected",
                "message": "sidecar WS disconnected (supervisor respawn in progress)"
            }
        }));
    }
    count
}

// reconnect_ws phase helpers (extracted from the former god function).

/// TCP-connect + WS handshake with timeout. Enforces 1 MiB frame cap.
/// NOTE: C-WS-1 accepts only auth_ok/ready as first post-auth frame.
async fn ws_connect(
    port: u16,
) -> Result<(SplitSink<WsStream, Message>, SplitStream<WsStream>), String> {
    let url = format!("ws://127.0.0.1:{}", port);
    // ADR-0020 §10: 1 MiB frame cap. WebSocketConfig is #[non_exhaustive].
    let mut ws_config = tokio_tungstenite::tungstenite::protocol::WebSocketConfig::default();
    ws_config.max_message_size = Some(MAX_FRAME_BYTES);
    ws_config.max_frame_size = Some(MAX_FRAME_BYTES);
    let connect_result = tokio::time::timeout(
        Duration::from_secs(WS_CONNECT_TIMEOUT_SECS),
        connect_async_with_config(&url, Some(ws_config), false),
    )
    .await;
    let (ws, _) = match connect_result {
        Ok(connect_inner) => connect_inner.map_err(|e| format!("WS reconnect failed: {e}"))?,
        Err(_) => {
            return Err(format!(
                "WS reconnect timed out after {}s",
                WS_CONNECT_TIMEOUT_SECS
            ));
        }
    };
    Ok(ws.split())
}

/// Create bounded writer channel, queue auth frame, store ws_tx, bump
/// generation (C-WS-3). SeqCst pairs with cleanup's generation load.
pub(super) async fn queue_auth_and_store_ws_tx(
    state: &Arc<SidecarState>,
    token: &str,
) -> Result<(mpsc::Receiver<Message>, u64), String> {
    let (ws_tx, ws_rx) = mpsc::channel::<Message>(WS_WRITER_CHANNEL_CAPACITY);
    // protocol_version is additive; older sidecars ignore unknown fields.
    let auth = json!({
        "type": "auth",
        "token": token,
        "protocol_version": EXPECTED_PROTOCOL_VERSION,
    });
    // Auth is the first frame on an empty channel — Full is impossible.
    ws_tx
        .try_send(Message::Text(auth.to_string().into()))
        .map_err(|e| match e {
            mpsc::error::TrySendError::Full(_) => {
                "auth frame queued beyond capacity (impossible at reconnect start)".to_string()
            }
            mpsc::error::TrySendError::Closed(_) => {
                "failed to queue auth frame (writer task closed channel)".to_string()
            }
        })?;
    // Drop guard before any await (MutexGuard is !Send).
    {
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        *ws_tx_guard = Some(ws_tx);
    }
    // C-WS-3: bump AFTER store; cleanup compares captured generation.
    let my_generation = state.ws_generation.fetch_add(1, Ordering::SeqCst) + 1;
    Ok((ws_rx, my_generation))
}

/// Wait for auth_ok OR ready (C-WS-1). Reject any other first frame.
/// Python currently emits `ready`; `auth_ok` is the future contract.
/// On ready: re-emit as Tauri event so renderer listeners still see it.
async fn wait_for_auth_ok(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    mut read: SplitStream<WsStream>,
) -> Result<SplitStream<WsStream>, String> {
    // catch_unwind: a panic must not kill the supervisor's long-lived thread.
    let app_for_body = app.clone();
    let state_for_body = state.clone();
    let result = AssertUnwindSafe(async move {
        let app = &app_for_body;
        let state = &state_for_body;
        let auth_result =
            tokio::time::timeout(Duration::from_secs(WS_AUTH_OK_TIMEOUT_SECS), read.next()).await;
        match auth_result {
            Err(_) => {
                log::error!(
                    "[WS-AUTH] auth_ok/ready timeout ({}s): closing WS and \
                 triggering supervisor",
                    WS_AUTH_OK_TIMEOUT_SECS
                );
                cleanup_and_trigger_respawn(app, state).await;
                Err(format!(
                    "WS auth timed out after {}s",
                    WS_AUTH_OK_TIMEOUT_SECS
                ))
            }
            Ok(None) => {
                log::error!("[WS-AUTH] stream closed before auth_ok/ready");
                cleanup_and_trigger_respawn(app, state).await;
                Err("WS stream closed during auth".to_string())
            }
            Ok(Some(Err(e))) => {
                log::error!("[WS-AUTH] error reading auth_ok/ready: {}", e);
                cleanup_and_trigger_respawn(app, state).await;
                Err(format!("WS auth read error: {}", e))
            }
            Ok(Some(Ok(msg))) => {
                let text = match msg {
                    Message::Text(t) => t.to_string(),
                    Message::Binary(b) => match String::from_utf8(b.to_vec()) {
                        Ok(s) => s,
                        Err(_) => {
                            log::warn!("[WS-AUTH] unexpected binary frame during auth");
                            cleanup_and_trigger_respawn(app, state).await;
                            return Err("WS auth received non-UTF8 binary".to_string());
                        }
                    },
                    Message::Close(_) => {
                        log::warn!("[WS-AUTH] server closed during auth");
                        cleanup_and_trigger_respawn(app, state).await;
                        return Err("WS closed during auth".to_string());
                    }
                    _ => {
                        log::warn!("[WS-AUTH] unexpected frame type (ping/pong) during auth");
                        cleanup_and_trigger_respawn(app, state).await;
                        return Err("WS auth unexpected frame type".to_string());
                    }
                };
                let v: Value = match serde_json::from_str(&text) {
                    Ok(v) => v,
                    Err(_) => {
                        log::warn!("[WS-AUTH] invalid JSON in auth response: {}", text);
                        cleanup_and_trigger_respawn(app, state).await;
                        return Err(format!("WS auth invalid JSON: {}", text));
                    }
                };
                let t = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
                if t == "auth_failed" {
                    log::error!("[WS-AUTH] auth_failed received from server");
                    cleanup_and_trigger_respawn(app, state).await;
                    return Err("WS auth rejected by server".to_string());
                }
                // C-WS-1: ONLY auth_ok or ready — any other first frame is a
                // protocol violation (compromised sidecar must not skip auth).
                if t == "auth_ok" {
                    log::info!("[WS-AUTH] auth_ok received, proceeding to reader task");
                } else if t == "ready" {
                    // Consume ready as auth signal; re-emit so renderer
                    // usePythonEvent("ready") listeners still fire.
                    log::info!(
                        "[WS-AUTH] ready frame received (auth confirmed): \
                     re-emitting as Tauri event"
                    );
                    let payload = v.get("data").cloned().unwrap_or(json!({}));
                    if let Err(e) = app.emit("ready", payload.clone()) {
                        log::warn!("[WS-AUTH] failed to re-emit ready event: {}", e);
                    }
                    if let Err(e) =
                        app.emit("python-event", python_event_envelope("ready", payload))
                    {
                        log::warn!(
                            "[WS-AUTH] failed to re-emit ready (python-event) event: {}",
                            e
                        );
                    }
                } else {
                    log::warn!(
                        "[WS-AUTH] expected auth_ok or ready, got: {}, \
                     treating as protocol violation, cleaning up and triggering respawn",
                        t
                    );
                    cleanup_and_trigger_respawn(app, state).await;
                    return Err(format!("WS auth unexpected frame type: {}", t));
                }
                Ok(read)
            }
        }
    })
    .catch_unwind()
    .await;
    match result {
        Ok(inner) => inner,
        Err(_panic_payload) => {
            log::error!(
                "[WS-AUTH] auth-read path panicked: running cleanup and \
                 triggering supervisor respawn"
            );
            cleanup_and_trigger_respawn(app, state).await;
            Err("WS auth path panicked (cleanup triggered)".to_string())
        }
    }
}

/// Connect → queue auth → writer → auth handshake → reader → heartbeat.
/// NOTE: see docs/code-notes/tauri-host.md#ws-handshake-order-c-ws-1
pub(crate) async fn reconnect_ws(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    port: u16,
    token: &str,
) -> Result<(), String> {
    let (write, read) = ws_connect(port).await?;
    let (ws_rx, my_generation) = queue_auth_and_store_ws_tx(state, token).await?;
    // Writer cleanup also generation-gated (C-WS-3).
    spawn_writer_task(app.clone(), state.clone(), write, ws_rx, my_generation);
    let state_clone = state.clone();
    let app_handle = app.clone();
    let read = wait_for_auth_ok(&app_handle, &state_clone, read).await?;
    spawn_reader_task(app_handle.clone(), state_clone.clone(), read, my_generation);
    spawn_heartbeat_task(app_handle, state_clone).await;
    Ok(())
}
