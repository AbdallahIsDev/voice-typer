//! WebSocket reconnect + reader/writer tasks (ADR-0020 §1 + §9 + §10).
//!
//! Module split (review.md FZ-24 / ZR-86): the original 2534-line
//! monolith was split into focused submodules under `ws/`:
//! - `ws/respawn_scheduler.rs` — long-lived supervisor thread,
//!   oneshot-fallback path, `cleanup_and_trigger_respawn`.
//! - `ws/event_protocol.rs` — server-initiated event allowlist +
//!   snake→kebab bubble-lifecycle translation.
//! - `ws/heartbeat.rs` — `spawn_heartbeat_task` + shared
//!   `abort_heartbeat` helper (called by both shutdown paths).
//! - `ws/reader.rs` — `spawn_reader_task` (frame parsing, dispatch
//!   fulfillment, event fan-out + cleanup block).
//! - `ws/writer.rs` — `spawn_writer_task` (writer-channel drain +
//!   symmetric cleanup block).
//!
//! This file holds the WS connect/auth pipeline (`ws_connect`,
//! `queue_auth_and_store_ws_tx`, `wait_for_auth_ok`, `reconnect_ws`,
//! and the `drain_pending_with_disconnect_error` helper shared with
//! `respawn_scheduler` and the reader/writer submodules).

// Tauri-side heartbeat dispatches a `heartbeat`
// command every 10s; on 3 consecutive misses it triggers supervisor respawn
// to detect application-level sidecar hangs (GIL contention, infinite
// loop, blocking C call) that keep the WS socket open but don't
// respond to dispatches. The heartbeat task itself lives in
// `ws/heartbeat.rs`; this file only spawns it via `reconnect_ws`.
//
// Cross-session merge note: session 1's ws.rs used `dispatch_frame`
// directly, but session 5 demoted `dispatch_frame` to private (only
// `dispatch_inner` is `pub(crate)` across all sessions). The
// heartbeat task uses `dispatch_inner` (the public wrapper) so the
// merged code compiles regardless of which session's `sidecar_cmds.rs`
// is picked by the owning sub-agent. `dispatch_inner` delegates to
// `dispatch_frame` internally — same WS-send path, same response
// semantics.

// Submodule declarations (review.md FZ-24 module split).
mod event_protocol;
mod heartbeat;
mod reader;
mod respawn_scheduler;
mod writer;

// Re-exports preserving the pre-split public API surface.
// `crate::sidecar::ws::translate_event_name` is referenced from
// `commands/bubble/commands.rs` comments; `crate::sidecar::ws::
// abort_heartbeat` is called from `state.rs::shutdown_sidecar_for_exit`
// and `commands/sidecar_cmds.rs::shutdown_sidecar`.
pub(crate) use event_protocol::translate_event_name;
pub(crate) use heartbeat::abort_heartbeat;

// Pull the submodule helpers used in the WS pipeline below into
// local scope so the call sites read the same as before the split.
// `pub(super)`: the sibling `sidecar/ws_tests.rs` module cannot see the
// private `event_protocol` submodule, so the emission gates and the
// envelope builder are re-exported here for the tests to pin the exact
// contract production exercises.
pub(super) use event_protocol::{
    is_allowed_event_type, is_high_rate_event_type, python_event_envelope,
};
use heartbeat::spawn_heartbeat_task;
// The reader/writer task bodies live in the `ws/reader.rs` /
// `ws/writer.rs` submodules; pull them into local scope so the
// `reconnect_ws` call sites read the same as before the split.
use reader::spawn_reader_task;
use respawn_scheduler::cleanup_and_trigger_respawn;
use writer::spawn_writer_task;

use crate::state::SidecarState;
// poison-safe Mutex helper for the cleanup block.
use crate::state::lock as mutex_lock;
// frame-size cap enforced in `ws_connect` (ADR-0020 §10 1 MiB limit).
// (`BUBBLE_LEVEL_COALESCE_HZ` moved to `ws/reader.rs` together with
// the reader task that consumes it.)
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

// type alias for the WebSocket stream returned by
// `connect_async_with_config`. Used by the `ws_connect`, `wait_for_auth_ok`,
// `spawn_writer_task`, and `spawn_reader_task` helpers so the split
// sink/stream halves can be passed between them without restating the
// full generic signature everywhere.
type WsStream = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;

