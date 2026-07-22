//! FT-1 supervisor: respawn + bubble-level coalesce (ADR-0020 §9 + §10).

use crate::state::SidecarState;
// G4-H-27 (session 4): poison-safe Mutex helper. Replacing
// `state.X.lock().unwrap()` with `mutex_lock(&state.X)` so a poisoned
// mutex (a prior panic while holding the lock) doesn't re-panic and
// brick the FT-1 resilience layer.
use crate::state::lock as mutex_lock;
use crate::sidecar::spawn::spawn_sidecar_and_get_port;
use crate::sidecar::ws::reconnect_ws;
use crate::util::{generate_token, FT1_BACKOFF_MS, PRE_RESTART_DELAY_MS};
// PVT-G5-033: reuse the migration module's atomic write helper so the
// restart counter is durable against mid-write crashes (see
// `write_ft1_restart_counter` below).
use crate::migrate::atomic_write_bytes;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use serde_json::json;
use tauri::Emitter;

/// CR-29: max number of `app.restart()` attempts before the FT-1
/// supervisor gives up and emits `ft1_failed` instead of looping
/// forever. Each `ft1_respawn` call increments a disk-persisted
/// counter; on successful `ft1_reconnected` the counter resets to 0.
/// 3 attempts is enough to ride out transient sidecar crashes without
/// masking a permanently-broken install (missing binary, corrupt env).
const FT1_MAX_RESTART_ATTEMPTS: u32 = 3;

/// G4-H-28: stale-count cutoff. The disk-persisted FT-1 restart
/// counter now carries a Unix timestamp (seconds). If the timestamp
/// is older than this many seconds, the count is treated as 0 — a
/// stale counter from a previous session (e.g., the user had 2 FT-1
/// failures last week) doesn't trip the circuit breaker on a single
/// new crash. 10 minutes is long enough to catch a tight flap loop
/// (3 crashes within 10 minutes is clearly a broken install) but
/// short enough to not accumulate across sessions.
const FT1_COUNTER_STALE_SECS: u64 = 10 * 60;

/// G4-H-28 helper: current Unix time in seconds. Returns 0 on
/// pre-epoch clock skew (won't happen in practice but the
/// `duration_since` API requires handling it).
fn now_unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// PVT-G5-051: parse the FT-1 restart counter from a JSON value with
/// a SATURATING cast. Previously the reader used `c as u32` after
/// `as_u64()`, which silently truncates any u64 value above `u32::MAX`
/// (e.g., a corrupted counter file with an absurdly large `count`
/// field would wrap to a small number, bypassing the circuit breaker).
/// Saturating at `u32::MAX` keeps the value well above
/// `FT1_MAX_RESTART_ATTEMPTS` (3) so the breaker trips correctly.
///
/// Extracted as a `pub(crate)` helper so unit tests can verify the
/// saturating behavior without touching the filesystem.
pub(crate) fn parse_ft1_counter(v: &serde_json::Value) -> u32 {
    v.get("count")
        .and_then(|c| c.as_u64())
        .map(|c| u32::try_from(c).unwrap_or(u32::MAX))
        .unwrap_or(0)
}

/// CR-29: read the disk-persisted FT-1 restart counter. Returns 0 on
/// any error (missing file, parse error, etc.) — fail-open is safer
/// than blocking recovery on a transient disk issue.
///
/// PVT-G5-087: dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only reads a disk file and never touches
/// the shared state. All call sites updated.
///
/// G4-H-28: the counter file now carries a `ts` field (Unix seconds).
/// If `ts` is older than `FT1_COUNTER_STALE_SECS` (10 minutes), the
/// count is treated as 0 — a stale count from a previous session
/// doesn't trip the circuit breaker on a single new crash.
fn read_ft1_restart_counter() -> u32 {
    let path = match crate::platform::paths::config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
        std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
    ) {
        p if p.as_os_str().is_empty() => return 0,
        p => p.join("ft1_restart_counter.json"),
    };
    match std::fs::read_to_string(&path) {
        Ok(s) => {
            let v: serde_json::Value = match serde_json::from_str(&s) {
                Ok(v) => v,
                Err(_) => return 0,
            };
            // G4-H-28: stale-count cutoff. If the timestamp is missing
            // (legacy file from before this fix) or older than
            // FT1_COUNTER_STALE_SECS, treat the count as 0.
            let ts = v.get("ts").and_then(|t| t.as_u64()).unwrap_or(0);
            if ts == 0 {
                // No timestamp → legacy file. Assume fresh (count=0) to
                // avoid penalizing users who upgrade mid-session.
                return 0;
            }
            let now = now_unix_secs();
            if now < ts || now - ts > FT1_COUNTER_STALE_SECS {
                log::info!(
                    "[FT-1] restart counter stale (ts={}, now={}, age={}s > {}s) — resetting to 0",
                    ts,
                    now,
                    now.saturating_sub(ts),
                    FT1_COUNTER_STALE_SECS
                );
                return 0;
            }
            parse_ft1_counter(&v)
        }
        Err(_) => 0,
    }
}

