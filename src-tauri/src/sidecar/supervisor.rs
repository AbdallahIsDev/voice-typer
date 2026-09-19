//! Sidecar supervisor: respawn + backoff (ADR-0020 §10).
//! Owns ONLY respawn / backoff / restart-counter logic.
//! NOTE: see docs/code-notes/tauri-host.md#supervisor-respawn

use crate::state::SidecarState;
use crate::sidecar::spawn::spawn_sidecar_and_get_port_with_shutdown;
use crate::sidecar::ws::reconnect_ws;
use crate::state::lock as mutex_lock;
use crate::util::{
    atomic_write_bytes, generate_token, PRE_RESTART_DELAY_MS, SHUTDOWN_ACK_TIMEOUT_MS,
    SUPERVISOR_BACKOFF_MS,
};
// FutureExt brings `.catch_unwind()` into scope.
use futures_util::FutureExt;
use serde_json::json;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::Emitter;

/// Circuit-breaker max `app.restart()` attempts before `supervisor_failed`.
/// Reset on successful reconnect. 3 rides out transient crashes without
/// masking a permanently-broken install.
pub(super) const MAX_RESTART_ATTEMPTS: u32 = 3;

/// Stale-count cutoff (seconds). A counter older than this is treated as 0
/// so an old session's failures don't trip the breaker on a new crash.
pub(super) const COUNTER_STALE_SECS: u64 = 10 * 60;

