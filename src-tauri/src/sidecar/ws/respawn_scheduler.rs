//! Respawn supervisor scheduling + auth-failure cleanup (ADR-0020 §1/§10).
//! C-WS-3: carry expected_generation; re-check at dequeue so stale requests cannot kill a newer connection.
//! C-TOKIO-1: `reconnect_ws` is `!Send`, so respawn runs on a dedicated std thread + `block_on`.

use crate::sidecar::supervisor::respawn;
use crate::state::lock as mutex_lock;
use crate::state::SidecarState;
// poison-safe Mutex helper for the cleanup block.
use serde_json::json;
use std::sync::atomic::Ordering;
use std::sync::{Arc, OnceLock};
use tauri::Emitter;

// Long-lived supervisor: OnceLock holds Mutex<Option<SyncSender>> so a failed spawn can be retried.
// Channel is bounded(8): full → drop (in-flight respawn already covers the outage).
type RespawnRequest = (tauri::AppHandle, Arc<SidecarState>, Option<u64>);

static RESPAWN_SUPERVISOR_TX: OnceLock<
    std::sync::Mutex<Option<std::sync::mpsc::SyncSender<RespawnRequest>>>,
> = OnceLock::new();

/// Enqueue a respawn for the failure observed by `expected_generation`.
/// C-WS-3: `None` is only for heartbeat/auth-failure (generation-agnostic).
/// C-TOKIO-1: supervisor uses std::thread + block_on because respawn is `!Send`.
/// Queue full → drop (in-flight respawn covers it). Supervisor missing/dead → one-shot thread.
pub(super) fn trigger_respawn_off_thread(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    // C-WS-3: generation of the connection that observed the failure; dequeue re-checks.
    expected_generation: Option<u64>,
) {
    match respawn_supervisor_sender() {
        Some(tx) => match tx.try_send((app, state, expected_generation)) {
            Ok(()) => {}
            Err(std::sync::mpsc::TrySendError::Full((_app, _state, _gen))) => {
                log::warn!(
                    "[SUPERVISOR] respawn request queue full (capacity=8): \
                     dropping request: supervisor already processing)"
                );
            }
            Err(std::sync::mpsc::TrySendError::Disconnected((app, state, _gen))) => {
                log::error!(
                    "[SUPERVISOR] failed to enqueue respawn request to supervisor \
                     thread (it may have panicked): disconnected, falling back to \
                     one-shot std::thread::spawn (fallback after supervisor disconnect)"
                );
                // Clear dead sender so the next call retries the long-lived spawn.
                if let Some(mutex) = RESPAWN_SUPERVISOR_TX.get() {
                    if let Ok(mut guard) = mutex.lock() {
                        *guard = None;
                    }
                }
                spawn_oneshot_respawn_thread(app, state, expected_generation);
            }
        },
        None => {
            log::warn!(
                "[SUPERVISOR] long-lived supervisor thread unavailable: using \
                 one-shot std::thread::spawn fallback (long-lived thread unavailable)"
            );
            spawn_oneshot_respawn_thread(app, state, expected_generation);
        }
    }
}

/// One-shot fallback when the long-lived supervisor is unavailable.
/// C-TOKIO-1: std::thread + block_on (respawn is `!Send`). C-WS-3: same generation re-check.
fn spawn_oneshot_respawn_thread(
    app: tauri::AppHandle,
    state: Arc<SidecarState>,
    expected_generation: Option<u64>,
) {
    if let Err(e) = std::thread::Builder::new()
        .name("respawn-oneshot".into())
        .spawn(move || {
            tauri::async_runtime::block_on(async move {
                // C-WS-3: same stale-generation guard as the supervisor loop.
                if let Some(gen) = expected_generation {
                    let current = state.ws_generation.load(Ordering::SeqCst);
                    if current != gen {
                        log::info!(
                            "[SUPERVISOR] dropping STALE one-shot respawn request \
                             (request gen={}, current gen={}): a newer WS connection \
                             owns the link; killing it would ping-pong respawns",
                            gen,
                            current
                        );
                        return;
                    }
                }
                if let Err(e) = respawn(&app, &state).await {
                    log::error!(
                        "[WS] supervisor respawn failed: {}, app may be in a degraded state",
                        e
                    );
                }
            });
        })
    {
        log::error!(
            "[SUPERVISOR] fallback std::thread::spawn failed: {}, respawn \
             request dropped; resilience layer is degraded until manual relaunch",
            e
        );
    }
}

fn respawn_supervisor_sender() -> Option<std::sync::mpsc::SyncSender<RespawnRequest>> {
    // Mutex under OnceLock so a failed spawn can be retried; hold lock for the spawn attempt.
    let mutex: &'static std::sync::Mutex<Option<std::sync::mpsc::SyncSender<RespawnRequest>>> =
        RESPAWN_SUPERVISOR_TX.get_or_init(|| std::sync::Mutex::new(None));
    let mut guard = match mutex.lock() {
        Ok(g) => g,
        Err(poisoned) => {
            log::warn!("[SUPERVISOR] respawn-supervisor mutex poisoned, recovering");
            poisoned.into_inner()
        }
    };
    if let Some(ref tx) = *guard {
        return Some(tx.clone());
    }
    let (tx, rx) = std::sync::mpsc::sync_channel::<RespawnRequest>(8);
    match std::thread::Builder::new()
        .name("respawn-supervisor".into())
        .spawn(move || {
            for (app, state, expected_gen) in rx {
                // C-WS-3: dequeue-time generation re-check; None (heartbeat/auth) never skipped.
                if let Some(gen) = expected_gen {
                    let current = state.ws_generation.load(Ordering::SeqCst);
                    if current != gen {
                        log::info!(
                            "[SUPERVISOR] dropping STALE respawn request \
                             (request gen={}, current gen={}): a newer WS \
                             connection owns the link",
                            gen,
                            current
                        );
                        continue;
                    }
                }
                tauri::async_runtime::block_on(async move {
                    if let Err(e) = respawn(&app, &state).await {
                        log::error!(
                            "[WS] supervisor respawn failed: {}, app may be in a degraded state",
                            e
                        );
                    }
                });
            }
        }) {
        Ok(_) => {
            *guard = Some(tx.clone());
            drop(guard);
            log::info!("[SUPERVISOR] long-lived respawn-supervisor thread spawned");
            Some(tx)
        }
        Err(e) => {
            drop(guard);
            log::error!(
                "[SUPERVISOR] failed to spawn long-lived respawn-supervisor \
                 thread: {}, will retry on next call; using per-trigger \
                 std::thread::spawn fallback this call (long-lived thread spawn failed)",
                e
            );
            None
        }
    }
}

/// Auth-failed / auth-timeout cleanup: clear ws_tx, drain pending (dispatches can
/// queue during the auth window — they would otherwise hang 120s), then respawn.
/// C-WS-3: generation is `None` here (must respawn regardless of in-flight reconnect).
pub(super) async fn cleanup_and_trigger_respawn(app: &tauri::AppHandle, state: &Arc<SidecarState>) {
    {
        // Poisoned lock is safe: clearing ws_tx to None is always valid.
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        *ws_tx_guard = None;
    }
    let drained = super::drain_pending_with_disconnect_error(state).await;
    if drained > 0 {
        log::warn!(
            "[WS-AUTH] drained {} pending dispatch requests on auth failure/timeout",
            drained
        );
    }
    let _ = app.emit(
        "supervisor_relaunching",
        json!({"reason": "auth_failed_or_timeout"}),
    );
    // C-WS-3: None generation on the auth-failure path.
    trigger_respawn_off_thread(app.clone(), state.clone(), None);
}
