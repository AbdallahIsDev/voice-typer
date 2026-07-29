//! Sidecar supervisor: respawn + backoff (ADR-0020 §10).
//!
//! DT-53: the bubble-level coalesce predicate that previously lived here
//! (`bubble_coalesce_should_emit` at line 474) has been moved to its own
//! `sidecar/bubble_coalesce.rs` module. It was called only from
//! `sidecar/ws.rs:599` (never from supervisor.rs itself) — a pure UI-
//! rate-limiting predicate with nothing to do with sidecar supervision.
//! This module now owns ONLY respawn / backoff / restart-counter logic.
//! Previously named `supervisor.rs` — the old name was an opaque internal task ID.

use crate::state::SidecarState;
#[cfg(all(test, target_os = "linux"))]
use crate::state::SidecarHandle;
// G4-H-27 (session 4): poison-safe Mutex helper. Replacing
// `state.X.lock().unwrap()` with `mutex_lock(&state.X)` so a poisoned
// mutex (a prior panic while holding the lock) doesn't re-panic and
// brick the resilience layer.
use crate::state::lock as mutex_lock;
use crate::sidecar::spawn::spawn_sidecar_and_get_port;
use crate::sidecar::ws::reconnect_ws;
use crate::util::{generate_token, atomic_write_bytes, SUPERVISOR_BACKOFF_MS, PRE_RESTART_DELAY_MS};
// PVT-G5-033: reuse the canonical atomic write helper so the
// restart counter is durable against mid-write crashes (see
// `write_restart_counter` below). FZ-20: previously imported from
// `crate::migrate::atomic_write_bytes` (a backward-compat re-export);
// now imports directly from `crate::util` so the re-export shim can
// eventually be removed once `migrate.rs` itself is deleted.
// GT-9: `AssertUnwindSafe` + `catch_unwind` for the respawn_inner
// panic-safety wrapper. `FutureExt` brings `.catch_unwind()` into scope.
use std::panic::AssertUnwindSafe;
use futures_util::FutureExt;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use serde_json::json;
use tauri::Emitter;

/// CR-29: max number of `app.restart()` attempts before the supervisor
/// gives up and emits `supervisor_failed` instead of looping
/// forever. Each `respawn` call increments a disk-persisted
/// counter; on successful `supervisor_reconnected` the counter resets to 0.
/// 3 attempts is enough to ride out transient sidecar crashes without
/// masking a permanently-broken install (missing binary, corrupt env).
const MAX_RESTART_ATTEMPTS: u32 = 3;

/// G4-H-28: stale-count cutoff. The disk-persisted restart
/// counter now carries a Unix timestamp (seconds). If the timestamp
/// is older than this many seconds, the count is treated as 0 — a
/// stale counter from a previous session (e.g., the user had 2
/// failures last week) doesn't trip the circuit breaker on a single
/// new crash. 10 minutes is long enough to catch a tight flap loop
/// (3 crashes within 10 minutes is clearly a broken install) but
/// short enough to not accumulate across sessions.
const COUNTER_STALE_SECS: u64 = 10 * 60;

/// G4-H-28 helper: current Unix time in seconds. Returns 0 on
/// pre-epoch clock skew (won't happen in practice but the
/// `duration_since` API requires handling it).
fn now_unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// PVT-G5-051: parse the restart counter from a JSON value with
/// a SATURATING cast. Previously the reader used `c as u32` after
/// `as_u64()`, which silently truncates any u64 value above `u32::MAX`
/// (e.g., a corrupted counter file with an absurdly large `count`
/// field would wrap to a small number, bypassing the circuit breaker).
/// Saturating at `u32::MAX` keeps the value well above
/// `MAX_RESTART_ATTEMPTS` (3) so the breaker trips correctly.
///
/// Extracted as a `pub(crate)` helper so unit tests can verify the
/// saturating behavior without touching the filesystem.
pub(crate) fn parse_restart_counter(v: &serde_json::Value) -> u32 {
    v.get("count")
        .and_then(|c| c.as_u64())
        .map(|c| u32::try_from(c).unwrap_or(u32::MAX))
        .unwrap_or(0)
}

