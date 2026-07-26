//! WebSocket reconnect + reader/writer tasks (ADR-0020 §1 + §9 + §10).

// PVT-1 (session 1): Tauri-side heartbeat dispatches a `heartbeat`
// command every 10s; on 3 consecutive misses it triggers supervisor respawn
// to detect application-level sidecar hangs (GIL contention, infinite
// loop, blocking C call) that keep the WS socket open but don't
// respond to dispatches.
//
// Cross-session merge note: session 1's ws.rs used `dispatch_frame`
// directly, but session 5 demoted `dispatch_frame` to private (only
// `dispatch_inner` is `pub(crate)` across all sessions). We use
// `dispatch_inner` (the public wrapper) so the merged code compiles
// regardless of which session's `sidecar_cmds.rs` is picked by the
// owning sub-agent. `dispatch_inner` delegates to `dispatch_frame`
// internally — same WS-send path, same response semantics.
use crate::commands::sidecar_cmds::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
// G4-H-27 (session 4): poison-safe Mutex helper for the cleanup block.
use crate::state::lock as mutex_lock;
// DT-53: `bubble_coalesce_should_emit` moved out of `supervisor.rs` into
// its own `sidecar/bubble_coalesce.rs` module — it's a pure UI-rate-
// limiting predicate with nothing to do with sidecar supervision. The
// supervisor module now owns ONLY respawn/backoff logic.
use crate::sidecar::bubble_coalesce::bubble_coalesce_should_emit;
use crate::sidecar::supervisor::respawn;
// DT-44: heartbeat interval / response timeout / max misses are now named
// constants in `util.rs` (previously inline `Duration::from_secs(10)` /
// `Duration::from_secs(15)` / `>= 3` literals below).
use crate::util::{
    BUBBLE_LEVEL_COALESCE_HZ, HEARTBEAT_INTERVAL_SECS, HEARTBEAT_MAX_MISSES,
    HEARTBEAT_RESPONSE_TIMEOUT_SECS, MAX_FRAME_BYTES,
};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use std::time::{Duration, Instant};
use futures_util::{
    stream::{SplitSink, SplitStream},
    FutureExt, SinkExt, StreamExt,
};
use serde_json::{json, Value};
use tauri::Emitter;
use tokio::sync::mpsc;
use tokio_tungstenite::{
    connect_async_with_config, tungstenite::Message, MaybeTlsStream, WebSocketStream,
};

// XZ-11: type alias for the WebSocket stream returned by
// `connect_async_with_config`. Used by the `ws_connect`, `wait_for_auth_ok`,
// `spawn_writer_task`, and `spawn_reader_task` helpers so the split
// sink/stream halves can be passed between them without restating the
// full generic signature everywhere.
type WsStream = WebSocketStream<MaybeTlsStream<tokio::net::TcpStream>>;

// ─── G4-H-32: server-initiated event-type allowlist ──────────────────────
//
// ADR-0020 §9: only known server-initiated event types may be emitted
// to the renderer as Tauri events. An unknown `type` field on an
// inbound WS frame is dropped with a `[WS-READER]` warning — this is
// defense-in-depth against a compromised sidecar process (or a
// protocol regression) trying to inject arbitrary event names that
// the renderer's `usePythonEvent(type, ...)` listeners might be
// tricked into handling.
//
// The first block below is the G4-H-32 spec list (verbatim). The
// second block is the set of additional events the Python sidecar
// ACTUALLY publishes today (`rg '"type":\s*"<name>"' voice_typer/server`)
// — without these, the host would silently drop `ready`, `bubble_show`,
// `history_changed`, etc. and break startup / bubble UI / history UI.
// Keep both blocks in sync with the server's `event_bus.publish`
// call sites. Drop the legacy `electron_notification` alias after
// one release cycle with no rolling-upgrade traffic.
const ALLOWED_EVENT_TYPES: &[&str] = &[
    // ── G4-H-32 spec list (verbatim) ──
    "status_change", "bubble_level", "notification", "relaunch_app",
    "tray_menu", "tray_state", "supervisor_relaunching", "supervisor_reconnected", "crash_recovery",
    "transcription_partial", "transcription_final", "transcription_interim",
    "recording_state", "vocabulary_suggestion", "model_download_progress",
    "audio_status", "server_started",
    // ── Additional known server-published events (G4-H-32 extension) ──
    // Lifecycle / window management:
    "ready", "quit_app", "show_window", "navigate",
    // Bubble UI:
    "bubble_show", "bubble_hide", "bubble_config", "bubble_set_state",
    // Recording (server emits *_started/*_stopped; `recording_state` in
    // the spec list above is the umbrella name some future server may
    // adopt — keep both):
    "recording_started", "recording_stopped",
    // Settings / config / history:
    "config_changed", "history_changed", "consent_required",
    // Hotkey capture:
    "hotkey_capture_cancel",
    // Microphone settings:
    "microphone_test_complete", "microphones_changed",
    // Model download (server emits `download_progress`; the spec list
    // above has the umbrella `model_download_progress`):
    "download_progress",
    // Engine fallback:
    "parakeet_cpu_fallback",
    // Paste error:
    "paste_failed",
    // GT-E3-6: legacy aliases `relaunch_electron` and
    // `electron_notification` REMOVED. The Python sidecar has published
    // the canonical `relaunch_app` and `notification` event names for
    // more than one release cycle; the rolling-upgrade grace period is
    // over. Old sidecars that still emit the legacy names will now have
    // those frames DROPPED by the `ALLOWED_EVENT_TYPES` allowlist
    // (logged at `[WS-READER] dropping unknown event type:`).
];