/// CR-29: write the disk-persisted FT-1 restart counter. Best-effort
/// — if the write fails, log and continue (the counter is a safety
/// gate, not a correctness requirement).
///
/// PVT-G5-087: dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only writes a disk file and never touches
/// the shared state. All call sites updated.
///
/// PVT-G5-033: switched from non-atomic `std::fs::write` (truncate-
/// then-write) to `atomic_write_bytes` (temp + fsync + rename). A
/// crash mid-write previously could leave a partially-written
/// counter file that fails to parse on next launch — `read_ft1_restart_counter`
/// then returns 0, silently bypassing the circuit breaker. Atomic
/// write guarantees the counter is either fully-old or fully-new.
///
/// PVT-3 (session 1): promoted from `fn` to `pub(crate) fn` so
/// `main.rs` can reset the counter to 0 at the start of each fresh
/// app launch (in `.setup`, before `spawn_sidecar_and_get_port`).
/// Without that reset, 3 consecutive bad cold-starts (transient AV
/// quarantine, slow disk, etc.) brick the install permanently — the
/// 4th launch reads `count: 3`, trips the breaker, emits `ft1_failed`,
/// and the user has no recovery short of manually deleting
/// `ft1_restart_counter.json`.
///
/// G4-H-28 (session 4): the counter file now includes a `ts` field
/// (Unix seconds) so `read_ft1_restart_counter` can detect + ignore
/// stale counts from previous sessions. `write_ft1_restart_counter(0)`
/// is called both on successful FT-1 reconnect (the existing CR-29
/// path) AND on successful cold start (the new G4-H-28 path) so the
/// counter doesn't accumulate stale failures across sessions.
pub(crate) fn write_ft1_restart_counter(count: u32) {
    let path = match crate::platform::paths::config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
        std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
    ) {
        p if p.as_os_str().is_empty() => return,
        p => p.join("ft1_restart_counter.json"),
    };
    // G4-H-28: include `ts` so future reads can detect staleness.
    let payload = json!({"count": count, "ts": now_unix_secs()});
    if let Err(e) = atomic_write_bytes(&path, payload.to_string().as_bytes()) {
        log::warn!("[FT-1] failed to persist restart counter to {:?}: {}", path, e);
    }
}

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
    // CR-29: circuit breaker — persist restart-attempt counter to
    // disk so we don't enter an infinite restart loop on a broken
    // install (missing sidecar binary, corrupted Python env, etc.).
    // If counter >= FT1_MAX_RESTART_ATTEMPTS, STOP the loop and emit
    // a `ft1_failed` event so the UI can surface the error instead of
    // silently restart-looping forever. Counter is reset on successful
    // `ft1_reconnected` event.
    let restart_count = read_ft1_restart_counter();
    if restart_count >= FT1_MAX_RESTART_ATTEMPTS {
        log::error!(
            "[FT-1] circuit breaker tripped — restart count {} >= max {}. Stopping FT-1 supervisor.",
            restart_count,
            FT1_MAX_RESTART_ATTEMPTS
        );
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "ft1_failed",
            json!({
                "reason": "circuit_breaker_tripped",
                "restart_count": restart_count,
                "message": "Voice Typer could not start its backend after multiple attempts. Please reinstall."
            }),
        );
        return Err(format!(
            "FT-1 circuit breaker tripped (restart_count={})",
            restart_count
        ));
    }
    // Increment counter before attempting restart — will be reset on
    // successful reconnect.
    write_ft1_restart_counter(restart_count + 1);
    // PVT-G5-031: DO NOT clear `respawn_in_progress` here unconditionally.
    // The inner function `ft1_respawn_inner` is responsible for clearing
    // the flag on its success path (line ~269, before `return Ok(())`)
    // so that a fast-double-crash disconnect detected by the freshly-
    // spawned WS reader can immediately acquire the flag and start its
    // own respawn. The circuit-breaker path above (line ~96) clears the
    // flag itself before returning Err. The `app.restart()` exhaustion
    // path at the bottom of `ft1_respawn_inner` is `-> !` (never
    // returns), so no clear is needed there.
    //
    // The previous unconditional clear here was a double-clear race:
    // if the inner had ALREADY cleared the flag (success path) and a
    // concurrent reader had already acquired it for a NEW respawn,
    // this stale clear would clobber the new owner's flag — letting a
    // THIRD concurrent respawn slip through the serialization gate.
    ft1_respawn_inner(app, state).await
}

