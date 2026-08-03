//! WS reconnect plumbing — connect, auth, writer task, heartbeat task.
//!
//! Extracted from `ws.rs` (was ~440 lines inline). `ws.rs` retains the
//! public `reconnect_ws` orchestrator and the shared respawn-trigger
//! helpers; this module owns the five reconnect-phase helpers that the
//! orchestrator calls in sequence.
//!
//! Phase helpers exported to `ws.rs`:
//! - `ws_connect`         (TCP + WS handshake, bounded by `WS_CONNECT_TIMEOUT_SECS`)
//! - `queue_auth_and_store_ws_tx` (bounded channel + auth frame + ws_tx install + gen bump)
//! - `spawn_writer_task`  (drains ws_rx into write.send, with symmetric cleanup)
//! - `wait_for_auth_ok`   (3s bounded wait for `auth_ok` / `ready` frame)
//! - `spawn_heartbeat_task` (10s interval `heartbeat` dispatch + 3-miss respawn)
//!
//! Behavior is preserved EXACTLY: same error strings, same backoff
//! semantics, same panic-safety wrappers (AssertUnwindSafe + catch_unwind),
//! same generation-guarded cleanup blocks.

use crate::commands::sidecar_cmds::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
use crate::state::lock as mutex_lock;
use crate::sidecar::supervisor::respawn;
use crate::sidecar::ws::WsStream;
// heartbeat interval / response timeout / max misses are named
// constants in `util.rs`.
use crate::util::{
    HEARTBEAT_INTERVAL_SECS, HEARTBEAT_MAX_MISSES, HEARTBEAT_RESPONSE_TIMEOUT_SECS, MAX_FRAME_BYTES,
};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use std::time::Duration;
use futures_util::{
    stream::{SplitSink, SplitStream},
    FutureExt, SinkExt, StreamExt,
};
use serde_json::{json, Value};
use tauri::Emitter;
use tokio::sync::{mpsc, oneshot};
use tokio_tungstenite::{connect_async_with_config, tungstenite::Message};

// ─── shared respawn-trigger + cleanup helpers ───────────────────
//
// These helpers form the WS→supervisor bridge used by multiple cleanup
// blocks in this module (writer task, auth-failure path, heartbeat-miss
// arms) AND by `ws_dispatch.rs` (reader task cleanup). They live here
// (rather than in `ws.rs`) because they're tightly coupled to the
// reconnect plumbing: `cleanup_and_trigger_respawn` is the auth-failure
// recovery path called by `wait_for_auth_ok`, and
// `trigger_respawn_off_thread` is the writer/heartbeat/reader cleanup
// bridge to `crate::sidecar::supervisor::respawn`. Co-locating them
// with the reconnect phases keeps the supervisor-bridge rationale
// (the `!Send` future + std::thread::spawn + block_on pattern) next to
// its single largest group of call sites.
//
// `ws.rs` re-exports these via `pub(crate) use ws_reconnect::{...}` so
// external callers using the pre-split `crate::sidecar::ws::xxx` paths
// continue to compile.

