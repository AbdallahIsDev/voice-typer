//! Respawn-supervisor scheduling + auth-failure cleanup helpers
//! (ADR-0020 §1 + §10).
//!
//! Extracted from the original 2534-line `ws.rs` monolith
//! (review.md FZ-24 / ZR-86). Holds the long-lived supervisor
//! thread, the oneshot-fallback path, and the auth-failure
//! `cleanup_and_trigger_respawn` helper that drains pending
//! dispatches and triggers supervisor respawn on a separate OS
//! thread (the supervisor's `reconnect_ws` future is `!Send`, so
//! `tokio::spawn` can't drive it directly).
//!
//! Visibility contract:
//! - `trigger_respawn_off_thread` + `cleanup_and_trigger_respawn`
//!   are `pub(super)` — visible to the parent `ws` module (call
//!   sites in `spawn_writer_task`, `spawn_reader_task`,
//!   `wait_for_auth_ok`) AND to the sibling `heartbeat` submodule
//!   (call site in `spawn_heartbeat_task`).
//! - The supervisor-thread plumbing (`respawn_supervisor_sender`,
//!   `spawn_oneshot_respawn_thread`, `RespawnRequest`,
//!   `RESPAWN_SUPERVISOR_TX`) is private to this module.
//!
//! The drain helper `drain_pending_with_disconnect_error` stays in
//! `ws.rs` (it's also called from the WS reader/writer cleanup
//! blocks there). It's declared `pub(super)` so this module can
//! call it via `super::drain_pending_with_disconnect_error`.

use crate::sidecar::supervisor::respawn;
use crate::state::lock as mutex_lock;
use crate::state::SidecarState;
// poison-safe Mutex helper for the cleanup block.
use serde_json::json;
use std::sync::{Arc, OnceLock};
use tauri::Emitter;

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

// the supervisor queue is now a bounded `sync_channel(8)`
// instead of an unbounded `channel()`. An unbounded channel has no
// backpressure — a stalled supervisor (stuck in a long `respawn`
// backoff) combined with a flapping sidecar (reader exits every 1-2s
// triggering another respawn request) could enqueue an unbounded
// number of `(AppHandle, Arc<SidecarState>)` tuples, each holding
// strong references to the AppHandle and the full SidecarState (child
// handle, ws_tx, pending map). Bounded to 8 — generous enough for
// normal operation (a healthy supervisor drains the queue in
// milliseconds) but small enough to fail-fast on a stuck supervisor.
// On full, the request is DROPPED (logged): the in-flight respawn
// already observes the sidecar-down condition when it completes its
// reconnect cycle, so re-queuing is redundant; the supervisor's
// `respawn_in_progress` compare_exchange would no-op the duplicate
// anyway.
//
// the `OnceLock` now holds an `Option<SyncSender>` instead of
// a bare `SyncSender`. `Some(tx)` means the long-lived supervisor
// thread spawned successfully; `None` means it failed (low memory,
// RLIMIT_NPROC, sandbox restrictions, etc.) and callers should fall
// back to a per-trigger `std::thread::spawn`. Critically, the failure
// is stored ONCE inside `get_or_init` — the `OnceLock` is NOT
// poisoned by a thread-spawn failure (which would happen with the old
// `.expect()` form). All subsequent callers read the cached `None` and
// use the fallback path without re-attempting the spawn (and without
// re-panicking).
// pre-fix this was `OnceLock<Option<SyncSender>>`. The
// ``get_or_init`` closure cached ``None`` permanently on transient
// thread-spawn failure (RLIMIT_NPROC, sandbox, low memory), so a
// single startup-time failure degraded the resilience layer to
// per-trigger one-shot ``std::thread::spawn`` fallbacks for the
// ENTIRE process lifetime — even after the resource pressure cleared.
// Switching to ``OnceLock<Mutex<Option<SyncSender>>>`` lets each
// subsequent ``respawn_supervisor_sender()`` call re-attempt the
// spawn when the cached sender is ``None``, mirroring a bounded-retry
// pattern: rate-limited by the OS thread-spawn cost (microseconds)
// and never more than one concurrent spawn attempt (mutex-serialized).
static RESPAWN_SUPERVISOR_TX: OnceLock<
    std::sync::Mutex<Option<std::sync::mpsc::SyncSender<RespawnRequest>>>,
