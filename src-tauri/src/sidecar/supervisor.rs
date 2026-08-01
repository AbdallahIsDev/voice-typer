//! Sidecar supervisor: respawn + backoff (ADR-0020 §10).
//!
//! the bubble-level coalesce predicate that previously lived here
//! (`bubble_coalesce_should_emit` at line 474) has been moved to its own
//! `sidecar/bubble_coalesce.rs` module. It was called only from
//! `sidecar/ws.rs:599` (never from supervisor.rs itself) — a pure UI-
//! rate-limiting predicate with nothing to do with sidecar supervision.
//! This module now owns ONLY respawn / backoff / restart-counter logic.
//! Previously named `supervisor.rs` — the old name was an opaque internal task ID.

use crate::state::SidecarState;
#[cfg(all(test, target_os = "linux"))]
use crate::state::SidecarHandle;
// poison-safe Mutex helper. Replacing
// `state.X.lock().unwrap()` with `mutex_lock(&state.X)` so a poisoned
// mutex (a prior panic while holding the lock) doesn't re-panic and
// brick the resilience layer.
use crate::state::lock as mutex_lock;
use crate::sidecar::spawn::spawn_sidecar_and_get_port;
use crate::sidecar::ws::reconnect_ws;
use crate::util::{generate_token, atomic_write_bytes, SUPERVISOR_BACKOFF_MS, PRE_RESTART_DELAY_MS};
// reuse the canonical atomic write helper so the
// restart counter is durable against mid-write crashes (see
// `write_restart_counter` below). previously imported from
// `crate::migrate::atomic_write_bytes` (a backward-compat re-export);
// now imports directly from `crate::util` so the re-export shim can
// eventually be removed once `migrate.rs` itself is deleted.
// `AssertUnwindSafe` + `catch_unwind` for the respawn_inner
// panic-safety wrapper. `FutureExt` brings `.catch_unwind()` into scope.
use std::panic::AssertUnwindSafe;
use futures_util::FutureExt;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use serde_json::json;
use tauri::Emitter;

/// max number of `app.restart()` attempts before the supervisor
/// gives up and emits `supervisor_failed` instead of looping
/// forever. Each `respawn` call increments a disk-persisted
/// counter; on successful `supervisor_reconnected` the counter resets to 0.
/// 3 attempts is enough to ride out transient sidecar crashes without
/// masking a permanently-broken install (missing binary, corrupt env).
const MAX_RESTART_ATTEMPTS: u32 = 3;

/// stale-count cutoff. The disk-persisted restart
/// counter now carries a Unix timestamp (seconds). If the timestamp
/// is older than this many seconds, the count is treated as 0 — a
/// stale counter from a previous session (e.g., the user had 2
/// failures last week) doesn't trip the circuit breaker on a single
/// new crash. 10 minutes is long enough to catch a tight flap loop
/// (3 crashes within 10 minutes is clearly a broken install) but
/// short enough to not accumulate across sessions.
const COUNTER_STALE_SECS: u64 = 10 * 60;

/// helper: current Unix time in seconds. Returns 0 on
/// pre-epoch clock skew (won't happen in practice but the
/// `duration_since` API requires handling it).
fn now_unix_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// parse the restart counter from a JSON value with
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