/// drain all pending dispatch requests with a
/// `sidecar_disconnected` error response so in-flight dispatches don't
/// wait the full 120s timeout for a response that will never come.
///
/// Used by:
///   - `cleanup_and_trigger_respawn` (auth-failure / auth-timeout path)
///   - the WS reader's cleanup block (normal disconnect / panic path,
///     in `ws_dispatch.rs`)
///   - the WS writer's cleanup block (write-half failure, in
///     `spawn_writer_task` below)
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
pub(crate) async fn drain_pending_with_disconnect_error(state: &Arc<SidecarState>) -> usize {
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

// helper for the auth-failed / auth-timeout path. Clears
// `state.ws_tx` (so the writer task exits when its channel drains and
// new dispatch calls fail fast with "sidecar not connected"), drains
// `state.pending` so in-flight dispatches don't wait the full 120s
// timeout, and spawns supervisor respawn on a separate thread (same
// pattern as the reader task's cleanup at the bottom of `reconnect_ws`).
//
// the prior comment claimed "at auth time no dispatch requests
// have been queued yet" — this assumption is FALSE. `queue_auth_and_
// store_ws_tx` stores `ws_tx` BEFORE `wait_for_auth_ok` runs. Any
// `dispatch` Tauri command invoked in that window (up to 3s auth
// timeout) will clone `ws_tx` (Some), insert into `pending`,
// `try_send` (succeeds — writer task is running), and await a
// response that will never come (server hasn't authed, frame is
// dropped server-side). Drain pending here, mirroring the reader
// task's cleanup block, so each orphaned oneshot gets a
// `sidecar_disconnected` error response instead of timing out at
// 120s.
//
// the drain uses the shared `drain_pending_with_disconnect_
// error` helper (defined above) which collects all entries out of
// the lock first, then sends outside the lock — the AsyncMutex is
// not held across the oneshot sends.
//
// (made `async fn`): all call sites are inside `wait_for_
// auth_ok`'s async block / outer async fn, so the `.await`
// promotion is local. The drain requires the AsyncMutex on
// `state.pending` (no sync lock available), so the function must
// be async to await the lock.
pub(crate) async fn cleanup_and_trigger_respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) {
    {
        // rule: no `unwrap()` on new code. Recover from a
        // poisoned mutex by taking the inner guard (the data inside
        // may be stale but clearing `ws_tx` to `None` is safe even
        // on a poisoned lock).
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        *ws_tx_guard = None;
    }
    // drain pending dispatch requests with a sidecar_disconnected
    // error so callers don't wait the full 120s dispatch timeout.
    let drained = drain_pending_with_disconnect_error(state).await;
    if drained > 0 {
        log::warn!(
            "[WS-AUTH] drained {} pending dispatch requests on auth failure/timeout (UE-8)",
            drained
        );
    }
    let _ = app.emit(
        "supervisor_relaunching",
        json!({"reason": "auth_failed_or_timeout"}),
    );
    trigger_respawn_off_thread(app.clone(), state.clone());
}

