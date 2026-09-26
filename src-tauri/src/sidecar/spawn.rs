//! Sidecar spawn + stdout handshake (ADR-0020 §1 + §4.1 + §14).
//! Submodules own the actual process spawn; this file is orchestration.
//! Both spawn paths `.env_clear()` then re-add only the OS-required allowlist.
//! C-TOKIO-1: panic capture is `AssertUnwindSafe(fut).catch_unwind().await`,
//! never `block_on` on a runtime worker.
//! Layout: docs/code-notes/tauri-host.md#module-layout

// `pub(crate)` so lifecycle can consult `is_dev_mode()` for tray-Restart.
pub(crate) mod dev_mode;
mod env_allowlist;
mod handshake;
mod handshake_loop;
// Permanent child-event drain: keeps the bounded shell event channel
// drained post-handshake so child stderr can never block its writers.
pub(crate) mod event_drain;
mod release_mode;
// Worker exe spawn (runtime-pack split). Sidecar is the worker's WS client.
pub(crate) mod worker;
// `pub(crate)` so platform::worker_path can resolve the per-platform worker name.
pub(crate) mod target_triple;

// Test-only re-exports for spawn_tests.rs (`use super::*`).
#[cfg(test)]
pub(crate) use dev_mode::is_dev_mode_for;
#[cfg(test)]
pub(crate) use env_allowlist::{passthrough_env_allowlist, vt_start_hidden_env};
#[cfg(test)]
pub(crate) use handshake::{is_shutting_down, parse_server_started, parse_worker_started};
#[cfg(test)]
pub(crate) use target_triple::{current_target_triple, target_triple_for};
#[cfg(test)]
pub(crate) use worker::try_claim_restart_slot;
#[cfg(test)]
pub(crate) use worker::{should_start_worker, worker_shared_env, worker_started_relay_frame};

use crate::state::SidecarHandle;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::Manager;
use tauri_plugin_shell::process::CommandEvent;
use tokio::sync::mpsc;

// C-TOKIO-1: catch_unwind on the future, never block_on inside a runtime worker.
use futures_util::future::FutureExt;

// ─── Sidecar spawn + stdout handshake (ADR-0020 §1) ───────────────────

/// Pure parser for adopted-backend env pair.
/// `None` on missing/invalid port or missing token; caller falls through
/// to normal spawn.
pub(crate) fn parse_adopted_backend_env(
    port_raw: Option<&str>,
    token: Option<&str>,
) -> Option<(u16, String)> {
    let port_raw = port_raw?;
    let token = token?;
    let port = port_raw.parse::<u16>().ok()?;
    if port == 0 {
        return None;
    }
    Some((port, token.to_string()))
}

/// Env reader over `parse_adopted_backend_env` (VT_PYTHON_PORT + VT_IPC_TOKEN).
/// Host must NOT spawn a second backend when attaching to a parent CLI flow.
pub(crate) fn adopted_backend_env() -> Option<(u16, String)> {
    let port_raw = std::env::var("VT_PYTHON_PORT").ok()?;
    let token = std::env::var("VT_IPC_TOKEN").ok()?;
    match parse_adopted_backend_env(Some(&port_raw), Some(&token)) {
        Some((port, token)) => {
            log::info!(
                "[SETUP] VT_PYTHON_PORT={} set, attaching to existing backend (no spawn)",
                port
            );
            Some((port, token))
        }
        None => {
            log::warn!(
                "[SETUP] VT_PYTHON_PORT={} is not a valid port, ignoring adopt env",
                port_raw
            );
            None
        }
    }
}

/// Spawn sidecar via Tauri externalBin / dev source, read `server_started`
/// from stdout. `shutting_down` lets the handshake loop abort mid-wait.
/// NOTE: see docs/code-notes/tauri-host.md#sidecar-spawn
pub(crate) async fn spawn_sidecar_and_get_port_with_shutdown(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: &AtomicBool,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    spawn_sidecar_and_get_port_inner(app, token, Some(shutting_down)).await
}

