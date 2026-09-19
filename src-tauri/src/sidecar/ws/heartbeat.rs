//! Heartbeat task spawn + abort helpers (ADR-0020 §10).
//! 10s `heartbeat` dispatch loop; N misses → supervisor respawn.
//! `abort_heartbeat` is shared by both shutdown paths.
//! NOTE: see docs/code-notes/tauri-host.md#heartbeat

use crate::commands::sidecar_cmds::{dispatch_inner, DispatchArgs};
use crate::state::SidecarState;
use crate::util::{HEARTBEAT_INTERVAL_SECS, HEARTBEAT_MAX_MISSES, HEARTBEAT_RESPONSE_TIMEOUT_SECS};
use futures_util::FutureExt;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;

/// Shared idempotent heartbeat abort (both shutdown paths).
/// Without this, the Tauri-command path leaked a task dispatching into
/// a dead WS for up to HEARTBEAT_MAX_MISSES intervals.
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

/// Spawn the Tauri-side heartbeat task.
/// Detects app-level sidecar hangs (GIL / infinite loop) that keep the
/// socket open but stop responding. On N consecutive misses → respawn.
/// Heartbeat-liveness respawn may pass generation None (C-WS-3).
pub(super) async fn spawn_heartbeat_task(
    heartbeat_app: tauri::AppHandle,
    heartbeat_state: Arc<SidecarState>,
) {
    // Abort previous handle before spawning (reconnect_ws is called on
    // every respawn; without abort, N reconnects leak N heartbeat tasks).
    //
    // Hold the handle lock across take+spawn+store: cold-start and
    // supervisor reconnects can otherwise interleave and leak a handle.
    // spawn is submit-only (sync); abort after lock release.
    //
    // 15s outer timeout cancels dispatch_inner; PendingEntryGuard in
    // dispatch.rs removes the pending entry on that drop.
    let heartbeat_state_for_task = heartbeat_state.clone();
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
                    // catch_unwind: a panic inside dispatch_inner is a miss,
                    // not a dead heartbeat task (reader/writer already wrap).
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
                                "[HEARTBEAT] {} consecutive misses: triggering supervisor respawn",
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
                                "[HEARTBEAT] {} consecutive misses: triggering supervisor respawn",
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
                        // Panic counts as a miss; same threshold.
                        Err(_) => {
                            missed += 1;
                            log::error!(
                                "[HEARTBEAT] dispatch_inner panicked (miss #{}/{}): \
                             task staying alive",
                                missed,
                                HEARTBEAT_MAX_MISSES
                            );
                            if missed >= HEARTBEAT_MAX_MISSES {
                                log::warn!(
                                "[HEARTBEAT] {} consecutive panic-misses: triggering supervisor respawn",
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
        // Store new handle inside the lock (take+spawn+store atomic vs other callers).
        *hb_guard = Some(handle);
        prev
    };
    // Abort previous AFTER lock release (abort posts cancellation; does not join).
    if let Some(prev) = prev_handle_opt {
        prev.abort();
        log::info!("[HEARTBEAT] aborted previous heartbeat task before spawning new one");
    }
}

// C-TEST-5: sibling test file.
#[cfg(test)]
#[path = "heartbeat_tests.rs"]
mod heartbeat_tests;