// G4-M-64: bound the WS connect attempt so a hung sidecar that
// accepts the TCP connection but never completes the WS handshake
// doesn't stall the supervisor forever.
const WS_CONNECT_TIMEOUT_SECS: u64 = 5;

// G4-L-02: bound the wait for the `auth_ok` frame so a sidecar that
// never sends one (e.g. crashed between TCP accept and WS auth, or a
// malicious server holding the connection open) doesn't stall the
// reconnect path.
const WS_AUTH_OK_TIMEOUT_SECS: u64 = 3;

// G4-L-02: helper for the auth-failed / auth-timeout path. Clears
// `state.ws_tx` (so the writer task exits when its channel drains and
// new dispatch calls fail fast with "sidecar not connected") and
// spawns supervisor respawn on a separate thread (same pattern as the
// reader task's cleanup at the bottom of `reconnect_ws`).
//
// Mirrors the reader task's cleanup shape but WITHOUT draining
// `pending` — at auth time no dispatch requests have been queued
// yet (auth is the very first frame), so there's nothing to drain.
fn cleanup_and_trigger_respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) {
    {
        // G4-H-27 rule: no `unwrap()` on new code. Recover from a
        // poisoned mutex by taking the inner guard (the data inside
        // may be stale but clearing `ws_tx` to `None` is safe even
        // on a poisoned lock).
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        *ws_tx_guard = None;
    }
    let _ = app.emit(
        "supervisor_relaunching",
        json!({"reason": "auth_failed_or_timeout"}),
    );
    trigger_respawn_off_thread(app.clone(), state.clone());
}

// EC-FIX-5 (EC-18): extracted helper for the respawn trigger
// pattern that was duplicated at the WS-reader cleanup site and the
// two heartbeat-miss arms (`Ok(Err(_))` and `Err(_)` from the 15s
// timeout). All three sites had the identical block:
//
//   let app_clone = <handle>.clone();
//   let state_clone = <state>.clone();
//   std::thread::spawn(move || {
//       tauri::async_runtime::block_on(async move {
//           let _ = respawn(&app_clone, &state_clone).await;
//       });
//   });
//
// The thread + `block_on` bridge is required because `respawn`
// awaits `reconnect_ws`, whose future is `!Send` (tokio-tungstenite
// holds a `!Send` across an await). `tokio::spawn` requires `Send`
// futures, so we drive the `!Send` future on a dedicated std thread
// with its own `block_on` runtime. NF-R19-1 documents the failed
// attempt to use a direct `tokio::spawn` here.
//
// This helper takes ownership (`app: AppHandle`, `state: Arc<SidecarState>`)
// so callers pass `.clone()`d handles in and the helper moves them
// into the spawned thread. Returns nothing — the supervisor is best-effort.
fn trigger_respawn_off_thread(app: tauri::AppHandle, state: Arc<SidecarState>) {
    // GT-C4-4: send on the long-lived supervisor channel instead of
    // spawning a new OS thread per call. The supervisor thread is
    // lazily spawned on first use via `respawn_supervisor_sender()`.
    if let Err(e) = respawn_supervisor_sender().send((app, state)) {
        log::error!(
            "[SUPERVISOR] failed to enqueue respawn request to supervisor thread (it may have panicked): {}",
            e
        );
    }
}

// GT-C4-4: single long-lived supervisor thread, lazily spawned on
// first use via a `OnceLock<mpsc::Sender>`. Replaces the prior
// pattern of spawning a NEW OS thread per trigger (WS reader
// cleanup, heartbeat miss #3, auth failure). Thread creation is
// ~50µs but the real cost is observability noise and a subtle
// pile-up risk if `respawn_in_progress` gets stuck. The supervisor
// loops on `rx.recv()` and runs `block_on(respawn)` for each
// request sequentially. Since `respawn` is already serialized
// by the `respawn_in_progress` compare_exchange, concurrent requests
// just queue in the channel buffer and no-op when the supervisor
// gets to them. The supervisor thread lives for the process
// lifetime (parked on `rx.recv()` when idle, ~8KB stack, zero CPU).
type RespawnRequest = (tauri::AppHandle, Arc<SidecarState>);

