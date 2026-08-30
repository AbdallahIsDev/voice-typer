//! Unit tests for `state.rs` (ADR-0020 §1, §10, §14).
//!
//! Moved verbatim from the inline `#[cfg(test)] mod tests` block in
//! `state.rs` as part of the C-TEST-5 test-isolation migration. No test
//! logic changed — only the module path adjusted (now a sibling of
//! `state` rather than a child). All items referenced by the tests were
//! already `pub(crate)`, so no visibility bumps were needed in
//! `state.rs`.

use crate::state::{
    shutdown_sidecar_for_exit, PendingMap, SidecarHandle, SidecarState, WorkerState,
};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{oneshot, Mutex as AsyncMutex};
// The devmode Drop tests below spawn REAL `sleep 30` subprocesses
// (children of the test binary) — serialize against the own-pid
// enumeration tests (see test_support.rs CHILD_PROCESS_TEST_LOCK).
#[cfg(unix)]
use crate::test_support::CHILD_PROCESS_TEST_LOCK;

//`SidecarState::new()` must initialize
/// `heartbeat_handle` to `None`. Also verifies the `Default` impl.
#[tokio::test]
async fn test_sidecar_state_new_heartbeat_handle_is_none() {
    let state = SidecarState::new();
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "fresh SidecarState must have heartbeat_handle = None"
    );
    let default_state = SidecarState::default();
    assert!(
        default_state.heartbeat_handle.lock().await.is_none(),
        "SidecarState::default() must have heartbeat_handle = None"
    );
}

//PendingMap type alias no longer wraps an outer Arc.
#[tokio::test]
async fn test_pending_map_no_outer_arc_compiles_and_works() {
    let pending: PendingMap = AsyncMutex::new(HashMap::new());
    let (tx, _rx) = oneshot::channel::<Value>();
    pending.lock().await.insert(1u64, tx);
    assert_eq!(pending.lock().await.len(), 1);
    let _ = pending.lock().await.remove(&1u64);
    assert_eq!(pending.lock().await.len(), 0);
}

