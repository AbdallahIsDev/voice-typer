//! Heartbeat task spawn + abort helpers (ADR-0020 §10).
//!
//! Extracted from the original 2534-line `ws.rs` monolith. Holds:
//! - `spawn_heartbeat_task` — drives a 10s-interval `heartbeat`
//!   dispatch loop on the Tauri async runtime, aborting the
//!   previous handle (if any) before storing the new one.
//! - `abort_heartbeat` — shared idempotent abort helper used by
//!   BOTH shutdown paths (`sidecar/shutdown.rs::shutdown_sidecar_for_exit`
//!   and `commands/sidecar_cmds/shutdown.rs::shutdown_sidecar`) so an
//!   in-flight heartbeat task is aborted whether the app exits
//!   via `RunEvent::Exit` OR via the renderer-invocable Tauri
//!   command.
//!
//! Visibility contract:
//! - `spawn_heartbeat_task` is `pub(super)` — visible to the
//!   parent `ws` module (single call site in `reconnect_ws`).
//! - `abort_heartbeat` is `pub(crate)` (and re-exported from
//!   `ws.rs`) so external callers in `sidecar/shutdown.rs` /
//!   `commands/sidecar_cmds/shutdown.rs` keep working through
//!   `crate::sidecar::ws::abort_heartbeat`.

use crate::commands::sidecar_cmds::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
// heartbeat interval / response timeout / max misses are named
// constants in `util.rs` (previously inline `Duration::from_secs(10)` /
// `Duration::from_secs(15)` / `>= 3` literals below).
use crate::util::{HEARTBEAT_INTERVAL_SECS, HEARTBEAT_MAX_MISSES, HEARTBEAT_RESPONSE_TIMEOUT_SECS};
use futures_util::FutureExt;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