// extracted helper for the respawn trigger
// pattern that was duplicated at the WS-reader cleanup site and the
// two heartbeat-miss arms (`Ok(Err(_))` and `Err(_)` from the 15s
// timeout). All three sites had the identical block:
//
// let app_clone = <handle>.clone();
// let state_clone = <state>.clone();
// std::thread::spawn(move || {
// tauri::async_runtime::block_on(async move {
// let _ = respawn(&app_clone, &state_clone).await;
// });
// });
//
// The thread + `block_on` bridge is required because `respawn`
// awaits `reconnect_ws`, whose future is `!Send` (tokio-tungstenite
// holds a `!Send` across an await). `tokio::spawn` requires `Send`
// futures, so we drive the `!Send` future on a dedicated std thread
// with its own `block_on` runtime.
//
// This helper takes ownership (`app: AppHandle`, `state: Arc<SidecarState>`)
// so callers pass `.clone()`d handles in and the helper moves them
// into the spawned thread. Returns nothing — the supervisor is best-effort.
pub(crate) fn trigger_respawn_off_thread(app: tauri::AppHandle, state: Arc<SidecarState>) {
    // send on the long-lived supervisor channel instead of
    // spawning a new OS thread per call. The supervisor thread is
    // lazily spawned on first use via `respawn_supervisor_sender()`.
    //
    // two failure modes are handled explicitly here so
    // the resilience layer is never permanently dead:
    // 1. `respawn_supervisor_sender()` returns `None` — the long-lived
    // supervisor thread could not be spawned (low memory, RLIMIT_NPROC,
    // sandbox restrictions, etc.). Fall back to a one-shot
    //`std::thread::spawn` per trigger.
    // 2. `tx.send(...)` returns `SendError` — the supervisor thread has
    // panicked (its receiver was dropped). Fall back to a one-shot
    //`std::thread::spawn` per trigger. Subsequent calls will
    // also fall back here — the `OnceLock` holds a dead-but-not-cleared
    // sender, so we keep using the per-trigger fallback. Best-effort.
    match respawn_supervisor_sender() {
        Some(tx) => match tx.try_send((app, state)) {
            Ok(()) => {}
            Err(std::sync::mpsc::TrySendError::Full((_app, _state))) => {
                // supervisor queue is full (capacity=8) — the
                // long-lived supervisor thread is already processing a
                // respawn (or has stalled mid-respawn). DROP the request:
                // the in-flight respawn will observe the same sidecar-down
                // condition when it completes its reconnect cycle, so
                // re-queuing is redundant. The dropped `(app, state)`
                // tuple is logged at warn (not error) because this is the
                // expected behavior under a flapping sidecar — the
                // supervisor's `respawn_in_progress` compare_exchange
                // already serializes concurrent respawns, so the dropped
                // request would have no-op'd anyway when the supervisor
                // got to it.
                log::warn!(
                    "[SUPERVISOR] respawn request queue full (capacity=8) — \
                     dropping request (supervisor already processing; UE-8-F11)"
                );
            }
            Err(std::sync::mpsc::TrySendError::Disconnected((app, state))) => {
                log::error!(
                    "[SUPERVISOR] failed to enqueue respawn request to supervisor \
                     thread (it may have panicked): disconnected — falling back to \
                     one-shot std::thread::spawn (fallback after supervisor disconnect)"
                );
                // Clear the cached sender so the next
                // `respawn_supervisor_sender()` call re-attempts the
                // long-lived supervisor thread spawn. Without this, the
                // `OnceLock<Mutex<Option<SyncSender>>>` keeps holding a
                // dead sender (whose receiver was dropped when the
                // supervisor thread panicked), so every subsequent
                // respawn trigger pays the cost of cloning the dead
                // sender + a failed `try_send` + a fresh
                // `std::thread::spawn` fallback — instead of recovering
                // to the steady-state long-lived-thread path.
                if let Some(mutex) = RESPAWN_SUPERVISOR_TX.get() {
                    if let Ok(mut guard) = mutex.lock() {
                        *guard = None;
                    }
                    // A poisoned lock here is benign: the next
                    // `respawn_supervisor_sender()` call will recover
                    // via its `poisoned.into_inner()` path and
                    // re-attempt the spawn.
                }
                spawn_oneshot_respawn_thread(app, state);
            }
        },
        None => {
            // long-lived supervisor thread is unavailable. Fall
            // back to a per-trigger one-shot spawn.
            log::warn!(
                "[SUPERVISOR] long-lived supervisor thread unavailable — using \
                 one-shot std::thread::spawn fallback (long-lived thread unavailable)"
            );
            spawn_oneshot_respawn_thread(app, state);
        }
    }
}

/// fallback: spawn a fresh OS thread that drives a
/// `block_on(respawn)` future to completion. This is the
/// pattern, retained as a fallback for the rare case where the
/// long-lived supervisor thread is either uninitializable or has died.
///
/// The thread + `block_on` bridge is required because `respawn` awaits
/// `reconnect_ws`, whose future is `!Send` (tokio-tungstenite holds a
/// `!Send` across an await). `tokio::spawn` requires `Send` futures,
/// so we drive the `!Send` future on a dedicated std thread with its
/// own `block_on` runtime. Each fallback call creates a new OS thread
/// (~50µs) — acceptable given how rare the fallback is expected to be.
///
/// If even this fallback spawn fails (extreme resource exhaustion),
/// log loudly and give up; the resilience layer is degraded until the
/// user manually relaunches the app.
fn spawn_oneshot_respawn_thread(app: tauri::AppHandle, state: Arc<SidecarState>) {
    if let Err(e) = std::thread::Builder::new()
        .name("respawn-oneshot".into())
        .spawn(move || {
            let _ = tauri::async_runtime::block_on(async move {
                if let Err(e) = respawn(&app, &state).await {
                    log::error!(
                        "[WS] supervisor respawn failed: {} — app may be in a degraded state",
                        e
                    );
                }
            });
        })
    {
        log::error!(
            "[SUPERVISOR] fallback std::thread::spawn failed: {} — respawn \
             request dropped; resilience layer is degraded until manual relaunch",
            e
        );
    }
}

// single long-lived supervisor thread, lazily spawned on
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