/// `shutting_down` is `Option<&AtomicBool>`: None = no-flag caller.
async fn spawn_sidecar_and_get_port_inner(
    app: &tauri::AppHandle,
    token: &str,
    shutting_down: Option<&AtomicBool>,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    if dev_mode::is_dev_mode() {
        let (port, child) = dev_mode::spawn_sidecar_dev_mode(token, shutting_down).await?;
        return Ok((port, child, None));
    }
    let (port, child, rx) = release_mode::spawn_sidecar_release(app, token, shutting_down).await?;
    Ok((port, child, Some(rx)))
}

/// Cold-start sidecar init (C-ARCH-1: logic lives here, not in main.rs).
/// Spawn → install state → reconnect_ws; fall back to supervisor on failure.
/// Adopted-backend mode: connect only; no child; no respawn.
pub(crate) async fn initialize_sidecar(
    app_handle: &tauri::AppHandle,
    state: Arc<crate::state::SidecarState>,
) {
    // Parent backend owns the process; kill/stop/respawn are no-ops.
    if let Some((port, token)) = adopted_backend_env() {
        *state.adopted_backend.lock().await = true;
        if let Err(e) = crate::sidecar::ws::reconnect_ws(app_handle, &state, port, &token).await {
            log::error!("[SETUP] initial WS connect to adopted backend failed: {}", e);
            // NO respawn fallback in adopted mode (would double-spawn parent).
        }
        return;
    }

    let token = crate::util::generate_token();

    match spawn_sidecar_and_get_port_with_shutdown(app_handle, &token, &state.shutting_down).await {
        Ok((port, child, exit_rx)) => {
            // Post-spawn shutting_down re-check: kill child that outlived the host.
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!(
                    "[SETUP] shutting_down set during sidecar spawn: \
                     killing freshly-spawned sidecar"
                );
                if let Err(e) = child.kill_tree().await {
                    log::warn!(
                        "[SETUP] kill_tree on freshly-spawned sidecar failed (best-effort): {}",
                        e
                    );
                }
                return;
            }
            *crate::state::lock(&state.child) = Some(child);
            *state.child_exit_rx.lock().await = exit_rx;
            if let Err(e) = crate::sidecar::ws::reconnect_ws(app_handle, &state, port, &token).await
            {
                log::error!("[SETUP] initial WS connect failed: {}", e);
                let _ = crate::sidecar::supervisor::respawn(app_handle, &state).await;
            }
        }
        Err(e) => {
            log::error!("[SETUP] sidecar spawn failed: {}", e);
            let _ = crate::sidecar::supervisor::respawn(app_handle, &state).await;
        }
    }
}

/// Panic-captured cold-start body for main.rs's background spawn.
/// C-ARCH-1 + C-TOKIO-1: AssertUnwindSafe(...).catch_unwind().await —
/// NEVER block_on (panics with "Cannot start a runtime from within a runtime").
/// Migration runs first so the sidecar boots against migrated data.
pub(crate) async fn initialize_sidecar_guarded(app_handle: tauri::AppHandle) {
    // ADR-0020 §8: one-time predecessor→Tauri migration on the blocking pool.
    crate::migrate::migrate_legacy_userdata_async(&app_handle).await;
    let state: tauri::State<'_, Arc<crate::state::SidecarState>> = app_handle.state();
    let state = state.inner().clone();
    // C-TOKIO-1: catch_unwind on the AssertUnwindSafe-wrapped future.
    let result = AssertUnwindSafe(initialize_sidecar(&app_handle, state))
        .catch_unwind()
        .await;
    if let Err(payload) = result {
        let msg = payload
            .downcast_ref::<&'static str>()
            .copied()
            .or_else(|| payload.downcast_ref::<String>().map(|s| s.as_str()))
            .unwrap_or("<non-string panic>");
        log::error!("[MAIN] initialize_sidecar task panicked: {}", msg);
    }
}

// C-TEST-5: sibling test file.
#[cfg(test)]
#[path = "spawn_tests.rs"]
mod spawn_tests;

