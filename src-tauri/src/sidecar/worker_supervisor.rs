//! Worker crash→respawn policy (plan §7.2/§7.3).
//! NOTE: see docs/code-notes/worker-lifecycle-policy.md

use futures_util::FutureExt;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tauri_plugin_shell::process::CommandEvent;

use super::lifecycle::format_duration_suffix;
use super::spawn::event_drain::spawn_child_event_drain;
use super::spawn::spawn_worker_and_get_port_with_shutdown;
use super::spawn::worker::{stop_worker_child, try_claim_restart_slot};
use crate::state::{lock as mutex_lock, WorkerState};
use crate::util::SUPERVISOR_BACKOFF_MS;

/// Poll interval for the liveness probe (dev-mode path; release mode
/// wakes on the drained exit channel instead).
const WORKER_WATCH_POLL_MS: u64 = 1000;

/// Backoff delay before respawn attempt `attempt` (0-based). `None`
/// past the schedule end: the episode is exhausted. Shares the sidecar
/// supervisor schedule: one doubling policy, not two.
pub(crate) fn worker_backoff_delay_ms(attempt: usize) -> Option<u64> {
    SUPERVISOR_BACKOFF_MS.get(attempt).copied()
}

/// C-WS-3: a respawn request is stale when it names a generation older
/// than the current one. `None` (liveness path) never goes stale.
pub(crate) fn worker_generation_is_stale(expected: Option<u64>, current: u64) -> bool {
    matches!(expected, Some(exp) if exp != current)
}

/// Plan §7.3 idle hook. The worker is long-lived; the future "Keep
/// offline engine running" toggle threads through here. Hook only:
/// identity today, so `worker_unloaded` semantics stay untouched.
#[allow(dead_code)] // wired when the idle-unload toggle lands (pending)
pub(crate) fn should_keep_worker_running(keep_running_setting: bool) -> bool {
    keep_running_setting
}

/// Watch the worker child and respawn on exit. Spawned once per
/// successful `initialize_worker`; racers serialize on the restart
/// slot, so at most one respawn runs at a time.
pub(crate) fn spawn_worker_exit_watcher(app: &tauri::AppHandle, state: Arc<WorkerState>) {
    let app_handle = app.clone();
    tauri::async_runtime::spawn(async move {
        worker_exit_watch_loop(&app_handle, &state).await;
    });
}

async fn worker_exit_watch_loop(app: &tauri::AppHandle, state: &Arc<WorkerState>) {
    loop {
        if state.shutting_down.load(Ordering::SeqCst) {
            break;
        }
        let rx_opt = state.child_exit_rx.lock().await.take();
        if let Some(mut rx) = rx_opt {
            match rx.recv().await {
                Some(CommandEvent::Terminated(payload)) => {
                    log::warn!(
                        "[WORKER] worker exited (code={:?}, signal={:?}): respawning",
                        payload.code,
                        payload.signal
                    );
                    let generation = state.ws_generation.load(Ordering::SeqCst);
                    let _ = respawn_worker(app, state, Some(generation)).await;
                    continue;
                }
                _ => {
                    log::debug!("[WORKER] exit channel closed without Terminated: probing liveness");
                }
            }
        }
        if worker_probe_exited(state) {
            let generation = state.ws_generation.load(Ordering::SeqCst);
            let _ = respawn_worker(app, state, Some(generation)).await;
            continue;
        }
        tokio::time::sleep(Duration::from_millis(WORKER_WATCH_POLL_MS)).await;
    }
    log::info!("[WORKER] exit watcher stopping (host shutting down)");
}

/// Non-blocking liveness probe for the dev-mode child (release mode
/// reports through the exit channel above). True only on observed
/// death; unknown states stay false so a healthy worker is never
/// reaped by a misfiring probe.
fn worker_probe_exited(state: &Arc<WorkerState>) -> bool {
    let mut guard = mutex_lock(&state.child);
    match guard.as_mut() {
        Some(handle) => match handle.try_wait() {
            Ok(Some(exited)) => {
                if exited {
                    log::warn!("[WORKER] liveness probe observed dead worker: respawning");
                }
                exited
            }
            Ok(None) => false,
            Err(e) => {
                log::debug!("[WORKER] liveness probe failed (best-effort): {}", e);
                false
            }
        },
        None => false,
    }
}