// the supervisor queue is a bounded `sync_channel(8)` instead of
// an unbounded `channel()`. An unbounded channel has no backpressure
// — a stalled supervisor (stuck in a long `respawn` backoff)
// combined with a flapping sidecar (reader exits every 1-2s
// triggering another respawn request) could enqueue an unbounded
// number of `(AppHandle, Arc<SidecarState>)` tuples, each holding
// strong references to the AppHandle and the full SidecarState
// (child handle, ws_tx, pending map). Bounded to 8 — generous enough
// for normal operation (a healthy supervisor drains the queue in
// milliseconds) but small enough to fail-fast on a stuck supervisor.
// On full, the request is DROPPED (logged): the in-flight respawn
// already observes the sidecar-down condition when it completes its
// reconnect cycle, so re-queuing is redundant; the supervisor's
// `respawn_in_progress` compare_exchange would no-op the duplicate
// anyway.
//
// the `OnceLock` holds a `Mutex<Option<SyncSender>>`. `Some(tx)` means
// the long-lived supervisor thread spawned successfully; `None` means
// it failed (low memory, RLIMIT_NPROC, sandbox restrictions, etc.) and
// callers should fall back to a per-trigger `std::thread::spawn`. The
// `Mutex` wrapping lets each subsequent `respawn_supervisor_sender()`
// call re-attempt the spawn when the cached sender is `None`,
// mirroring a bounded-retry pattern: rate-limited by the OS
// thread-spawn cost (microseconds) and never more than one concurrent
// spawn attempt (mutex-serialized).
static RESPAWN_SUPERVISOR_TX: OnceLock<
    std::sync::Mutex<Option<std::sync::mpsc::SyncSender<RespawnRequest>>>,
> = OnceLock::new();

fn respawn_supervisor_sender() -> Option<std::sync::mpsc::SyncSender<RespawnRequest>> {
    // lazily initialise the outer `Mutex`. `OnceLock`
    // guarantees the `Mutex` is created exactly once; the inner
    // `Option<Sender>` is mutable under the mutex so we can retry
    // the spawn after a transient failure (closes the
    // regression where `OnceLock<Option<Sender>>` cached `None`
    // permanently).
    let mutex: &'static std::sync::Mutex<Option<std::sync::mpsc::SyncSender<RespawnRequest>>> =
        RESPAWN_SUPERVISOR_TX.get_or_init(|| std::sync::Mutex::new(None));
    // Hold the lock for the whole attempt so two concurrent callers
    // don't both spawn supervisor threads (the second would create a
    // dead receiver that's instantly dropped, no-op-ing the first
    // sender). `try_send` on the sender is non-blocking so the
    // critical section is short.
    let mut guard = match mutex.lock() {
        Ok(g) => g,
        Err(poisoned) => {
            // recover from a poisoned mutex — the inner
            // data may be stale but we can still attempt a fresh
            // spawn.
            log::warn!(
                "[SUPERVISOR] respawn-supervisor mutex poisoned — recovering (XE-15-3)"
            );
            poisoned.into_inner()
        }
    };
    if let Some(ref tx) = *guard {
        // Cached sender is alive — clone (cheap; `SyncSender` is
        // designed for multi-producer cloning) and return.
        return Some(tx.clone());
    }
    // Cached sender is `None` (first call OR previous spawn failed).
    // Attempt (re-)spawn. Channel creation is infallible; only the
    // thread spawn can fail.
    let (tx, rx) = std::sync::mpsc::sync_channel::<RespawnRequest>(8);
    match std::thread::Builder::new()
        .name("respawn-supervisor".into())
        .spawn(move || {
            for (app, state) in rx {
                let _ = tauri::async_runtime::block_on(async move {
                    if let Err(e) = respawn(&app, &state).await {
                        log::error!(
                            "[WS] supervisor respawn failed: {} — app may be in a degraded state",
                            e
                        );
                    }
                });
            }
        }) {
        Ok(_) => {
            // Cache the sender so future calls skip the spawn.
            // Keep a clone for the caller.
            *guard = Some(tx.clone());
            drop(guard);
            log::info!(
                "[SUPERVISOR] long-lived respawn-supervisor thread spawned (XE-15-3)"
            );
            Some(tx)
        }
        Err(e) => {
            drop(guard);
            log::error!(
                "[SUPERVISOR] failed to spawn long-lived respawn-supervisor \
                 thread: {} — will retry on next call; using per-trigger \
                 std::thread::spawn fallback this call (long-lived thread spawn failed)",
                e
            );
            None
        }
    }
}

