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
// poison-safe Mutex helper. Replacing
// `state.X.lock().unwrap()` with `mutex_lock(&state.X)` so a poisoned
// mutex (a prior panic while holding the lock) doesn't re-panic and
// brick the resilience layer.
use crate::sidecar::spawn::spawn_sidecar_and_get_port_with_shutdown;
use crate::sidecar::ws::reconnect_ws;
use crate::state::lock as mutex_lock;
use crate::util::{
    atomic_write_bytes, generate_token, PRE_RESTART_DELAY_MS, SUPERVISOR_BACKOFF_MS,
};
// reuse the canonical atomic write helper so the
// restart counter is durable against mid-write crashes (see
// `write_restart_counter` below). previously imported from
// `crate::migrate::atomic_write_bytes` (a backward-compat re-export);
// now imports directly from `crate::util` so the re-export shim can
// eventually be removed once `migrate.rs` itself is deleted.
// `AssertUnwindSafe` + `catch_unwind` for the respawn_inner
// panic-safety wrapper. `FutureExt` brings `.catch_unwind()` into scope.
use futures_util::FutureExt;
use serde_json::json;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tauri::Emitter;

/// max number of `app.restart()` attempts before the supervisor
/// gives up and emits `supervisor_failed` instead of looping
/// forever. Each `respawn` call increments a disk-persisted
/// counter; on successful `supervisor_reconnected` the counter resets to 0.
/// 3 attempts is enough to ride out transient sidecar crashes without
/// masking a permanently-broken install (missing binary, corrupt env).
pub(super) const MAX_RESTART_ATTEMPTS: u32 = 3;

/// stale-count cutoff. The disk-persisted restart
/// counter now carries a Unix timestamp (seconds). If the timestamp
/// is older than this many seconds, the count is treated as 0 — a
/// stale counter from a previous session (e.g., the user had 2
/// failures last week) doesn't trip the circuit breaker on a single
/// new crash. 10 minutes is long enough to catch a tight flap loop
/// (3 crashes within 10 minutes is clearly a broken install) but
/// short enough to not accumulate across sessions.
pub(super) const COUNTER_STALE_SECS: u64 = 10 * 60;