//`shutdown_sidecar_for_exit` must be idempotent. The first call
/// sets `shutting_down` IMMEDIATELY (before the 30s dev-mode sleep)
/// so a concurrent second call short-circuits via the idempotency
/// guard. We verify the contract structurally without waiting the
/// full 30s sleep — spawn the first call, poll `shutting_down` until
/// it flips true, then verify the second call returns immediately.
#[tokio::test]
async fn test_shutdown_sidecar_for_exit_is_idempotent() {
    let state = Arc::new(SidecarState::new());
    let state_clone = state.clone();
    // Spawn the first call but don't await — it would block for the
    // full EXIT_SHUTDOWN_ACK_TIMEOUT_MS (30s) on the dev-mode None
    // child_exit_rx path.
    let first_handle = tokio::spawn(async move {
        shutdown_sidecar_for_exit(&state_clone).await;
    });
    // Poll `shutting_down` until it flips true (set by the first
    // call's idempotency guard, BEFORE the 30s sleep). 1s is
    // generous — the guard runs in the first few microseconds of
    // the call.
    let mut guard_set = false;
    for _ in 0..100 {
        if state
            .shutting_down
            .load(std::sync::atomic::Ordering::SeqCst)
        {
            guard_set = true;
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert!(
        guard_set,
        "shutting_down must be set within 1s of the first call (idempotency guard runs before the 30s sleep)"
    );
    // Second call must short-circuit immediately because
    // `shutting_down` is already set. 100ms is generous for a
    // no-op return.
    let state_clone2 = state.clone();
    let second = tokio::time::timeout(
        Duration::from_millis(100),
        shutdown_sidecar_for_exit(&state_clone2),
    )
    .await;
    assert!(
        second.is_ok(),
        "second shutdown_sidecar_for_exit must short-circuit immediately (idempotency)"
    );
    // Abort the first call to clean up — don't wait for the 30s
    // sleep. The spawned task is still in the dev-mode sleep; abort
    // drops it without panicking.
    first_handle.abort();
}

// ── SidecarState::begin_shutdown ───────────────────────────────

/// `begin_shutdown` is the canonical swap + wakeup pair: the first
/// call swaps `shutting_down` false→true (returning `false`), the
/// second sees it already set (returning `true`), and BOTH calls fire
/// the supervisor wakeup. A `notified()` future registered BEFORE the
/// notify must complete on its first poll — the stored-permit
/// semantics that let the production teardown path
/// (`shutdown_sidecar_for_exit`, which performs the same adjacent
/// pair inline) wake a supervisor that is only just entering its
/// backoff `tokio::select!`, instead of that supervisor sleeping out
/// the full backoff step before re-checking `shutting_down`.
#[tokio::test]
async fn test_begin_shutdown_swaps_flag_and_wakes_notify_waiter() {
    let state = SidecarState::new();
    assert!(!state
        .shutting_down
        .load(std::sync::atomic::Ordering::SeqCst));

    // Register the waiter BEFORE the notify (mirrors a supervisor
    // task parked in — or about to enter — `notified()`).
    let waiter = state.shutdown_notify.notified();

    // First call: performs the swap and fires the wakeup.
    assert!(
        !state.begin_shutdown(),
        "first begin_shutdown must report the flag was previously clear"
    );
    assert!(
        state
            .shutting_down
            .load(std::sync::atomic::Ordering::SeqCst),
        "begin_shutdown must set shutting_down"
    );
    // Second call: idempotent swap — reports the flag was already set.
    assert!(
        state.begin_shutdown(),
        "second begin_shutdown must report the flag was already set"
    );

    // The pre-registered waiter completes on its first poll via the
    // stored permit — no sleep, no re-poll. This is the property that
    // makes the shutdown wakeup Notify-based (sub-ms) rather than
    // poll-based (up to one full backoff step of latency).
    tokio::time::timeout(Duration::from_secs(1), waiter)
        .await
        .expect("notified() waiter must complete via the stored permit");
}

// ── SidecarHandle::Drop ────────────────────────────────────────

/// `SidecarHandle::ShellPlugin(None)` must be constructible and
/// droppable without panic. This pins the `Option<CommandChild>`
/// wrapper added so the Drop impl can `take()` the child out of
/// `&mut self`. The `None` state is what `kill()` / `kill_tree()`
/// leave behind after they consume the inner child — Drop on that
/// state must be a no-op (no double-kill, no panic).
#[test]
fn test_shell_plugin_none_drops_cleanly() {
    let h = SidecarHandle::ShellPlugin(None);
    assert_eq!(h.pid(), None);
    drop(h);
}

/// `SidecarHandle::ShellPlugin(None).kill().await` and
/// `.kill_tree().await` must both return Ok — the kill call on an
/// already-taken handle is a no-op. This pins the "no double-kill"
/// contract: when kill_tree() internally calls kill() at the end,
/// and Drop runs on the consumed value, both see None and are
/// no-ops.
#[tokio::test]
async fn test_shell_plugin_none_kill_returns_ok() {
    let h = SidecarHandle::ShellPlugin(None);
    let result = h.kill().await;
    assert!(
        result.is_ok(),
        "kill() on ShellPlugin(None) must return Ok: {:?}",
        result
    );

    let h2 = SidecarHandle::ShellPlugin(None);
    let result2 = h2.kill_tree().await;
    assert!(
        result2.is_ok(),
        "kill_tree() on ShellPlugin(None) must return Ok: {:?}",
        result2
    );
}

/// `SidecarHandle::DevMode` must kill the child process on Drop
/// via `kill_on_drop(true)` (set in `spawn_sidecar_dev_mode`).
/// This pins the contract for the DevMode variant: even
/// though `SidecarHandle::Drop` is a no-op for DevMode, the inner
/// `tokio::process::Child`'s own Drop kills the process because
/// `kill_on_drop(true)` was set at construction.
#[cfg(unix)]
#[tokio::test]
async fn test_devmode_drop_kills_child_when_kill_on_drop_set() {
    use std::time::Duration;

    // Spawns a REAL child of the test binary — serialize against the
    // own-pid enumeration tests (see test_support.rs). The guard is
    // non-Send and held across `.await`, which only compiles on the
    // default `current_thread` tokio flavor.
    let _child_lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());

    let mut cmd = tokio::process::Command::new("sleep");
    cmd.arg("30").kill_on_drop(true);
    cmd.stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    let child = cmd.spawn().expect("failed to spawn `sleep 30` for test");
    let pid = child
        .id()
        .expect("child must have a pid immediately after spawn");

    let handle = SidecarHandle::DevMode(child);
    drop(handle);

    tokio::time::sleep(Duration::from_millis(200)).await;

    let still_alive = unsafe { libc::kill(pid as i32, 0) == 0 };
    assert!(
        !still_alive,
        "DevMode child must be killed by Drop (kill_on_drop=true) — pid {} is still alive",
        pid
    );
}

/// Regression guard: when `kill_on_drop(true)` is NOT set (the
/// negative case), dropping `SidecarHandle::DevMode` does NOT kill
/// the child. This test documents the contract that
/// `spawn_sidecar_dev_mode` MUST set `kill_on_drop(true)` —
/// otherwise the DevMode Drop path leaks the process. We clean up
/// the leaked child at the end so the test doesn't leave a zombie.
#[cfg(unix)]
#[tokio::test]
async fn test_devmode_drop_does_not_kill_when_kill_on_drop_unset() {
    use std::time::Duration;

    // Spawns a REAL child of the test binary — serialize against the
    // own-pid enumeration tests (see test_support.rs). The guard is
    // non-Send and held across `.await`, which only compiles on the
    // default `current_thread` tokio flavor.
    let _child_lock = CHILD_PROCESS_TEST_LOCK
        .lock()
        .unwrap_or_else(|e| e.into_inner());

    let mut cmd = tokio::process::Command::new("sleep");
    cmd.arg("30")
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
    let child = cmd.spawn().expect("failed to spawn `sleep 30` for test");
    let pid = child
        .id()
        .expect("child must have a pid immediately after spawn");

    let handle = SidecarHandle::DevMode(child);
    drop(handle);
    tokio::time::sleep(Duration::from_millis(200)).await;

    let still_alive = unsafe { libc::kill(pid as i32, 0) == 0 };
    assert!(
        still_alive,
        "regression guard: without kill_on_drop(true), DevMode Drop must NOT kill the child"
    );

    let _ = crate::platform::process::kill_process_tree(pid);
    // Settle so tokio's process driver reaps the just-killed child
    // BEFORE this test returns and releases CHILD_PROCESS_TEST_LOCK.
    // Otherwise the zombie can linger in
    // /proc/<test_pid>/task/<test_pid>/children for a sub-ms window
    // and flake the own-pid empty-assertion tests that acquire the
    // lock next (see test_support.rs CHILD_PROCESS_TEST_LOCK).
    tokio::time::sleep(Duration::from_millis(100)).await;
}

// ── WorkerState (Phase 2a — runtime-pack split, §7) ─────────────────
//
// Tests for the WorkerState struct (parallel to SidecarState for the
// ML worker exe). The struct mirrors SidecarState's field set +
// adds `auth_token` + `lock_file_path` (both `OnceLock`s). These
// tests pin the constructor contract: every field starts in the
// "no worker spawned" state (None / false / 0 / empty OnceLock).

/// `WorkerState::new()` must initialize `child` to `None`. The
/// `Option<SidecarHandle>` is the worker child slot — `None` means
/// "no worker spawned yet" (the slim-core sidecar hasn't connected,
/// the worker hasn't been started, OR the worker was killed and is
/// awaiting respawn).
#[test]
fn test_worker_state_new_child_is_none() {
    let state = WorkerState::new();
    assert!(
        state
            .child
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_none(),
        "fresh WorkerState must have child = None"
    );
    let default_state = WorkerState::default();
    assert!(
        default_state
            .child
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_none(),
        "WorkerState::default() must have child = None"
    );
}

/// `WorkerState::new()` must initialize `ws_tx` to `None`. The WS
/// writer channel is `None` until the slim-core sidecar connects to
/// the worker as a WS client (§7.1 — new 1-host↔2-processes pattern).
#[tokio::test]
async fn test_worker_state_new_ws_tx_is_none() {
    let state = WorkerState::new();
    assert!(
        state
            .ws_tx
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .is_none(),
        "fresh WorkerState must have ws_tx = None"
    );
}

/// `WorkerState::new()` must initialize `pending` to an empty
/// `HashMap` (no in-flight RPC requests to the worker). Mirrors
/// `SidecarState::pending`'s constructor contract.
#[tokio::test]
async fn test_worker_state_new_pending_is_empty() {
    let state = WorkerState::new();
    assert!(
        state.pending.lock().await.is_empty(),
        "fresh WorkerState must have an empty pending map"
    );
}

/// `WorkerState::new()` must initialize `next_id` to 1 (NOT 0 —
/// request id 0 is reserved as a sentinel for "no request" in the
/// WS dispatch path, matching `SidecarState::next_id`'s convention).
#[test]
fn test_worker_state_new_next_id_is_one() {
    let state = WorkerState::new();
    assert_eq!(
        state.next_id.load(std::sync::atomic::Ordering::SeqCst),
        1,
        "fresh WorkerState must have next_id = 1 (0 is reserved as 'no request')"
    );
}

/// `WorkerState::new()` must initialize `shutting_down` to `false`.
/// The flag flips to `true` only when `shutdown_worker_for_exit` is
/// called (the host is quitting). SEPARATE from
/// `SidecarState::shutting_down` per §7.2 — the worker's lifecycle
/// is independent of the sidecar's.
#[test]
fn test_worker_state_new_shutting_down_is_false() {
    let state = WorkerState::new();
    assert!(
        !state
            .shutting_down
            .load(std::sync::atomic::Ordering::SeqCst),
        "fresh WorkerState must have shutting_down = false"
    );
}

/// `WorkerState::new()` must initialize `respawn_in_progress` to
/// `false`. The flag is the worker respawn serialization guard
/// (parallel to `SidecarState::respawn_in_progress`). SEPARATE from
/// the sidecar's flag per §7.2 — worker crashes must NOT trip the
/// sidecar's circuit breaker.
#[test]
fn test_worker_state_new_respawn_in_progress_is_false() {
    let state = WorkerState::new();
    assert!(
        !state
            .respawn_in_progress
            .load(std::sync::atomic::Ordering::SeqCst),
        "fresh WorkerState must have respawn_in_progress = false"
    );
}

/// `WorkerState::new()` must initialize `child_exit_rx` to `None`.
/// The exit receiver is installed by the future `initialize_worker`
/// after the worker spawn succeeds (parallel to
/// `SidecarState::child_exit_rx`).
#[tokio::test]
async fn test_worker_state_new_child_exit_rx_is_none() {
    let state = WorkerState::new();
    assert!(
        state.child_exit_rx.lock().await.is_none(),
        "fresh WorkerState must have child_exit_rx = None"
    );
}

/// `WorkerState::new()` must initialize `heartbeat_handle` to `None`.
/// The heartbeat task is spawned by the future `reconnect_worker_ws`
/// on every successful reconnect (parallel to
/// `SidecarState::heartbeat_handle`).
#[tokio::test]
async fn test_worker_state_new_heartbeat_handle_is_none() {
    let state = WorkerState::new();
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "fresh WorkerState must have heartbeat_handle = None"
    );
    let default_state = WorkerState::default();
    assert!(
        default_state.heartbeat_handle.lock().await.is_none(),
        "WorkerState::default() must have heartbeat_handle = None"
    );
}