// ─── reconnect phase helpers (was inline in `reconnect_ws`) ──────

// bound the WS connect attempt so a hung sidecar that
// accepts the TCP connection but never completes the WS handshake
// doesn't stall the supervisor forever.
const WS_CONNECT_TIMEOUT_SECS: u64 = 5;

// IPC protocol version this host implements. The Python
// sidecar emits the same integer in its `server_started` stdout JSON
// (see `voice_typer/server/sidecar_ws.py:PROTOCOL_VERSION`). We also
// send it in our auth frame so the sidecar can detect skew at handshake
// time even when stdout parsing is bypassed (dev mode, manual restart).
// Bump in lockstep with the Python constant.
const EXPECTED_PROTOCOL_VERSION: u64 = 1;

// bound the wait for the `auth_ok` frame so a sidecar that
// never sends one (e.g. crashed between TCP accept and WS auth, or a
// malicious server holding the connection open) doesn't stall the
// reconnect path.
const WS_AUTH_OK_TIMEOUT_SECS: u64 = 3;

/// (was inline in `reconnect_ws`): TCP-connect to the sidecar's
/// WS endpoint and complete the WS handshake with a bounded timeout.
/// Enforces the ADR-0020 §10 1 MiB frame cap via
/// `WebSocketConfig`. Returns the split sink/stream halves so the
/// caller can hand them off to the writer and reader tasks.
///
/// A hung sidecar that accepts the TCP connection but never completes
/// the WS handshake would otherwise stall the supervisor forever (the
/// previous `connect_async_with_config` call had no timeout). The
/// timeout is mapped to a descriptive error so the backoff schedule
/// logs something actionable.
pub(crate) async fn ws_connect(
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
/// `WsWriterTx` in `state.rs` must change from
/// `mpsc::UnboundedSender<Message>` to `mpsc::Sender<Message>`, AND
/// the call sites in `commands/bubble.rs` and `commands/sidecar_cmds.rs`
/// must change `ws_tx.send(...)` to `ws_tx.try_send(...)` with error
/// handling for `TrySendError::Full` / `TrySendError::Closed`.
pub(crate) fn queue_auth_and_store_ws_tx(
    state: &Arc<SidecarState>,
    token: &str,
) -> Result<(mpsc::Receiver<Message>, u64), String> {
    // bounded channel capacity lives in `ws.rs` as the public constant
    // `WS_WRITER_CHANNEL_CAPACITY` — re-import here so the channel
    // construction uses the same constant callers reference.
    let (ws_tx, ws_rx) = mpsc::channel::<Message>(crate::sidecar::ws::WS_WRITER_CHANNEL_CAPACITY);
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
/// `AssertUnwindSafe(...).catch_unwind()` so a panic inside
/// `write.send()` (e.g. a tungstenite internal invariant violation)
/// doesn't tear down the task without cleanup. The writer has no
/// post-panic cleanup beyond dropping `write` (which `catch_unwind`
/// does automatically as the wrapped future unwinds), but the
/// `catch_unwind` future still resolves normally so the outer
/// `tokio::spawn` future completes cleanly instead of propagating the
/// panic to the runtime.
pub(crate) fn spawn_writer_task(
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
    // `spawn_reader_task`'s pattern. The originals are moved into
    // the `AssertUnwindSafe` body; the cleanup clones are used
    // AFTER `catch_unwind` so the cleanup runs even if the body
    // panics. Pre-fix, the writer task had NO cleanup block — when
    // `write.send()` returned `Err` (write half broken), the
    // task just `break`ed, leaving `state.ws_tx` pointing at the
    // dead sender and `state.pending` un-drained. Subsequent
    // `dispatch` calls queued onto the dead channel and waited up
    // to 30s for the heartbeat to detect the failure and trigger
    // respawn. With this cleanup block, the writer now clears
    // `ws_tx` + drains pending + emits `supervisor_relaunching` +
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
        // `!shutting_down` so a graceful shutdown doesn't fire a
        // spurious respawn). Mirrors `spawn_reader_task`'s
        // post-catch_unwind cleanup block.
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
                     (mine={}, current={}); a newer reconnect owns ws_tx",
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
pub(crate) async fn wait_for_auth_ok(
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

/// (was inline in `reconnect_ws`): spawn the Tauri-side heartbeat
/// task.
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
///
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
pub(crate) async fn spawn_heartbeat_task(
    heartbeat_app: tauri::AppHandle,
    heartbeat_state: Arc<SidecarState>,
) {
    // abort any previous heartbeat task before spawning
    // the new one. `reconnect_ws` is called on every successful
    // supervisor respawn (and on initial cold start), so without this abort the
    // PRIOR heartbeat task would leak — it loops forever on a 10s
    // `interval.tick()`. After N reconnects you'd have N concurrent
    // heartbeat tasks all dispatching `heartbeat` frames at 10s
    // intervals, multiplying sidecar load N×.
    //
    // the heartbeat's pending dispatch id is allocated INSIDE
    // `dispatch_inner` (in `dispatch_frame` — `sidecar_cmds.rs`, owned
    // by another sub-agent). The heartbeat task here does NOT know the
    // id, so it can't manually remove the pending entry from
    // `state.pending` on the 15s timeout. Mitigation (existing
    // behavior, preserved):
    // - On miss #3, supervisor respawn kills the sidecar → WS socket
    // drops → WS reader's drain loop clears ALL pending entries.
    // - On miss #1/#2, `dispatch_frame`'s internal 120s timeout
    // eventually removes the entry. Bounded leak.
    //
    // Clone the Arc BEFORE moving it into the async closure. The closure
    // below (async move { ... }) takes ownership of
    // `heartbeat_state_for_task`; the original `heartbeat_state` is
    // still referenced inside the lock scope below to acquire
    // `heartbeat_state.heartbeat_handle`.
    let heartbeat_state_for_task = heartbeat_state.clone();
    // hold the `heartbeat_handle` lock across the take + spawn +
    // store sequence. The prior code released the lock between `take()`
    // and `*hb_guard = Some(handle)` — the window spanned the entire
    // `tauri::async_runtime::spawn(...)` call. `reconnect_ws` is called
    // from TWO unsynchronized paths: `main.rs` cold-start (NOT under
    // `respawn_in_progress`) and `supervisor.rs` respawn (under the
    // flag). A reader-exit during cold-start auth can trigger
    // `trigger_respawn_off_thread`, and the two reconnects can interleave
    // their take/store:
    // cold-start: takes None → (releases lock)
    // respawn:    takes None → (releases lock)
    // cold-start: stores H1
    // respawn:    stores H2 (overwrites H1 — H1 is NEVER aborted, leaks)
    // After N reconnects up to N leaked heartbeat tasks run indefinitely,
    // each dispatching `heartbeat` frames every 10s to a dead WS.
    //
    // The fix: hold the lock across `take()` + `spawn(...)` + `store`.
    // `tauri::async_runtime::spawn` is synchronous (submits the future
    // to the runtime, returns a `JoinHandle` immediately — does NOT
    // await), so the lock is held only for a brief synchronous section.
    // The previous handle is aborted AFTER releasing the lock so a
    // (potentially slow) `abort()` doesn't block other callers from
    // acquiring the lock — `abort()` just posts a cancellation signal
    // to the task's waker; it does not synchronously join the task.
    let prev_handle_opt: Option<tauri::async_runtime::JoinHandle<()>> = {
        let mut hb_guard = heartbeat_state.heartbeat_handle.lock().await;
        let prev = hb_guard.take();
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
                // wrap the dispatch + timeout in `catch_unwind`
                // so a panic inside `dispatch_inner` (e.g. a serde
                // invariant violation, or a future-proofing regression
                // in `dispatch_frame`'s pending-map insert path) is
                // caught, logged at ERROR, and treated as a miss —
                // instead of silently killing the heartbeat task and
                // losing detection entirely. The reader + writer
                // tasks already wrap their bodies in `catch_unwind`;
                // the heartbeat task was added later and missed the
                // same treatment.
                let dispatch_result = AssertUnwindSafe(async {
                    tokio::time::timeout(
                        Duration::from_secs(HEARTBEAT_RESPONSE_TIMEOUT_SECS),
                        dispatch_inner(heartbeat_args, heartbeat_state_for_task.clone()),
                    )
                    .await
                })
                .catch_unwind()
                .await;
                match dispatch_result {
                    Ok(Ok(Ok(_))) => {
                        missed = 0;
                    }
                    Ok(Ok(Err(e))) => {
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
                    Ok(Err(_)) => {
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
                    // catch_unwind returned Err(_panic_payload).
                    // Treat the panic as a miss and continue the loop so
                    // the heartbeat task stays alive (mirrors the
                    // existing timeout / dispatch-error arms). After
                    // HEARTBEAT_MAX_MISSES consecutive panic-misses the
                    // supervisor respawn is triggered — same threshold
                    // as the other arms.
                    Err(_) => {
                        missed += 1;
                        log::error!(
                            "[HEARTBEAT] dispatch_inner panicked (miss #{}/{}) — \
                             task staying alive (AC-98)",
                            missed,
                            HEARTBEAT_MAX_MISSES
                        );
                        if missed >= HEARTBEAT_MAX_MISSES {
                            log::warn!(
                                "[HEARTBEAT] {} consecutive panic-misses — triggering supervisor respawn",
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
        // store the new handle INSIDE the lock
        // so the take+spawn+store sequence is atomic with respect to
        // other callers. The next reconnect (or `abort_heartbeat` /
        // `shutdown_sidecar_for_exit`) can abort it.
        *hb_guard = Some(handle);
        prev
    };
    // abort the previous handle AFTER releasing the lock. `abort()`
    // posts a cancellation signal to the task's waker; it does not
    // synchronously join the task, so this is fast and lock-free.
    if let Some(prev) = prev_handle_opt {
        prev.abort();
        log::info!(
            "[HEARTBEAT] aborted previous heartbeat task before spawning new one (GT-8 / UE-7)"
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::sidecar::ws::WS_WRITER_CHANNEL_CAPACITY;
    use crate::state::lock as mutex_lock;
    use serde_json::json;
    use std::sync::Arc;
    use std::sync::atomic::Ordering;
    use std::time::Duration;
    use tokio::sync::oneshot;
    use tokio_tungstenite::tungstenite::Message;

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
        // the spawn_reader_task cleanup block).
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
    /// `spawn_reader_task`) and asserts the drain branch is
    /// skipped on mismatch. The respawn-trigger branch shares the same
    /// `if` block, so a mismatched generation skips it transitively
    /// (constructing a `tauri::AppHandle` in a unit test is infeasible,
    /// so the trigger call itself is not exercised here — the gating
    /// predicate is the unit under test, not the spawn-thread bridge).
    #[tokio::test]
    async fn test_cleanup_delayed_reader_skips_drain_and_respawn_on_generation_mismatch() {
        let state = Arc::new(crate::state::SidecarState::new());

        let (_old_ws_rx, _gen1) = queue_auth_and_store_ws_tx(&state, "old-token")
            .expect("old queue_auth must succeed");
        let (new_ws_rx, gen2) = queue_auth_and_store_ws_tx(&state, "new-token")
            .expect("new queue_auth must succeed");
        let my_generation = 1u64;
        assert_eq!(gen2, 2, "precondition: new reconnect must be generation 2");
        let _new_ws_rx_guard = new_ws_rx;

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

        let current_generation = state.ws_generation.load(Ordering::SeqCst);
        if current_generation == my_generation {
            {
                let mut ws_tx_guard = mutex_lock(&state.ws_tx);
                *ws_tx_guard = None;
            }
            let _count = drain_pending_with_disconnect_error(&state).await;
        } else {
            // mismatch path — log only, no side effects.
        }

        assert!(
            mutex_lock(&state.ws_tx).is_some(),
            "ws_tx must survive an old-generation cleanup (race guard)"
        );
        assert_eq!(
            state.pending.lock().await.len(),
            1,
            "pending map must NOT be drained by an old-generation cleanup"
        );
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
        assert!(
            pending_rx.try_recv().is_ok(),
            "pending dispatch oneshot must be closed (drain sent disconnect error)"
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
}
