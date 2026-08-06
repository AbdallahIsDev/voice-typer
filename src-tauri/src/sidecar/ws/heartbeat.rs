//! Heartbeat task spawn + abort helpers (ADR-0020 §10).
//!
//! Extracted from the original 2534-line `ws.rs` monolith
//! (review.md FZ-24 / ZR-86). Holds:
//! - `spawn_heartbeat_task` — drives a 10s-interval `heartbeat`
//!   dispatch loop on the Tauri async runtime, aborting the
//!   previous handle (if any) before storing the new one.
//! - `abort_heartbeat` — shared idempotent abort helper used by
//!   BOTH shutdown paths (`state.rs::shutdown_sidecar_for_exit`
//!   and `commands/sidecar_cmds.rs::shutdown_sidecar`) so an
//!   in-flight heartbeat task is aborted whether the app exits
//!   via `RunEvent::Exit` OR via the renderer-invocable Tauri
//!   command.
//!
//! Visibility contract:
//! - `spawn_heartbeat_task` is `pub(super)` — visible to the
//!   parent `ws` module (single call site in `reconnect_ws`).
//! - `abort_heartbeat` is `pub(crate)` (and re-exported from
//!   `ws.rs`) so external callers in `state.rs` /
//!   `commands/sidecar_cmds.rs` keep working through
//!   `crate::sidecar::ws::abort_heartbeat`.

use crate::commands::sidecar_cmds::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
// heartbeat interval / response timeout / max misses are named
// constants in `util.rs` (previously inline `Duration::from_secs(10)` /
// `Duration::from_secs(15)` / `>= 3` literals below).
use crate::util::{
    HEARTBEAT_INTERVAL_SECS, HEARTBEAT_MAX_MISSES, HEARTBEAT_RESPONSE_TIMEOUT_SECS,
};
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use futures_util::FutureExt;