/// helper: current Unix time in seconds. Returns 0 on
/// pre-epoch clock skew (won't happen in practice but the
/// `duration_since` API requires handling it).
pub(super) fn now_unix_secs() -> u64 {
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
pub(super) fn read_restart_counter() -> u32 {
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
/// Persistence ownership: ``restart_counter.json`` is the TAURI-ONLY
/// sidecar-respawn circuit breaker ({count, ts}, 10-minute staleness
/// window, cleared on successful reconnect). It is intentionally
/// INDEPENDENT of the Electron runtime's ``restart_history.json``
/// (voice_typer/client/src/main/python/relaunch-app.ts: an array of
/// epoch-ms relaunch timestamps for the app-relaunch crash-loop
/// breaker). The two runtimes never coexist and their schemas /
/// semantics / lifecycles differ — do NOT merge them into one
/// "restart" file.
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
/// is called ONLY on successful `reconnect_ws` (the existing path).
/// It is NOT called on cold start — that would defeat the circuit
/// breaker (see `main.rs` cold-start reset intentionally removed).
/// The counter persists on disk for `COUNTER_STALE_SECS` (600s) after
/// the breaker trips, so a transient flap (e.g., 3 crashes during an
/// OS update) self-heals after 10 minutes without manual intervention.
/// For user-initiated restarts that need to bypass the stale window
/// immediately, see `clear_restart_counter_for_user_restart` below.
pub(crate) fn write_restart_counter(count: u32) {
    // route through the cached `config_dir()` (OnceLock-backed).
    let path = match crate::platform::paths::config_dir() {
        p if p.as_os_str().is_empty() => return,
        p => p.join("restart_counter.json"),
    };
    //include `ts` so future reads can detect staleness.
    let payload = json!({"count": count, "ts": now_unix_secs()});
    if let Err(e) = atomic_write_bytes(&path, payload.to_string().as_bytes()) {
        log::warn!(
            "[SUPERVISOR] failed to persist restart counter to {:?}: {}",
            path,
            e
        );
    }
}

/// clear the disk-persisted restart counter on a USER-INITIATED
/// restart (e.g., the tray "Restart" button). This is the middle-ground
/// fix: the breaker still trips automatically on a broken install, but
/// a user who knows they want to retry (after, say, re-plugging a
/// microphone or freeing disk space) can clear the persisted count and
/// get a fresh 3-attempt budget immediately — instead of being locked
/// out for the remaining `COUNTER_STALE_SECS` (up to 600s).
///
/// # When to call
///
/// Call this ONLY from a user-initiated restart path — never from the
/// supervisor's own `app.restart()` exhaustion path or any automatic
/// respawn logic. Wiring it into the supervisor would defeat the
/// circuit breaker: every supervisor-initiated relaunch would reset
/// the count to 0 and the app could loop forever on a broken install.
///
/// The intended caller is the Tauri command bound to the tray
/// "Restart" menu item (see `main.rs` / `commands/sidecar_cmds.rs`).
/// This function is intentionally defined here in `supervisor.rs`
/// (where the counter lives) but NOT wired into any caller — the
/// caller is owned by a different lane and will be added separately.
///
/// The `_state` parameter is accepted (and unused) for two reasons:
/// (1) future-proofing — a caller that already holds `&Arc<SidecarState>`
///     can pass it without an extra signature change later; and
/// (2) it documents that this is a user-restart-scoped operation tied
///     to the same `SidecarState` instance, not a free-floating helper.
///     The function only writes a disk file; it does not touch the
///     shared state.
#[allow(dead_code)] // intended: caller is a different lane, wired separately (see doc above)
pub(crate) fn clear_restart_counter_for_user_restart(_state: &Arc<SidecarState>) {
    log::info!(
        "[SUPERVISOR] user-initiated restart requested — clearing persisted restart counter \
         (was {}) so the next respawn gets a fresh attempt budget",
        read_restart_counter()
    );
    write_restart_counter(0);
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
        Err(panic_payload) => {
            let msg = panic_payload
                .downcast_ref::<&'static str>()
                .copied()
                .or_else(|| panic_payload.downcast_ref::<String>().map(|s| s.as_str()))
                .unwrap_or("<non-string panic payload>");
            log::error!(
                "[SUPERVISOR] respawn_inner panicked: {} — clearing respawn_in_progress \
                 so future respawns can proceed",
                msg
            );
            state.respawn_in_progress.store(false, Ordering::SeqCst);
            Err(format!("respawn_inner panicked: {}", msg))
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
        log::warn!(
            "[SUPERVISOR] respawn attempt {} after {}ms",
            attempt + 1,
            delay_ms
        );
        // Cancellable backoff: `tokio::select!` between the remaining
        // backoff sleep and `shutdown_notify.notified()`. This eliminates
        // the prior 100ms polling wakeups entirely (the loop used to
        // wake every ≤100ms to re-check `shutting_down`) while
        // preserving sub-ms cancellation latency — `shutdown_sidecar_for_exit`
        // calls `notify_one()` immediately after the `shutting_down` swap,
        // so a quit during the backoff sleep (up to 8s on the 5th
        // iteration) wakes the supervisor within microseconds instead of
        // up to 100ms later. `Notify::notify_one()` stores a single
        // permit when no waiter is registered, so a notify fired BEFORE
        // the supervisor starts awaiting `notified()` is consumed by the
        // very next `notified()` call (no race window between the
        // top-of-loop `shutting_down.load()` and the `select!` await).
        let sleep_target = tokio::time::Instant::now() + Duration::from_millis(*delay_ms);
        loop {
            if state.shutting_down.load(Ordering::SeqCst) {
                log::info!("[SUPERVISOR] shutting down during backoff sleep — aborting respawn");
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
                    // Backoff elapsed — fall through to the spawn path.
                    // Re-loop (rather than `break`) so the top-of-loop
                    // `shutting_down.load()` re-check fires before spawn.
                }
                _ = state.shutdown_notify.notified() => {
                    // Shutdown fired mid-backoff. `notify_one()` was
                    // called by `shutdown_sidecar_for_exit` right after
                    // the `shutting_down` swap — re-loop so the
                    // top-of-loop check observes the new flag value and
                    // returns Ok(()) (clearing `respawn_in_progress`).
                    log::info!(
                        "[SUPERVISOR] shutdown_notify fired during backoff sleep — re-checking shutting_down"
                    );
                }
            }
        }

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
        // Pass `&state.shutting_down` so the stdout-read loop
        // inside `spawn_sidecar_release` / `spawn_sidecar_dev_mode`
        // short-circuits if the user quits the app mid-respawn. Without
        // this, a respawn initiated seconds before quit would block for
        // up to SERVER_STARTED_TIMEOUT_MS (30s) waiting for a
        // `server_started` line that will never arrive.
        match spawn_sidecar_and_get_port_with_shutdown(app, &new_token, &state.shutting_down).await
        {
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
                    log::info!(
                        "[SUPERVISOR] killing old sidecar before installing new one"
                    );
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
                        last_error = format!("attempt {}: WS reconnect failed: {}", attempt + 1, e);
                        // fix: kill the just-spawned child before
                        // continuing to the next retry iteration,
                        // otherwise it would be orphaned when the next
                        // iteration overwrites state.child.
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
                // Short-circuit on the "shutdown" sentinel returned by
                // `spawn_sidecar_and_get_port_with_shutdown` when the
                // stdout-read loop detected `shutting_down`. Treat it
                // the same as the top-of-loop shutting_down check:
                // clear the flag and return Ok (no retry, no backoff
                // sleep — the host is going away, retrying would just
                // delay the exit).
                if e == "shutdown" {
                    log::info!(
                        "[SUPERVISOR] spawn loop detected shutting_down — exiting respawn cleanly"
                    );
                    state.respawn_in_progress.store(false, Ordering::SeqCst);
                    return Ok(());
                }
                log::warn!("[SUPERVISOR] sidecar spawn failed: {}", e);
                // capture the per-iteration error.
                last_error = format!("attempt {}: sidecar spawn failed: {}", attempt + 1, e);
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
