//! Unit tests for `state.rs` (ADR-0020 §1, §10, §14).
//!
//! Moved verbatim from the inline `#[cfg(test)] mod tests` block in
//! `state.rs` as part of the C-TEST-5 test-isolation migration. No test
//! logic changed — only the module path adjusted (now a sibling of
//! `state` rather than a child). All items referenced by the tests were
//! already `pub(crate)`, so no visibility bumps were needed in
//! `state.rs`.

use crate::state::{shutdown_sidecar_for_exit, PendingMap, SidecarHandle, SidecarState};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{oneshot, Mutex as AsyncMutex};

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
}