static RESPAWN_SUPERVISOR_TX: OnceLock<std::sync::mpsc::Sender<RespawnRequest>> = OnceLock::new();

fn respawn_supervisor_sender() -> &'static std::sync::mpsc::Sender<RespawnRequest> {
    RESPAWN_SUPERVISOR_TX.get_or_init(|| {
        let (tx, rx) = std::sync::mpsc::channel::<RespawnRequest>();
        std::thread::Builder::new()
            .name("respawn-supervisor".into())
            .spawn(move || {
                for (app, state) in rx {
                    let _ = tauri::async_runtime::block_on(async move {
                        let _ = respawn(&app, &state).await;
                    });
                }
            })
            .expect("GT-C4-4: failed to spawn respawn-supervisor thread");
        tx
    })
}

// ─── XZ-11: phase helpers extracted from `reconnect_ws` ──────────────────
//
// `reconnect_ws` was a 585-line god function (Finding EC-18) covering
// five distinct phases: (1) WS connect with timeout, (2) writer
// channel + auth-frame queue + writer task spawn, (3) auth handshake
// (wait for `auth_ok` / `ready`), (4) reader task spawn with
// catch_unwind cleanup, (5) heartbeat task spawn. Each phase is now
// a focused helper; `reconnect_ws` is a thin orchestrator that calls
// them in order. Behavior is preserved EXACTLY — same error strings,
// same retry/backoff semantics, same logging, same panic-safety
// wrappers, same supervisor trigger pattern.

/// XZ-11 (was inline in `reconnect_ws`): TCP-connect to the sidecar's
/// WS endpoint and complete the WS handshake with a bounded timeout
/// (G4-M-64). Enforces the ADR-0020 §10 1 MiB frame cap via
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
    // XZ-CC-10: tungstenite 0.27 marked `WebSocketConfig` as
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

/// XZ-11 (was inline in `reconnect_ws`): set up the WS writer channel
/// and queue the auth frame on it. Returns the receiver for the writer
/// task to drain.
///
/// PVT-G5-059: previously `mpsc::unbounded_channel::<Message>()`.
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
) -> Result<mpsc::Receiver<Message>, String> {
    let (ws_tx, ws_rx) = mpsc::channel::<Message>(256);
    // Send the auth frame via the channel so the writer task sends it.
    let auth = json!({"type": "auth", "token": token});
    // PVT-G5-059: use `try_send` (bounded channel) instead of `send`
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
    Ok(ws_rx)
}

/// XZ-11 (was inline in `reconnect_ws`): spawn the WS writer task.
///
/// Drains `ws_rx` into `write.send`. The body is wrapped in
/// `AssertUnwindSafe(...).catch_unwind()` (G4-H-26) so a panic inside
/// `write.send()` (e.g. a tungstenite internal invariant violation)
/// doesn't tear down the task without cleanup. The writer has no
/// post-panic cleanup beyond dropping `write` (which `catch_unwind`
/// does automatically as the wrapped future unwinds), but the
/// `catch_unwind` future still resolves normally so the outer
/// `tokio::spawn` future completes cleanly instead of propagating the
/// panic to the runtime.
fn spawn_writer_task(write: SplitSink<WsStream, Message>, mut ws_rx: mpsc::Receiver<Message>) {
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
    });
}

