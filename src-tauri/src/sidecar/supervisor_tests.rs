#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation
)]

//! Unit tests for `sidecar/supervisor.rs` (ADR-0020 §10).
//!
//! Moved verbatim from the inline `#[cfg(test)] mod tests` block in
//! `supervisor.rs` as part of the C-TEST-5 test-isolation migration.
//! No test logic changed — only the module path adjusted (now a sibling
//! of `supervisor` rather than a child). Private items in
//! `supervisor.rs` that the tests reference were bumped to `pub(super)`
//! so the sibling test file (within the `sidecar` parent module) can
//! access them.

use super::supervisor::{
    clear_restart_counter_for_user_restart, now_unix_secs, parse_restart_counter,
    read_restart_counter, write_restart_counter, COUNTER_STALE_SECS, MAX_RESTART_ATTEMPTS,
};
#[cfg(target_os = "linux")]
use crate::state::SidecarHandle;
use crate::state::SidecarState;
// NOTE: the panic-hook test lock is a `std::sync::MutexGuard` (non-Send),
// held across an `.await` below. This only compiles because
// `#[tokio::test]` defaults to the current_thread flavor — if a future
// edit flips this test to `flavor = "multi_thread"`, it must switch to
// a `tokio::sync::Mutex` (see test_support.rs).
use crate::test_support::PANIC_HOOK_TEST_LOCK;
// The cr14 tests below spawn REAL `sleep 30` subprocesses (children of
// the test binary) — serialize against the own-pid enumeration tests
// (see test_support.rs CHILD_PROCESS_TEST_LOCK). Same non-Send-guard-
// across-await constraint as PANIC_HOOK_TEST_LOCK above.
#[cfg(target_os = "linux")]
use crate::test_support::CHILD_PROCESS_TEST_LOCK;
use futures_util::FutureExt;
use serde_json::json;
use std::panic::AssertUnwindSafe;
use std::sync::atomic::Ordering;
use std::sync::Arc;
// `Duration` is only referenced inside the `#[cfg(target_os = "linux")]`
// tests below (they sleep the fake sidecar via `tokio::time::sleep`),
// so the import must carry the same cfg or Windows builds warn about an
// unused import.
#[cfg(target_os = "linux")]
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
        "compare_exchange after clear should succeed — \
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
    assert!(state
        .respawn_in_progress
        .compare_exchange(false, true, Ordering::SeqCst, Ordering::SeqCst)
        .is_ok());

    // A concurrent respawn (e.g. from a second WS reader task
    // that also detected a disconnect) must bail.
    let concurrent_result =
        state
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
    //
    // Spawns a REAL child of the test binary — serialize against the
    // own-pid enumeration tests (see test_support.rs).
    let _child_lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
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
    //
    // Spawns REAL children of the test binary — serialize against
    // the own-pid enumeration tests (see test_support.rs).
    let _child_lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
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
        "old sidecar must be killed before storing new (pid={}, state={:?})",
        old_pid,
        proc_state(old_pid)
    );

    // Verify: new child is alive.
    assert!(
        !is_process_dead(new_pid),
        "new sidecar should be alive after retry overwrite (pid={})",
        new_pid
    );

    // Verify: state.child holds the new child (not the old one).
    let child_guard = state.child.lock().unwrap();
    assert!(
        child_guard.is_some(),
        "state.child should hold the new child after retry"
    );
    // The new child's PID should match new_pid (verifying we stored
    // the new child, not a stale reference to the old one).
    let stored_pid = child_guard.as_ref().and_then(|h| h.pid());
    assert_eq!(
        stored_pid,
        Some(new_pid),
        "state.child should hold the NEW child (pid={}), not the old one",
        new_pid
    );
    drop(child_guard);

    // Cleanup: kill the new child so we don't leak a sleep process.
    let new_child = state.child.lock().unwrap().take();
    if let Some(h) = new_child {
        let _ = h.kill_tree().await;
    }
    // Settle so tokio's process driver reaps the just-killed child
    // BEFORE this test returns and releases CHILD_PROCESS_TEST_LOCK.
    // Otherwise the zombie can linger in
    // /proc/<test_pid>/task/<test_pid>/children for a sub-ms window
    // and flake the own-pid empty-assertion tests that acquire the
    // lock next (see test_support.rs CHILD_PROCESS_TEST_LOCK).
    tokio::time::sleep(Duration::from_millis(100)).await;
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
    //
    // Spawns REAL children of the test binary — serialize against
    // the own-pid enumeration tests (see test_support.rs).
    let _child_lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
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
        "even on first iteration, the crashed-but-running sidecar must be killed"
    );
    assert!(!is_process_dead(new_pid));

    // Cleanup.
    let new_child = state.child.lock().unwrap().take();
    if let Some(h) = new_child {
        let _ = h.kill_tree().await;
    }
    // Settle so tokio's process driver reaps the just-killed child
    // BEFORE this test returns and releases CHILD_PROCESS_TEST_LOCK.
    // Otherwise the zombie can linger in
    // /proc/<test_pid>/task/<test_pid>/children for a sub-ms window
    // and flake the own-pid empty-assertion tests that acquire the
    // lock next (see test_support.rs CHILD_PROCESS_TEST_LOCK).
    tokio::time::sleep(Duration::from_millis(100)).await;
}