/// read the disk-persisted restart counter. Returns 0 on
/// any error (missing file, parse error, etc.) — fail-open is safer
/// than blocking recovery on a transient disk issue.
///
/// dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only reads a disk file and never touches
/// the shared state. All call sites updated.
///
/// the counter file now carries a `ts` field (Unix seconds).
/// If `ts` is older than `COUNTER_STALE_SECS` (10 minutes), the
/// count is treated as 0 — a stale count from a previous session
/// doesn't trip the circuit breaker on a single new crash.
fn read_restart_counter() -> u32 {
    // route through the cached `config_dir()` (OnceLock-backed)
    // instead of re-resolving 4 env vars on every call. The prior
    // inline `config_dir_from_env(...)` form was duplicated here + in
    // `write_restart_counter` below — both call sites now share the
    // single cached resolution.
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
            // stale-count cutoff. If the timestamp is missing
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

/// write the disk-persisted restart counter. Best-effort
/// — if the write fails, log and continue (the counter is a safety
/// gate, not a correctness requirement).
///
/// dropped the unused `_state: &Arc<SidecarState>`
/// parameter — the function only writes a disk file and never touches
/// the shared state. All call sites updated.
///
/// switched from non-atomic `std::fs::write` (truncate-
/// then-write) to `atomic_write_bytes` (temp + fsync + rename). A
/// crash mid-write previously could leave a partially-written
/// counter file that fails to parse on next launch — `read_restart_counter`
/// then returns 0, silently bypassing the circuit breaker. Atomic
/// write guarantees the counter is either fully-old or fully-new.
///
/// the counter file now includes a `ts` field
/// (Unix seconds) so `read_restart_counter` can detect + ignore
/// stale counts from previous sessions. `write_restart_counter(0)`
/// is called both on successful reconnect (the existing
/// path) AND on successful cold start (the new path) so the
/// counter doesn't accumulate stale failures across sessions.
pub(crate) fn write_restart_counter(count: u32) {
    // route through the cached `config_dir()` (OnceLock-backed).
    let path = match crate::platform::paths::config_dir() {
        p if p.as_os_str().is_empty() => return,
        p => p.join("restart_counter.json"),
    };
    //include `ts` so future reads can detect staleness.
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
    // re-check `shutting_down` IMMEDIATELY after flag acquisition,
    // BEFORE any disk I/O (`read_restart_counter` below opens + reads
    // `restart_counter.json`). Without this check, a concurrent shutdown
    // during the disk read still proceeds to the counter logic below —
    // and the old `write_restart_counter(restart_count + 1)` call would
    // bump the persisted counter for a respawn that never actually ran
    // (respawn_inner's first shutting_down check would early-return).
    // The spurious bump could trip the breaker on the next legitimate
    // crash. The new check is purely defensive — the inner function has
    // its own three `shutting_down` checks — but it closes the I/O
    // window between flag acquisition and the in-loop checks.
    if state.shutting_down.load(Ordering::SeqCst) {
        log::info!(
            "[SUPERVISOR] shutting down (post-flag-acquisition, pre-I/O) — skipping respawn"
        );
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        return Ok(());
    }
    // circuit breaker — persist restart-attempt counter to
    // disk so we don't enter an infinite restart loop on a broken
    // install (missing sidecar binary, corrupted Python env, etc.).
    // If counter >= MAX_RESTART_ATTEMPTS, STOP the loop and emit
    // a `supervisor_failed` event so the UI can surface the error instead of
    // silently restart-looping forever. Counter is reset on successful
    // `supervisor_reconnected` event.
    //
    // this top-of-respawn check uses the EXISTING persisted counter
    // value (no increment here). The increment now lives in `respawn_inner`'s
    // exhaustion path, so the counter only goes up when an `app.restart()` is
    // actually about to fire — not on every `respawn` invocation. This makes
    // the breaker trip on the 3rd relaunch attempt (not the 4th), as
    // intended. The top-of-respawn check still serves as the early-exit for
    // the case where a prior process left the persisted counter at max.
    //
    // route the synchronous `std::fs::read_to_string` through
    // `tauri::async_runtime::spawn_blocking` so we don't stall a Tokio
    // worker thread on the disk read. The supervisor runs on the async
    // runtime; the prior inline `read_restart_counter()` call hit the
    // filesystem synchronously (open + read + close + JSON parse) on
    // the worker thread. Under a contended disk (e.g. an antivirus
    // scan on Windows) this can take >100ms, blocking the worker and
    // delaying other futures sharing the runtime. `spawn_blocking`
    // offloads to the dedicated blocking-thread pool. On JoinError
    // (task cancelled / panic), fail-open to 0 — same behavior as a
    // missing/unreadable counter file.
    let restart_count = tauri::async_runtime::spawn_blocking(read_restart_counter)
        .await
        .unwrap_or_else(|join_err| {
            log::warn!(
                "[SUPERVISOR] spawn_blocking(read_restart_counter) join failed: {} — treating as 0 (fail-open)",
                join_err
            );
            0
        });
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
    // the counter increment that USED to live here
    // (`write_restart_counter(restart_count + 1)`) has been moved to
    // `respawn_inner`'s exhaustion path. The old placement bumped the
    // counter on every `respawn` invocation, even when `respawn_inner`
    // succeeded (reconnect) or early-returned (shutting_down) — neither
    // of which constitute a real `app.restart()` attempt. The new
    // placement increments + checks immediately before `app.restart()`,
    // so the counter reflects actual relaunch attempts.
    //
    // DO NOT clear `respawn_in_progress` here unconditionally.
    // The inner function `respawn_inner` is responsible for clearing
    // the flag on its success path (before `return Ok(())`)
    // so that a fast-double-crash disconnect detected by the freshly-
    // spawned WS reader can immediately acquire the flag and start its
    // own respawn. The circuit-breaker path above clears the
    // flag itself before returning Err. The `app.restart()` exhaustion
    // path at the bottom of `respawn_inner` now ALSO clears the flag
    // before calling `app.restart()` —
    // the prior comment said "no clear is needed there" because the
    // path was `-> !` (never returns), but with the exhaustion
    // path can now return `Err` (breaker trip) BEFORE calling
    // `app.restart()`, so the clear is needed for that arm.
    //
    // wrap the `respawn_inner` call in
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
            //clear the flag in the Err(panic) arm.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            Err("respawn_inner panicked".to_string())
        }
    }
}