/// XZ-11 (was inline in `reconnect_ws`): wait for the `auth_ok` frame
/// (with a 3s timeout) before handing the read stream off to the
/// reader task. On success returns `read` so the caller can pass it
/// to `spawn_reader_task`. On any failure (timeout, stream close,
/// error, invalid frame, `auth_failed`) calls
/// `cleanup_and_trigger_respawn` and returns `Err`.
///
/// G4-L-02: the Python sidecar's `_handle_connection` flow is:
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
            cleanup_and_trigger_respawn(app, state);
            Err(format!(
                "WS auth timed out after {}s",
                WS_AUTH_OK_TIMEOUT_SECS
            ))
        }
        Ok(None) => {
            log::error!("[WS-AUTH] stream closed before auth_ok/ready");
            cleanup_and_trigger_respawn(app, state);
            Err("WS stream closed during auth".to_string())
        }
        Ok(Some(Err(e))) => {
            log::error!("[WS-AUTH] error reading auth_ok/ready: {}", e);
            cleanup_and_trigger_respawn(app, state);
            Err(format!("WS auth read error: {}", e))
        }
        Ok(Some(Ok(msg))) => {
            let text = match msg {
                // XZ-CC-10: tungstenite 0.27 changed `Message::Text`'s
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
                            cleanup_and_trigger_respawn(app, state);
                            return Err(
                                "WS auth received non-UTF8 binary".to_string()
                            );
                        }
                    }
                }
                Message::Close(_) => {
                    log::warn!("[WS-AUTH] server closed during auth");
                    cleanup_and_trigger_respawn(app, state);
                    return Err("WS closed during auth".to_string());
                }
                _ => {
                    log::warn!(
                        "[WS-AUTH] unexpected frame type (ping/pong) during auth"
                    );
                    cleanup_and_trigger_respawn(app, state);
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
                    cleanup_and_trigger_respawn(app, state);
                    return Err(format!("WS auth invalid JSON: {}", text));
                }
            };
            let t = v.get("type").and_then(|x| x.as_str()).unwrap_or("");
            if t == "auth_failed" {
                log::error!("[WS-AUTH] auth_failed received from server");
                cleanup_and_trigger_respawn(app, state);
                return Err("WS auth rejected by server".to_string());
            }
            // Accept either `auth_ok` (future contract) or `ready`
            // (current Python sidecar contract — see sidecar_ws.py:503)
            // as the auth-success signal.
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
                let _ = app.emit("ready", payload.clone());
                let _ = app.emit(
                    "python-event",
                    json!({"type": "ready", "data": payload}),
                );
            } else {
                log::warn!(
                    "[WS-AUTH] expected auth_ok or ready, got: {} — \
                     proceeding anyway (best-effort, frame consumed)",
                    t
                );
                // The frame is consumed and lost. This is acceptable
                // because:
                // 1. The current sidecar sends `ready` first, which
                //    matches the accept condition above.
                // 2. Any other frame type at auth time is a protocol
                //    violation worth logging but not blocking.
            }
            Ok(read)
        }
    }
}

/// XZ-11 (was inline in `reconnect_ws`): spawn the WS reader task.
///
/// Parses incoming frames, fulfills pending dispatch requests by id,
/// emits Tauri events for server-initiated events. The body is wrapped
/// in `AssertUnwindSafe(...).catch_unwind()` (G4-H-26) so a panic
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
fn spawn_reader_task(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    mut read: SplitStream<WsStream>,
) {
    let app_for_reader = app.clone();
    let state_for_reader = state.clone();
    // G4-H-26: clone handles for the cleanup block, which runs OUTSIDE
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
                            log::warn!(
                                "[WS-READER] frame has non-numeric id field, ignoring: {}",
                                text
                            );
                            continue;
                        }
                        // Otherwise it's a server-initiated event
                        // (channel 2). Emit it as a Tauri event.
                        let event_type = v.get("type").and_then(|t| t.as_str()).unwrap_or("unknown");
                        let payload = v.get("data").cloned().unwrap_or(json!({}));

                        // G4-H-32: drop unknown event types BEFORE
                        // emitting (defense-in-depth against a
                        // compromised sidecar process injecting
                        // arbitrary event names that the renderer's
                        // `usePythonEvent(type, ...)` listeners might
                        // be tricked into handling). The allowlist
                        // `ALLOWED_EVENT_TYPES` is defined at the top
                        // of this file and covers all event types the
                        // Python sidecar is known to publish today.
                        if !ALLOWED_EVENT_TYPES.contains(&event_type) {
                            log::warn!(
                                "[WS-READER] dropping unknown event type: {}",
                                event_type
                            );
                            continue;
                        }

                        // ADR-0020 §9: coalesce bubble_level from ~60 Hz
                        // to ≤30 Hz.
                        if event_type == "bubble_level" {
                            last_bubble_payload = Some(payload);
                            let now = Instant::now();
                            if bubble_coalesce_should_emit(last_bubble_level, now, BUBBLE_LEVEL_COALESCE_HZ) {
                                last_bubble_level = Some(now);
                                // PVT-G5-049: previously
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
                                    // ADR-0020 §6.3: emit BOTH the specific event
                                    // (for direct listeners like the bubble window)
                                    // AND the generic `python-event` (for the
                                    // usePython hook's onEvent catch-all, matching
                                    // the Electron path's ipcRenderer.on("python-event")).
                                    let _ = app_for_reader.emit("bubble_level", p.clone());
                                    let _ = app_for_reader.emit("python-event", json!({"type": "bubble_level", "data": p}));
                                }
                            }
                            continue;
                        }

                        // PVT-G5-062: extracted the event-name translation
                        // into `translate_event_name` so it can be unit-
                        // tested without a Tauri runtime, and so additional
                        // bubble-related event renames can be added in one
                        // place (the Python sidecar still publishes some
                        // events under snake_case names that the renderer
                        // expects as `bubble:*` kebab-case).
                        let emit_name = translate_event_name(event_type);
                        // ADR-0020 §6.3: emit BOTH the specific event (for
                        // direct listeners) AND the generic `python-event`
                        // (for the usePython hook's onEvent catch-all).
                        let _ = app_for_reader.emit(emit_name, payload.clone());
                        let _ = app_for_reader.emit("python-event", json!({"type": emit_name, "data": payload}));

                        // GT-E3-6: the legacy `electron_notification` →
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
        // shutting down). This cleanup runs UNCONDITIONALLY — even if
        // the body panicked — because it uses the cloned
        // `state_for_cleanup` / `app_for_cleanup` handles (not the
        // originals, which were moved into the `AssertUnwindSafe`
        // body and may have been partially consumed before the panic).
        {
            // Clear ws_tx first so new dispatch calls return
            // "sidecar not connected" immediately.
            // G4-H-27: poison-safe lock helper.
            let mut ws_tx_guard = mutex_lock(&state_for_cleanup.ws_tx);
            *ws_tx_guard = None;
        }
        {
            // Drain pending requests — reject each with an error so
            // callers don't wait the full 120s timeout.
            let mut pending = state_for_cleanup.pending.lock().await;
            let count = pending.len();
            for (_id, tx) in pending.drain() {
                let _ = tx.send(json!({
                    "type": "error",
                    "data": {
                        "code": "sidecar_disconnected",
                        "message": "sidecar WS disconnected (supervisor respawn in progress)"
                    }
                }));
            }
            if count > 0 {
                log::warn!("[WS-READER] drained {} pending dispatch requests", count);
            }
        }
        if !state_for_cleanup.shutting_down.load(Ordering::SeqCst) {
            // CR-5 (ADR-0020 §10): emit `supervisor_relaunching` IMMEDIATELY
            // at disconnect start so the UI can show a "reconnecting…"
            // banner before the backoff schedule runs. The eventual
            // `supervisor_reconnected` (on success) or second `supervisor_relaunching`
            // (on exhaustion) supersedes this event.
            let _ = app_for_cleanup.emit(
                "supervisor_relaunching",
                json!({"reason": "disconnected"}),
            );
            log::warn!("[WS-READER] unexpected close — triggering supervisor");
            // EC-FIX-5 (EC-18): spawn supervisor respawn on a separate thread via the
            // shared `trigger_respawn_off_thread` helper. The thread
            // + `block_on` bridge is required because `respawn`
            // awaits `reconnect_ws`, whose future is `!Send`
            // (tokio-tungstenite holds a `!Send` across an await), and
            // `tokio::spawn` requires `Send` futures. NF-R19-1
            // documents the failed attempt to use a direct
            // `tokio::spawn` here. See the helper's doc comment for
            // the full rationale.
            trigger_respawn_off_thread(
                app_for_cleanup.clone(),
                state_for_cleanup.clone(),
            );
        }
    });
}