/// Crash→respawn engine: slot-serialized, exponential backoff, old
/// child killed first. Independent of the sidecar breaker: no restart
/// counter, no app relaunch; exhaustion only logs + returns Err.
pub(crate) async fn respawn_worker(
    app: &tauri::AppHandle,
    state: &Arc<WorkerState>,
    expected_generation: Option<u64>,
) -> Result<(), String> {
    if state.shutting_down.load(Ordering::SeqCst) {
        return Ok(());
    }
    if !try_claim_restart_slot(&state.respawn_in_progress) {
        log::info!("[WORKER] respawn already in flight: skipping duplicate");
        return Ok(());
    }
    if worker_generation_is_stale(expected_generation, state.ws_generation.load(Ordering::SeqCst)) {
        log::info!("[WORKER] stale respawn request for an older generation: skipping");
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        return Ok(());
    }
    let inner = AssertUnwindSafe(respawn_worker_inner(app, state))
        .catch_unwind()
        .await;
    match inner {
        Ok(r) => r,
        Err(payload) => {
            let msg = payload
                .downcast_ref::<&'static str>()
                .copied()
                .or_else(|| payload.downcast_ref::<String>().map(|s| s.as_str()))
                .unwrap_or("<non-string panic payload>");
            log::error!("[WORKER] respawn task panicked: {}", msg);
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            Err(format!("worker respawn panicked: {}", msg))
        }
    }
}

async fn respawn_worker_inner(
    app: &tauri::AppHandle,
    state: &Arc<WorkerState>,
) -> Result<(), String> {
    let started = Instant::now();
    let attempts = SUPERVISOR_BACKOFF_MS.len();
    let mut attempt: usize = 0;
    while let Some(delay_ms) = worker_backoff_delay_ms(attempt) {
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[WORKER] shutting down, skipping respawn");
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }
        log::warn!(
            "[WORKER] respawn attempt {} of {} after {}ms",
            attempt + 1,
            attempts,
            delay_ms
        );
        let target = tokio::time::Instant::now() + Duration::from_millis(delay_ms);
        loop {
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!("[WORKER] shutting down during backoff sleep, aborting respawn");
                state.respawn_in_progress.store(false, Ordering::SeqCst);
                return Ok(());
            }
            let now = tokio::time::Instant::now();
            if now >= target {
                break;
            }
            let remaining = target - now;
            tokio::select! {
                _ = tokio::time::sleep(remaining) => {}
                _ = state.shutdown_notify.notified() => {}
            }
        }
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[WORKER] shutting down (pre-spawn re-check), skipping respawn");
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }
        stop_worker_child(state).await;
        match spawn_worker_and_get_port_with_shutdown(app, state.clone(), &state.shutting_down).await
        {
            Ok((port, child, exit_rx)) => {
                let mut child_opt = Some(child);
                let old_handle = {
                    let mut guard = mutex_lock(&state.child);
                    if state.shutting_down.load(Ordering::SeqCst) {
                        None
                    } else {
                        let old = guard.take();
                        if let Some(fresh) = child_opt.take() {
                            *guard = Some(fresh);
                        }
                        old
                    }
                };
                if let Some(orphan) = child_opt {
                    if let Err(e) = orphan.kill_tree().await {
                        log::warn!("[WORKER] post-spawn shutdown kill failed (best-effort): {}", e);
                    }
                    state.respawn_in_progress.store(false, Ordering::SeqCst);
                    return Ok(());
                }
                if let Some(old) = old_handle {
                    if let Err(e) = old.kill_tree().await {
                        log::warn!("[WORKER] racing old child kill failed (best-effort): {}", e);
                    }
                }
                *state.child_exit_rx.lock().await =
                    exit_rx.map(|rx| spawn_child_event_drain("[WORKER]", rx));
                state.ws_generation.fetch_add(1, Ordering::SeqCst);
                log::info!(
                    "[WORKER] respawn succeeded on attempt {} (port={}){}",
                    attempt + 1,
                    port,
                    format_duration_suffix(started.elapsed())
                );
                state.respawn_in_progress.store(false, Ordering::SeqCst);
                return Ok(());
            }
            Err(e) => {
                if e == "shutdown" {
                    log::info!("[WORKER] spawn loop detected shutting_down: exiting respawn cleanly");
                    state.respawn_in_progress.store(false, Ordering::SeqCst);
                    return Ok(());
                }
                log::warn!("[WORKER] respawn spawn failed: {}", e);
            }
        }
        attempt += 1;
    }
    log::error!(
        "[WORKER] backoff exhausted after {} attempts{}: worker left stopped (sidecar unaffected, no app relaunch)",
        attempts,
        format_duration_suffix(started.elapsed())
    );
    state.respawn_in_progress.store(false, Ordering::SeqCst);
    Err("worker respawn backoff exhausted".to_string())
}