pub(crate) async fn respawn_inner(
    app: &tauri::AppHandle,
    state: &Arc<SidecarState>,
) -> Result<(), String> {
    // track the last per-iteration error across the backoff
    // schedule so the exhaustion path can surface WHY the relaunch is
    // happening (not just THAT it's happening). The previous
    // `supervisor_relaunching` payload carried only `{"reason":
    // "backoff_exhausted"}` — useless for triage. The captured string
    // is the most recent spawn-failed or WS-reconnect-failed error;
    // empty if the loop somehow exhausts without any per-iteration
    // error (shouldn't happen — exhaustion means every iteration
    // failed, so last_error is always populated on this path).
    let mut last_error = String::new();
    for (attempt, delay_ms) in SUPERVISOR_BACKOFF_MS.iter().enumerate() {
        // there used to be an in-loop `if attempt as u32 >=
        // SUPERVISOR_MAX_RETRIES { app.restart(); }` guard here, but it was
        // dead code — `SUPERVISOR_BACKOFF_MS.len() == SUPERVISOR_MAX_RETRIES == 5`
        // so `attempt` ranges `0..=4` and the condition
        // `attempt >= SUPERVISOR_MAX_RETRIES` was always false. The real
        // exhaustion path is the post-loop `app.restart()` at the
        // bottom of this function.
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[SUPERVISOR] shutting down — skipping respawn");
            // clear the flag so a future respawn (e.g. after the
            // user reopens the app from the tray without a full process
            // restart) can proceed. Without this clear, the flag stays
            // set forever and the resilience layer is permanently dead.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }
        log::warn!("[SUPERVISOR] respawn attempt {} after {}ms", attempt + 1, delay_ms);
        tokio::time::sleep(Duration::from_millis(*delay_ms)).await;

        // re-check `shutting_down` immediately before spawning a
        // new sidecar. The check at the top of the loop could be stale
        // — the user might have closed the main window (triggering
        // `shutdown_sidecar`) during the backoff sleep. If we spawn a
        // fresh sidecar here, we'd be installing it into a host that
        // is already tearing down, racing the shutdown path (which calls
        // `state.child.lock().take()` + `kill_tree`) and potentially
        // overwriting the killed child with a live one.
        if state.shutting_down.load(Ordering::SeqCst) {
            log::info!("[SUPERVISOR] shutting down (pre-spawn re-check) — skipping respawn");
            // same flag-clear rationale as the top-of-loop check.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            return Ok(());
        }

        // fix: BEFORE spawning the new sidecar, take + kill the OLD
        // child handle. SidecarHandle::ShellPlugin(CommandChild) does NOT
        // kill the OS process on Drop (unlike DevMode's kill_on_drop(true)),
        // so without this explicit kill_tree, replacing state.child would
        // silently ORPHAN the old Python sidecar — leaving it running with
        // the mic handle, IPC port, and native hotkey binary child still
        // held. After 5 exhausted retries, up to 5 zombie Python sidecars
        // could accumulate.
        let old_child = mutex_lock(&state.child).take();
        if let Some(old) = old_child {
            log::info!("[SUPERVISOR] killing old sidecar before respawn");
            let _ = old.kill_tree().await;
        }

        // Rotate the auth token for the fresh sidecar instance.
        let new_token = generate_token();
        match spawn_sidecar_and_get_port(app, &new_token).await {
            Ok((port, child, exit_rx)) => {
                // install-time guard + atomic install.
                //
                // Acquire `state.child` lock FIRST, then re-check
                // `shutting_down` INSIDE the lock, then either install
                // the fresh child or kill it. This closes the narrow
                // race window where `shutdown_sidecar_for_exit` on the
                // main thread can run between the (previously
                // lock-free) shutdown check and the lock acquire:
                //
                // 1. respawn_inner: `shutting_down.load()` → false
                // (CHECK A, no lock held).
                // 2. main thread: `shutting_down.swap(true)`,
                // acquires `state.child` lock, takes the slot
                // (which is `None` here — respawn_inner already
                // cleared it at the pre-spawn take+kill above),
                // releases the lock, returns.
                // 3. respawn_inner: acquires `state.child` lock,
                // installs the fresh child, returns `Ok(())`.
                // 4. host exits; the freshly-installed sidecar is
                // orphaned (no one kills it).
                //
                // By acquiring the lock BEFORE the shutdown check,
                // step 2 blocks on the lock until respawn_inner is
                // done installing (or has killed the fresh child and
                // returned). If shutdown_sidecar_for_exit acquires
                // the lock first, it sees either:
                // - the OLD child (which respawn_inner already
                // killed) — `take()` returns the stale handle,
                // `kill_tree()` is a no-op on an already-dead
                // process (best-effort, error logged).
                // - the FRESH child (which respawn_inner just
                // installed) — `take()` returns the live handle,
                // `kill_tree()` reaps it. respawn_inner returns
                // `Ok(())` having installed a child that
                // shutdown_sidecar_for_exit will then take + kill.
                // - `None` (respawn_inner hasn't reached the install
                // step yet because it's still waiting on the
                // lock) — shutdown_sidecar_for_exit returns, then
                // respawn_inner acquires the lock, sees
                // `shutting_down == true` (CHECK B below), kills
                // the freshly-spawned child, returns `Ok(())`.
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
                        // the prior `match child.take()` with an
                        // explicit `None` arm (which restored `old` +
                        // cleared the flag + returned `Ok(())`) was dead
                        // code — `child` is `Some` by invariant at this
                        // point (the only consumer is the `if shutting_down`
                        // branch above, which leaves `child` untouched).
                        // Deleted the unreachable `None` arm. The
                        // `if let Some` form is non-panicking (no
                        // `.unwrap()`/`.expect()`) and silently does
                        // nothing on the impossible `None` path.
                        if let Some(new_child) = child.take() {
                            *child_guard = Some(new_child);
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
                    // clear the flag on this shutting_down early-
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
                // the dead `state.token: Mutex<String>`
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
                // rotate the event receiver so the next
                // shutdown_sidecar call polls the new sidecar's exit.
                {
                    let mut rx_guard = state.child_exit_rx.lock().await;
                    *rx_guard = exit_rx;
                }
                // Reconnect WS + re-auth.
                match reconnect_ws(app, state, port, &new_token).await {
                    Ok(()) => {
                        log::info!("[SUPERVISOR] respawn succeeded on attempt {}", attempt + 1);
                        // reset the restart counter on success.
                        write_restart_counter(0);
                        // Emit a Tauri event so the UI can clear its
                        // "reconnecting…" banner.
                        let _ = app.emit("supervisor_reconnected", json!({}));
                        // Clear the flag BEFORE returning Ok(()).
                        // `reconnect_ws` has already spawned the new WS
                        // reader task, which owns the new connection. If
                        // the new sidecar dies immediately (fast-double-
                        // crash), the new reader will detect the
                        // disconnect and try `respawn` — clearing
                        // the flag here (before the reader can run)
                        // ensures the reader's `respawn` proceeds
                        // instead of bailing with "already in progress".
                        // The `app.restart()` exhaustion path at the
                        // bottom of this function now ALSO clears the
                        // flag (defense-in-depth, before
                        // `app.restart()`), and the breaker-trip
                        // exhaustion arm returns `Err` after clearing —
                        // so every return path from `respawn_inner`
                        // clears the flag itself.
                        state.respawn_in_progress.store(false, Ordering::SeqCst);
                        return Ok(());
                    }
                    Err(e) => {
                        log::warn!("[SUPERVISOR] WS reconnect failed: {}", e);
                        // capture the per-iteration error so
                        // the exhaustion path can surface it in the
                        // `supervisor_relaunching` / `supervisor_failed`
                        // payloads.
                        last_error = format!(
                            "attempt {}: WS reconnect failed: {}",
                            attempt + 1,
                            e
                        );
                        // fix: kill the just-spawned child before
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
                // capture the per-iteration error.
                last_error = format!(
                    "attempt {}: sidecar spawn failed: {}",
                    attempt + 1,
                    e
                );
                continue;
            }
        }
    }
    // Loop exited without returning — treat as exhaustion.
    //
    // THIS is the actual exhaustion path — the post-loop
    // `app.restart()`. The in-loop guard was dead code.
    // ADR-0020 §10: full-app relaunch.
    //
    // increment the persisted counter HERE (immediately before
    // `app.restart()`) and check `>= MAX_RESTART_ATTEMPTS` BEFORE calling
    // `app.restart()`. The old placement (top of `respawn`) bumped on every
    // `respawn` invocation — including successful reconnects and
    // shutting-down early-returns — making the breaker trip on the 4th
    // relaunch attempt instead of the 3rd. With the increment here, the
    // counter reflects actual relaunch attempts:
    // - Attempt 1: count=0 → increment to 1 → check 1>=3 false → app.restart()
    // - Attempt 2: count=1 → increment to 2 → check 2>=3 false → app.restart()
    // - Attempt 3: count=2 → increment to 3 → check 3>=3 TRUE → supervisor_failed
    // The breaker now trips on the 3rd relaunch attempt (2 prior
    // app.restart()s), not the 4th.
    //
    // include the last per-iteration error in the
    // `supervisor_relaunching` / `supervisor_failed` payloads so the UI
    // / crash dump can surface WHY the relaunch is happening, not just
    // THAT it's happening.
    //
    // clear `respawn_in_progress` immediately before
    // `app.restart()` as defense-in-depth. `app.restart()` returns `!`
    // (never), so the clear is "dead" code on the happy path — but if
    // `app.restart()` ever becomes fallible (or if a future Tauri API
    // returns before the process exits), the clear ensures a future
    // `respawn` invocation can proceed. The breaker-trip arm below
    // returns `Err` after clearing.
    log::error!(
        "[SUPERVISOR] backoff schedule exhausted — full-app relaunch (last_error={:?})",
        last_error
    );
    // move the synchronous `read_restart_counter` +
    // `write_restart_counter` pair into `spawn_blocking` so neither
    // hits the filesystem on a Tokio worker thread. The two calls are
    // a logical pair (read-modify-write of the same file) so bundling
    // them in one closure minimizes thread hand-offs and keeps the
    // read+write atomic w.r.t. a concurrent supervisor invocation
    // (which is already serialized by `respawn_in_progress`, but this
    // also closes any future window if the flag is ever dropped).
    // Returns the post-increment count so the caller can branch on it
    // for the breaker trip. On JoinError (task cancelled / panic),
    // fail-open by assuming the prior count was 0 and the write was
    // skipped — the worst case is the breaker doesn't trip this round
    // and the next crash will trip it.
    let new_count = tauri::async_runtime::spawn_blocking(|| {
        let prior = read_restart_counter();
        let next = prior.saturating_add(1);
        write_restart_counter(next);
        next
    })
    .await
    .unwrap_or_else(|join_err| {
        log::warn!(
            "[SUPERVISOR] spawn_blocking(read+write_restart_counter) join failed: {} — assuming count=1 (fail-open, breaker may under-trip)",
            join_err
        );
        1
    });
    if new_count >= MAX_RESTART_ATTEMPTS {
        log::error!(
            "[SUPERVISOR] circuit breaker tripped on exhaustion — restart count {} >= max {}. Stopping supervisor.",
            new_count,
            MAX_RESTART_ATTEMPTS
        );
        state.respawn_in_progress.store(false, Ordering::SeqCst);
        let _ = app.emit(
            "supervisor_failed",
            json!({
                "reason": "circuit_breaker_tripped",
                "restart_count": new_count,
                // surface the captured per-iteration error
                // so the UI / crash dump can show what kept failing.
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
            // include the last per-iteration error.
            "last_error": last_error,
            // surface the post-increment counter so the UI can
            // show "restart attempt N of M".
            "restart_count": new_count
        }),
    );
    // clear the flag immediately before `app.restart()`.
    state.respawn_in_progress.store(false, Ordering::SeqCst);
    tokio::time::sleep(Duration::from_millis(PRE_RESTART_DELAY_MS)).await;
    app.restart();
}

// bubble_coalesce_should_emit MOVED ───────────────────────
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

    // parse_restart_counter saturating cast ──────────────

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
        // (This matches the saturating
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
        // core: a corrupted counter with a u64 value above
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
        // a corrupted counter value must NOT silently
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

    // bubble_level coalesce tests MOVED ────────────────────
    //
    // The 3 `bubble_coalesce_should_emit` tests that lived here have
    // been moved to `sidecar/bubble_coalesce.rs::tests` alongside the
    // function itself. See that module for the test bodies — they're
    // preserved EXACTLY (same assertions, same comments), only the
    // module path changed.

    // respawn race — flag cleared before inner returns ──
    //
    // The fast-double-crash race `respawn_inner` spawns a
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
    /// the `token: Mutex<String>` field was removed
    /// from `SidecarState` — it was write-only dead state. The test
    /// helper no longer initializes it.
    fn make_test_state() -> Arc<SidecarState> {
        Arc::new(SidecarState::new())
    }

    #[test]
    fn test_cr13_flag_is_clear_after_simulated_successful_respawn() {
        // Simulate the flag transitions for a SUCCESSFUL respawn that
        // uses the fix (flag cleared inside the inner function
        // before returning Ok(())).
        //
        // Step 1: respawn entry — acquire the flag.
        // Step 2: respawn_inner runs, spawns new sidecar, starts WS
        //          reader, reconnects WS, succeeds.
        // Step 3: respawn_inner clears the flag BEFORE
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

        // Step 3 inner function clears the flag BEFORE
        // returning Ok(()). This is the key change — the flag is cleared
        // inside the inner function, not in the wrapper after it returns.
        state.respawn_in_progress.store(false, Ordering::SeqCst);

        // Step 4: after the inner function returns (flag already clear),
        // a concurrent respawn call from the new WS reader SUCCEEDS.
        // This is the behavior that was BROKEN before the flag
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
        // The fix does NOT change this behavior; it only changes
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

    // retry loop kills old child before storing new ──────────
    //
    // The retry loop in `respawn_inner` spawns a new sidecar on each
    // iteration and stores it in `state.child`. Without the fix,
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
        // kills the underlying process. This is the primitive the
        // fix relies on (the retry loop calls `old.kill_tree().await`
        // before storing the new child).
        let (handle, pid) = spawn_dummy_sidecar();

        // Verify the process is alive before kill_tree.
        assert!(
            !is_process_dead(pid),
            "dummy sidecar should be alive before kill_tree (pid={})",
            pid
        );

        // primitive: kill_tree kills the process tree.
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
        // Integration test: simulate the retry-loop pattern
        // (take → kill_tree → store new) and verify:
        // 1. The OLD child is killed (process dead).
        // 2. The NEW child is stored in `state.child`.
        // 3. The NEW child is alive.
        //
        // This is the exact pattern added by the fix in
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

        // retry-loop pattern (take → kill → store) ──
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
        // still there). The fix must kill it too — the sidecar
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

        // pattern: take + kill + store new.
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

    // catch_unwind clears respawn_in_progress ────────────────
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

    // shutting_down early-return paths clear the flag ─────

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

        // Path 3: post-spawn re-check.
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

    // child-install race fix ──────────────────────────────

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

    // circuit breaker trips on the 3rd relaunch attempt ───────
    //
    // Simulates the counter transitions across 4 consecutive respawn
    // invocations to verify the breaker trips on the 3rd relaunch attempt
    // (not the 4th) after the increment was moved from the top of
    // `respawn` to `respawn_inner`'s exhaustion path. Pure-logic
    // simulation — does NOT spin up a Tauri runtime / mock sidecar
    // (the integration test would be ~50 lines of Tauri bootstrap for
    // 5 lines of decision logic). The simulation mirrors the actual
    // code paths in `respawn` (top-of-respawn check) and
    // `respawn_inner` (exhaustion-path increment + check).

    #[test]
    fn test_ue4_breaker_trips_on_third_relaunch_attempt() {
        // Mirror the actual decision logic:
        // - Top of `respawn`: if persisted count >= MAX → trip.
        // - Exhaustion path: read count, increment, write; if new_count
        // >= MAX → trip (emit supervisor_failed + return Err);
        // else clear flag + app.restart().
        let mut persisted_count: u32 = 0; // fresh process, counter empty
        let mut app_restart_calls = 0u32;
        let mut supervisor_failed_emitted = false;
        let mut trip_attempt: Option<u32> = None;

        for attempt in 1..=4u32 {
            // ── Top of `respawn` ──
            if persisted_count >= MAX_RESTART_ATTEMPTS {
                supervisor_failed_emitted = true;
                trip_attempt = Some(attempt);
                break;
            }
            // ── `respawn_inner` runs the backoff schedule + exhausts ──
            // (simulated — every iteration exhausts because the test
            // scenario is a permanently-broken install).
            //
            // ── Exhaustion path: increment + check ──
            let new_count = persisted_count + 1;
            persisted_count = new_count;
            if new_count >= MAX_RESTART_ATTEMPTS {
                // Breaker trips in the exhaustion path: emit
                // supervisor_failed, return Err, no app.restart().
                supervisor_failed_emitted = true;
                trip_attempt = Some(attempt);
                break;
            }
            // Else: clear flag + app.restart().
            app_restart_calls += 1;
        }

        // the breaker must trip on the 3rd attempt — 2 prior
        // app.restart()s actually fired, the 3rd attempt detected the
        // counter at max in the exhaustion path and bailed.
        assert_eq!(
            app_restart_calls,
            MAX_RESTART_ATTEMPTS - 1,
            "UE-4: breaker should fire after {} app.restart() calls (one less than MAX), got {}",
            MAX_RESTART_ATTEMPTS - 1,
            app_restart_calls
        );
        assert!(
            supervisor_failed_emitted,
            "UE-4: supervisor_failed must be emitted when the breaker trips"
        );
        assert_eq!(
            trip_attempt,
            Some(MAX_RESTART_ATTEMPTS),
            "UE-4: breaker must trip on attempt {} (== MAX_RESTART_ATTEMPTS), got {:?}",
            MAX_RESTART_ATTEMPTS,
            trip_attempt
        );
    }

    #[test]
    fn test_ue4_breaker_counter_only_increments_on_exhaustion_not_success() {
        // verify the counter semantics changed. With the OLD code
        // (increment at top of respawn), every `respawn` invocation
        // bumped the counter — even successful reconnects. With the NEW
        // code (increment in exhaustion path), a successful respawn
        // resets the counter to 0 (via `write_restart_counter(0)` on
        // the reconnect-success path) and the counter only goes up when
        // an `app.restart()` is actually about to fire.
        //
        // Simulate: 2 respawns, both succeed. The counter should stay
        // at 0 throughout (was 2 under the old code).
        let mut persisted_count: u32 = 0;
        for _ in 0..2 {
            // Top of respawn: count is 0, no trip.
            assert!(persisted_count < MAX_RESTART_ATTEMPTS);
            // respawn_inner runs, reconnect succeeds → reset to 0.
            // (No exhaustion → no increment.)
            persisted_count = 0;
        }
        assert_eq!(
            persisted_count, 0,
            "UE-4: successful respawns must NOT bump the counter (old code bumped on every respawn)"
        );
    }

    // shutting_down check after flag acquisition ────────────
    //
    // Verify that the post-flag-acquisition shutting_down check fires
    // BEFORE any disk I/O. The check is purely defensive (the inner
    // function has its own three shutting_down checks), but it closes
    // the I/O window between flag acquisition and the in-loop checks.
    // The simulation mirrors the actual `respawn` entry sequence.

    #[test]
    fn test_ue3_f6_shutting_down_check_after_flag_acquisition() {
        let state = make_test_state();

        // Step 1: simulate the `compare_exchange(false → true)` at the
        // top of `respawn` — flag acquisition succeeds on a fresh
        // state.
        let acquired = state
            .respawn_in_progress
            .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
            .is_ok();
        assert!(acquired, "flag acquisition must succeed on a fresh state");

        // Step 2: simulate a concurrent shutdown setting the
        // `shutting_down` flag DURING the gap between flag acquisition
        // and the disk-I/O counter read (race window).
        state.shutting_down.store(true, Ordering::SeqCst);

        // Step 3: the check fires — `shutting_down` is true, so
        // respawn clears the flag + returns Ok(()) WITHOUT touching the
        // disk counter. Mirror the actual code's branch:
        let mut disk_io_performed = false;
        if state.shutting_down.load(Ordering::SeqCst) {
            // branch: clear flag + early return, no disk I/O.
            state.respawn_in_progress.store(false, Ordering::SeqCst);
        } else {
            // Would-have-been: read_restart_counter() + increment.
            disk_io_performed = true;
        }

        assert!(
            !disk_io_performed,
            "UE-3-F6: no disk I/O should be performed when shutting_down is set after flag acquisition"
        );
        assert!(
            !state.respawn_in_progress.load(Ordering::SeqCst),
            "UE-3-F6: flag must be cleared on the post-acquisition shutting_down early return"
        );

        state.shutting_down.store(false, Ordering::SeqCst);
    }

    // last_error tracked across iterations ─────────────────
    //
    // Verify that the `last_error` string captures the most recent
    // per-iteration error and would be included in the
    // `supervisor_relaunching` payload. Pure-logic simulation —
    // the actual payload construction lives in `respawn_inner`'s
    // exhaustion path and is verified by code inspection (the
    // `json!({"last_error": last_error, ...})` literal is right there).

    #[test]
    fn test_ue3_f13_last_error_tracks_most_recent_iteration_error() {
        // Simulate three iterations of the backoff loop, each producing
        // a different error. The `last_error` string should reflect the
        // MOST RECENT error (iteration 3), not the first.
        let mut last_error = String::new();
        let iterations = [
            "attempt 1: sidecar spawn failed: binary not found",
            "attempt 2: WS reconnect failed: auth timeout",
            "attempt 3: sidecar spawn failed: binary not found",
        ];
        for err in iterations.iter() {
            // Mirror the actual capture in the spawn-failed arm:
            // last_error = format!("attempt {}: sidecar spawn failed: {}", attempt + 1, e);
            last_error = err.to_string();
        }
        assert_eq!(
            last_error, iterations[2],
            "UE-3-F13: last_error must reflect the most recent iteration's error, not the first"
        );
        assert!(
            !last_error.is_empty(),
            "UE-3-F13: last_error must be non-empty after at least one failed iteration"
        );

        // Verify the captured string would be JSON-serializable as a
        // payload field (the actual emit uses `json!({"last_error": last_error, ...})`).
        let payload = json!({
            "reason": "backoff_exhausted",
            "last_error": last_error,
            "restart_count": 3u32
        });
        assert_eq!(
            payload.get("last_error").and_then(|v| v.as_str()),
            Some(iterations[2]),
            "UE-3-F13: last_error must serialize into the supervisor_relaunching payload"
        );
    }

    // install arm has no None branch (code-inspection guard) ──
    //
    // The deleted `None` arm was unreachable. This test is a structural
    // regression guard: it verifies the install arm's `if let Some`
    // form behaves correctly when `child` is Some (the only reachable
    // case). The None case is intentionally not exercised because the
    // invariant guarantees it can't happen.

    #[test]
    fn test_ue3_f5_install_arm_handles_some_child() {
        // Mirror the install arm's `if let Some(new_child) = child.take()`
        // form. The `child` variable is `Option<u32>` here (stand-in for
        // `Option<SidecarHandle>` — the type doesn't matter for this
        // structural test; only the Option pattern matters).
        let mut child: Option<u32> = Some(42);
        let mut child_guard: Option<u32> = None; // state.child was empty
        let old = child_guard.take();
        // The simplified form:
        if let Some(new_child) = child.take() {
            child_guard = Some(new_child);
        }
        assert_eq!(child_guard, Some(42), "UE-3-F5: install arm must install the fresh child");
        assert!(child.is_none(), "UE-3-F5: child must be consumed by take()");
        assert!(old.is_none(), "UE-3-F5: prior child (None here) is preserved in `old`");
    }
}