/// `WorkerState::new()` must initialize `ws_generation` to 0 (NOT 1 —
/// 0 is the "never connected" sentinel, distinguishable from any live
/// generation ≥ 1, matching `SidecarState::ws_generation`'s contract).
#[test]
fn test_worker_state_new_ws_generation_is_zero() {
    let state = WorkerState::new();
    assert_eq!(
        state
            .ws_generation
            .load(std::sync::atomic::Ordering::SeqCst),
        0,
        "fresh WorkerState must have ws_generation = 0 (never connected)"
    );
}

/// `WorkerState::new()` must initialize `auth_token` to an EMPTY
/// `OnceLock` (i.e. `get()` returns `None`). The token is set
/// lazily on first worker spawn via `util::generate_token()`. The
/// `OnceLock` ensures the token is generated exactly once per host
/// launch + survives worker respawns (the worker inherits the
/// host's token across respawns per §7.2).
#[test]
fn test_worker_state_new_auth_token_is_unset() {
    let state = WorkerState::new();
    assert!(
        state.auth_token.get().is_none(),
        "fresh WorkerState must have auth_token unset (OnceLock not yet set)"
    );
}

/// `WorkerState::new()` must initialize `lock_file_path` to an EMPTY
/// `OnceLock`. The lock path is resolved lazily on first worker
/// spawn via
/// `worker_path::worker_exe_path().with_file_name("worker.lock")`
/// (parallel to `VoiceTyperSingleInstance`'s lock file pattern).
#[test]
fn test_worker_state_new_lock_file_path_is_unset() {
    let state = WorkerState::new();
    assert!(
        state.lock_file_path.get().is_none(),
        "fresh WorkerState must have lock_file_path unset (OnceLock not yet set)"
    );
}