/// Shared heartbeat-abort helper. Idempotent — a no-op if
/// `heartbeat_handle` is already `None`.
///
/// Used by BOTH shutdown paths so the in-flight heartbeat task is
/// aborted whether the app exits via `RunEvent::Exit`
/// (`shutdown_sidecar_for_exit` in `sidecar/shutdown.rs`) OR via the renderer-
/// invocable `shutdown_sidecar` Tauri command
/// (`commands/sidecar_cmds/shutdown.rs`). Previously only
/// `shutdown_sidecar_for_exit` aborted; the Tauri-command path leaked
/// a heartbeat task that kept dispatching `heartbeat` frames into the
/// dead WS for up to `HEARTBEAT_MAX_MISSES` (30s) before self-terminating.
///
/// Extracted as a `pub(crate)` helper in this file (ws.rs owns the
/// heartbeat spawn logic) so both call sites share one implementation.
///
/// **Call-site status:** both shutdown paths abort the heartbeat:
///   - `commands/sidecar_cmds/shutdown.rs` (`shutdown_sidecar`) calls
///     `crate::sidecar::ws::abort_heartbeat(state.inner()).await`
///     after the `shutting_down` swap, before sending the shutdown
///     frame.
///   - `sidecar/shutdown.rs` (`shutdown_sidecar_for_exit`, the
///     app-exit path) aborts the same handle inline
///     (`hb_guard.take()` + `handle.abort()`) — functionally
///     equivalent to this helper.
pub(crate) async fn abort_heartbeat(state: &Arc<SidecarState>) {
    let prev = {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        hb_guard.take()
    };
    if let Some(handle) = prev {
        handle.abort();
        log::info!("[HEARTBEAT] aborted in-flight heartbeat task via abort_heartbeat helper");
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
/// in a 15s timeout (`HEARTBEAT_RESPONSE_TIMEOUT_SECS`) to bound the
/// liveness probe. On 3 consecutive misses (≥30s of
/// unresponsiveness) we trigger supervisor respawn via the same
/// `std::thread::spawn` + `block_on` bridge used by the WS reader
/// above (the supervisor's `reconnect_ws` future is `!Send` —
/// tokio-tungstenite holds a `!Send` across an await — so it can't
/// be awaited from a `tokio::spawn` directly).
///
/// The 15s outer timeout cancels `dispatch_inner` by dropping its
/// future, which leaks the pending entry: `dispatch_frame`'s own 15s
/// timeout branch never gets to run (the future is dropped at the
/// same deadline, before the branch can fire). The supervisor
/// respawn triggered at miss #3 kills the sidecar, which drops
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
///
/// `pub(super)` so the parent `ws` module's `reconnect_ws` can call
/// it. Calls `super::respawn_scheduler::trigger_respawn_off_thread`
/// on miss #3.
pub(super) async fn spawn_heartbeat_task(
    heartbeat_app: tauri::AppHandle,
    heartbeat_state: Arc<SidecarState>,
) {
    // Abort any previous heartbeat task before spawning
    // the new one. `reconnect_ws` is called on every successful
    // supervisor respawn (and on initial cold start), so without this abort the
    // PRIOR heartbeat task would leak — it loops forever on a 10s
    // `interval.tick()`. After N reconnects you'd have N concurrent
    // heartbeat tasks all dispatching `heartbeat` frames at 10s
    // intervals, multiplying sidecar load N×.
    //
    // The heartbeat's pending dispatch id is allocated INSIDE
    // `dispatch_inner` (in `dispatch_frame`,
    // `commands/sidecar_cmds/dispatch.rs`), so the heartbeat task
    // here does NOT know the id and can't manually remove the
    // pending entry from `state.pending` on the 15s timeout.
    // Mitigation (existing behavior, preserved):
    // - On miss #3, supervisor respawn kills the sidecar → WS socket
    // drops → WS reader's drain loop clears ALL pending entries.
    // - On miss #1/#2, the leaked entry is cleared when the sidecar
    // eventually responds (the reader removes the id on ANY
    // id-bearing response) or at miss #3. `dispatch_frame`'s own
    // 15s timeout never fires here — the outer 15s wrapper drops
    // the whole dispatch future at the same deadline, before the
    // internal timeout branch can run.
    // Known limitation: no Drop guard exists on the dispatch path,
    // so the pending entry is NOT removed when the dispatch future is
    // dropped (which happens when the 15s outer timeout cancels
    // `dispatch_inner`); it lingers until a late response or the
    // miss-#3 respawn clears it.
    // Clone the Arc BEFORE moving it into the async closure. The closure
    // below (async move { ... }) takes ownership of `heartbeat_state_for_
    // task`; the original `heartbeat_state` is still referenced inside the
    // lock scope below to acquire `heartbeat_state.heartbeat_handle`.
    let heartbeat_state_for_task = heartbeat_state.clone();
    // Hold the `heartbeat_handle` lock across the take + spawn +
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
        let handle: tauri::async_runtime::JoinHandle<()> = tauri::async_runtime::spawn(
            async move {
                let mut missed: u32 = 0;
                let mut interval =
                    tokio::time::interval(Duration::from_secs(HEARTBEAT_INTERVAL_SECS));
                loop {
                    if heartbeat_state_for_task
                        .shutting_down
                        .load(Ordering::SeqCst)
                    {
                        break;
                    }
                    interval.tick().await;
                    if heartbeat_state_for_task
                        .shutting_down
                        .load(Ordering::SeqCst)
                    {
                        break;
                    }
                    let heartbeat_args = DispatchArgs {
                        cmd: "heartbeat".to_string(),
                        data: None,
                    };
                    // Wrap the dispatch + timeout in `catch_unwind`
                    // so a panic inside `dispatch_inner` (e.g. a serde
                    // invariant violation, or a future-proofing regression
                    // in `dispatch_frame`'s pending-map insert path) is
                    // caught, logged at ERROR, and treated as a miss —
                    // instead of silently killing the heartbeat task and
                    // losing hang detection entirely. The reader + writer
                    // tasks already wrap their bodies in `catch_unwind`;
                    // the heartbeat task was added later
                    // and missed the same treatment.
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
                                super::respawn_scheduler::trigger_respawn_off_thread(
                                    heartbeat_app.clone(),
                                    heartbeat_state_for_task.clone(),
                                    None,
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
                                super::respawn_scheduler::trigger_respawn_off_thread(
                                    heartbeat_app.clone(),
                                    heartbeat_state_for_task.clone(),
                                    None,
                                );
                                break;
                            }
                        }
                        // `catch_unwind` returned Err(_panic_payload).
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
                             task staying alive",
                                missed,
                                HEARTBEAT_MAX_MISSES
                            );
                            if missed >= HEARTBEAT_MAX_MISSES {
                                log::warn!(
                                "[HEARTBEAT] {} consecutive panic-misses — triggering supervisor respawn",
                                HEARTBEAT_MAX_MISSES
                            );
                                super::respawn_scheduler::trigger_respawn_off_thread(
                                    heartbeat_app.clone(),
                                    heartbeat_state_for_task.clone(),
                                    None,
                                );
                                break;
                            }
                        }
                    }
                }
            },
        );
        // Store the new handle INSIDE the lock
        // so the take+spawn+store sequence is atomic with respect to
        // other callers. The next reconnect (or `abort_heartbeat` /
        // `shutdown_sidecar_for_exit`) can abort it.
        *hb_guard = Some(handle);
        prev
    };
    // Abort the previous handle AFTER releasing the lock. `abort()`
    // posts a cancellation signal to the task's waker; it does not
    // synchronously join the task, so this is fast and lock-free.
    if let Some(prev) = prev_handle_opt {
        prev.abort();
        log::info!("[HEARTBEAT] aborted previous heartbeat task before spawning new one");
    }
}

// Sibling test module — tests live in `heartbeat_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "heartbeat_tests.rs"]
mod heartbeat_tests;