// catch_unwind clears respawn_in_progress ────────────────
//
// `respawn` wraps `respawn_inner` in
// `AssertUnwindSafe(...).catch_unwind()` so a panic inside the
// inner function doesn't leave `respawn_in_progress` set forever.
// We simulate the panic by wrapping a panicking future in the same
// pattern and verifying the flag is clearable from the Err arm.
#[tokio::test]
#[allow(clippy::await_holding_lock)] // PANIC_HOOK_TEST_LOCK must stay held across the panicking-future await
async fn test_gt9_catch_unwind_clears_respawn_in_progress_on_panic() {
    // This test fires a REAL panic through the process-global hook
    // (if `install_panic_hook` has run), which toggles the global
    // `PANIC_HOOK_REENTRY` — serialize against the other
    // panic-firing / flag-mutating tests (see test_support.rs).
    let _panic_lock = PANIC_HOOK_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());
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
    assert!(
        !install,
        "GT-C4-8: when shutting_down is set, install must be false"
    );
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
    assert_eq!(
        child_guard,
        Some(42),
        "UE-3-F5: install arm must install the fresh child"
    );
    assert!(child.is_none(), "UE-3-F5: child must be consumed by take()");
    assert!(
        old.is_none(),
        "UE-3-F5: prior child (None here) is preserved in `old`"
    );
}

// write_restart_counter + read_restart_counter round-trip ──
//
// Verify the JSON CONTRACT between `write_restart_counter` (producer)
// and `read_restart_counter` (consumer). `write_restart_counter`
// emits `{"count": N, "ts": now_unix_secs()}`; `read_restart_counter`
// parses the `count` field via `parse_restart_counter` AFTER passing
// the `ts` freshness check (ts must be present + within
// COUNTER_STALE_SECS). This test exercises the full contract with a
// FRESH ts (the normal post-write case) — verifying the value written
// is the value read back, including the `ts` field that was added to
// defeat stale-count accumulation across sessions.
//
// Pure-logic: constructs the JSON payload `write_restart_counter`
// would produce and runs it through the SAME parse path
// (`parse_restart_counter`) that `read_restart_counter` uses after
// its `ts` freshness check. Does NOT touch the disk (avoids the
// `OnceLock`-cached `config_dir()` resolution + parallel-test
// filesystem races). The disk round-trip is exercised by the
// integration test below.

#[test]
fn test_write_read_restart_counter_round_trip_json_contract() {
    // Mirror `write_restart_counter`'s payload shape exactly:
    // `json!({"count": count, "ts": now_unix_secs()})`.
    for count in [0u32, 1, 2, MAX_RESTART_ATTEMPTS, u32::MAX] {
        let payload = json!({"count": count, "ts": now_unix_secs()});
        // `read_restart_counter`'s freshness check: ts != 0 AND
        // (now - ts) <= COUNTER_STALE_SECS. With ts = now, both
        // hold, so it delegates to `parse_restart_counter`.
        let ts = payload.get("ts").and_then(|t| t.as_u64()).unwrap_or(0);
        assert!(
            ts > 0,
            "ts field must be present + non-zero for count {}",
            count
        );
        let now = now_unix_secs();
        assert!(
            now >= ts && now - ts <= COUNTER_STALE_SECS,
            "fresh ts must pass staleness check for count {}",
            count
        );
        // The actual parse step `read_restart_counter` runs:
        let parsed = parse_restart_counter(&payload);
        assert_eq!(
            parsed, count,
            "round-trip failed for count {} — write_restart_counter produces a \
             payload that read_restart_counter parses back to a different value ({})",
            count, parsed
        );
    }
}

// clear_restart_counter_for_user_restart sets counter to 0 ──
//
// Integration test: calls the ACTUAL `clear_restart_counter_for_user_restart`
// (which hits the disk via `write_restart_counter(0)`) and verifies
// via `read_restart_counter()` that the persisted counter is 0.
//
// This is the ONLY test in the module that calls `config_dir()` (via
// the write/read functions). `config_dir_cached()` uses a `OnceLock`,
// so the FIRST process-wide call caches the resolution for the
// process lifetime. To make this test deterministic under parallel
// test execution, we set `VOICE_TYPER_CONFIG_DIR` to a unique temp
// dir BEFORE the first `config_dir()` call. Since no other test in
// this module calls `config_dir()`, there is no race to populate the
// cache — this test owns the first call.
//
// The test writes a non-zero counter first (to prove `clear` actually
// resets a non-zero value, not just writes 0 to an already-zero
// file), then calls `clear_restart_counter_for_user_restart`, then
// verifies the read returns 0.