/// `WorkerState::auth_token`'s `OnceLock::set` must succeed on the
/// first call + fail (return Err) on subsequent calls. This pins
/// the "token is set ONCE per host launch" contract — a future
/// refactor that calls `set()` on every respawn would silently
/// ignore the second `set()`, preserving the original token (which
/// is the correct behavior for the §7.2 worker-inherits-token
/// contract).
#[test]
fn test_worker_state_auth_token_set_once_is_idempotent() {
    let state = WorkerState::new();
    let first = state.auth_token.set("token-v1".to_string());
    assert!(
        first.is_ok(),
        "first auth_token.set must succeed (OnceLock was empty)"
    );
    let second = state.auth_token.set("token-v2".to_string());
    assert!(
        second.is_err(),
        "second auth_token.set must fail (OnceLock already set — preserves the original token)"
    );
    // The token must remain the first one set.
    assert_eq!(
        state.auth_token.get().map(|s| s.as_str()),
        Some("token-v1"),
        "auth_token must retain the FIRST set value (OnceLock contract)"
    );
}

/// `WorkerState::auth_token` must accept an empty string (defensive —
/// the future `initialize_worker` should NOT pass an empty string,
/// but the `OnceLock` itself doesn't validate). The contract is:
/// `util::generate_token()` always returns a 64-char hex string
/// (pinned by `util_tests::test_generate_token_is_64_char_hex`), so
/// an empty token in production would be a bug in `generate_token`,
/// not in `WorkerState`. This test pins that the `OnceLock` doesn't
/// add its own validation layer.
#[test]
fn test_worker_state_auth_token_accepts_empty_string() {
    let state = WorkerState::new();
    let result = state.auth_token.set(String::new());
    assert!(
        result.is_ok(),
        "auth_token.set must accept an empty string (OnceLock has no validation layer)"
    );
    assert_eq!(
        state.auth_token.get().map(|s| s.as_str()),
        Some(""),
        "auth_token.get must return the empty string that was set"
    );
}