/// XZ-11 (was inline in `reconnect_ws`): spawn the Tauri-side heartbeat
/// task (PVT-1).
///
/// Detects application-level sidecar hangs (GIL contention, infinite
/// loop, blocking C call) that keep the WS socket open but don't
/// respond to dispatches. Without this, the supervisor only
/// triggers on WS-close/process exit, so a hung sidecar leaves the
/// UI frozen for the full 120s dispatch timeout on EVERY
/// `invoke('dispatch', ...)` call.
///
/// Every 10s we send a `heartbeat` dispatch (the Python sidecar's
/// `_handle_heartbeat` is already registered in `_COMMAND_REGISTRY`
/// — see `voice_typer/server/ipc_server.py:2013`). We wrap the call
/// in a 15s timeout — `dispatch_frame`'s own 120s timeout is too
/// long for a liveness probe. On 3 consecutive misses (≥30s of
/// unresponsiveness) we trigger supervisor respawn via the same
/// `std::thread::spawn` + `block_on` bridge used by the WS reader
/// above (the supervisor's `reconnect_ws` future is `!Send` —
/// tokio-tungstenite holds a `!Send` across an await — so it can't
/// be awaited from a `tokio::spawn` directly).
///
/// The 15s outer timeout may leak a pending entry inside
/// `dispatch_frame` until its internal 120s timeout fires, but the
/// supervisor respawn triggered at miss #3 kills the sidecar, which drops
/// the TCP socket, which makes the WS reader's drain loop clear all
/// pending entries. So the leak is bounded and self-healing.
/// This function is `async fn` (was `fn` calling
/// `blocking_lock()`). The caller `reconnect_ws` is already `async`,
/// so the change is local — we can hold the `AsyncMutex` guard across
/// the (very short) synchronous section without blocking a Tokio
/// worker thread. The previous `blocking_lock()` form would panic if
/// called from within an async runtime worker thread in certain
/// configurations (Tokio's `blocking_lock` panics if the current
/// thread is a runtime worker that has run out of blocking-thread
/// budget — see tokio-rs/tokio#3716). The `async fn` + `lock().await`
/// form is the canonical Tokio pattern and avoids the panic risk.
async fn spawn_heartbeat_task(heartbeat_app: tauri::AppHandle, heartbeat_state: Arc<SidecarState>) {
    // GT-8 / GT-C4-3: abort any previous heartbeat task before spawning
    // the new one. `reconnect_ws` is called on every successful
    // supervisor respawn (and on initial cold start), so without this abort the
    // PRIOR heartbeat task would leak — it loops forever on a 10s
    // `interval.tick()`. After N reconnects you'd have N concurrent
    // heartbeat tasks all dispatching `heartbeat` frames at 10s
    // intervals, multiplying sidecar load N×.
    //
    // GT-C4-1: the heartbeat's pending dispatch id is allocated INSIDE
    // `dispatch_inner` (in `dispatch_frame` — `sidecar_cmds.rs`, owned
    // by GT-FIX-20). The heartbeat task here does NOT know the id, so
    // it can't manually remove the pending entry from `state.pending`
    // on the 15s timeout. Mitigation (existing behavior, preserved):
    //   - On miss #3, supervisor respawn kills the sidecar → WS socket
    //     drops → WS reader's drain loop clears ALL pending entries.
    //   - On miss #1/#2, `dispatch_frame`'s internal 120s timeout
    //     eventually removes the entry. Bounded leak.
    // GT-FIX-20 will add a Drop guard on the dispatch path (GT-49) so
    // the pending entry is removed immediately when the dispatch
    // future is dropped (which happens when the 15s outer timeout
    // cancels `dispatch_inner`).
    let prev_handle_opt = {
        let mut hb_guard = heartbeat_state.heartbeat_handle.lock().await;
        hb_guard.take()
    };
    if let Some(prev) = prev_handle_opt {
        prev.abort();
        log::info!("[HEARTBEAT] aborted previous heartbeat task before spawning new one (GT-8)");
    }
    // Clone the Arc BEFORE moving it into the async closure. The
    // closure below (async move { ... }) takes ownership of
    // `heartbeat_state_for_task`; the original `heartbeat_state` is
    // still referenced after the spawn to store the new JoinHandle
    // (line `*hb_guard = Some(handle)` below).
    let heartbeat_state_for_task = heartbeat_state.clone();
    let handle: tauri::async_runtime::JoinHandle<()> =
        tauri::async_runtime::spawn(async move {
            let mut missed: u32 = 0;
            let mut interval = tokio::time::interval(Duration::from_secs(HEARTBEAT_INTERVAL_SECS));
            loop {
                if heartbeat_state_for_task.shutting_down.load(Ordering::SeqCst) {
                    break;
                }
                interval.tick().await;
                if heartbeat_state_for_task.shutting_down.load(Ordering::SeqCst) {
                    break;
                }
                let heartbeat_args = DispatchArgs {
                    cmd: "heartbeat".to_string(),
                    data: None,
                };
                match tokio::time::timeout(
                    Duration::from_secs(HEARTBEAT_RESPONSE_TIMEOUT_SECS),
                    dispatch_inner(heartbeat_args, heartbeat_state_for_task.clone()),
                )
                .await
                {
                    Ok(Ok(_)) => {
                        missed = 0;
                    }
                    Ok(Err(e)) => {
                        missed += 1;
                        log::warn!(
                            "[HEARTBEAT] dispatch error (miss #{}/{}): {}",
                            missed,
                            HEARTBEAT_MAX_MISSES,
                            e
                        );
                        if missed >= HEARTBEAT_MAX_MISSES {
                            log::warn!(
                                "[HEARTBEAT] {} consecutive misses — triggering supervisor respawn",
                                HEARTBEAT_MAX_MISSES
                            );
                            trigger_respawn_off_thread(
                                heartbeat_app.clone(),
                                heartbeat_state_for_task.clone(),
                            );
                            break;
                        }
                    }
                    Err(_) => {
                        missed += 1;
                        log::warn!(
                            "[HEARTBEAT] {}s response timeout (miss #{}/{})",
                            HEARTBEAT_RESPONSE_TIMEOUT_SECS,
                            missed,
                            HEARTBEAT_MAX_MISSES
                        );
                        if missed >= HEARTBEAT_MAX_MISSES {
                            log::warn!(
                                "[HEARTBEAT] {} consecutive misses — triggering supervisor respawn",
                                HEARTBEAT_MAX_MISSES
                            );
                            trigger_respawn_off_thread(
                                heartbeat_app.clone(),
                                heartbeat_state_for_task.clone(),
                            );
                            break;
                        }
                    }
                }
            }
        });
    // GT-8 / GT-C4-3: store the new handle so the next reconnect (or
    // `shutdown_sidecar_for_exit`) can abort it.
    {
        let mut hb_guard = heartbeat_state.heartbeat_handle.lock().await;
        *hb_guard = Some(handle);
    }
}