// ─── Worker spawn (runtime-pack split) ────────────────
// Worker is a SECOND child with its own circuit breaker.
// Sidecar (not the host) owns the worker WS client connection.
// NOTE: see docs/code-notes/tauri-host.md#workerstate-runtime-pack-split

/// Spawn ML worker (release externalBin / dev python -m), read
/// `worker_started` from stdout. Returns same shape as sidecar spawn.
pub(crate) async fn spawn_worker_and_get_port_with_shutdown(
    app: &tauri::AppHandle,
    state: Arc<crate::state::WorkerState>,
    shutting_down: &AtomicBool,
) -> Result<(u16, SidecarHandle, Option<mpsc::Receiver<CommandEvent>>), String> {
    // Token set once by initialize_worker (OnceLock); respawn reuses it.
    let token = state
        .auth_token
        .get()
        .ok_or_else(|| "worker auth token not set: call initialize_worker first".to_string())?;

    if dev_mode::is_dev_mode() {
        let (port, child) = worker::spawn_worker_dev_mode(token, Some(shutting_down)).await?;
        return Ok((port, child, None));
    }
    let (port, child, rx) = worker::spawn_worker_release(app, token, Some(shutting_down)).await?;
    Ok((port, child, Some(rx)))
}

/// Cold-start worker init: spawn + install WorkerState. No host WS client
/// (slim-core sidecar owns that connection).
pub(crate) async fn initialize_worker(
    app_handle: &tauri::AppHandle,
    state: Arc<crate::state::WorkerState>,
) {
    // OnceLock: second call (respawn) reuses the host's token.
    state.auth_token.get_or_init(crate::util::generate_token);

    // Single-instance lock path for the worker process.
    state.lock_file_path.get_or_init(|| {
        crate::platform::worker_path::worker_exe_path().with_file_name("worker.lock")
    });

    let spawn_started = std::time::Instant::now();
    match spawn_worker_and_get_port_with_shutdown(app_handle, state.clone(), &state.shutting_down)
        .await
    {
        Ok((port, child, exit_rx)) => {
            // Capture the pid before the move into state (kill path below
            // consumes the child; the relay needs the pid after it).
            let worker_pid = child.pid();
            // Post-spawn shutting_down re-check (mirror initialize_sidecar).
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!(
                    "[WORKER-INIT] shutting_down set during worker spawn: \
                     killing freshly-spawned worker"
                );
                if let Err(e) = child.kill_tree().await {
                    log::warn!(
                        "[WORKER-INIT] kill_tree on freshly-spawned worker failed \
                         (best-effort): {}",
                        e
                    );
                }
                return;
            }
            *crate::state::lock(&state.child) = Some(child);
            // Drain wraps the real event channel so worker stderr cannot
            // block its writers. Future worker-respawn path must do the same.
            let exit_rx = exit_rx.map(|rx| event_drain::spawn_child_event_drain("[WORKER]", rx));
            *state.child_exit_rx.lock().await = exit_rx;
            // Crash→respawn supervisor owns the child from here on.
            // NOTE: see docs/code-notes/worker-lifecycle-policy.md
            super::worker_supervisor::spawn_worker_exit_watcher(app_handle, state.clone());
            // Never log the bearer token (ADR-0020 §3; pinned by
            // test_externalbin_spawn_windows.py).
            log::info!(
                "[WORKER-INIT] worker spawned (port={}){}: respawn supervisor active; \
                 worker WS client is the next phase (plan §7.2/§7.3)",
                port,
                super::lifecycle::format_duration_suffix(spawn_started.elapsed())
            );
            // ADR-0024 Step 2: relay the bind to the sidecar over the
            // existing host↔sidecar WS hop (fast path + bounded retry).
            // NOTE: see docs/code-notes/worker-port-relay.md#host-emit
            worker::relay_worker_started_to_sidecar(app_handle, worker_pid, port);
        }
        Err(e) => {
            log::error!("[WORKER-INIT] worker spawn failed: {}", e);
        }
    }
}