/// Unix time in seconds; 0 on pre-epoch clock skew.
pub(super) fn now_unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Parse restart counter from JSON with a saturating cast (corrupt huge
/// counts must still trip the breaker, not wrap).
pub(crate) fn parse_restart_counter(v: &serde_json::Value) -> u32 {
    v.get("count")
        .and_then(|c| c.as_u64())
        .map(|c| u32::try_from(c).unwrap_or(u32::MAX))
        .unwrap_or(0)
}

/// Read disk-persisted restart counter; fail-open to 0 on any error.
/// Missing/legacy `ts` or stale `ts` → count treated as 0.
pub(super) fn read_restart_counter() -> u32 {
    let path = match crate::platform::paths::config_dir() {
        p if p.as_os_str().is_empty() => return 0,
        p => p.join("restart_counter.json"),
    };
    match std::fs::read_to_string(&path) {
        Ok(s) => {
            let v: serde_json::Value = match serde_json::from_str(&s) {
                Ok(v) => v,
                Err(_) => return 0,
            };
            // Stale/missing ts → treat as 0 (don't penalize upgrades).
            let ts = v.get("ts").and_then(|t| t.as_u64()).unwrap_or(0);
            if ts == 0 {
                return 0;
            }
            let now = now_unix_secs();
            if now < ts || now - ts > COUNTER_STALE_SECS {
                log::info!(
                    "[SUPERVISOR] restart counter stale (ts={}, now={}, age={}s > {}s): resetting to 0",
                    ts,
                    now,
                    now.saturating_sub(ts),
                    COUNTER_STALE_SECS
                );
                return 0;
            }
            parse_restart_counter(&v)
        }
        Err(_) => 0,
    }
}

/// write the disk-persisted restart counter (`{count, ts}`).
/// The counter is NOT reset on a fresh app launch; the only reset is on
/// reconnect-success. Best-effort atomic write; do NOT merge with
/// predecessor `restart_history.json` (different runtime/schema/lifecycle).
pub(crate) fn write_restart_counter(count: u32) {
    let path = match crate::platform::paths::config_dir() {
        p if p.as_os_str().is_empty() => return,
        p => p.join("restart_counter.json"),
    };
    let payload = json!({"count": count, "ts": now_unix_secs()});
    if let Err(e) = atomic_write_bytes(&path, payload.to_string().as_bytes()) {
        log::warn!(
            "[SUPERVISOR] failed to persist restart counter to {:?}: {}",
            path,
            e
        );
    }
}

/// Clear restart counter on a USER-INITIATED restart only (tray Restart).
/// Never call from supervisor exhaustion — that would defeat the breaker.
/// Callers: `sidecar/lifecycle.rs::on_relaunch_app` (production + dev).
pub(crate) fn clear_restart_counter_for_user_restart(_state: &Arc<SidecarState>) {
    log::info!(
        "[SUPERVISOR] user-initiated restart requested: clearing persisted restart counter \
         (was {}) so the next respawn gets a fresh attempt budget",
        read_restart_counter()
    );
    write_restart_counter(0);
}

// ─── Power suspend / resume sidecar actions ─────────────

/// OS suspend stop. Does NOT call `begin_shutdown` — host is freezing, not
/// quitting; arming shutting_down would brick resume respawn.
/// Abort heartbeat → cooperative shutdown frame → short wait → force-kill.
/// Adopted-backend mode: no-op. Caller must run on tokio runtime.
pub(crate) async fn stop_sidecar_for_suspend(state: &Arc<SidecarState>) {
    if *state.adopted_backend.lock().await {
        log::info!(
            "[POWER] adopted-backend mode: suspend stop is a no-op (backend is our parent)"
        );
        return;
    }
    if state.shutting_down.load(Ordering::SeqCst) {
        log::info!("[POWER] host already shutting down: suspend stop skipped");
        return;
    }
    {
        let mut hb_guard = state.heartbeat_handle.lock().await;
        if let Some(handle) = hb_guard.take() {
            handle.abort();
            log::info!("[POWER] aborted heartbeat task before suspend stop");
        }
    }
    let frame = serde_json::json!({"type": "shutdown"});
    if let Some(ws_tx) = mutex_lock(&state.ws_tx).clone() {
        if let Err(e) = ws_tx.try_send(tokio_tungstenite::tungstenite::Message::Text(
            frame.to_string().into(),
        )) {
            log::warn!(
                "[POWER] try_send of shutdown frame failed (best-effort): {}",
                e
            );
        }
    } else {
        log::info!("[POWER] no ws_tx, skipping cooperative shutdown frame");
    }
    let deadline = Duration::from_millis(SHUTDOWN_ACK_TIMEOUT_MS);
    let rx_opt = {
        let mut rx_guard = state.child_exit_rx.lock().await;
        rx_guard.take()
    };
    if let Some(mut rx) = rx_opt {
        match tokio::time::timeout(deadline, rx.recv()).await {
            Ok(Some(tauri_plugin_shell::process::CommandEvent::Terminated(payload))) => {
                log::info!(
                    "[POWER] sidecar exited gracefully on suspend (code={:?})",
                    payload.code
                );
            }
            Ok(_) | Err(_) => {
                log::info!(
                    "[POWER] sidecar did not exit within {}ms: force-killing",
                    SHUTDOWN_ACK_TIMEOUT_MS
                );
            }
        }
    }
    let child_opt = mutex_lock(&state.child).take();
    if let Some(child) = child_opt {
        if let Err(e) = child.kill_tree().await {
            log::warn!("[POWER] suspend kill_tree failed (best-effort): {}", e);
        }
    }
    // Drop writer so dispatches fail fast instead of queueing into a frozen peer.
    *mutex_lock(&state.ws_tx) = None;
    log::info!("[POWER] sidecar stopped for suspend");
}

/// Ensure sidecar runs after OS resume. No-op if WS still live.
/// Otherwise request one supervisor respawn so wake recovers immediately.
/// Adopted-backend / shutting_down: no-op.
pub(crate) async fn ensure_sidecar_after_resume(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) {
    if *state.adopted_backend.lock().await {
        log::info!(
            "[POWER] adopted-backend mode: resume ensure is a no-op (backend is our parent)"
        );
        return;
    }
    if state.shutting_down.load(Ordering::SeqCst) {
        log::info!("[POWER] host shutting down: resume ensure skipped");
        return;
    }
    if mutex_lock(&state.ws_tx).is_some() {
        log::info!("[POWER] sidecar already connected on resume: ensure is a no-op");
        return;
    }
    log::info!("[POWER] resume: requesting sidecar respawn");
    if let Err(e) = respawn(app, state).await {
        log::warn!(
            "[POWER] resume respawn failed (supervisor/backoff will retry): {}",
            e
        );
    }
}

// ─── Supervisor (ADR-0020 §10) ───────────────────────────────────

/// Serialize respawns; run `respawn_inner` under panic capture so
/// `respawn_in_progress` is always cleared.
/// NOTE: see docs/code-notes/tauri-host.md#supervisor-respawn
pub(crate) async fn respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    // MO-110: adopted backend is our PARENT — do not double-spawn.
    if *state.adopted_backend.lock().await {
        log::info!(
            "[SUPERVISOR] adopted-backend mode (VT_PYTHON_PORT attach): respawn disabled"
        );
        return Ok(());
    }
    // MO-126: mid-sleep spawn would target a frozen process; resume path owns recovery.
    if state.power_suspended.load(Ordering::SeqCst) {
        log::info!("[SUPERVISOR] host is suspended: skipping respawn");
        return Ok(());
    }
    // Only one respawn at a time.
    if state
        .respawn_in_progress
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        log::info!("[SUPERVISOR] respawn already in progress, skipping");
        return Ok(());
    }
    // Re-check shutting_down BEFORE disk I/O so a concurrent shutdown
    // cannot spuriously bump the restart counter.
    if state.shutting_down.load(Ordering::SeqCst) {
        log::info!("[SUPERVISOR] shutting down (post-flag-acquisition, pre-I/O): skipping respawn");
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        return Ok(());
    }
    // Circuit breaker: check EXISTING persisted counter (no increment here —
    // increment lives in respawn_inner's exhaustion path). Disk read via
    // spawn_blocking (C-TOKIO-1: never stall a runtime worker on fs I/O).
    let restart_count = tauri::async_runtime::spawn_blocking(read_restart_counter)
        .await
        .unwrap_or_else(|join_err| {
            log::warn!(
                "[SUPERVISOR] spawn_blocking(read_restart_counter) join failed: {}, treating as 0 (fail-open)",
                join_err
            );
            0
        });
    if restart_count >= MAX_RESTART_ATTEMPTS {
        log::error!(
            "[SUPERVISOR] circuit breaker tripped: restart count {} >= max {}. Stopping supervisor.",
            restart_count,
            MAX_RESTART_ATTEMPTS
        );
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "supervisor_failed",
            json!({
                "reason": "circuit_breaker_tripped",
                "restart_count": restart_count,
                "message": format!(
                    "{} could not start its backend after multiple attempts. Please reinstall.",
                    crate::branding::APP_NAME
                )
            }),
        );
        return Err(format!(
            "Supervisor circuit breaker tripped (restart_count={})",
            restart_count
        ));
    }
    // Panic capture so a panic inside respawn_inner cannot leave
    // respawn_in_progress stuck forever (bricking the resilience layer).
    let inner_result = AssertUnwindSafe(respawn_inner(app, state))
        .catch_unwind()
        .await;
    match inner_result {
        Ok(r) => r,
        Err(panic_payload) => {
            let msg = panic_payload
                .downcast_ref::<&'static str>()
                .copied()
                .or_else(|| panic_payload.downcast_ref::<String>().map(|s| s.as_str()))
                .unwrap_or("<non-string panic payload>");
            log::error!(
                "[SUPERVISOR] respawn_inner panicked: {}, clearing respawn_in_progress \
                 so future respawns can proceed",
                msg
            );
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            Err(format!("respawn_inner panicked: {}", msg))
        }
    }
}