// XZ-11: thin orchestrator extracted from the original 585-line
// `reconnect_ws` god function (Finding EC-18). The five phases —
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
    // PVT-G5-088: the parameter was previously named `_app` (underscore
    // prefix implies unused), but it IS used below at `app.clone()` for
    // the reader/writer tasks. Renamed to `app` to reflect actual use
    // and silence the misleading-underscore lint.
    let (write, read) = ws_connect(port).await?;
    let ws_rx = queue_auth_and_store_ws_tx(state, token)?;
    spawn_writer_task(write, ws_rx);
    let state_clone = state.clone();
    let app_handle = app.clone();
    let read = wait_for_auth_ok(&app_handle, &state_clone, read).await?;
    spawn_reader_task(app_handle.clone(), state_clone.clone(), read);
    // `spawn_heartbeat_task` is now `async fn` — `.await` it
    // instead of fire-and-forget. The function only holds the
    // `AsyncMutex` guard for the brief synchronous take/store sections
    // (no `.await` inside the critical section), so this doesn't add
    // meaningful latency to `reconnect_ws`.
    spawn_heartbeat_task(app_handle, state_clone).await;
    Ok(())
}

/// PVT-G5-062: translate Python-sidecar event names to the renderer's
/// canonical event names. The Python sidecar publishes some events
/// under snake_case names inherited from the Electron era (e.g.
/// `bubble_set_state`) that the renderer expects as kebab-case
/// `bubble:*` (matching the `bubble:show`, `bubble:hide` events
/// already documented in ADR-0020 §6.3). Unknown event names pass
/// through unchanged so this function is forward-compatible with new
/// sidecar events without requiring a host-side code change.
///
/// Extracted from the WS reader's inline `match event_type { ... }`
/// so the translation table is unit-testable without a Tauri runtime
/// and so future renames are localized to one place.
pub(crate) fn translate_event_name(event_type: &str) -> &str {
    match event_type {
        // PVT-2 cleanup (session 1): the `relaunch_electron` →
        // `relaunch_app` rename arm was REMOVED here — the Python
        // sidecar now publishes the event under the canonical
        // `relaunch_app` name directly (see `app.py::restart_app`),
        // so it passes through unchanged. `main.rs::setup` registers
        // `app.listen("relaunch_app", ...)` which calls
        // `app.restart()`. The renderer-side parity tests in
        // `tests/tauri/mig19/test_wire_swap_recovery.py`
        // (`test_ws_reader_does_not_rename_relaunch_app`) lock this
        // in: re-adding the arm will fail that test.
        //
        // PVT-G5-062: bubble lifecycle events. The Python sidecar
        // still publishes these under the snake_case names that the
        // Electron bridge used; the Tauri renderer's `bubble.ts`
        // preload + `bubble-runtime.json` capability file use the
        // kebab-case `bubble:*` names. Without this translation the
        // events would be emitted under names the renderer never
        // listens for, silently dropping the bubble state changes.
        "bubble_set_state" => "bubble:set-state",
        "bubble_show" => "bubble:show",
        "bubble_hide" => "bubble:hide",
        "bubble_config" => "bubble:config",
        // Forward-compatible: unknown events pass through unchanged
        // so new sidecar events don't require a host-side release.
        other => other,
    }
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
        // GT-E3-5: PendingMap no longer wrapped in outer Arc —
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
        // GT-E3-5: PendingMap no longer wrapped in outer Arc.
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

    // ── PVT-G5-062: translate_event_name ────────────────────────────

    #[test]
    fn test_translate_event_name_relaunch_app_passes_through() {
        // PVT-2 cleanup (session 1): the `relaunch_electron` →
        // `relaunch_app` rename arm was REMOVED. The Python sidecar
        // publishes `relaunch_app` directly (see `app.py::restart_app`),
        // and `main.rs::setup` listens for `relaunch_app` via
        // `app.listen(...)`. Both `relaunch_app` and the legacy
        // `relaunch_electron` (kept in the ALLOWED_EVENT_TYPES block-list
        // for one release cycle so old Python sidecars don't get
        // silently dropped) must pass through `translate_event_name`
        // UNCHANGED — re-adding the rename arm would break the
        // `test_ws_reader_does_not_rename_relaunch_app` parity test in
        // `tests/tauri/mig19/test_wire_swap_recovery.py`.
        assert_eq!(translate_event_name("relaunch_app"), "relaunch_app");
        assert_eq!(translate_event_name("relaunch_electron"), "relaunch_electron");
    }

    #[test]
    fn test_translate_event_name_bubble_lifecycle_kebab() {
        // PVT-G5-062: snake_case bubble events from the Python sidecar
        // must be translated to the kebab-case `bubble:*` names the
        // renderer's preload + capability file expect.
        assert_eq!(translate_event_name("bubble_set_state"), "bubble:set-state");
        assert_eq!(translate_event_name("bubble_show"), "bubble:show");
        assert_eq!(translate_event_name("bubble_hide"), "bubble:hide");
        assert_eq!(translate_event_name("bubble_config"), "bubble:config");
    }

    #[test]
    fn test_translate_event_name_unknown_passes_through() {
        // Forward-compat: unknown event names must pass through unchanged
        // so new sidecar events don't require a host-side release.
        assert_eq!(translate_event_name("bubble_level"), "bubble_level");
        assert_eq!(translate_event_name("notification"), "notification");
        assert_eq!(translate_event_name("electron_notification"), "electron_notification");
        assert_eq!(translate_event_name("some_brand_new_event"), "some_brand_new_event");
        assert_eq!(translate_event_name(""), "");
    }

    #[test]
    fn test_translate_event_name_bubble_level_not_renamed() {
        // `bubble_level` is the high-frequency coalesced event — it must
        // NOT be translated (it's matched literally in the reader task's
        // coalesce branch above). A regression that mapped `bubble_level`
        // to `bubble:level` would break the coalesce path silently.
        assert_eq!(translate_event_name("bubble_level"), "bubble_level");
    }

    // ── GT-E3-6: legacy event aliases removed from ALLOWED_EVENT_TYPES ─

    #[test]
    fn test_gt_e3_6_legacy_aliases_not_in_allowlist() {
        // GT-E3-6: `relaunch_electron` and `electron_notification` were
        // removed from `ALLOWED_EVENT_TYPES`. Old Python sidecars that
        // still emit these legacy names will have their frames DROPPED
        // by the WS reader's allowlist check.
        assert!(
            !ALLOWED_EVENT_TYPES.contains(&"relaunch_electron"),
            "GT-E3-6: legacy `relaunch_electron` must NOT be in the allowlist"
        );
        assert!(
            !ALLOWED_EVENT_TYPES.contains(&"electron_notification"),
            "GT-E3-6: legacy `electron_notification` must NOT be in the allowlist"
        );
        // Canonical names must still be present.
        assert!(
            ALLOWED_EVENT_TYPES.contains(&"relaunch_app"),
            "canonical `relaunch_app` must remain in the allowlist"
        );
        assert!(
            ALLOWED_EVENT_TYPES.contains(&"notification"),
            "canonical `notification` must remain in the allowlist"
        );
    }

    // ── GT-8: heartbeat task abort on reconnect ────────────────────

    #[tokio::test]
    async fn test_gt8_heartbeat_handle_slot_round_trips_take_abort_replace() {
        let state = Arc::new(crate::state::SidecarState::new());
        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "GT-8: fresh state must have heartbeat_handle = None"
        );

        let h1 = tauri::async_runtime::spawn(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
        });
        *state.heartbeat_handle.lock().await = Some(h1);
        assert!(state.heartbeat_handle.lock().await.is_some());

        // Simulate a second reconnect: take + abort + replace.
        let prev = state.heartbeat_handle.lock().await.take();
        assert!(prev.is_some());
        if let Some(h) = prev {
            h.abort();
        }
        assert!(state.heartbeat_handle.lock().await.is_none());

        let h2 = tauri::async_runtime::spawn(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
        });
        *state.heartbeat_handle.lock().await = Some(h2);
        assert!(state.heartbeat_handle.lock().await.is_some());

        // Cleanup.
        let mut guard = state.heartbeat_handle.lock().await;
        if let Some(h) = guard.take() {
            h.abort();
        }
    }

    /// GT-8: `shutdown_sidecar_for_exit` must abort any in-flight
    /// heartbeat task stored on `state.heartbeat_handle`.
    #[tokio::test]
    async fn test_gt8_shutdown_sidecar_for_exit_aborts_heartbeat_handle() {
        use crate::state::shutdown_sidecar_for_exit;
        let state = Arc::new(crate::state::SidecarState::new());
        let h = tauri::async_runtime::spawn(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
        });
        *state.heartbeat_handle.lock().await = Some(h);
        assert!(state.heartbeat_handle.lock().await.is_some());

        let state_clone = state.clone();
        tokio::time::timeout(
            Duration::from_millis(3000),
            shutdown_sidecar_for_exit(&state_clone),
        )
        .await
        .expect("shutdown_sidecar_for_exit should complete within 3s");

        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "GT-8: shutdown_sidecar_for_exit must abort + clear the heartbeat handle"
        );
    }
}