// bound the WS connect attempt so a hung sidecar that
// accepts the TCP connection but never completes the WS handshake
// doesn't stall the supervisor forever.
const WS_CONNECT_TIMEOUT_SECS: u64 = 5;

// Bounded capacity for the WS writer channel
// (`queue_auth_and_store_ws_tx`).
// previously 256 — the worst-case queued memory was 256 ×
// MAX_FRAME_BYTES (1 MiB) = 256 MiB, a non-trivial OOM surface if a
// runaway renderer (or a stuck WS writer task) fills the channel before
// the writer task drains it. Reduced to 64: caps worst-case queued
// memory at 64 MiB, still large enough to absorb brief bursts (config +
// state + bubble-init frames at sidecar startup), small enough to
// fail-fast on a stuck writer. Callers in `commands/bubble.rs` and
// `commands/sidecar_cmds.rs` already use `ws_tx.try_send(...)` (not
// `send(...)`) and handle `TrySendError::Full` / `TrySendError::Closed`,
// so a smaller cap surfaces backpressure as a structured error rather
// than a silent OOM.
pub(crate) const WS_WRITER_CHANNEL_CAPACITY: usize = 64;

// IPC protocol version this host implements. The Python
// sidecar emits the same integer in its `server_started` stdout JSON
// (see `voice_typer/server/sidecar_ws.py:PROTOCOL_VERSION`). We also
// send it in our auth frame so the sidecar can detect skew at handshake
// time even when stdout parsing is bypassed (dev mode, manual restart).
// Bump in lockstep with the Python constant. History:
// initial protocol-version negotiation.
const EXPECTED_PROTOCOL_VERSION: u64 = 1;

// bound the wait for the `auth_ok` frame so a sidecar that
// never sends one (e.g. crashed between TCP accept and WS auth, or a
// malicious server holding the connection open) doesn't stall the
// reconnect path.
const WS_AUTH_OK_TIMEOUT_SECS: u64 = 3;

// cap the amount of WS frame text logged at the flood-prone
// reader warn sites (HU-31). Inbound frames can carry
// `transcription_partial` / `transcription_final` event data — the
// user's dictated speech (PII). A malformed/truncated frame would
// still contain partial transcription text; logging it verbatim
// persists it to the host's rotating log file (which may be shared
// with support or attached to crash reports). Keep at most
// MAX_LOGGED_FRAME_TEXT_BYTES bytes (char-boundary safe) with a
// `...[truncated]` marker so operators keep enough context to debug
// while unbounded PII stays out of the log.
const MAX_LOGGED_FRAME_TEXT_BYTES: usize = 256;

/// Truncate WS frame text for logging (HU-31): keep at most
/// [`MAX_LOGGED_FRAME_TEXT_BYTES`] bytes — never splitting a UTF-8
/// char — and append `...[truncated]` when the input was cut. Short
/// frames pass through unchanged.
///
/// `pub(super)` so the sibling `sidecar/ws_tests.rs` test module can
/// unit-test the truncation contract (boundary safety + marker).
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

/// drain all pending dispatch requests with a
/// `sidecar_disconnected` error response so in-flight dispatches don't
/// wait the full 120s timeout for a response that will never come.
///
/// Used by:
///   - `cleanup_and_trigger_respawn` (auth-failure / auth-timeout path)
///   - the WS reader's cleanup block (normal disconnect / panic path)
///
/// collect all entries out of the lock FIRST, then send outside
/// the lock. `oneshot::Sender::send` is non-blocking (it returns Err
/// immediately if the receiver was already dropped), but holding the
/// AsyncMutex across N sends is still an anti-pattern — a concurrent
/// dispatch path's `pending.insert(...)` would be stalled behind the
/// drain loop. The collect-then-send pattern bounds the lock hold time
/// to O(N) HashMap iteration (no I/O, no allocations beyond the Vec).
///
/// Returns the number of entries drained (for logging at the call site).
///
/// `pub(super)` so the sibling `ws/respawn_scheduler.rs` submodule
/// can call it from `cleanup_and_trigger_respawn` (which moved there
/// during the FZ-24 module split).
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