/// Backoff loop: spawn + reconnect; on exhaustion increment counter and
/// `app.restart()`. Every return path must clear `respawn_in_progress`.
/// NOTE: see docs/code-notes/tauri-host.md#supervisor-respawn
pub(crate) async fn respawn_inner(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    // Last per-iteration error for supervisor_relaunching/failed payloads.
    let mut last_error = String::new();
    for (attempt, delay_ms) in SUPERVISOR_BACKOFF_MS.iter().enumerate() {
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[SUPERVISOR] shutting down, skipping respawn");
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }
        log::warn!(
            "[SUPERVISOR] respawn attempt {} after {}ms",
            attempt + 1,
            delay_ms
        );
        // Cancellable backoff: select! on sleep vs shutdown_notify.
        // Notify stores a permit if no waiter is registered (no race).
        let sleep_target = tokio::time::Instant::now() + Duration::from_millis(*delay_ms);
        loop {
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!("[SUPERVISOR] shutting down during backoff sleep, aborting respawn");
                state.respawn_in_progress.store(false, Ordering::SeqCst);
                return Ok(());
            }
            let now = tokio::time::Instant::now();
            if now >= sleep_target {
                break;
            }
            let remaining = sleep_target - now;
            tokio::select! {
                _ = tokio::time::sleep(remaining) => {
                    // Re-loop so top-of-loop shutting_down check runs before spawn.
                }
                _ = state.shutdown_notify.notified() => {
                    log::info!(
                        "[SUPERVISOR] shutdown_notify fired during backoff sleep: re-checking shutting_down"
                    );
                }
            }
        }

        // Re-check before spawn (top-of-loop check may be stale after sleep).
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[SUPERVISOR] shutting down (pre-spawn re-check), skipping respawn");
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }

        // Kill OLD child first: ShellPlugin child does NOT kill on Drop —
        // without this the old Python sidecar is orphaned (mic/port held).
        let old_child = mutex_lock(&state.child).take();
        if let Some(old) = old_child {
            log::info!("[SUPERVISOR] killing old sidecar before respawn");
            let _ = old.kill_tree().await;
        }

        // Fresh auth token per spawn.
        let new_token = generate_token();
        // shutting_down flag lets the stdout-read loop short-circuit mid-handshake.
        match spawn_sidecar_and_get_port_with_shutdown(app, &new_token, &state.shutting_down).await
        {
            Ok((port, child, exit_rx)) => {
                // Atomic install: hold state.child lock, re-check shutting_down
                // INSIDE the lock, then install or kill. Lock released before
                // any await (std MutexGuard is !Send).
                let mut child = Some(child);
                let old_handle = {
                    let mut child_guard = mutex_lock(&state.child);
                    if state.shutting_down.load(Ordering::SeqCst) {
                        log::info!(
                            "[SUPERVISOR] shutting down (post-spawn re-check inside lock): killing freshly-spawned sidecar instead of installing"
                        );
                        None
                    } else {
                        let old = child_guard.take();
                        if let Some(new_child) = child.take() {
                            *child_guard = Some(new_child);
                        }
                        old
                    }
                }; // child_guard dropped: no !Send across await
                if let Some(c) = child {
                    // shutting_down was true: child was NOT installed.
                    if let Err(e) = c.kill_tree().await {
                        log::warn!(
                            "[SUPERVISOR] freshly-spawned child kill_tree failed (best-effort): {}",
                            e
                        );
                    }
                    state.respawn_in_progress.store(false, Ordering::SeqCst);
                    return Ok(());
                }
                if let Some(old) = old_handle {
                    log::info!("[SUPERVISOR] killing old sidecar before installing new one");
                    let _ = old.kill_tree().await;
                }
                // Rotate exit receiver so shutdown polls the new sidecar.
                {
                    let mut rx_guard = state.child_exit_rx.lock().await;
                    *rx_guard = exit_rx;
                }
                // Reconnect WS + re-auth.
                match reconnect_ws(app, state, port, &new_token).await {
                    Ok(()) => {
                        log::info!("[SUPERVISOR] respawn succeeded on attempt {}", attempt + 1);
                        // Reset counter off the async worker (atomic write + fsync).
                        // Awaited so reset completes BEFORE supervisor_reconnected emit.
                        if let Err(join_err) =
                            tauri::async_runtime::spawn_blocking(|| write_restart_counter(0)).await
                        {
                            log::warn!(
                                "[SUPERVISOR] spawn_blocking(write_restart_counter(0)) join \
                                 failed: {}, counter keeps its prior value (best-effort)",
                                join_err
                            );
                        }
                        let _ = app.emit("supervisor_reconnected", json!({}));
                        // Clear flag BEFORE return so a fast-double-crash from the
                        // new reader can acquire it immediately.
                        state.respawn_in_progress.store(false, Ordering::SeqCst);
                        return Ok(());
                    }
                    Err(e) => {
                        log::warn!("[SUPERVISOR] WS reconnect failed: {}", e);
                        last_error = format!("attempt {}: WS reconnect failed: {}", attempt + 1, e);
                        // Kill just-spawned child before next retry (avoid orphan).
                        let orphan = mutex_lock(&state.child).take();
                        if let Some(c) = orphan {
                            log::info!(
                                "[SUPERVISOR] killing respawned sidecar after WS reconnect failure"
                            );
                            let _ = c.kill_tree().await;
                        }
                        continue;
                    }
                }
            }
            Err(e) => {
                // "shutdown" sentinel: stdout loop saw shutting_down mid-spawn.
                if e == "shutdown" {
                    log::info!(
                        "[SUPERVISOR] spawn loop detected shutting_down: exiting respawn cleanly"
                    );
                    state.respawn_in_progress.store(false, Ordering::SeqCst);
                    return Ok(());
                }
                log::warn!("[SUPERVISOR] sidecar spawn failed: {}", e);
                last_error = format!("attempt {}: sidecar spawn failed: {}", attempt + 1, e);
                continue;
            }
        }
    }
    // Exhaustion path: increment counter HERE (immediately before app.restart())
    // so the breaker trips on the 3rd actual relaunch, not on every respawn call.
    // Disk read+write bundled in one spawn_blocking closure (C-TOKIO-1).
    log::error!(
        "[SUPERVISOR] backoff schedule exhausted: full-app relaunch (last_error={:?})",
        last_error
    );
    let new_count = tauri::async_runtime::spawn_blocking(|| {
        let prior = read_restart_counter();
        let next = prior.saturating_add(1);
        write_restart_counter(next);
        next
    })
    .await
    .unwrap_or_else(|join_err| {
        log::warn!(
            "[SUPERVISOR] spawn_blocking(read+write_restart_counter) join failed: {}, assuming count=1 (fail-open, breaker may under-trip)",
            join_err
        );
        1
    });
    if new_count >= MAX_RESTART_ATTEMPTS {
        log::error!(
            "[SUPERVISOR] circuit breaker tripped on exhaustion: restart count {} >= max {}. Stopping supervisor.",
            new_count,
            MAX_RESTART_ATTEMPTS
        );
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "supervisor_failed",
            json!({
                "reason": "circuit_breaker_tripped",
                "restart_count": new_count,
                "last_error": last_error,
                "message": format!(
                    "{} could not start its backend after multiple attempts. Please reinstall.",
                    crate::branding::APP_NAME
                )
            }),
        );
        return Err(format!(
            "Supervisor circuit breaker tripped on exhaustion (restart_count={}, last_error={})",
            new_count, last_error
        ));
    }
    let _ = app.emit(
        "supervisor_relaunching",
        json!({
            "reason": "backoff_exhausted",
            "last_error": last_error,
            "restart_count": new_count
        }),
    );
    // Mark host shutdown BEFORE relaunch so racing respawns abort and
    // RunEvent::Exit teardown is idempotent. Sidecar is already dead here.
    if state.begin_shutdown() {
        log::info!(
            "[SUPERVISOR] host shutdown already in flight: full-app relaunch proceeds \
             (respawns stay disabled)"
        );
    } else {
        log::info!(
            "[SUPERVISOR] full-app relaunch: marking host shutdown (respawns disabled \
             during the restart window)"
        );
    }
    // Clear flag immediately before app.restart() (defense-in-depth).
    state.respawn_in_progress.store(false, Ordering::SeqCst);
    tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
    app.restart();
}