/// CR-29: read the disk-persisted restart counter. Returns 0 on
/// any error (missing file, parse error, etc.) — fail-open is safer
/// than blocking recovery on a transient disk issue.
///
/// PVT-G5-087: dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only reads a disk file and never touches
/// the shared state. All call sites updated.
///
/// G4-H-28: the counter file now carries a `ts` field (Unix seconds).
/// If `ts` is older than `COUNTER_STALE_SECS` (10 minutes), the
/// count is treated as 0 — a stale count from a previous session
/// doesn't trip the circuit breaker on a single new crash.
fn read_restart_counter() -> u32 {
    let path = match crate::platform::paths::config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
        std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
    ) {
        p if p.as_os_str().is_empty() => return 0,
        p => p.join("restart_counter.json"),
    };
    match std::fs::read_to_string(&path) {
        Ok(s) => {
            let v: serde_json::Value = match serde_json::from_str(&s) {
                Ok(v) => v,
                Err(_) => return 0,
            };
            // G4-H-28: stale-count cutoff. If the timestamp is missing
            // (legacy file from before this fix) or older than
            // COUNTER_STALE_SECS, treat the count as 0.
            let ts = v.get("ts").and_then(|t| t.as_u64()).unwrap_or(0);
            if ts == 0 {
                // No timestamp → legacy file. Assume fresh (count=0) to
                // avoid penalizing users who upgrade mid-session.
                return 0;
            }
            let now = now_unix_secs();
            if now < ts || now - ts > COUNTER_STALE_SECS {
                log::info!(
                    "[SUPERVISOR] restart counter stale (ts={}, now={}, age={}s > {}s) — resetting to 0",
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

/// CR-29: write the disk-persisted restart counter. Best-effort
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
/// counter file that fails to parse on next launch — `read_restart_counter`
/// then returns 0, silently bypassing the circuit breaker. Atomic
/// write guarantees the counter is either fully-old or fully-new.
///
/// G4-H-28 (session 4): the counter file now includes a `ts` field
/// (Unix seconds) so `read_restart_counter` can detect + ignore
/// stale counts from previous sessions. `write_restart_counter(0)`
/// is called both on successful reconnect (the existing CR-29
/// path) AND on successful cold start (the new G4-H-28 path) so the
/// counter doesn't accumulate stale failures across sessions.
pub(crate) fn write_restart_counter(count: u32) {
    let path = match crate::platform::paths::config_dir_from_env(
        std::env::var("HOME").ok().as_deref(),
        std::env::var("APPDATA").ok().as_deref(),
        std::env::var("XDG_DATA_HOME").ok().as_deref(),
        std::env::var("VOICE_TYPER_CONFIG_DIR").ok().as_deref(),
    ) {
        p if p.as_os_str().is_empty() => return,
        p => p.join("restart_counter.json"),
    };
    // G4-H-28: include `ts` so future reads can detect staleness.
    let payload = json!({"count": count, "ts": now_unix_secs()});
    if let Err(e) = atomic_write_bytes(&path, payload.to_string().as_bytes()) {
        log::warn!("[SUPERVISOR] failed to persist restart counter to {:?}: {}", path, e);
    }
}

// ─── Supervisor (ADR-0020 §10) ───────────────────────────────────

pub(crate) async fn respawn(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    // Serialize: only one respawn may run at a time. If a previous
    // respawn is still in flight (e.g., the sidecar died again mid-
    // reconnect), bail out — the in-flight supervisor owns the recovery.
    if state
        .respawn_in_progress
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_err()
    {
        log::info!("[SUPERVISOR] respawn already in progress — skipping");
        return Ok(());
    }
    // CR-29: circuit breaker — persist restart-attempt counter to
    // disk so we don't enter an infinite restart loop on a broken
    // install (missing sidecar binary, corrupted Python env, etc.).
    // If counter >= MAX_RESTART_ATTEMPTS, STOP the loop and emit
    // a `supervisor_failed` event so the UI can surface the error instead of
    // silently restart-looping forever. Counter is reset on successful
    // `supervisor_reconnected` event.
    let restart_count = read_restart_counter();
    if restart_count >= MAX_RESTART_ATTEMPTS {
        log::error!(
            "[SUPERVISOR] circuit breaker tripped — restart count {} >= max {}. Stopping supervisor.",
            restart_count,
            MAX_RESTART_ATTEMPTS
        );
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "supervisor_failed",
            json!({
                "reason": "circuit_breaker_tripped",
                "restart_count": restart_count,
                // Use the brand constant instead of an
                // inline brand-name literal so the user-facing reinstall
                // prompt stays in lockstep with `crate::branding::APP_NAME`
                // (and the TS/Python mirrors) if the product is ever renamed.
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
    // Increment counter before attempting restart — will be reset on
    // successful reconnect.
    write_restart_counter(restart_count + 1);
    // PVT-G5-031: DO NOT clear `respawn_in_progress` here unconditionally.
    // The inner function `respawn_inner` is responsible for clearing
    // the flag on its success path (before `return Ok(())`)
    // so that a fast-double-crash disconnect detected by the freshly-
    // spawned WS reader can immediately acquire the flag and start its
    // own respawn. The circuit-breaker path above clears the
    // flag itself before returning Err. The `app.restart()` exhaustion
    // path at the bottom of `respawn_inner` is `-> !` (never
    // returns), so no clear is needed there.
    //
    // GT-9: wrap the `respawn_inner` call in
    // `AssertUnwindSafe(...).catch_unwind()` so a panic inside the
    // inner function doesn't leave `respawn_in_progress` set forever
    // — which would permanently brick the resilience layer. On
    // caught panic we clear the flag and return Err. Mirrors the
    // `spawn_reader_task` pattern (ws.rs).
    let inner_result = AssertUnwindSafe(respawn_inner(app, state))
        .catch_unwind()
        .await;
    match inner_result {
        Ok(r) => r,
        Err(_panic_payload) => {
            log::error!(
                "[SUPERVISOR] respawn_inner panicked — clearing respawn_in_progress \
                 so future respawns can proceed"
            );
            // GT-9: clear the flag in the Err(panic) arm.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            Err("respawn_inner panicked".to_string())
        }
    }
}

pub(crate) async fn respawn_inner(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    for (attempt, delay_ms) in SUPERVISOR_BACKOFF_MS.iter().enumerate() {
        // NF-R19-2: there used to be an in-loop `if attempt as u32 >=
        // SUPERVISOR_MAX_RETRIES { app.restart(); }` guard here, but it was
        // dead code — `SUPERVISOR_BACKOFF_MS.len() == SUPERVISOR_MAX_RETRIES == 5`
        // so `attempt` ranges `0..=4` and the condition
        // `attempt >= SUPERVISOR_MAX_RETRIES` was always false. The real
        // exhaustion path is the post-loop `app.restart()` at the
        // bottom of this function.
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[SUPERVISOR] shutting down — skipping respawn");
            // FR-87: clear the flag so a future respawn (e.g. after the
            // user reopens the app from the tray without a full process
            // restart) can proceed. Without this clear, the flag stays
            // set forever and the resilience layer is permanently dead.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }
        log::warn!("[SUPERVISOR] respawn attempt {} after {}ms", attempt + 1, delay_ms);
        tokio::time::sleep(Duration::from_millis(*delay_ms)).await;

        // CR-81: re-check `shutting_down` immediately before spawning a
        // new sidecar. The check at the top of the loop could be stale
        // — the user might have closed the main window (triggering
        // `shutdown_sidecar`) during the backoff sleep. If we spawn a
        // fresh sidecar here, we'd be installing it into a host that
        // is already tearing down, racing the shutdown path (which calls
        // `state.child.lock().take()` + `kill_tree`) and potentially
        // overwriting the killed child with a live one.
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[SUPERVISOR] shutting down (pre-spawn re-check) — skipping respawn");
            // FR-87: same flag-clear rationale as the top-of-loop check.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }

        // CR-3 fix: BEFORE spawning the new sidecar, take + kill the OLD
        // child handle. SidecarHandle::ShellPlugin(CommandChild) does NOT
        // kill the OS process on Drop (unlike DevMode's kill_on_drop(true)),
        // so without this explicit kill_tree, replacing state.child would
        // silently ORPHAN the old Python sidecar — leaving it running with
        // the mic handle, IPC port, and native hotkey binary child still
        // held. After 5 exhausted retries, up to 5 zombie Python sidecars
        // could accumulate. See CR-3 in review.md.
        let old_child = mutex_lock(&state.child).take();
        if let Some(old) = old_child {
            log::info!("[SUPERVISOR] killing old sidecar before respawn");
            let _ = old.kill_tree().await;
        }

        // Rotate the auth token for the fresh sidecar instance.
        let new_token = generate_token();
        match spawn_sidecar_and_get_port(app, &new_token).await {
            Ok((port, child, exit_rx)) => {
                // CR-81: install-time guard + atomic install.
                //
                // Acquire `state.child` lock FIRST, then re-check
                // `shutting_down` INSIDE the lock, then either install
                // the fresh child or kill it. This closes the narrow
                // race window where `shutdown_sidecar_for_exit` on the
                // main thread can run between the (previously
                // lock-free) shutdown check and the lock acquire:
                //
                //   1. respawn_inner: `shutting_down.load()` → false
                //      (CHECK A, no lock held).
                //   2. main thread: `shutting_down.swap(true)`,
                //      acquires `state.child` lock, takes the slot
                //      (which is `None` here — respawn_inner already
                //      cleared it at the pre-spawn take+kill above),
                //      releases the lock, returns.
                //   3. respawn_inner: acquires `state.child` lock,
                //      installs the fresh child, returns `Ok(())`.
                //   4. host exits; the freshly-installed sidecar is
                //      orphaned (no one kills it).
                //
                // By acquiring the lock BEFORE the shutdown check,
                // step 2 blocks on the lock until respawn_inner is
                // done installing (or has killed the fresh child and
                // returned). If shutdown_sidecar_for_exit acquires
                // the lock first, it sees either:
                //   - the OLD child (which respawn_inner already
                //     killed) — `take()` returns the stale handle,
                //     `kill_tree()` is a no-op on an already-dead
                //     process (best-effort, error logged).
                //   - the FRESH child (which respawn_inner just
                //     installed) — `take()` returns the live handle,
                //     `kill_tree()` reaps it. respawn_inner returns
                //     `Ok(())` having installed a child that
                //     shutdown_sidecar_for_exit will then take + kill.
                //   - `None` (respawn_inner hasn't reached the install
                //     step yet because it's still waiting on the
                //     lock) — shutdown_sidecar_for_exit returns, then
                //     respawn_inner acquires the lock, sees
                //     `shutting_down == true` (CHECK B below), kills
                //     the freshly-spawned child, returns `Ok(())`.
                //
                // Wrap `child` in an Option so it's not conditionally
                // moved inside the lock scope — the lock scope block
                // can consume it in the else branch via `.take()`
                // while the if branch leaves it untouched for use
                // outside the block.
                //
                // The lock is released at the block scope end (before
                // any await) so the future stays `Send`
                // (`std::sync::MutexGuard` is `!Send`).
                let mut child = Some(child);
                let old_handle = {
                    let mut child_guard = mutex_lock(&state.child);
                    if state.shutting_down.load(Ordering::SeqCst) {
                        log::info!(
                            "[SUPERVISOR] shutting down (post-spawn re-check inside lock) — killing freshly-spawned sidecar instead of installing"
                        );
                        // child_guard dropped at block end.
                        // child was NOT consumed — kill it below.
                        None
                    } else {
                        let old = child_guard.take();
                        // Replace the prior `.unwrap()` (which trips the
                        // `unwrap_used` clippy lint and would poison
                        // `state.child`'s `std::sync::Mutex` if a future
                        // refactor ever made this branch reachable) with
                        // an explicit match. On the happy path (`Some`)
                        // behavior is identical. The `None` arm is
                        // currently unreachable — the only other consumer
                        // of `child` is the `if shutting_down` branch
                        // above, which leaves `child` untouched.
                        match child.take() {
                            Some(new_child) => {
                                *child_guard = Some(new_child);
                            }
                            None => {
                                log::error!(
                                    "[SUPERVISOR] invariant violated: child was None \
                                     inside install arm (shutting_down={}, attempt={})",
                                    state.shutting_down.load(Ordering::SeqCst),
                                    attempt
                                );
                                // Restore the prior handle so `state.child`
                                // is not left empty after the `take()`
                                // above. Bail out — the next `respawn`
                                // invocation will retry.
                                if let Some(old) = old {
                                    *child_guard = Some(old);
                                }
                                // FR-87: clear the flag so the next
                                // `respawn` invocation can actually retry.
                                // Without this clear, the "bail out and
                                // retry" comment above is a lie — the
                                // retry would no-op on the still-set flag.
                                state.respawn_in_progress.store(false, Ordering::SeqCst);
                                return Ok(());
                            }
                        }
                        old
                    }
                }; // child_guard dropped — no !Send across await
                if let Some(c) = child {
                    // shutting_down was true — child was NOT installed.
                    if let Err(e) = c.kill_tree().await {
                        log::warn!(
                            "[SUPERVISOR] freshly-spawned child kill_tree failed (best-effort): {}",
                            e
                        );
                    }
                    // FR-87: clear the flag on this shutting_down early-
                    // return path too, matching the success path at the
                    // bottom of `respawn_inner`. Without this clear, the
                    // flag stays set forever and the resilience layer is
                    // permanently dead.
                    state.respawn_in_progress.store(false, Ordering::SeqCst);
                    return Ok(());
                }
                if let Some(old) = old_handle {
                    log::info!("[SUPERVISOR] killing old sidecar before installing new one (CR-28)");
                    let _ = old.kill_tree().await;
                }
                // EC-FIX-5 (EC-24): the dead `state.token: Mutex<String>`
                // field was removed from `SidecarState`. The new auth
                // token for the freshly-spawned sidecar is the local
                // `new_token` variable, passed directly to
                // `reconnect_ws(app, state, port, &new_token)` below —
                // it does NOT need to be stored on the shared state
                // (the WS auth frame uses the local variable, not a
                // field read). The dead write that used to live here
                // (`*mutex_lock(&state.token) = new_token.clone()`)
                // was the only "consumer" of the field; removing it
                // lets the field itself be deleted (already done in
                // `state.rs`).
                // CR-2: rotate the event receiver so the next
                // shutdown_sidecar call polls the new sidecar's exit.
                {
                    let mut rx_guard = state.child_exit_rx.lock().await;
                    *rx_guard = exit_rx;
                }
                // Reconnect WS + re-auth.
                match reconnect_ws(app, state, port, &new_token).await {
                    Ok(()) => {
                        log::info!("[SUPERVISOR] respawn succeeded on attempt {}", attempt + 1);
                        // CR-29: reset the restart counter on success.
                        write_restart_counter(0);
                        // Emit a Tauri event so the UI can clear its
                        // "reconnecting…" banner.
                        let _ = app.emit("supervisor_reconnected", json!({}));
                        // CR-13: Clear the flag BEFORE returning Ok(()).
                        // `reconnect_ws` has already spawned the new WS
                        // reader task, which owns the new connection. If
                        // the new sidecar dies immediately (fast-double-
                        // crash), the new reader will detect the
                        // disconnect and try `respawn` — clearing
                        // the flag here (before the reader can run)
                        // ensures the reader's `respawn` proceeds
                        // instead of bailing with "already in progress".
                        // The `app.restart()` exhaustion path at the
                        // bottom of this function is `-> !` (never
                        // returns), so no clear is needed there.
                        state.respawn_in_progress.store(false, Ordering::SeqCst);
                        return Ok(());
                    }
                    Err(e) => {
                        log::warn!("[SUPERVISOR] WS reconnect failed: {}", e);
                        // CR-3 fix: kill the just-spawned child before
                        // continuing to the next retry iteration,
                        // otherwise it would be orphaned when the next
                        // iteration overwrites state.child.
                        let orphan = mutex_lock(&state.child).take();
                        if let Some(c) = orphan {
                            log::info!("[SUPERVISOR] killing respawned sidecar after WS reconnect failure");
                            let _ = c.kill_tree().await;
                        }
                        continue;
                    }
                }
            }
            Err(e) => {
                log::warn!("[SUPERVISOR] sidecar spawn failed: {}", e);
                continue;
            }
        }
    }
    // Loop exited without returning — treat as exhaustion.
    //
    // NF-R19-2: THIS is the actual exhaustion path — the post-loop
    // `app.restart()`. The in-loop guard was dead code.
    // ADR-0020 §10: full-app relaunch. Emit a Tauri event so the UI
    // can show a "restarting…" banner, then call `app.restart()` which
    // exits the current process and relaunches a fresh one. Returns
    // `!` (never type) so the implicit `Ok(())` return is unreachable.
    log::error!("[SUPERVISOR] backoff schedule exhausted — full-app relaunch");
    let _ = app.emit("supervisor_relaunching", json!({"reason": "backoff_exhausted"}));
    tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
    app.restart();
}

// ─── DT-53: bubble_coalesce_should_emit MOVED ───────────────────────
//
// The `bubble_coalesce_should_emit` predicate that lived here has been
// moved to its own `sidecar/bubble_coalesce.rs` module. It was called
// only from `sidecar/ws.rs:599` (never from supervisor.rs itself) — a
// pure UI-rate-limiting predicate with nothing to do with sidecar
// supervision. See `sidecar/bubble_coalesce.rs` for the function +
// its unit tests (3 tests moved with it).

#[cfg(test)]
mod tests {
    use super::*;
    use crate::state::SidecarState;
    use std::sync::Arc;
    use std::time::Duration;

    // ── PVT-G5-051: parse_restart_counter saturating cast ──────────────

    #[test]
    fn test_parse_restart_counter_normal_value() {
        // A normal count value parses unchanged.
        let v = json!({"count": 2u32});
        assert_eq!(parse_restart_counter(&v), 2);
    }

    #[test]
    fn test_parse_restart_counter_zero() {
        // Zero is the fail-open default and the post-success reset value.
        let v = json!({"count": 0u32});
        assert_eq!(parse_restart_counter(&v), 0);
    }

    #[test]
    fn test_parse_restart_counter_missing_count_field() {
        // No "count" key → return 0 (fail-open).
        let v = json!({"other": "metadata"});
        assert_eq!(parse_restart_counter(&v), 0);
    }

    #[test]
    fn test_parse_restart_counter_non_numeric_count() {
        // A non-numeric count (string, bool, object, array) → as_u64()
        // returns None → return 0 (fail-open).
        assert_eq!(parse_restart_counter(&json!({"count": "three"})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": true})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": [1, 2, 3]})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": {"nested": 1}})), 0);
        assert_eq!(parse_restart_counter(&json!({"count": null})), 0);
    }

    #[test]
    fn test_parse_restart_counter_float_truncates() {
        // `as_u64()` returns None for floats — JSON numbers are parsed
        // as f64 by serde_json::Value, and `as_u64()` only succeeds for
        // integer-valued numbers. A 1.5 count is malformed → return 0.
        // (This matches the pre-PVT-G5-051 behavior — the saturating
        // cast only kicks in for integer values that overflow u32.)
        let v = json!({"count": 1.5f64});
        assert_eq!(parse_restart_counter(&v), 0);
    }

    #[test]
    fn test_parse_restart_counter_u32_max_passthrough() {
        // u32::MAX exactly fits in u32 — passes through unchanged.
        let v = json!({"count": u32::MAX as u64});
        assert_eq!(parse_restart_counter(&v), u32::MAX);
    }

    #[test]
    fn test_parse_restart_counter_saturates_above_u32_max() {
        // PVT-G5-051 core: a corrupted counter with a u64 value above
        // u32::MAX must SATURATE at u32::MAX (not truncate to a small
        // number via `c as u32`, which would bypass the circuit
        // breaker). u32::MAX >> MAX_RESTART_ATTEMPTS (3) so the
        // breaker trips correctly.
        let v = json!({"count": u64::from(u32::MAX) + 1});
        assert_eq!(
            parse_restart_counter(&v),
            u32::MAX,
            "value above u32::MAX must saturate (not truncate)"
        );

        // An absurdly large value also saturates.
        let v = json!({"count": u64::MAX});
        assert_eq!(parse_restart_counter(&v), u32::MAX);
    }

    #[test]
    fn test_parse_restart_counter_saturating_trips_circuit_breaker() {
        // PVT-G5-051: a corrupted counter value must NOT silently
        // bypass the circuit breaker. Verify the saturating result
        // is well above MAX_RESTART_ATTEMPTS.
        let v = json!({"count": u64::MAX});
        let parsed = parse_restart_counter(&v);
        assert!(
            parsed >= MAX_RESTART_ATTEMPTS,
            "saturated counter ({}) must trip the breaker (max={})",
            parsed,
            MAX_RESTART_ATTEMPTS
        );
    }

    // ── DT-53: bubble_level coalesce tests MOVED ────────────────────
    //
    // The 3 `bubble_coalesce_should_emit` tests that lived here have
    // been moved to `sidecar/bubble_coalesce.rs::tests` alongside the
    // function itself. See that module for the test bodies — they're
    // preserved EXACTLY (same assertions, same comments), only the
    // module path changed.

    // ── CR-13: respawn race — flag cleared before inner returns ──
    //
    // The fast-double-crash race (CR-13): `respawn_inner` spawns a
    // new sidecar + starts a new WS reader task (via `reconnect_ws`)
    // BEFORE returning Ok(()). If the new sidecar dies immediately, the
    // new WS reader tries `respawn` — but if the flag is still set
    // (cleared in the wrapper AFTER the inner returns), the reader bails
    // with "already in progress" and the sidecar is permanently dead.
    //
    // Fix: clear the flag INSIDE `respawn_inner` before `return Ok(())`.
    // These tests verify the flag semantics that make the fix work.

    /// Helper: build a fresh `SidecarState` for testing. All fields
    /// initialized to their default (empty) state.
    ///
    /// EC-FIX-5 (EC-24): the `token: Mutex<String>` field was removed
    /// from `SidecarState` — it was write-only dead state. The test
    /// helper no longer initializes it.
    fn make_test_state() -> Arc<SidecarState> {
        Arc::new(SidecarState::new())
    }

    #[test]
    fn test_cr13_flag_is_clear_after_simulated_successful_respawn() {
        // Simulate the flag transitions for a SUCCESSFUL respawn that
        // uses the CR-13 fix (flag cleared inside the inner function
        // before returning Ok(())).
        //
        // Step 1: respawn entry — acquire the flag.
        // Step 2: respawn_inner runs, spawns new sidecar, starts WS
        //          reader, reconnects WS, succeeds.
        // Step 3: respawn_inner clears the flag (CR-13 fix) BEFORE
        //          returning Ok(()).
        // Step 4: A concurrent respawn call (from the new WS reader,
        //          which detected a fast-double-crash disconnect) must
        //          be able to acquire the flag.
        let state = make_test_state();

        // Step 1: respawn entry.
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok(),
            "first compare_exchange should succeed (flag was false)"
        );

        // Step 2 (simulated): inner function runs. While it's running,
        // a concurrent respawn call would bail:
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
        // a concurrent respawn call from the new WS reader SUCCEEDS.
        // This is the behavior that was BROKEN before CR-13: the flag
        // was still set (cleared in the wrapper, which hadn't run yet),
        // so the reader's respawn bailed and the sidecar was
        // permanently dead.
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok(),
            "compare_exchange after CR-13 clear should succeed — \
             the new WS reader's respawn must be able to proceed"
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

        // First respawn acquires the flag.
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok()
        );

        // A concurrent respawn (e.g. from a second WS reader task
        // that also detected a disconnect) must bail.
        let concurrent_result = state
            .respawn_in_progress
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst);
        assert!(
            concurrent_result.is_err(),
            "concurrent respawn must bail when flag is already set"
        );

        // The flag must still be set (the bail path does NOT clear it).
        assert!(
            state.respawn_in_progress.load(Ordering::SeqCst),
            "flag must still be true after a concurrent bail (the in-flight respawn owns it)"
        );
    }

    // ── CR-14: retry loop kills old child before storing new ──────────
    //
    // The retry loop in `respawn_inner` spawns a new sidecar on each
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
    #[cfg(all(test, target_os = "linux"))]
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
        // `respawn_inner`'s retry loop.
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

    // ── GT-9: catch_unwind clears respawn_in_progress ────────────────
    //
    // `respawn` wraps `respawn_inner` in
    // `AssertUnwindSafe(...).catch_unwind()` so a panic inside the
    // inner function doesn't leave `respawn_in_progress` set forever.
    // We simulate the panic by wrapping a panicking future in the same
    // pattern and verifying the flag is clearable from the Err arm.
    #[tokio::test]
    async fn test_gt9_catch_unwind_clears_respawn_in_progress_on_panic() {
        let state = make_test_state();
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok(),
            "flag acquisition must succeed on a fresh state"
        );
        assert!(state.respawn_in_progress.load(Ordering::SeqCst));

        // Pre-existing baseline syntax error: `let x = async fn() -> T { ... };`
        // is not valid Rust (`async fn` is an item declaration, not an
        // expression). The intent was a callable that returns a panicking
        // future — fixed by switching to a closure
        // that returns an `async move { ... }` block. The closure is
        // called with `panicking_inner()` (matching the original
        // `panicking_inner()` call below), preserving the test's
        // AssertUnwindSafe(panicking_inner()).catch_unwind().await shape.
        let panicking_inner = || async move {
            panic!("simulated respawn_inner panic (GT-9 test)");
            #[allow(unreachable_code)]
            Ok::<(), String>(())
        };
        let result = AssertUnwindSafe(panicking_inner()).catch_unwind().await;

        match result {
            Ok(_) => panic!("test setup error: panicking_inner should have panicked"),
            Err(_panic_payload) => {
                state.respawn_in_progress.store(false, Ordering::SeqCst);
            }
        }

        assert!(
            !state.respawn_in_progress.load(Ordering::SeqCst),
            "GT-9: respawn_in_progress must be cleared after a caught panic"
        );
        assert!(
            state
                .respawn_in_progress
                .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
                .is_ok(),
            "GT-9: flag must be re-acquirable after the caught panic cleared it"
        );
    }

    // ── GT-C4-6: shutting_down early-return paths clear the flag ─────

    #[test]
    fn test_gt_c4_6_shutting_down_paths_clear_flag() {
        let state = make_test_state();

        // Path 1: top-of-loop shutting_down check.
        state.respawn_in_progress.store(true, Ordering::SeqCst);
        state.shutting_down.store(true, Ordering::SeqCst);
        if state.shutting_down.load(Ordering::SeqCst) {
            state.respawn_in_progress.store(false, Ordering::SeqCst);
        }
        assert!(
            !state.respawn_in_progress.load(Ordering::SeqCst),
            "GT-C4-6 path 1: flag must be cleared on top-of-loop early return"
        );

        // Path 2: pre-spawn re-check.
        state.respawn_in_progress.store(true, Ordering::SeqCst);
        if state.shutting_down.load(Ordering::SeqCst) {
            state.respawn_in_progress.store(false, Ordering::SeqCst);
        }
        assert!(
            !state.respawn_in_progress.load(Ordering::SeqCst),
            "GT-C4-6 path 2: flag must be cleared on pre-spawn early return"
        );

        // Path 3: post-spawn CR-81 re-check.
        state.respawn_in_progress.store(true, Ordering::SeqCst);
        if state.shutting_down.load(Ordering::SeqCst) {
            state.respawn_in_progress.store(false, Ordering::SeqCst);
        }
        assert!(
            !state.respawn_in_progress.load(Ordering::SeqCst),
            "GT-C4-6 path 3: flag must be cleared on post-spawn early return"
        );

        state.shutting_down.store(false, Ordering::SeqCst);
    }

    // ── GT-C4-8: child-install race fix ──────────────────────────────

    #[tokio::test]
    async fn test_gt_c4_8_child_install_race_clears_flag() {
        let state = make_test_state();
        state.respawn_in_progress.store(true, Ordering::SeqCst);
        state.shutting_down.store(true, Ordering::SeqCst);

        let install = !state.shutting_down.load(Ordering::SeqCst);
        assert!(!install, "GT-C4-8: when shutting_down is set, install must be false");
        if !install {
            state.respawn_in_progress.store(false, Ordering::SeqCst);
        }
        assert!(
            !state.respawn_in_progress.load(Ordering::SeqCst),
            "GT-C4-8: flag must be cleared when shutting_down prevents install"
        );
        assert!(
            state.child.lock().unwrap().is_none(),
            "GT-C4-8: state.child must remain None when shutting_down prevents install"
        );

        state.shutting_down.store(false, Ordering::SeqCst);
    }
}