> = OnceLock::new();

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
// with its own `block_on` runtime. documents the failed
// attempt to use a direct `tokio::spawn` here.
//
// This helper takes ownership (`app: AppHandle`, `state: Arc<SidecarState>`)
// so callers pass `.clone()`d handles in and the helper moves them
// into the spawned thread. Returns nothing — the supervisor is best-effort.
//
// `pub(super)` so it's visible to the parent `ws` module (call sites
// in `spawn_writer_task` + `spawn_reader_task` cleanup blocks) AND
// to the sibling `heartbeat` submodule (call site in
// `spawn_heartbeat_task` miss-#3 arm).
pub(super) fn trigger_respawn_off_thread(app: tauri::AppHandle, state: Arc<SidecarState>) {
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
                    //via its `poisoned.into_inner()` path ()
                    // and re-attempt the spawn.
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

fn respawn_supervisor_sender() -> Option<std::sync::mpsc::SyncSender<RespawnRequest>> {
    // lazily initialise the outer ``Mutex``. ``OnceLock``
    // guarantees the ``Mutex`` is created exactly once; the inner
    // ``Option<Sender>`` is mutable under the mutex so we can retry
    // the spawn after a transient failure (closes the
    // regression where ``OnceLock<Option<Sender>>`` cached ``None``
    // permanently).
    let mutex: &'static std::sync::Mutex<Option<std::sync::mpsc::SyncSender<RespawnRequest>>> =
        RESPAWN_SUPERVISOR_TX.get_or_init(|| std::sync::Mutex::new(None));
    // Hold the lock for the whole attempt so two concurrent callers
    // don't both spawn supervisor threads (the second would create a
    // dead receiver that's instantly dropped, no-op-ing the first
    // sender). ``try_send`` on the sender is non-blocking so the
    // critical section is short.
    let mut guard = match mutex.lock() {
        Ok(g) => g,
        Err(poisoned) => {
            // recover from a poisoned mutex — the inner
            // data may be stale but we can still attempt a fresh
            // spawn.
            log::warn!("[SUPERVISOR] respawn-supervisor mutex poisoned — recovering (XE-15-3)");
            poisoned.into_inner()
        }
    };
    if let Some(ref tx) = *guard {
        // Cached sender is alive — clone (cheap; ``SyncSender`` is
        // designed for multi-producer cloning) and return.
        return Some(tx.clone());
    }
    // Cached sender is ``None`` (first call OR previous spawn failed).
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
            log::info!("[SUPERVISOR] long-lived respawn-supervisor thread spawned (XE-15-3)");
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
// error` helper (defined in the parent `ws` module) which collects
// all entries out of the lock first, then sends outside the lock —
// the AsyncMutex is not held across the oneshot sends.
//
// (made `async fn`): all 10 call sites are inside `wait_for_
// auth_ok`'s async block / outer async fn (in the parent `ws`
// module), so the `.await` promotion is local to that file. The
// drain requires the AsyncMutex on `state.pending` (no sync lock
// available), so the function must be async to await the lock.
//
// `pub(super)` so the parent `ws` module's `wait_for_auth_ok` can
// call it. The drain helper `drain_pending_with_disconnect_error`
// lives in the parent `ws` module and is also `pub(super)` so this
// module can call it via `super::drain_pending_with_disconnect_error`.
pub(super) async fn cleanup_and_trigger_respawn(app: &tauri::AppHandle, state: &Arc<SidecarState>) {
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
    let drained = super::drain_pending_with_disconnect_error(state).await;
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