/// shared heartbeat-abort helper. Idempotent — a no-op if
/// `heartbeat_handle` is already `None`.
///
/// Used by BOTH shutdown paths so the in-flight heartbeat task is
/// aborted whether the app exits via `RunEvent::Exit`
/// (`shutdown_sidecar_for_exit` in `state.rs`) OR via the renderer-
/// invocable `shutdown_sidecar` Tauri command
/// (`commands/sidecar_cmds.rs`). Previously only
/// `shutdown_sidecar_for_exit` aborted; the Tauri-command path leaked
/// a heartbeat task that kept dispatching `heartbeat` frames into the
/// dead WS for up to `HEARTBEAT_MAX_MISSES` (30s) before self-terminating.
///
/// Extracted as a `pub(crate)` helper in this file (ws.rs owns the
/// heartbeat spawn logic) so both call sites share one implementation.
///
/// **Coordination note (file-disjoint rule):** the call sites are owned
/// by OTHER sub-agents; this helper is wired in once each lands:
///   - `commands/sidecar_cmds.rs` (`shutdown_sidecar`) — DONE: calls
///     `crate::sidecar::ws::abort_heartbeat(state.inner()).await;` after
///     the `shutting_down` swap, before sending the shutdown frame.
///   - `state.rs` (`shutdown_sidecar_for_exit`, lines ~292-300) — still
///     uses the inline `hb_guard.take()` + `handle.abort()` block; may
///     be migrated to `crate::sidecar::ws::abort_heartbeat(&state).await;`
///     but is functionally equivalent (both abort the same handle).
pub(crate) async fn abort_heartbeat(state: &Arc<SidecarState>) {
    let prev = {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        hb_guard.take()
    };
    if let Some(handle) = prev {
        handle.abort();
        log::info!(
            "[HEARTBEAT] aborted in-flight heartbeat task via abort_heartbeat helper (UE-8-F10)"
        );
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
pub(super) async fn spawn_heartbeat_task(heartbeat_app: tauri::AppHandle, heartbeat_state: Arc<SidecarState>) {
    //abort any previous heartbeat task before spawning
    // the new one. `reconnect_ws` is called on every successful
    // supervisor respawn (and on initial cold start), so without this abort the
    // PRIOR heartbeat task would leak — it loops forever on a 10s
    // `interval.tick()`. After N reconnects you'd have N concurrent
    // heartbeat tasks all dispatching `heartbeat` frames at 10s
    // intervals, multiplying sidecar load N×.
    //
    // the heartbeat's pending dispatch id is allocated INSIDE
    // `dispatch_inner` (in `dispatch_frame` — `sidecar_cmds.rs`, owned
    // The heartbeat task here does NOT know the id, so
    // it can't manually remove the pending entry from `state.pending`
    // on the 15s timeout. Mitigation (existing behavior, preserved):
    // - On miss #3, supervisor respawn kills the sidecar → WS socket
    // drops → WS reader's drain loop clears ALL pending entries.
    // - On miss #1/#2, `dispatch_frame`'s internal 120s timeout
    // eventually removes the entry. Bounded leak.
    //will add a Drop guard on the dispatch path so
    // the pending entry is removed immediately when the dispatch
    // future is dropped (which happens when the 15s outer timeout
    // cancels `dispatch_inner`).
    // Clone the Arc BEFORE moving it into the async closure. The closure
    // below (async move { ... }) takes ownership of `heartbeat_state_for_
    // task`; the original `heartbeat_state` is still referenced inside the
    // lock scope below to acquire `heartbeat_state.heartbeat_handle`.
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
                //wrap the dispatch + timeout in `catch_unwind`
                // so a panic inside `dispatch_inner` (e.g. a serde
                // invariant violation, or a future-proofing regression
                // in `dispatch_frame`'s pending-map insert path) is
                // caught, logged at ERROR, and treated as a miss —
                // instead of silently killing the heartbeat task and
                //losing  detection entirely. The reader + writer
                // tasks already wrap their bodies in `catch_unwind`
                //the heartbeat task was added later
                //and missed the same treatment.
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
                            );
                            break;
                        }
                    }
                    //catch_unwind returned Err(_panic_payload).
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
    use std::time::Duration;
    use std::sync::Arc;

    // heartbeat task abort on reconnect ────────────────────

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

    /// `shutdown_sidecar_for_exit` must abort any in-flight
    /// heartbeat task stored on `state.heartbeat_handle`.
    ///
    /// The handle is aborted + cleared EARLY in the function (before the
    /// graceful-exit wait). In dev-mode (`child_exit_rx = None`) that
    /// wait sleeps `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) before the
    /// force-kill backstop, so this test runs the shutdown on the async
    /// runtime and polls for the early handle-clear instead of awaiting
    /// full completion.
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
        let shutdown_task = tauri::async_runtime::spawn(async move {
            shutdown_sidecar_for_exit(&state_clone).await;
        });

        // Poll for the early handle-clear with a bounded deadline. The
        // 20ms interval is intentional (event-based poll, not a fixed
        // sleep — matches the XS-53 bounded-polling pattern elsewhere).
        tokio::time::timeout(Duration::from_millis(3000), async {
            loop {
                if state.heartbeat_handle.lock().await.is_none() {
                    break;
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        })
        .await
        .expect(
            "GT-8: shutdown_sidecar_for_exit must abort + clear the heartbeat handle within 3s",
        );

        // Stop the spawned shutdown task — its remaining 30s dev-mode
        // sleep is irrelevant to the assertion above.
        shutdown_task.abort();
    }

    // abort_heartbeat helper ────────────────────────────

    /// `abort_heartbeat` must clear the `heartbeat_handle`
    /// slot and abort the in-flight task. Verifies the helper is
    /// callable and idempotent — the two shutdown paths
    /// (`shutdown_sidecar_for_exit` in state.rs, `shutdown_sidecar` in
    /// sidecar_cmds.rs) both need to call it safely even if the other
    /// path already ran.
    #[tokio::test]
    async fn test_ue8_f10_abort_heartbeat_clears_handle_and_aborts_task() {
        let state = Arc::new(crate::state::SidecarState::new());
        // Spawn a long-running task (sleep 60s — well beyond the test
        // timeout). The heartbeat task in production runs an infinite
        // loop; a 60s sleep simulates "in-flight" for the test window.
        let h = tauri::async_runtime::spawn(async {
            tokio::time::sleep(Duration::from_secs(60)).await;
        });
        *state.heartbeat_handle.lock().await = Some(h);
        assert!(
            state.heartbeat_handle.lock().await.is_some(),
            "precondition: heartbeat_handle must be Some before abort_heartbeat"
        );

        // Call the abort_heartbeat helper.
        abort_heartbeat(&state).await;

        // The handle must be cleared.
        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "UE-8-F10: abort_heartbeat must clear the heartbeat handle"
        );

        // Calling abort_heartbeat again must be a no-op (idempotent) —
        // a second shutdown path arriving after the first must not panic.
        abort_heartbeat(&state).await;
        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "UE-8-F10: abort_heartbeat must be idempotent on a None handle"
        );
    }

    /// `abort_heartbeat` on a fresh state (handle is None)
    /// must be a no-op without panicking. Pins the "idempotent on empty"
    /// contract for the cold-start path where no heartbeat has been
    /// spawned yet but a shutdown is initiated.
    #[tokio::test]
    async fn test_ue8_f10_abort_heartbeat_on_fresh_state_is_noop() {
        let state = Arc::new(crate::state::SidecarState::new());
        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "precondition: fresh state must have heartbeat_handle = None"
        );
        // Calling on a fresh state must not panic.
        abort_heartbeat(&state).await;
        assert!(
            state.heartbeat_handle.lock().await.is_none(),
            "UE-8-F10: abort_heartbeat on fresh state must leave handle as None"
        );
    }
}
