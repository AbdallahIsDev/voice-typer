//! FT-1 supervisor: respawn + bubble-level coalesce (ADR-0020 §9 + §10).

use crate::state::SidecarState;
use crate::sidecar::spawn::spawn_sidecar_and_get_port;
use crate::sidecar::ws::reconnect_ws;
use crate::util::{generate_token, FT1_BACKOFF_MS, FT1_MAX_RETRIES, PRE_RESTART_DELAY_MS};
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use serde_json::json;
use tauri::Emitter;

// ─── FT-1 supervisor (ADR-0020 §10) ───────────────────────────────────

pub(crate) async fn ft1_respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    // Serialize: only one ft1_respawn may run at a time. If a previous
    // respawn is still in flight (e.g., the sidecar died again mid-
    // reconnect), bail out — the in-flight supervisor owns the recovery.
    if state
        .respawn_in_progress
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        log::info!("[FT-1] respawn already in progress — skipping");
        return Ok(());
    }
    // Scope the respawn body so we can clear the flag on every exit path
    // (including the `app.restart()` paths, which are `-> !` so the
    // clear is unreachable but harmless; the Ok() paths need it).
    let result = ft1_respawn_inner(app, state).await;
    state.respawn_in_progress.store(false, Ordering::SeqCst);
    result
}

pub(crate) async fn ft1_respawn_inner(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate() {
        if attempt as u32 >= FT1_MAX_RETRIES {
            log::error!(
                "[FT-1] exhausted {} retries — falling back to full-app relaunch",
                FT1_MAX_RETRIES
            );
            // ADR-0020 §10: full-app relaunch. Emit a Tauri event so
            // the UI can show a "restarting…" banner, then call
            // app.restart() which exits the current process and
            // relaunches a fresh one.
            let _ = app.emit("ft1_relaunching", json!({"reason": "exhausted_retries"}));
            // Small delay so the UI event can render before restart.
            tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
            // ADR-0020 §10: `app.restart()` is defined on the core
            // `tauri::AppHandle` directly (tauri-2.11.5/src/app.rs:588).
            // It exits with RESTART_EXIT_CODE so the Tauri launcher
            // spawns a fresh instance before the old one fully exits.
            // Returns `!` (never type) so following code is unreachable.
            app.restart();
        }
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[FT-1] shutting down — skipping respawn");
            return Ok(());
        }
        log::warn!("[FT-1] respawn attempt {} after {}ms", attempt + 1, delay_ms);
        tokio::time::sleep(Duration::from_millis(*delay_ms)).await;

        // Rotate token + respawn.
        let new_token = generate_token();
        match spawn_sidecar_and_get_port(app, &new_token).await {
            Ok((port, child, exit_rx)) => {
                // Drop the MutexGuards BEFORE awaiting reconnect_ws so
                // the future is Send (std::sync::MutexGuard is !Send).
                {
                    let mut child_guard = state.child.lock().unwrap();
                    *child_guard = Some(child);
                }
                {
                    let mut token_guard = state.token.lock().unwrap();
                    *token_guard = new_token.clone();
                }
                // CR-2: rotate the event receiver so the next
                // shutdown_sidecar call polls the new sidecar's exit.
                {
                    let mut rx_guard = state.child_exit_rx.lock().await;
                    *rx_guard = exit_rx;
                }
                // Reconnect WS + re-auth.
                match reconnect_ws(app, state, port, &new_token).await {
                    Ok(()) => {
                        log::info!("[FT-1] respawn succeeded on attempt {}", attempt + 1);
                        // Emit a Tauri event so the UI can clear its
                        // "reconnecting…" banner.
                        let _ = app.emit("ft1_reconnected", json!({}));
                        return Ok(());
                    }
                    Err(e) => {
                        log::warn!("[FT-1] WS reconnect failed: {}", e);
                        continue;
                    }
                }
            }
            Err(e) => {
                log::warn!("[FT-1] sidecar spawn failed: {}", e);
                continue;
            }
        }
    }
    // Loop exited without returning — this happens if FT1_BACKOFF_MS
    // is shorter than FT1_MAX_RETRIES. Treat as exhaustion.
    log::error!("[FT-1] backoff schedule exhausted — full-app relaunch");
    let _ = app.emit("ft1_relaunching", json!({"reason": "backoff_exhausted"}));
    tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
    app.restart();
}