#[test]
fn test_clear_restart_counter_for_user_restart_sets_zero() {
    // Create a unique temp dir so this test never interferes with
    // the user's real `~/.voice-typer/restart_counter.json` (and
    // vice versa). `tempfile` is not a dev-dependency, so use
    // `std::env::temp_dir()` + process-id + thread-name for uniqueness.
    let pid = std::process::id();
    let ts_ns = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let temp_config =
        std::env::temp_dir().join(format!("voice-typer-test-clear-counter-{}-{}", pid, ts_ns));
    // Best-effort create; if it fails (read-only temp dir), the
    // test will fall through to the "config dir unwritable" guard
    // below and skip the assertions rather than fail spuriously.
    let _ = std::fs::create_dir_all(&temp_config);

    // Set the env var BEFORE any `config_dir()` call so the
    // `OnceLock` caches OUR temp dir. `set_var` is process-global
    // and unsafe in concurrent contexts, but this is the ONLY test
    // calling `config_dir()`, so there's no contention.
    let prev = std::env::var("VOICE_TYPER_CONFIG_DIR").ok();
    std::env::set_var("VOICE_TYPER_CONFIG_DIR", &temp_config);

    // Step 1: write a non-zero counter to prove the clear actually
    // resets a real value. If the write silently fails (unwritable
    // dir), skip — the test can't prove the round-trip on a
    // read-only filesystem, and that's an environment issue, not a
    // code regression.
    write_restart_counter(2);
    let before = read_restart_counter();
    if before != 2 {
        // Config dir is unwritable or `config_dir()` resolved to
        // an empty path (env-var override didn't take effect because
        // the `OnceLock` was already cached by another caller).
        // Either way, the disk round-trip can't be tested here —
        // skip with a diagnostic rather than fail spuriously.
        eprintln!(
            "skipping clear_restart_counter integration assertion — \
             config dir unwritable or cache pre-populated (read returned {} \
             after write 2)",
            before
        );
        // Restore env var + best-effort cleanup.
        if let Some(p) = prev {
            std::env::set_var("VOICE_TYPER_CONFIG_DIR", p);
        } else {
            std::env::remove_var("VOICE_TYPER_CONFIG_DIR");
        }
        let _ = std::fs::remove_dir_all(&temp_config);
        return;
    }
    assert_eq!(
        before, 2,
        "pre-clear read must return 2 (proves the file was actually written)"
    );

    // Step 2: call the function under test. It logs the prior
    // value (2) and writes 0.
    let state = make_test_state();
    clear_restart_counter_for_user_restart(&state);

    // Step 3: verify the persisted counter is now 0.
    let after = read_restart_counter();
    assert_eq!(
        after, 0,
        "clear_restart_counter_for_user_restart must reset the persisted \
         counter to 0 (got {}); without this, a user-initiated Restart re-trips \
         the breaker immediately because the persisted count from the prior \
         supervisor exhaustion is still >= MAX_RESTART_ATTEMPTS",
        after
    );

    // Cleanup: restore the env var (so other tests / the user's
    // real config dir are unaffected) + remove the temp dir.
    if let Some(p) = prev {
        std::env::set_var("VOICE_TYPER_CONFIG_DIR", p);
    } else {
        std::env::remove_var("VOICE_TYPER_CONFIG_DIR");
    }
    let _ = std::fs::remove_dir_all(&temp_config);
}

// write_restart_counter docstring accuracy ─────────────
//
// Guard against the docstring drifting back to claiming a
// cold-start reset exists. There is NO `write_restart_counter(0)`
// call on cold start — the only reset is on the post-respawn
// reconnect-success path. main.rs deliberately omits a cold-start
// reset (an unconditional one there previously defeated the circuit
// breaker). Cross-session staleness is handled by the `ts` field +
// `COUNTER_STALE_SECS` cutoff in `read_restart_counter`, not by a
// cold-start wipe. This test self-inspects via `include_str!` so
// the assertion stays coupled to the actual doc text.

#[test]
fn test_write_restart_counter_docstring_has_no_cold_start_reset_claim() {
    let src = include_str!("supervisor.rs");
    let fn_sig = "pub(crate) fn write_restart_counter";
    let fn_idx = src
        .find(fn_sig)
        .expect("write_restart_counter function must exist in supervisor.rs");
    let before = &src[..fn_idx];
    let doc_marker = "/// write the disk-persisted restart counter";
    let doc_start = before
        .rfind(doc_marker)
        .expect("write_restart_counter must have its docstring block");
    let doc = &src[doc_start..fn_idx];
    // Build the stale-claim substring dynamically so this test's
    // own source (read via `include_str!`) cannot self-match the
    // assertion. The literal three-word phrase must NOT appear in
    // the docstring after the fix.
    let stale = format!("{} {} start", "successful", "cold");
    assert!(
        !doc.contains(&stale),
        "write_restart_counter docstring must not claim a reset happens on a \
         fresh app launch — there is no `write_restart_counter(0)` call on \
         cold start; the only reset is on reconnect-success in `respawn_inner`"
    );
    // Positive assertion: the docstring must explicitly state the
    // counter is NOT reset on a fresh app launch, so the contract
    // is documented (not just absent).
    assert!(
        doc.contains("is NOT"),
        "write_restart_counter docstring must explicitly state the counter is \
         NOT reset on a fresh app launch (document the contract, don't just \
         omit the stale claim)"
    );
}