// phase helpers extracted from `reconnect_ws` ──────────────────
//
// `reconnect_ws` was a 585-line god function (Finding ) covering
// five distinct phases: (1) WS connect with timeout, (2) writer
// channel + auth-frame queue + writer task spawn, (3) auth handshake
// (wait for `auth_ok` / `ready`), (4) reader task spawn with
// catch_unwind cleanup, (5) heartbeat task spawn. Each phase is now
// a focused helper; `reconnect_ws` is a thin orchestrator that calls
// them in order. Behavior is preserved EXACTLY — same error strings,
// same retry/backoff semantics, same logging, same panic-safety
// wrappers, same supervisor trigger pattern.

/// (was inline in `reconnect_ws`): TCP-connect to the sidecar's
/// WS endpoint and complete the WS handshake with a bounded timeout
/// Enforces the ADR-0020 §10 1 MiB frame cap via
/// `WebSocketConfig`. Returns the split sink/stream halves so the
/// caller can hand them off to the writer and reader tasks.
///
/// A hung sidecar that accepts the TCP connection but never completes
/// the WS handshake would otherwise stall the supervisor forever (the
/// previous `connect_async_with_config` call had no timeout). The
/// timeout is mapped to a descriptive error so the backoff schedule
/// logs something actionable.
async fn ws_connect(
    port: u16,
) -> Result<(SplitSink<WsStream, Message>, SplitStream<WsStream>), String> {
    let url = format!("ws://127.0.0.1:{}", port);
    // ADR-0020 §10: enforce 1 MiB WS frame cap.
    // tungstenite 0.27 marked `WebSocketConfig` as
    // `#[non_exhaustive]`, so we can no longer construct it with a
    // struct expression. Use `Default::default()` and then set the
    // two fields we care about.
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

/// (was inline in `reconnect_ws`): set up the WS writer channel
/// and queue the auth frame on it. Returns the receiver for the writer
/// task to drain.
///
/// previously `mpsc::unbounded_channel::<Message>()`.
/// An unbounded channel provides NO backpressure — a runaway
/// renderer (or a stuck WS writer task) could enqueue unbounded
/// frames, each holding a `Message::Text(Utf8Bytes)` of up to
/// MAX_FRAME_BYTES (1 MiB), eventually OOM-killing the host.
/// Switched to a bounded channel of capacity 256: large enough to
/// absorb brief bursts (e.g. config + state + bubble-init frames at
/// sidecar startup), small enough to fail-fast on a stuck writer.
///
/// IMPORTANT API CHANGE: this changes the channel type from
/// `UnboundedSender<Message>` to `Sender<Message>`. The type alias
/// `WsWriterTx` in `state.rs` (line ~21) must change from
/// `mpsc::UnboundedSender<Message>` to `mpsc::Sender<Message>`, AND
/// the call sites in `commands/bubble.rs` (line ~363) and
/// `commands/sidecar_cmds.rs` (lines ~336, ~549) must change
/// `ws_tx.send(...)` to `ws_tx.try_send(...)` with error handling
/// for `TrySendError::Full` / `TrySendError::Closed`. Those files
/// are OUTSIDE this sub-agent's scope — see the return summary for
/// coordination instructions.
pub(super) async fn queue_auth_and_store_ws_tx(
    state: &Arc<SidecarState>,
    token: &str,
) -> Result<(mpsc::Receiver<Message>, u64), String> {
    let (ws_tx, ws_rx) = mpsc::channel::<Message>(WS_WRITER_CHANNEL_CAPACITY);
    // Send the auth frame via the channel so the writer task sends it.
    // include `protocol_version` so the sidecar can detect
    // host/sidecar version skew at handshake time. The field is
    // additive — older Python sidecars that don't yet parse it continue
    // to function (the sidecar's `_authenticate` ignores unknown fields).
    let auth = json!({
        "type": "auth",
        "token": token,
        "protocol_version": EXPECTED_PROTOCOL_VERSION,
    });
    // use `try_send` (bounded channel) instead of `send`
    // (which would await on a full channel). The auth frame is the
    // very first frame queued — the channel is empty so `try_send`
    // cannot return `Full`. `Closed` is possible only if the writer
    // task died between channel creation and this send (a few
    // microseconds — essentially impossible), but we handle it
    // defensively and map to the same error as before.
    ws_tx
        .try_send(Message::Text(auth.to_string().into()))
        .map_err(|e| match e {
            mpsc::error::TrySendError::Full(_) => {
                // Should be impossible at this point (channel is brand-
                // new and empty), but handle defensively.
                "auth frame queued beyond capacity (impossible at reconnect start)".to_string()
            }
            mpsc::error::TrySendError::Closed(_) => {
                "failed to queue auth frame (writer task closed channel)".to_string()
            }
        })?;
    // Drop the MutexGuard before spawning tasks (MutexGuard is !Send).
    {
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        *ws_tx_guard = Some(ws_tx);
    }
    // bump the generation counter AFTER storing
    // the new `ws_tx`, returning the new generation so the caller can
    // pass it to the spawned reader/writer tasks. Their cleanup blocks
    // compare this captured value against `state.ws_generation` at
    // cleanup time: if they differ, a newer reconnect has already
    // stored its own `ws_tx` and the cleanup must NOT clobber it.
    //
    // The fetch_add uses `Ordering::SeqCst` to pair with the cleanup
    // block's `SeqCst` load — we want a total order between the
    // "store ws_tx + bump gen" pair on the producer side and the
    // "load gen + clear ws_tx" pair on the consumer side. The
    // `state.ws_tx` Mutex already serializes the actual store/clear,
    // but the generation load happens BEFORE acquiring that Mutex in
    // the cleanup path, so SeqCst on both sides is the safe choice.
    let my_generation = state.ws_generation.fetch_add(1, Ordering::SeqCst) + 1;
    Ok((ws_rx, my_generation))
}

/// (was inline in `reconnect_ws`): wait for the `auth_ok` frame
/// (with a 3s timeout) before handing the read stream off to the
/// reader task. On success returns `read` so the caller can pass it
/// to `spawn_reader_task`. On any failure (timeout, stream close,
/// error, invalid frame, `auth_failed`) calls
/// `cleanup_and_trigger_respawn` and returns `Err`.
///
/// the Python sidecar's `_handle_connection` flow is:
/// (1) accept WS, (2) call `_authenticate` (validates the auth frame
/// we just sent), (3) on success, emit `{"type":"ready"}` and start
/// the dispatch loop.
///
/// NOTE: the current Python sidecar does NOT send an explicit
/// `auth_ok` frame — it emits `ready` on success and closes the
/// connection on auth failure. We accept EITHER `auth_ok` (future
/// contract) OR `ready` (current contract) as the auth-success
/// signal. On `auth_failed`, stream close, or timeout, we clear
/// `ws_tx` (which causes the writer task above to exit when its
/// channel drains) and trigger supervisor respawn.
///
/// This is a best-effort improvement: if the server sends neither
/// `auth_ok` nor `ready` within the timeout but the connection is
/// actually fine, we tear it down and let the supervisor try again. The
/// 3s timeout is generous enough that a healthy sidecar should
/// always respond within it.
async fn wait_for_auth_ok(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    mut read: SplitStream<WsStream>,
) -> Result<SplitStream<WsStream>, String> {
    // wrap the auth-read path (timeout + JSON parse + emit)
    // in `AssertUnwindSafe(...).catch_unwind()` so a panic inside any
    // of those steps doesn't propagate up to the supervisor's
    // `block_on` driver and permanently kill the long-lived
    // supervisor thread (which would degrade resilience to per-trigger
    // one-shot fallbacks). The reader/writer/heartbeat
    // task bodies are already wrapped; this closes
    // the asymmetry. On caught panic: log, call
    // `cleanup_and_trigger_respawn`, return a descriptive error.
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
                    "[WS-AUTH] auth_ok/ready timeout ({}s) — closing WS and \
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
                    // tungstenite 0.27 changed `Message::Text`'s
                    // inner type from `String` to `Utf8Bytes` (a smart
                    // pointer over `str`). `Utf8Bytes: Deref<Target=str>`,
                    // so `t.to_string()` works via the `str` impl and
                    // unifies the arm type with the `Message::Binary` arm
                    // (which produces a `String` from `String::from_utf8`).
                    Message::Text(t) => t.to_string(),
                    Message::Binary(b) => {
                        // Some clients send binary frames — try to decode
                        // as UTF-8 for the auth_ok check.
                        match String::from_utf8(b.to_vec()) {
                            Ok(s) => s,
                            Err(_) => {
                                log::warn!("[WS-AUTH] unexpected binary frame during auth");
                                cleanup_and_trigger_respawn(app, state).await;
                                return Err("WS auth received non-UTF8 binary".to_string());
                            }
                        }
                    }
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
                // tighten the auth-success contract. Accept ONLY
                // `auth_ok` (future contract) or `ready` (current Python
                // sidecar contract — see sidecar_ws.py:503) as the
                // auth-success signal. Any other frame type at auth time is
                // a protocol violation — reject it, clean up, and trigger
                // supervisor respawn. The previous "proceed anyway (best-
                // effort)" else branch accepted ANY non-`auth_failed` frame
                // as proof of auth, which let a buggy or compromised sidecar
                // skip the auth handshake by sending e.g. `{"type":
                // "bubble_level"}` first.
                if t == "auth_ok" {
                    log::info!("[WS-AUTH] auth_ok received — proceeding to reader task");
                } else if t == "ready" {
                    // The current Python sidecar emits `{"type":"ready"}`
                    // as the first post-auth frame. We consume it here as
                    // the auth-success signal, but we MUST re-emit it as a
                    // Tauri event so the renderer's `usePythonEvent("ready")`
                    // listeners (and the generic `python-event` catch-all)
                    // still see it — without this, the reader task never
                    // sees the frame and the event is silently lost.
                    log::info!(
                        "[WS-AUTH] ready frame received (auth confirmed) — \
                     re-emitting as Tauri event"
                    );
                    let payload = v.get("data").cloned().unwrap_or(json!({}));
                    // surface emit failures instead of silently
                    // dropping them. A failed `app.emit` here means the
                    // renderer won't see the `ready` event — log it so the
                    // miss is observable in diagnostics.
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
                    // protocol violation — reject, clean up, and
                    // trigger supervisor respawn. Do NOT proceed.
                    log::warn!(
                        "[WS-AUTH] expected auth_ok or ready, got: {} — \
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
                "[WS-AUTH] auth-read path panicked — running cleanup and \
                 triggering supervisor respawn"
            );
            cleanup_and_trigger_respawn(app, state).await;
            Err("WS auth path panicked (cleanup triggered)".to_string())
        }
    }
}

