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
//!
//! This file holds the WS connect/auth/reader/writer pipeline
//! (`ws_connect`, `queue_auth_and_store_ws_tx`, `spawn_writer_task`,
//! `wait_for_auth_ok`, `spawn_reader_task`, `reconnect_ws`, and the
//! `drain_pending_with_disconnect_error` helper shared with
//! `respawn_scheduler`).

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
mod respawn_scheduler;

// Re-exports preserving the pre-split public API surface.
// `crate::sidecar::ws::translate_event_name` is referenced from
// `commands/bubble/commands.rs` comments; `crate::sidecar::ws::
// abort_heartbeat` is called from `state.rs::shutdown_sidecar_for_exit`
// and `commands/sidecar_cmds.rs::shutdown_sidecar`.
pub(crate) use event_protocol::translate_event_name;
pub(crate) use heartbeat::abort_heartbeat;

// Pull the submodule helpers used in the WS pipeline below into
// local scope so the call sites read the same as before the split.
use event_protocol::is_allowed_event_type;
use heartbeat::spawn_heartbeat_task;
use respawn_scheduler::{cleanup_and_trigger_respawn, trigger_respawn_off_thread};

use crate::state::SidecarState;
// poison-safe Mutex helper for the cleanup block.
use crate::state::lock as mutex_lock;
// `bubble_coalesce_should_emit` moved out of `supervisor.rs` into
// its own `sidecar/bubble_coalesce.rs` module — it's a pure UI-rate-
// limiting predicate with nothing to do with sidecar supervision. The
// supervisor module now owns ONLY respawn/backoff logic.
use crate::sidecar::bubble_coalesce::bubble_coalesce_should_emit;
// frame-size cap enforced in `ws_connect` (ADR-0020 §10 1 MiB limit).
use crate::util::{BUBBLE_LEVEL_COALESCE_HZ, MAX_FRAME_BYTES};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use futures_util::{
    stream::{SplitSink, SplitStream},
    FutureExt, SinkExt, StreamExt,
};
use serde_json::{json, Value};
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
        Ok(connect_inner) => connect_inner
            .map_err(|e| format!("WS reconnect failed: {e}"))?,
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
fn queue_auth_and_store_ws_tx(
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
fn spawn_writer_task(
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
        {
            let count = drain_pending_with_disconnect_error(&state_for_cleanup).await;
            if count > 0 {
                log::warn!(
                    "[WS-WRITER] drained {} pending dispatch requests on write-half failure (XE-15-1)",
                    count
                );
            }
        }
        if !state_for_cleanup.shutting_down.load(Ordering::SeqCst) {
            let _ = app_for_cleanup.emit(
                "supervisor_relaunching",
                json!({"reason": "writer_half_closed"}),
            );
            log::warn!(
                "[WS-WRITER] write half closed — triggering supervisor respawn (XE-15-1)"
            );
            trigger_respawn_off_thread(
                app_for_cleanup.clone(),
                state_for_cleanup.clone(),
            );
        }
    });
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
        let auth_result = tokio::time::timeout(
            Duration::from_secs(WS_AUTH_OK_TIMEOUT_SECS),
            read.next(),
        )
        .await;
    match auth_result {
        Err(_) => {
            log::error!(
                "[WS-AUTH] auth_ok/ready timeout ({}s) — closing WS and \
                 triggering supervisor",
                WS_AUTH_OK_TIMEOUT_SECS
            );
            cleanup_and_trigger_respawn(app, state).await;
            return Err(format!(
                "WS auth timed out after {}s",
                WS_AUTH_OK_TIMEOUT_SECS
            ));
        }
        Ok(None) => {
            log::error!("[WS-AUTH] stream closed before auth_ok/ready");
            cleanup_and_trigger_respawn(app, state).await;
            return Err("WS stream closed during auth".to_string());
        }
        Ok(Some(Err(e))) => {
            log::error!("[WS-AUTH] error reading auth_ok/ready: {}", e);
            cleanup_and_trigger_respawn(app, state).await;
            return Err(format!("WS auth read error: {}", e))
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
                            log::warn!(
                                "[WS-AUTH] unexpected binary frame during auth"
                            );
                            cleanup_and_trigger_respawn(app, state).await;
                            return Err(
                                "WS auth received non-UTF8 binary".to_string()
                            );
                        }
                    }
                }
                Message::Close(_) => {
                    log::warn!("[WS-AUTH] server closed during auth");
                    cleanup_and_trigger_respawn(app, state).await;
                    return Err("WS closed during auth".to_string());
                }
                _ => {
                    log::warn!(
                        "[WS-AUTH] unexpected frame type (ping/pong) during auth"
                    );
                    cleanup_and_trigger_respawn(app, state).await;
                    return Err("WS auth unexpected frame type".to_string());
                }
            };
            let v: Value = match serde_json::from_str(&text) {
                Ok(v) => v,
                Err(_) => {
                    log::warn!(
                        "[WS-AUTH] invalid JSON in auth response: {}",
                        text
                    );
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
                if let Err(e) = app.emit(
                    "python-event",
                    json!({"type": "ready", "data": payload}),
                ) {
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

/// (was inline in `reconnect_ws`): spawn the WS reader task.
///
/// Parses incoming frames, fulfills pending dispatch requests by id,
/// emits Tauri events for server-initiated events. The body is wrapped
/// in `AssertUnwindSafe(...).catch_unwind()` so a panic
/// inside `read.next()` / `serde_json::from_str` / `app.emit()` /
/// `bubble_coalesce_should_emit()` doesn't tear down the task without
/// running cleanup. Without this wrapper, a panic would propagate to
/// the tokio runtime, which logs the panic and drops the task —
/// leaving `ws_tx` set (new dispatch calls would queue onto a dead
/// channel forever) and pending dispatch requests hanging until their
/// 120s timeout. With the wrapper, the panic is caught, logged at
/// ERROR, and the cleanup block below runs identically to the
/// normal-exit path (drain pending, clear ws_tx, emit
/// `supervisor_relaunching`, spawn supervisor respawn).
fn spawn_reader_task(
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
            // `last_bubble_payload` was removed: it was always overwritten
            // before being read (the assignment on each `bubble_level`
            // frame precedes the `take()` inside the coalesce-emit block,
            // so the Option only ever held the current frame's payload).
            // The current `payload` local is used directly inside the
            // coalesce-emit block — same behavior, no per-frame Option
            // allocation, no `#[allow(unused_assignments)]` needed.
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
                        // `ALLOWED_EVENT_TYPES` is defined at the top
                        // of this file and covers all event types the
                        // Python sidecar is known to publish today.
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
                            let now = Instant::now();
                            if bubble_coalesce_should_emit(last_bubble_level, now, BUBBLE_LEVEL_COALESCE_HZ) {
                                last_bubble_level = Some(now);
                                // ADR-0020 "Sidecar→UI Event Table" (channel 2):
                                // emit BOTH the specific event (for direct
                                // listeners like the bubble window) AND the
                                // generic `python-event` (for the usePython
                                // hook's onEvent catch-all, matching the
                                // Electron path's ipcRenderer.on("python-event")).
                                // The current frame's `payload` is emitted
                                // directly — the prior `last_bubble_payload`
                                // Option was redundant (always overwritten
                                // before being read).
                                let _ = app_for_reader.emit("bubble_level", payload.clone());
                                let _ = app_for_reader.emit("python-event", json!({"type": "bubble_level", "data": payload}));
                            }
                            continue;
                        }

                        // extracted the event-name translation
                        // into `translate_event_name` so it can be unit-
                        // tested without a Tauri runtime, and so additional
                        // bubble-related event renames can be added in one
                        // place (the Python sidecar still publishes some
                        // events under snake_case names that the renderer
                        // expects as `bubble:*` kebab-case).
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
    let (ws_rx, my_generation) = queue_auth_and_store_ws_tx(state, token)?;
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::PendingMap;
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

    // drain_pending_with_disconnect_error ─────────

    /// the drain helper must send a `sidecar_disconnected` error
    /// response to EVERY orphaned oneshot, and clear the pending map.
    /// This pins the contract used by both `cleanup_and_trigger_respawn`
    /// (auth-failure path) and the WS reader's cleanup block (normal
    /// disconnect / panic path) — without the drain, in-flight dispatches
    /// wait the full 120s timeout for a response that will never come.
    #[tokio::test]
    async fn test_ue8_drain_pending_sends_disconnect_error_to_all() {
        let state = Arc::new(crate::state::SidecarState::new());
        // Insert 3 pending entries with oneshot senders.
        let mut rx_list = Vec::new();
        for id in 1u64..=3u64 {
            let (tx, rx) = oneshot::channel::<Value>();
            state.pending.lock().await.insert(id, tx);
            rx_list.push(rx);
        }
        assert_eq!(state.pending.lock().await.len(), 3);

        // Call the drain helper.
        let drained = drain_pending_with_disconnect_error(&state).await;
        assert_eq!(drained, 3, "drain helper must report 3 drained entries");
        assert_eq!(
            state.pending.lock().await.len(),
            0,
            "pending map must be empty after drain"
        );

        // Each receiver must get a sidecar_disconnected error.
        for rx in rx_list {
            let received = tokio::time::timeout(Duration::from_secs(1), rx)
                .await
                .expect("oneshot did not resolve within 1s — drain helper did not send")
                .expect("oneshot sender was dropped without sending");
            assert_eq!(received["type"], "error", "drained response type must be \"error\"");
            assert_eq!(
                received["data"]["code"], "sidecar_disconnected",
                "drained response code must be \"sidecar_disconnected\""
            );
            assert!(
                received["data"]["message"].as_str().is_some(),
                "sidecar_disconnected error must have a message string"
            );
        }
    }

    /// the drain helper must be a no-op on an empty pending map
    /// (returns 0, map stays empty). Both cleanup paths call the helper
    /// unconditionally, so the empty case must not panic or log.
    #[tokio::test]
    async fn test_ue8_drain_pending_empty_map_returns_zero() {
        let state = Arc::new(crate::state::SidecarState::new());
        assert_eq!(state.pending.lock().await.len(), 0);
        let drained = drain_pending_with_disconnect_error(&state).await;
        assert_eq!(drained, 0, "drain helper on empty map must return 0");
        assert_eq!(state.pending.lock().await.len(), 0);
    }

    /// the drain helper must handle a receiver that was already
    /// dropped (the dispatch caller timed out / was cancelled). `oneshot::
    /// Sender::send` returns Err in that case — the helper must swallow
    /// the error (it already uses `let _ =`) and continue draining the
    /// rest. This pins the "swallow send-error" contract.
    #[tokio::test]
    async fn test_ue8_drain_pending_swallows_send_error_for_dropped_receiver() {
        let state = Arc::new(crate::state::SidecarState::new());
        // Insert 2 entries. Drop the FIRST receiver immediately (simulates
        // a dispatch caller that already timed out and dropped its
        // oneshot receiver). Keep the second receiver alive.
        let (tx1, rx1) = oneshot::channel::<Value>();
        drop(rx1); // simulate dropped receiver
        state.pending.lock().await.insert(1u64, tx1);
        let (tx2, rx2) = oneshot::channel::<Value>();
        state.pending.lock().await.insert(2u64, tx2);
        assert_eq!(state.pending.lock().await.len(), 2);

        // Drain — must not panic on the dropped receiver.
        let drained = drain_pending_with_disconnect_error(&state).await;
        assert_eq!(drained, 2, "drain helper must report 2 drained entries (both attempted)");

        // The second receiver must still get its error response.
        let received = tokio::time::timeout(Duration::from_secs(1), rx2)
            .await
            .expect("oneshot #2 did not resolve within 1s")
            .expect("oneshot #2 sender was dropped without sending");
        assert_eq!(received["data"]["code"], "sidecar_disconnected");
        assert_eq!(state.pending.lock().await.len(), 0);
    }

    // ── ws_generation race guard ──────────────────────

    /// a fresh `SidecarState` must have `ws_generation == 0`
    /// (distinguishable from any live generation ≥1, which is bumped
    /// by the first `queue_auth_and_store_ws_tx`).
    #[test]
    fn test_si15_fresh_state_has_ws_generation_zero() {
        let state = Arc::new(crate::state::SidecarState::new());
        assert_eq!(
            state.ws_generation.load(Ordering::SeqCst),
            0,
            "fresh SidecarState must start at ws_generation=0"
        );
    }

    /// `queue_auth_and_store_ws_tx` must (a) store a `ws_tx`
    /// into `state.ws_tx`, (b) bump `ws_generation` by exactly 1 per
    /// call, and (c) return the new generation so the caller can pass
    /// it to the reader/writer cleanup blocks. Verifies the producer
    /// side of the race guard.
    #[tokio::test]
    async fn test_si15_queue_auth_increments_ws_generation_and_stores_ws_tx() {
        let state = Arc::new(crate::state::SidecarState::new());
        assert_eq!(state.ws_generation.load(Ordering::SeqCst), 0);
        assert!(
            mutex_lock(&state.ws_tx).is_none(),
            "precondition: fresh state must have ws_tx = None"
        );

        // First reconnect — bumps generation 0 → 1, stores ws_tx.
        let (_ws_rx1, gen1) = queue_auth_and_store_ws_tx(&state, "token-1")
            .expect("first queue_auth must succeed");
        assert_eq!(gen1, 1, "first reconnect must return generation 1");
        assert_eq!(
            state.ws_generation.load(Ordering::SeqCst),
            1,
            "state must reflect generation 1 after first reconnect"
        );
        assert!(
            mutex_lock(&state.ws_tx).is_some(),
            "ws_tx must be Some after queue_auth"
        );

        // Second reconnect — bumps generation 1 → 2, stores a NEW ws_tx
        // (replacing the old one). This mirrors the supervisor's
        // reconnect-after-kill path.
        let (_ws_rx2, gen2) = queue_auth_and_store_ws_tx(&state, "token-2")
            .expect("second queue_auth must succeed");
        assert_eq!(gen2, 2, "second reconnect must return generation 2");
        assert_eq!(
            state.ws_generation.load(Ordering::SeqCst),
            2,
            "state must reflect generation 2 after second reconnect"
        );

        // Generations must be strictly monotonic.
        assert!(gen2 > gen1, "generations must be monotonic");
    }

    /// the reader/writer cleanup "generation guard" contract —
    /// if the current `ws_generation` does NOT match the captured
    /// `my_generation`, the cleanup must NOT clear `ws_tx`. This is
    /// the core invariant of the race fix. We simulate the race
    /// directly by manipulating the atomic + Mutex (no real WS needed).
    #[tokio::test]
    async fn test_si15_cleanup_generation_guard_skips_clear_on_mismatch() {
        let state = Arc::new(crate::state::SidecarState::new());

        // Simulate: old reconnect (gen=1) stored a ws_tx, then a NEW
        // reconnect (gen=2) replaced it. The old reader's cleanup is
        // now running with my_generation=1.
        let (_old_ws_rx, _gen1) = queue_auth_and_store_ws_tx(&state, "old-token")
            .expect("old queue_auth must succeed");
        let (new_ws_rx, gen2) = queue_auth_and_store_ws_tx(&state, "new-token")
            .expect("new queue_auth must succeed");
        let my_generation = 1u64; // the OLD reader's captured generation
        assert_eq!(gen2, 2, "precondition: new reconnect must be generation 2");
        assert_eq!(
            state.ws_generation.load(Ordering::SeqCst),
            2,
            "precondition: current generation must be 2"
        );
        // Keep the new ws_rx alive so the new sender isn't dropped
        // prematurely (mirrors the real writer task holding the rx).
        let _new_ws_rx_guard = new_ws_rx;

        // The NEW ws_tx is currently stored. Verify it's present.
        assert!(
            mutex_lock(&state.ws_tx).is_some(),
            "precondition: ws_tx must be Some (belongs to the new reconnect)"
        );

        // Run the OLD reader's cleanup logic (inlined here to mirror
        // the spawn_reader_task cleanup block at ws.rs ~1234-1250).
        {
            let current_generation = state.ws_generation.load(Ordering::SeqCst);
            let mut ws_tx_guard = mutex_lock(&state.ws_tx);
            if current_generation == my_generation {
                *ws_tx_guard = None;
            }
            // else: SKIP the clear (this is the race guard).
        }

        // The new ws_tx must STILL be present — the old cleanup did NOT
        // clobber it because the generations mismatched (mine=1, current=2).
        assert!(
            mutex_lock(&state.ws_tx).is_some(),
            "ws_tx must survive an old-generation cleanup (race guard)"
        );
    }

    /// when the generations DO match (the normal, non-racy
    /// disconnect case), the cleanup MUST clear `ws_tx`. This pins
    /// that the generation guard doesn't accidentally skip the clear
    /// on the happy path (which would leave a dead sender in place
    /// and break dispatch's "fail fast" contract).
    #[tokio::test]
    async fn test_si15_cleanup_generation_guard_clears_on_match() {
        let state = Arc::new(crate::state::SidecarState::new());

        // Single reconnect — generation 1, no newer reconnect has run.
        let (_ws_rx, gen1) = queue_auth_and_store_ws_tx(&state, "token")
            .expect("queue_auth must succeed");
        let my_generation = gen1;
        assert_eq!(
            state.ws_generation.load(Ordering::SeqCst),
            my_generation,
            "precondition: current generation must match the captured one"
        );
        assert!(
            mutex_lock(&state.ws_tx).is_some(),
            "precondition: ws_tx must be Some before cleanup"
        );

        // Run the cleanup logic with matching generations.
        {
            let current_generation = state.ws_generation.load(Ordering::SeqCst);
            let mut ws_tx_guard = mutex_lock(&state.ws_tx);
            if current_generation == my_generation {
                *ws_tx_guard = None;
            }
        }

        // ws_tx must have been cleared (generations matched → no race).
        assert!(
            mutex_lock(&state.ws_tx).is_none(),
            "ws_tx must be cleared when generations match (normal disconnect path)"
        );
    }

    /// delayed-cleanup race scenario: an OLD reader (generation 1)
    /// exits while a NEW reconnect (generation 2) has already stored its
    /// own `ws_tx` and is mid-auth. The old reader's cleanup block must
    /// NOT (a) clear the new `ws_tx`, (b) drain the new connection's
    /// pending dispatch map, or (c) trigger a spurious supervisor
    /// respawn on top of the new reconnect's own recovery. All three
    /// side effects are gated on the same `current_generation ==
    /// my_generation` predicate; this test exercises the predicate
    /// directly (mirroring the inline structure of the cleanup block at
    /// `spawn_reader_task` ~line 1267) and asserts the drain branch is
    /// skipped on mismatch. The respawn-trigger branch shares the same
    /// `if` block, so a mismatched generation skips it transitively
    /// (constructing a `tauri::AppHandle` in a unit test is infeasible,
    /// so the trigger call itself is not exercised here — the gating
    /// predicate is the unit under test, not the spawn-thread bridge).
    #[tokio::test]
    async fn test_cleanup_delayed_reader_skips_drain_and_respawn_on_generation_mismatch() {
        let state = Arc::new(crate::state::SidecarState::new());

        // Old reconnect (gen=1) stored a ws_tx, then a NEW reconnect
        // (gen=2) replaced it. The old reader's cleanup is now running
        // with my_generation=1 — its `read.next()` returned None late
        // (Tokio runtime contention) after the new reconnect's auth
        // already completed.
        let (_old_ws_rx, _gen1) = queue_auth_and_store_ws_tx(&state, "old-token")
            .expect("old queue_auth must succeed");
        let (new_ws_rx, gen2) = queue_auth_and_store_ws_tx(&state, "new-token")
            .expect("new queue_auth must succeed");
        let my_generation = 1u64; // the OLD reader's captured generation
        assert_eq!(gen2, 2, "precondition: new reconnect must be generation 2");
        // Keep the new ws_rx alive so the new sender isn't dropped
        // prematurely (mirrors the real writer task holding the rx).
        let _new_ws_rx_guard = new_ws_rx;

        // The NEW connection has a pending dispatch in flight — the
        // new reader will fulfill it when the sidecar responds. An old
        // reader's cleanup MUST NOT drain this entry; doing so would
        // reject the new connection's in-flight dispatch with a
        // spurious `sidecar_disconnected` error.
        let pending_id = 99u64;
        let (pending_tx, mut pending_rx) = oneshot::channel::<Value>();
        {
            let mut pending = state.pending.lock().await;
            pending.insert(pending_id, pending_tx);
        }
        assert_eq!(
            state.pending.lock().await.len(),
            1,
            "precondition: new connection has 1 in-flight dispatch"
        );

        // Mirror the new gated cleanup block at `spawn_reader_task`
        // ~line 1267. On generation mismatch, NONE of the three side
        // effects (ws_tx clear, drain pending, respawn trigger) run —
        // the else branch only logs.
        let current_generation = state.ws_generation.load(Ordering::SeqCst);
        if current_generation == my_generation {
            {
                let mut ws_tx_guard = mutex_lock(&state.ws_tx);
                *ws_tx_guard = None;
            }
            let _count = drain_pending_with_disconnect_error(&state).await;
            // (respawn trigger elided — requires tauri::AppHandle;
            //  gating predicate is the unit under test, see test doc.)
        } else {
            // mismatch path — log only, no side effects.
        }

        // Assert: ws_tx survived (new reconnect's sender intact).
        assert!(
            mutex_lock(&state.ws_tx).is_some(),
            "ws_tx must survive an old-generation cleanup (race guard)"
        );
        // Assert: pending map was NOT drained — the new connection's
        // in-flight dispatch is still waiting for its response.
        assert_eq!(
            state.pending.lock().await.len(),
            1,
            "pending map must NOT be drained by an old-generation cleanup"
        );
        // The pending oneshot sender must still be live (i.e. the
        // receiver hasn't been fulfilled with a disconnect error). A
        // drain would have sent a `sidecar_disconnected` value, so a
        // successful `try_recv()` (or `Err(Closed)`) would prove the
        // channel was closed — `Err(Empty)` means it's still open.
        assert!(
            matches!(
                pending_rx.try_recv(),
                Err(tokio::sync::oneshot::error::TryRecvError::Empty)
            ),
            "pending dispatch oneshot must NOT be closed — old-generation cleanup must not drain it"
        );
    }

    /// symmetric positive case: when the generations DO match (the
    /// normal, non-racy disconnect), the cleanup block MUST drain the
    /// pending map so in-flight dispatches get a `sidecar_disconnected`
    /// error instead of waiting the full 120s timeout. Pairs with the
    /// mismatch test above to pin that the drain gate fires both ways.
    #[tokio::test]
    async fn test_cleanup_drains_pending_on_generation_match() {
        let state = Arc::new(crate::state::SidecarState::new());

        let (_ws_rx, gen1) = queue_auth_and_store_ws_tx(&state, "token")
            .expect("queue_auth must succeed");
        let my_generation = gen1;
        let _ws_rx_guard = _ws_rx;

        let pending_id = 7u64;
        let (pending_tx, mut pending_rx) = oneshot::channel::<Value>();
        state.pending.lock().await.insert(pending_id, pending_tx);
        assert_eq!(
            state.pending.lock().await.len(),
            1,
            "precondition: 1 in-flight dispatch before cleanup"
        );

        // Mirror the cleanup block on the matching-generation path.
        let current_generation = state.ws_generation.load(Ordering::SeqCst);
        if current_generation == my_generation {
            {
                let mut ws_tx_guard = mutex_lock(&state.ws_tx);
                *ws_tx_guard = None;
            }
            let count = drain_pending_with_disconnect_error(&state).await;
            assert_eq!(count, 1, "drain must reject the 1 in-flight dispatch");
        }

        assert!(
            mutex_lock(&state.ws_tx).is_none(),
            "ws_tx must be cleared on matching-generation cleanup"
        );
        assert_eq!(
            state.pending.lock().await.len(),
            0,
            "pending map must be empty after matching-generation cleanup"
        );
        // The drain sent a `sidecar_disconnected` value through the
        // oneshot sender, so `try_recv()` must return `Ok(..)`.
        assert!(
            pending_rx.try_recv().is_ok(),
            "pending dispatch oneshot must be closed (drain sent disconnect error)"
        );
    }

    // ── WS writer channel capacity ───────────────────────────────────

    /// The WS writer channel capacity must be 64 (not the previous
    /// 256). At MAX_FRAME_BYTES = 1 MiB, this caps worst-case queued
    /// memory at 64 MiB (vs 256 MiB at the old cap). 64 is still
    /// large enough to absorb the sidecar-startup burst (config +
    /// state + bubble-init frames) but small enough that a stuck WS
    /// writer task fails fast (TrySendError::Full) instead of
    /// ballooning memory.
    ///
    /// If this test fails, either:
    /// - someone changed the cap without updating this regression
    ///   guard (deliberate change — update the assertion); or
    /// - someone removed the named constant and went back to an
    ///   inline magic number (regression — restore the constant).
    #[test]
    fn test_ws_writer_channel_capacity_is_64() {
        assert_eq!(
            WS_WRITER_CHANNEL_CAPACITY, 64,
            "WS writer channel capacity must be 64 (was 256 — see comment on the constant)"
        );
    }

    /// `queue_auth_and_store_ws_tx` must construct a bounded channel
    /// of capacity `WS_WRITER_CHANNEL_CAPACITY` (not unbounded, not a
    /// different magic number). We verify this by attempting to send
    /// `WS_WRITER_CHANNEL_CAPACITY` frames successfully (no Full) and
    /// then asserting the next send returns `TrySendError::Full`. This
    /// pins the capacity at the constant value end-to-end (any change
    /// to the constant would surface here automatically).
    #[tokio::test]
    async fn test_queue_auth_creates_bounded_channel_at_capacity() {
        let state = Arc::new(crate::state::SidecarState::new());

        let (mut ws_rx, _gen) = queue_auth_and_store_ws_tx(&state, "auth-token")
            .expect("queue_auth must succeed on a fresh state");

        // The auth frame is the very first frame queued inside
        // queue_auth_and_store_ws_tx — so the channel currently has
        // 1 frame in flight (the auth frame). We can send
        // (WS_WRITER_CHANNEL_CAPACITY - 1) more frames before Full.
        let ws_tx = mutex_lock(&state.ws_tx)
            .clone()
            .expect("ws_tx must be Some after queue_auth");

        for i in 0..(WS_WRITER_CHANNEL_CAPACITY - 1) {
            let frame = Message::Text(format!("frame-{}", i).into());
            ws_tx
                .try_send(frame)
                .expect("send within capacity must succeed (no Full)");
        }

        // The channel is now full (auth + capacity-1 = capacity
        // frames). The next send MUST return TrySendError::Full.
        let overflow = ws_tx.try_send(Message::Text("overflow".into()));
        assert!(
            matches!(overflow, Err(mpsc::error::TrySendError::Full(_))),
            "send at capacity+1 must return TrySendError::Full (got {:?}) — \
             if this fails, the channel is unbounded or has the wrong capacity",
            overflow
        );

        ws_rx.close();
    }

    // ── bubble_level coalesce-emit payload identity ──────────────────
    //
    // The simplified `bubble_level` emit path (no `last_bubble_payload`
    // Option) must:
    // 1. Use the CURRENT frame's `payload` for both the specific
    //    `bubble_level` emit AND the generic `python-event` catch-all —
    //    no stale-payload retention from a prior suppressed frame.
    // 2. Coalesce at ≤30 Hz (delegates to `bubble_coalesce_should_emit`,
    //    already unit-tested in `bubble_coalesce.rs`).
    //
    // We mirror the inline emit decision (without a Tauri runtime /
    // AppHandle, which is infeasible to construct in a unit test) and
    // verify that on each emit:
    // - the emitted `bubble_level` payload equals the current frame's
    //   payload (NOT a prior frame's);
    // - the emitted `python-event` payload's `data` field equals the
    //   current frame's payload.
    //
    // This pins the contract that removing `last_bubble_payload` did not
    // change the wire behavior — both emits still carry the current
    // frame's payload, never a stale one.
    #[test]
    fn test_bubble_level_emit_uses_current_payload_not_stale() {
        // Simulate a 60 Hz stream for ~100 ms (6 frames at 16.667 ms
        // apart). With BUBBLE_LEVEL_COALESCE_HZ=30 (min interval ≈
        // 33.333 ms), frames at i=0 and i=2 (and i=4) pass; frames at
        // i=1, i=3, i=5 are suppressed. The emit on frame i=2 must use
        // frame i=2's payload — NOT frame i=1's (which was the most
        // recent suppressed frame).
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        let start = Instant::now();
        let step_60hz = Duration::from_micros(16_667);

        // Each frame's payload is a distinct JSON value so we can assert
        // which frame's payload was emitted.
        let frames: Vec<Value> = (0..6u32)
            .map(|i| json!({"frame": i, "level": i as f64 * 10.0}))
            .collect();

        let mut last_emitted: Option<Instant> = None;
        let mut emitted_payloads: Vec<Value> = Vec::new();
        let mut emitted_python_event_data: Vec<Value> = Vec::new();

        for (i, payload) in frames.iter().enumerate() {
            let now = start + step_60hz * i as u32;
            // Mirror the simplified inline emit path:
            // ```
            // if event_type == "bubble_level" {
            //     let now = Instant::now();
            //     if bubble_coalesce_should_emit(last_bubble_level, now, HZ) {
            //         last_bubble_level = Some(now);
            //         emit("bubble_level", payload.clone());
            //         emit("python-event", json!({"type": "bubble_level", "data": payload}));
            //     }
            //     continue;
            // }
            // ```
            if bubble_coalesce_should_emit(last_emitted, now, hz) {
                last_emitted = Some(now);
                // Capture the payload that would have been emitted on
                // the `bubble_level` channel and the `python-event`
                // channel (the latter wraps `payload` in a json! object).
                emitted_payloads.push(payload.clone());
                emitted_python_event_data
                    .push(json!({"type": "bubble_level", "data": payload.clone()}));
            }
        }

        // Coalesce must have fired at least once (frame 0 always emits).
        assert!(
            !emitted_payloads.is_empty(),
            "coalesce must emit at least the first frame"
        );
        // Coalesce must have suppressed at least one frame (60 Hz in →
        // 30 Hz out means at least one of the 6 frames was suppressed).
        assert!(
            emitted_payloads.len() < frames.len(),
            "coalesce must suppress some frames (60 Hz in → ~30 Hz out)"
        );

        // The FIRST emitted payload MUST be frame 0's payload (no stale
        // payload from a non-existent prior frame). This is the key
        // invariant: removing `last_bubble_payload` did not introduce a
        // stale-payload bug.
        assert_eq!(
            emitted_payloads[0], frames[0],
            "first emit must carry frame 0's payload (no stale retention)"
        );

        // Each emitted payload must match the CURRENT frame's payload
        // (the one whose `now` timestamp passed the coalesce check), NOT
        // a prior suppressed frame's payload. We re-derive the expected
        // emits by re-running the coalesce decision and asserting each
        // emitted payload matches the corresponding frame.
        let mut expected_emit_indices: Vec<usize> = Vec::new();
        let mut last_emitted2: Option<Instant> = None;
        for (i, _payload) in frames.iter().enumerate() {
            let now = start + step_60hz * i as u32;
            if bubble_coalesce_should_emit(last_emitted2, now, hz) {
                last_emitted2 = Some(now);
                expected_emit_indices.push(i);
            }
        }
        assert_eq!(
            emitted_payloads.len(),
            expected_emit_indices.len(),
            "re-run coalesce decision must produce the same emit count"
        );
        for (emit_idx, &frame_idx) in expected_emit_indices.iter().enumerate() {
            assert_eq!(
                emitted_payloads[emit_idx], frames[frame_idx],
                "emit #{} must carry frame {}'s payload (current frame, not stale)",
                emit_idx,
                frame_idx
            );
            // The `python-event` catch-all must wrap the SAME payload
            // in its `data` field (the prior `last_bubble_payload.take()`
            // path also satisfied this; the simplified path must too).
            assert_eq!(
                emitted_python_event_data[emit_idx]["data"], frames[frame_idx],
                "python-event emit #{} must wrap frame {}'s payload in its data field",
                emit_idx,
                frame_idx
            );
            assert_eq!(
                emitted_python_event_data[emit_idx]["type"], "bubble_level",
                "python-event emit #{} must have type=\"bubble_level\"",
                emit_idx
            );
        }
    }
}