/// `WorkerState::lock_file_path`'s `OnceLock::set` must succeed on
/// the first call + fail on subsequent calls (mirrors the
/// `auth_token` contract).
#[test]
fn test_worker_state_lock_file_path_set_once_is_idempotent() {
    let state = WorkerState::new();
    let first = state
        .lock_file_path
        .set(std::path::PathBuf::from("/tmp/worker.lock"));
    assert!(
        first.is_ok(),
        "first lock_file_path.set must succeed (OnceLock was empty)"
    );
    let second = state
        .lock_file_path
        .set(std::path::PathBuf::from("/tmp/other.lock"));
    assert!(
        second.is_err(),
        "second lock_file_path.set must fail (OnceLock already set)"
    );
    assert_eq!(
        state.lock_file_path.get(),
        Some(&std::path::PathBuf::from("/tmp/worker.lock")),
        "lock_file_path must retain the FIRST set value"
    );
}

/// `WorkerState::shutdown_notify` must be a fresh `Notify` (no stored
/// permit). Calling `notified()` without a prior `notify_one()` must
/// await indefinitely (we test this with a 10ms timeout — a fresh
/// Notify should NOT resolve within 10ms).
///
/// Mirrors `SidecarState::shutdown_notify`'s constructor contract.
#[tokio::test]
async fn test_worker_state_new_shutdown_notify_has_no_permit() {
    let state = Arc::new(WorkerState::new());
    let state_clone = state.clone();
    let join = tokio::spawn(async move {
        // This should NOT resolve within 10ms (fresh Notify has no
        // stored permit).
        let result = tokio::time::timeout(
            Duration::from_millis(10),
            state_clone.shutdown_notify.notified(),
        )
        .await;
        result.is_err() // true = timed out = no permit (correct)
    });
    let timed_out = join.await.unwrap_or(false);
    assert!(
        timed_out,
        "fresh WorkerState.shutdown_notify must have no stored permit (notified() must not resolve immediately)"
    );
}