pub(crate) async fn ft1_respawn_inner(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    for (attempt, delay_ms) in FT1_BACKOFF_MS.iter().enumerate() {
        // NF-R19-2: there used to be an in-loop `if attempt as u32 >=
        // FT1_MAX_RETRIES { app.restart(); }` guard here, but it was
        // dead code — `FT1_BACKOFF_MS.len() == FT1_MAX_RETRIES == 5`
        // so `attempt` ranges `0..=4` and the condition
        // `attempt >= FT1_MAX_RETRIES` was always false. The real
        // exhaustion path is the post-loop `app.restart()` at the
        // bottom of this function.
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[FT-1] shutting down — skipping respawn");
            return Ok(());
        }
        log::warn!("[FT-1] respawn attempt {} after {}ms", attempt + 1, delay_ms);
        tokio::time::sleep(Duration::from_millis(*delay_ms)).await;

        // CR-81: re-check `shutting_down` immediately before spawning a
        // new sidecar. The check at the top of the loop (line ~54 above)
        // could be stale — the user might have closed the main window
        // (triggering `shutdown_sidecar`) during the backoff sleep. If
        // we spawn a fresh sidecar here, we'd be installing it into a
        // host that is already tearing down, racing the shutdown path
        // (which calls `state.child.lock().take()` + `kill_tree`) and
        // potentially overwriting the killed child with a live one.
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[FT-1] shutting down (pre-spawn re-check) — skipping respawn");
            return Ok(());
        }

        // CR-3 fix: BEFORE spawning the new sidecar, take + kill the OLD
        // child handle. SidecarHandle::ShellPlugin(CommandChild) does NOT
        // kill the OS process on Drop (unlike DevMode's kill_on_drop(true)),
        // so without this explicit kill_tree, replacing state.child would
        // silently ORPHAN the old Python sidecar — leaving it running with
        // the mic handle, IPC port, and native hotkey binary child still
        // held. After 5 exhausted retries, up to 5 zombie Python sidecars
        // could accumulate. See CR-3 in comprehensive-review.md.
        let old_child = mutex_lock(&state.child).take();
        if let Some(old) = old_child {
            log::info!("[FT-1] killing old sidecar before respawn");
            let _ = old.kill_tree().await;
        }

        // Rotate the auth token for the fresh sidecar instance.
        let new_token = generate_token();
        match spawn_sidecar_and_get_port(app, &new_token).await {
            Ok((port, child, exit_rx)) => {
                // CR-3: take AND kill_tree the PREVIOUS child before
                // installing the new one. Without this, every successful
                // respawn overwrites `state.child` with the new
                // `SidecarHandle` and the previous child is leaked:
                // `SidecarHandle::ShellPlugin(CommandChild)` has NO
                // `Drop` impl and `SidecarHandle::DevMode` only kills
                // on drop via `kill_on_drop(true)`. After 5 backoff
                // retries, up to 5 zombie Python sidecar processes can
                // run concurrently — each holding the microphone, the
                // native hotkey binary, and model subprocesses. The
                // single-instance mutex is disabled under
                // `TAURI_SIDECAR=1`, so nothing else gates them.
                //
                // `kill_tree` (state.rs) reaps the entire process tree
                // (sidecar + grandchildren: native hotkey binary, model
                // subprocesses) — `kill()` alone would orphan the
                // grandchildren. Best-effort: errors are logged but do
                // not abort the respawn (a kill failure here is
                // recoverable — the OS may reap the zombie on its own
                // once the parent exits, but we want it gone NOW so
                // the new sidecar can re-acquire the mic).
                let prev = mutex_lock(&state.child).take();
                if let Some(prev) = prev {
                    log::info!("[FT-1] killing previous sidecar handle before respawn install");
                    if let Err(e) = prev.kill_tree().await {
                        log::warn!("[FT-1] prev child kill_tree failed (best-effort): {}", e);
                    }
                }

                // CR-81: second re-check — install-time guard. If the
                // shutdown path ran between the spawn above and the
                // lock acquire below (e.g., `shutdown_sidecar` was
                // invoked while `spawn_sidecar_and_get_port` was
                // awaiting stdout), we MUST NOT install the fresh child
                // over the (possibly already-killed) one the shutdown
                // path took. Instead, kill the freshly-spawned child
                // and return — the host is shutting down and doesn't
                // need a live sidecar.
                if state.shutting_down.load(Ordering::SeqCst) {
                    log::info!(
                        "[FT-1] shutting down (post-spawn re-check) — killing freshly-spawned sidecar instead of installing"
                    );
                    // Best-effort kill of the new child we just spawned.
                    if let Err(e) = child.kill_tree().await {
                        log::warn!(
                            "[FT-1] freshly-spawned child kill_tree failed (best-effort): {}",
                            e
                        );
                    }
                    return Ok(());
                }

                // Drop the MutexGuard BEFORE awaiting kill_tree so the
                // future stays Send (std::sync::MutexGuard is !Send).
                // Take the old handle out under the lock, drop the
                // guard, then await the kill outside the critical
                // section (CR-28).
                let old_handle = {
                    let mut child_guard = mutex_lock(&state.child);
                    let old = child_guard.take();
                    *child_guard = Some(child);
                    old
                };
                if let Some(old) = old_handle {
                    log::info!("[FT-1] killing old sidecar before installing new one (CR-28)");
                    let _ = old.kill_tree().await;
                }
                {
                    let mut token_guard = mutex_lock(&state.token);
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
                        // CR-29: reset the restart counter on success.
                        write_ft1_restart_counter(0);
                        // Emit a Tauri event so the UI can clear its
                        // "reconnecting…" banner.
                        let _ = app.emit("ft1_reconnected", json!({}));
                        // CR-13: Clear the flag BEFORE returning Ok(()).
                        // `reconnect_ws` has already spawned the new WS
                        // reader task, which owns the new connection. If
                        // the new sidecar dies immediately (fast-double-
                        // crash), the new reader will detect the
                        // disconnect and try `ft1_respawn` — clearing
                        // the flag here (before the reader can run)
                        // ensures the reader's `ft1_respawn` proceeds
                        // instead of bailing with "already in progress".
                        // The `app.restart()` exhaustion path at the
                        // bottom of this function is `-> !` (never
                        // returns), so no clear is needed there.
                        state.respawn_in_progress.store(false, Ordering::SeqCst);
                        return Ok(());
                    }
                    Err(e) => {
                        log::warn!("[FT-1] WS reconnect failed: {}", e);
                        // CR-3 fix: kill the just-spawned child before
                        // continuing to the next retry iteration,
                        // otherwise it would be orphaned when the next
                        // iteration overwrites state.child.
                        let orphan = mutex_lock(&state.child).take();
                        if let Some(c) = orphan {
                            log::info!("[FT-1] killing respawned sidecar after WS reconnect failure");
                            let _ = c.kill_tree().await;
                        }
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
    //
    // NF-R19-2: THIS is the actual exhaustion path — the post-loop
    // `app.restart()` at line 106 below. (The in-loop guard that used
    // to live above was dead code — see the comment in the loop body.)
    // ADR-0020 §10: full-app relaunch. Emit a Tauri event so the UI
    // can show a "restarting…" banner, then call `app.restart()` which
    // exits the current process and relaunches a fresh one. Returns
    // `!` (never type) so the implicit `Ok(())` return is unreachable.
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
    use crate::state::{SidecarHandle, SidecarState};
    use crate::util::BUBBLE_LEVEL_COALESCE_HZ;
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicBool, AtomicU64};
    use std::sync::{Arc, Mutex};
    use std::time::{Duration, Instant};
    use tokio::sync::Mutex as AsyncMutex;

    // ── PVT-G5-051: parse_ft1_counter saturating cast ──────────────

    #[test]
    fn test_parse_ft1_counter_normal_value() {
        // A normal count value parses unchanged.
        let v = json!({"count": 2u32});
        assert_eq!(parse_ft1_counter(&v), 2);
    }

    #[test]
    fn test_parse_ft1_counter_zero() {
        // Zero is the fail-open default and the post-success reset value.
        let v = json!({"count": 0u32});
        assert_eq!(parse_ft1_counter(&v), 0);
    }

    #[test]
    fn test_parse_ft1_counter_missing_count_field() {
        // No "count" key → return 0 (fail-open).
        let v = json!({"other": "metadata"});
        assert_eq!(parse_ft1_counter(&v), 0);
    }

    #[test]
    fn test_parse_ft1_counter_non_numeric_count() {
        // A non-numeric count (string, bool, object, array) → as_u64()
        // returns None → return 0 (fail-open).
        assert_eq!(parse_ft1_counter(&json!({"count": "three"})), 0);
        assert_eq!(parse_ft1_counter(&json!({"count": true})), 0);
        assert_eq!(parse_ft1_counter(&json!({"count": [1, 2, 3]})), 0);
        assert_eq!(parse_ft1_counter(&json!({"count": {"nested": 1}})), 0);
        assert_eq!(parse_ft1_counter(&json!({"count": null})), 0);
    }

    #[test]
    fn test_parse_ft1_counter_float_truncates() {
        // `as_u64()` returns None for floats — JSON numbers are parsed
        // as f64 by serde_json::Value, and `as_u64()` only succeeds for
        // integer-valued numbers. A 1.5 count is malformed → return 0.
        // (This matches the pre-PVT-G5-051 behavior — the saturating
        // cast only kicks in for integer values that overflow u32.)
        let v = json!({"count": 1.5f64});
        assert_eq!(parse_ft1_counter(&v), 0);
    }

    #[test]
    fn test_parse_ft1_counter_u32_max_passthrough() {
        // u32::MAX exactly fits in u32 — passes through unchanged.
        let v = json!({"count": u32::MAX as u64});
        assert_eq!(parse_ft1_counter(&v), u32::MAX);
    }

    #[test]
    fn test_parse_ft1_counter_saturates_above_u32_max() {
        // PVT-G5-051 core: a corrupted counter with a u64 value above
        // u32::MAX must SATURATE at u32::MAX (not truncate to a small
        // number via `c as u32`, which would bypass the circuit
        // breaker). u32::MAX >> FT1_MAX_RESTART_ATTEMPTS (3) so the
        // breaker trips correctly.
        let v = json!({"count": u64::from(u32::MAX) + 1});
        assert_eq!(
            parse_ft1_counter(&v),
            u32::MAX,
            "value above u32::MAX must saturate (not truncate)"
        );

        // An absurdly large value also saturates.
        let v = json!({"count": u64::MAX});
        assert_eq!(parse_ft1_counter(&v), u32::MAX);
    }

    #[test]
    fn test_parse_ft1_counter_saturating_trips_circuit_breaker() {
        // The whole point of PVT-G5-051: a corrupted counter value
        // must NOT silently bypass the circuit breaker. Verify the
        // saturating result is well above FT1_MAX_RESTART_ATTEMPTS.
        let v = json!({"count": u64::MAX});
        let parsed = parse_ft1_counter(&v);
        assert!(
            parsed >= FT1_MAX_RESTART_ATTEMPTS,
            "saturated counter ({}) must trip the breaker (max={})",
            parsed,
            FT1_MAX_RESTART_ATTEMPTS
        );
    }

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

    // ── CR-13: FT-1 respawn race — flag cleared before inner returns ──
    //
    // The fast-double-crash race (CR-13): `ft1_respawn_inner` spawns a
    // new sidecar + starts a new WS reader task (via `reconnect_ws`)
    // BEFORE returning Ok(()). If the new sidecar dies immediately, the
    // new WS reader tries `ft1_respawn` — but if the flag is still set
    // (cleared in the wrapper AFTER the inner returns), the reader bails
    // with "already in progress" and the sidecar is permanently dead.
    //
    // Fix: clear the flag INSIDE `ft1_respawn_inner` before `return Ok(())`.
    // These tests verify the flag semantics that make the fix work.

    /// Helper: build a fresh `SidecarState` for testing. All fields
    /// initialized to their default (empty) state.
    fn make_test_state() -> Arc<SidecarState> {
        Arc::new(SidecarState {
            child: Mutex::new(None),
            token: Mutex::new(String::new()),
            ws_tx: Mutex::new(None),
            pending: Arc::new(AsyncMutex::new(HashMap::new())),
            next_id: AtomicU64::new(1),
            shutting_down: AtomicBool::new(false),
            respawn_in_progress: AtomicBool::new(false),
            child_exit_rx: AsyncMutex::new(None),
        })
    }

    #[test]
    fn test_cr13_flag_is_clear_after_simulated_successful_respawn() {
        // Simulate the flag transitions for a SUCCESSFUL respawn that
        // uses the CR-13 fix (flag cleared inside the inner function
        // before returning Ok(())).
        //
        // Step 1: ft1_respawn entry — acquire the flag.
        // Step 2: ft1_respawn_inner runs, spawns new sidecar, starts WS
        //          reader, reconnects WS, succeeds.
        // Step 3: ft1_respawn_inner clears the flag (CR-13 fix) BEFORE
        //          returning Ok(()).
        // Step 4: A concurrent ft1_respawn call (from the new WS reader,
        //          which detected a fast-double-crash disconnect) must
        //          be able to acquire the flag.
        let state = make_test_state();

        // Step 1: ft1_respawn entry.
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok(),
            "first compare_exchange should succeed (flag was false)"
        );

        // Step 2 (simulated): inner function runs. While it's running,
        // a concurrent ft1_respawn call would bail:
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_err(),
            "concurrent compare_exchange should fail while inner is running (flag is true)"
        );

        // Step 3 (CR-13 fix): inner function clears the flag BEFORE
        // returning Ok(()). This is the key change — the flag is cleared
        // inside the inner function, not in the wrapper after it returns.
        state.respawn_in_progress.store(false, Ordering::SeqCst);

        // Step 4: after the inner function returns (flag already clear),
        // a concurrent ft1_respawn call from the new WS reader SUCCEEDS.
        // This is the behavior that was BROKEN before CR-13: the flag
        // was still set (cleared in the wrapper, which hadn't run yet),
        // so the reader's ft1_respawn bailed and the sidecar was
        // permanently dead.
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok(),
            "compare_exchange after CR-13 clear should succeed — \
             the new WS reader's ft1_respawn must be able to proceed"
        );
    }

    #[test]
    fn test_cr13_flag_bails_when_already_in_progress() {
        // Verify the "already in progress" bail path still works
        // correctly (this is the normal single-crash serialization —
        // the flag prevents parallel respawns from corrupting state).
        // The CR-13 fix does NOT change this behavior; it only changes
        // WHEN the flag is cleared (inside the inner function vs. in
        // the wrapper after it returns).
        let state = make_test_state();

        // First ft1_respawn acquires the flag.
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
        );

        // A concurrent ft1_respawn (e.g. from a second WS reader task
        // that also detected a disconnect) must bail.
        let concurrent_result = state
            .respawn_in_progress
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst);
        assert!(
            concurrent_result.is_err(),
            "concurrent ft1_respawn must bail when flag is already set"
        );

        // The flag must still be set (the bail path does NOT clear it).
        assert!(
            state.respawn_in_progress.load(Ordering::SeqCst),
            "flag must still be true after a concurrent bail (the in-flight respawn owns it)"
        );
    }

    // ── CR-14: retry loop kills old child before storing new ──────────
    //
    // The retry loop in `ft1_respawn_inner` spawns a new sidecar on each
    // iteration and stores it in `state.child`. Without the CR-14 fix,
    // overwriting `state.child` orphans the old sidecar process (no Drop
    // kill on `SidecarHandle`). These tests verify the take-kill-store
    // pattern kills the old process before the new one is stored.

    /// Read the state char from `/proc/<pid>/stat`. Returns `None` if
    /// the process doesn't exist (fully reaped). Returns `Some('Z')` for
    /// a zombie (killed but not yet reaped). Returns `Some(other)` for a
    /// running/stopped process.
    #[cfg(target_os = "linux")]
    fn proc_state(pid: u32) -> Option<char> {
        let stat_path = format!("/proc/{}/stat", pid);
        let stat = std::fs::read_to_string(&stat_path).ok()?;
        // The stat format is: `pid (comm) state ...`. The comm field can
        // contain spaces and parens, so find the LAST ')' to skip comm.
        let after_comm = stat.rfind(')')?;
        let rest = &stat[after_comm + 1..];
        // rest is ` state ...` — trim leading space, take first char.
        rest.trim_start().chars().next()
    }

    /// Returns true if the process is dead (doesn't exist or is a zombie).
    #[cfg(target_os = "linux")]
    fn is_process_dead(pid: u32) -> bool {
        match proc_state(pid) {
            None => true,      // process doesn't exist (fully reaped)
            Some('Z') => true, // zombie (killed, awaiting reap)
            Some(_) => false,  // still running
        }
    }

    /// Spawn a long-running dummy process (sleep 30) as a dev-mode
    /// `SidecarHandle`. Returns the handle + its PID.
    fn spawn_dummy_sidecar() -> (SidecarHandle, u32) {
        let mut cmd = tokio::process::Command::new("sleep");
        cmd.arg("30");
        // Suppress stdout/stderr so test output stays clean.
        cmd.stdout(std::process::Stdio::null());
        cmd.stderr(std::process::Stdio::null());
        let child = cmd.spawn().expect("failed to spawn dummy sleep process");
        let pid = child.id().expect("child has no pid");
        (SidecarHandle::DevMode(child), pid)
    }

    #[tokio::test]
    #[cfg(target_os = "linux")]
    async fn test_cr14_kill_tree_kills_dev_mode_child() {
        // Foundation test: verify `SidecarHandle::kill_tree()` actually
        // kills the underlying process. This is the primitive the CR-14
        // fix relies on (the retry loop calls `old.kill_tree().await`
        // before storing the new child).
        let (handle, pid) = spawn_dummy_sidecar();

        // Verify the process is alive before kill_tree.
        assert!(
            !is_process_dead(pid),
            "dummy sidecar should be alive before kill_tree (pid={})",
            pid
        );

        // CR-14 primitive: kill_tree kills the process tree.
        let result = handle.kill_tree().await;
        assert!(
            result.is_ok(),
            "kill_tree should succeed, got: {:?}",
            result
        );

        // Give the kernel a moment to deliver SIGKILL and clean up.
        tokio::time::sleep(Duration::from_millis(100)).await;

        // Verify the process is dead (zombie or fully reaped).
        assert!(
            is_process_dead(pid),
            "dummy sidecar should be dead after kill_tree (pid={}, state={:?})",
            pid,
            proc_state(pid)
        );
    }

    #[tokio::test]
    #[cfg(target_os = "linux")]
    async fn test_cr14_retry_loop_kills_old_child_before_storing_new() {
        // Integration test: simulate the CR-14 retry-loop pattern
        // (take → kill_tree → store new) and verify:
        // 1. The OLD child is killed (process dead).
        // 2. The NEW child is stored in `state.child`.
        // 3. The NEW child is alive.
        //
        // This is the exact pattern added by the CR-14 fix in
        // `ft1_respawn_inner`'s retry loop.
        let state = make_test_state();

        // Setup: store an "old" sidecar in state.child (simulating a
        // previous spawn or retry iteration).
        let (old_handle, old_pid) = spawn_dummy_sidecar();
        *state.child.lock().unwrap() = Some(old_handle);

        // Verify the old process is alive.
        assert!(
            !is_process_dead(old_pid),
            "old sidecar should be alive before retry overwrite (pid={})",
            old_pid
        );

        // ── CR-14 retry-loop pattern (take → kill → store) ──
        // Step 1: take the old child out of the slot.
        let old_child = {
            let mut child_guard = state.child.lock().unwrap();
            child_guard.take()
        };
        // Step 2: kill the old child.
        if let Some(old) = old_child {
            let _ = old.kill_tree().await;
        }
        // Step 3: store the new child.
        let (new_handle, new_pid) = spawn_dummy_sidecar();
        {
            let mut child_guard = state.child.lock().unwrap();
            *child_guard = Some(new_handle);
        }

        // Give the kernel a moment to deliver SIGKILL to the old process.
        tokio::time::sleep(Duration::from_millis(100)).await;

        // Verify: old child is dead.
        assert!(
            is_process_dead(old_pid),
            "CR-14: old sidecar must be killed before storing new (pid={}, state={:?})",
            old_pid,
            proc_state(old_pid)
        );

        // Verify: new child is alive.
        assert!(
            !is_process_dead(new_pid),
            "CR-14: new sidecar should be alive after retry overwrite (pid={})",
            new_pid
        );

        // Verify: state.child holds the new child (not the old one).
        let child_guard = state.child.lock().unwrap();
        assert!(
            child_guard.is_some(),
            "CR-14: state.child should hold the new child after retry"
        );
        // The new child's PID should match new_pid (verifying we stored
        // the new child, not a stale reference to the old one).
        let stored_pid = child_guard.as_ref().and_then(|h| h.pid());
        assert_eq!(
            stored_pid,
            Some(new_pid),
            "CR-14: state.child should hold the NEW child (pid={}), not the old one",
            new_pid
        );
        drop(child_guard);

        // Cleanup: kill the new child so we don't leak a sleep process.
        let new_child = state.child.lock().unwrap().take();
        if let Some(h) = new_child {
            let _ = h.kill_tree().await;
        }
    }

    #[tokio::test]
    #[cfg(target_os = "linux")]
    async fn test_cr14_retry_loop_first_iteration_kills_crashed_sidecar() {
        // Edge case: on the FIRST retry iteration (attempt=0), the
        // `state.child` slot holds the CRASHED sidecar's handle (the WS
        // reader detected the disconnect, but the host's child handle is
        // still there). The CR-14 fix must kill it too — the sidecar
        // process may not be fully dead (the WS thread could have died
        // while the process is still running with mic/hotkeys held).
        //
        // This test verifies the take-kill-store pattern works correctly
        // when the "old" child is still alive (simulating a half-dead
        // sidecar where the WS thread died but the process is running).
        let state = make_test_state();

        // Setup: store a "crashed but still running" sidecar.
        let (old_handle, old_pid) = spawn_dummy_sidecar();
        *state.child.lock().unwrap() = Some(old_handle);

        // Verify the old process is alive (simulating half-dead sidecar).
        assert!(!is_process_dead(old_pid));

        // CR-14 pattern: take + kill + store new.
        let old_child = state.child.lock().unwrap().take();
        if let Some(old) = old_child {
            let _ = old.kill_tree().await;
        }
        let (new_handle, new_pid) = spawn_dummy_sidecar();
        *state.child.lock().unwrap() = Some(new_handle);

        tokio::time::sleep(Duration::from_millis(100)).await;

        // The "crashed but still running" sidecar must be killed.
        assert!(
            is_process_dead(old_pid),
            "CR-14: even on first iteration, the crashed-but-running sidecar must be killed"
        );
        assert!(!is_process_dead(new_pid));

        // Cleanup.
        let new_child = state.child.lock().unwrap().take();
        if let Some(h) = new_child {
            let _ = h.kill_tree().await;
        }
    }
}
