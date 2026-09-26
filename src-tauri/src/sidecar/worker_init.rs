//! Worker cold-start wiring (runtime-pack split).
//! C-ARCH-1: `main.rs` spawns `initialize_worker_guarded` only; the
//! trigger gate + spawn live here and in `spawn/worker.rs`.
//! C-TOKIO-1: panic capture is `catch_unwind().await`, never `block_on`.

use crate::state::WorkerState;
use std::panic::AssertUnwindSafe;
use std::sync::Arc;
use tauri::Manager;

// C-TOKIO-1: catch_unwind on the future, never block_on inside a runtime worker.
use futures_util::future::FutureExt;

/// Cold-start body: shared pack/binary gate, skips quietly when the
/// pack was never downloaded (the pack-verified path starts it later).
pub(crate) async fn initialize_worker_cold_start(app_handle: &tauri::AppHandle) {
    let started = std::time::Instant::now();
    let state: tauri::State<'_, Arc<WorkerState>> = app_handle.state();
    crate::sidecar::spawn::worker::start_worker_if_ready(app_handle, state.inner().clone()).await;
    log::info!(
        "[WORKER-INIT] cold-start worker init settled{}",
        crate::sidecar::lifecycle::format_duration_suffix(started.elapsed())
    );
}

/// Panic-captured cold-start entry point for `main.rs`'s background spawn.
pub(crate) async fn initialize_worker_guarded(app_handle: tauri::AppHandle) {
    let result = AssertUnwindSafe(initialize_worker_cold_start(&app_handle))
        .catch_unwind()
        .await;
    if let Err(payload) = result {
        let msg = payload
            .downcast_ref::<&'static str>()
            .copied()
            .or_else(|| payload.downcast_ref::<String>().map(|s| s.as_str()))
            .unwrap_or("<non-string panic>");
        log::error!("[WORKER-INIT] initialize_worker task panicked: {}", msg);
    }
}