/// `WorkerState::shutdown_notify.notify_one()` must wake a task
/// awaiting `notified()` (the cancellation contract for the worker
/// supervisor's backoff sleep). Mirrors `SidecarState::shutdown_notify`'s
/// wake contract.
#[tokio::test]
async fn test_worker_state_shutdown_notify_wakes_waiter() {
    let state = Arc::new(WorkerState::new());
    let state_clone = state.clone();
    let join = tokio::spawn(async move {
        // Wait for the notify_one() — should resolve quickly once
        // the parent fires it.
        state_clone.shutdown_notify.notified().await;
        true
    });
    // Give the waiter a moment to register.
    tokio::time::sleep(Duration::from_millis(10)).await;
    state.shutdown_notify.notify_one();
    let woken = tokio::time::timeout(Duration::from_millis(100), join)
        .await
        .map(|r| r.unwrap_or(false))
        .unwrap_or(false);
    assert!(
        woken,
        "shutdown_notify.notify_one() must wake a task awaiting notified()"
    );
}

/// `WorkerState` must be shareable via `Arc<WorkerState>` (the
/// production shape — `main.rs::setup` will install
/// `Arc::new(WorkerState::new())` via `app.manage(...)`). This test
/// pins that `WorkerState` is `Send + Sync` (required for `Arc`
/// shared across Tokio tasks). If a future field accidentally breaks
/// `Send`/`Sync` (e.g. adding a `Rc<...>` field), this test fails
/// at compile time.
#[test]
fn test_worker_state_is_send_sync_and_arcable() {
    fn assert_send_sync<T: Send + Sync>() {}
    assert_send_sync::<WorkerState>();
    let _ = |state: WorkerState| {
        let _: Arc<WorkerState> = Arc::new(state);
    };
    // Also verify Arc<WorkerState> is itself Send + Sync (required for
    // `tauri::State<'_, Arc<WorkerState>>` in command handlers).
    fn assert_arc_send_sync<T: Send + Sync>() {}
    assert_arc_send_sync::<Arc<WorkerState>>();
}