// ─── Bubble-level coalesce predicate (ADR-0020 §9) ───────────────────

/// Pure form of the bubble_level coalesce decision used by the WS
/// reader task (ADR-0020 §9). Returns `true` if the current event
/// should be emitted given the last-emitted timestamp and the target
/// Hz rate. Extracted from `reconnect_ws`'s inline coalesce logic so
/// unit tests can verify the 30 Hz cap without spinning up a Tauri
/// runtime + mock WS server.
///
/// The min interval is `Duration::from_millis(1000 / hz)` — for the
/// default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33ms (integer
/// division), so a 60 Hz input stream emits every other event = 30 Hz.
pub(crate) fn bubble_coalesce_should_emit(
    last_emitted: Option<Instant>,
    now: Instant,
    hz: u64,
) -> bool {
    last_emitted.map_or(true, |t| {
        now.duration_since(t) >= Duration::from_millis(1000 / hz)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::BUBBLE_LEVEL_COALESCE_HZ;
    use std::time::{Duration, Instant};

    // ── CR-13: bubble_level coalesce (ADR-0020 §9) ───────────────────

    #[test]
    fn test_bubble_coalesce_should_emit_first_event() {
        // First event (no prior emit) → always emit.
        let now = Instant::now();
        assert!(bubble_coalesce_should_emit(None, now, BUBBLE_LEVEL_COALESCE_HZ));
    }

    #[test]
    fn test_bubble_coalesce_should_emit_respects_min_interval() {
        // With hz=30, min_interval = 33ms. An event 32ms after the last
        // emit should be suppressed; an event 33ms after should pass.
        let start = Instant::now();
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        // 32ms gap → suppressed.
        let too_soon = start + Duration::from_millis(32);
        assert!(
            !bubble_coalesce_should_emit(Some(start), too_soon, hz),
            "event 32ms after last emit should be suppressed (min_interval=33ms)"
        );
        // 33ms gap → emitted (>= comparison).
        let just_enough = start + Duration::from_millis(33);
        assert!(
            bubble_coalesce_should_emit(Some(start), just_enough, hz),
            "event 33ms after last emit should pass (min_interval=33ms, >= comparison)"
        );
        // 100ms gap → emitted.
        let well_after = start + Duration::from_millis(100);
        assert!(
            bubble_coalesce_should_emit(Some(start), well_after, hz),
            "event 100ms after last emit should pass"
        );
    }

    #[test]
    fn test_bubble_level_coalesce_respects_30hz_cap() {
        // Simulate a 60 Hz event stream for ~1 second (60 events, ~16.67ms
        // apart). With BUBBLE_LEVEL_COALESCE_HZ=30 (min interval 33ms),
        // every other event passes the filter → exactly 30 emits per
        // simulated second, hitting the cap without exceeding it.
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        let start = Instant::now();
        let step_60hz = Duration::from_micros(16_667); // ~16.67ms = 1/60 s
        let mut last_emitted: Option<Instant> = None;
        let mut emitted = 0usize;
        for i in 0..60u32 {
            let now = start + step_60hz * i;
            if bubble_coalesce_should_emit(last_emitted, now, hz) {
                last_emitted = Some(now);
                emitted += 1;
            }
        }
        assert!(
            emitted <= 30,
            "emitted {} events in 1s, expected ≤30 (30 Hz cap)",
            emitted
        );
        // The 60 Hz stream downsampled to a 30 Hz cap should emit ~30
        // events per second (exactly 30 with 16.667ms spacing — every
        // other event). Allow a small ±2 tolerance in case integer
        // division edges shift the boundary by one.
        assert!(
            emitted >= 28,
            "emitted {} events in 1s, expected ~30 — coalesce is too aggressive",
            emitted
        );
    }
}