// thin orchestrator extracted from the original 585-line
// `reconnect_ws` god function (Finding). The five phases —
// WS connect, writer channel + auth frame + writer task spawn,
// auth handshake, reader task spawn, heartbeat task spawn — are now
// focused helpers above. This function calls them in sequence,
// propagating errors via `?`. Behavior is preserved EXACTLY: same
// error strings, same retry/backoff semantics (which live in the
// supervisor that calls this), same logging, same panic-safety
// wrappers, same supervisor trigger pattern.
pub(crate) async fn reconnect_ws(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
    port: u16,
    token: &str,
) -> Result<(), String> {
    //the parameter was previously named `_app` (underscore
    // prefix implies unused), but it IS used below at `app.clone()` for
    // the reader/writer tasks. Renamed to `app` to reflect actual use
    // and silence the misleading-underscore lint.
    let (write, read) = ws_connect(port).await?;
    let (ws_rx, my_generation) = queue_auth_and_store_ws_tx(state, token).await?;
    //pass ``app`` + ``state`` so ``spawn_writer_task`` can
    // run the symmetric cleanup block (clear ws_tx, drain pending,
    // trigger respawn) on write-half failure — previously the writer
    // task had no cleanup block, leaving dead writes blocking
    // dispatch callers for up to 30s.
    // pass `my_generation` so the writer cleanup block can
    // skip clearing `ws_tx` if a newer reconnect has already stored
    // its own sender (race guard).
    spawn_writer_task(app.clone(), state.clone(), write, ws_rx, my_generation);
    let state_clone = state.clone();
    let app_handle = app.clone();
    let read = wait_for_auth_ok(&app_handle, &state_clone, read).await?;
    spawn_reader_task(app_handle.clone(), state_clone.clone(), read, my_generation);
    // `spawn_heartbeat_task` is now `async fn` — `.await` it
    // instead of fire-and-forget. The function only holds the
    // `AsyncMutex` guard for the brief synchronous take/store sections
    // (no `.await` inside the critical section), so this doesn't add
    // meaningful latency to `reconnect_ws`.
    spawn_heartbeat_task(app_handle, state_clone).await;
    Ok(())
}
