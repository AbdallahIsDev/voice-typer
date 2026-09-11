#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation
)]

//! Unit tests for `sidecar::ws::heartbeat`.
//!
//! Extracted from the inline `#[cfg(test)] mod tests { ... }` block that
//! previously lived at the bottom of `heartbeat.rs`. The split
//! satisfies C-TEST-5 (no inline test code in production source files).
//!
//! Tests are wired via `#[cfg(test)] #[path = "heartbeat_tests.rs"]
//! mod heartbeat_tests;` declared in `heartbeat.rs` (same convention as
//! `sidecar/spawn.rs` → `spawn_tests.rs`), so `use super::*` below
//! resolves to the `heartbeat` module.

use super::*;
use std::sync::Arc;
use std::time::Duration;

// heartbeat task abort on reconnect ────────────────────

#[tokio::test]
async fn test_gt8_heartbeat_handle_slot_round_trips_take_abort_replace() {
    let state = Arc::new(crate::state::SidecarState::new());
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "GT-8: fresh state must have heartbeat_handle = None"
    );

    let h1 = tauri::async_runtime::spawn(async {
        tokio::time::sleep(Duration::from_secs(60)).await;
    });
    *state.heartbeat_handle.lock().await = Some(h1);
    assert!(state.heartbeat_handle.lock().await.is_some());

    // Simulate a second reconnect: take + abort + replace.
    let prev = state.heartbeat_handle.lock().await.take();
    assert!(prev.is_some());
    if let Some(h) = prev {
        h.abort();
    }
    assert!(state.heartbeat_handle.lock().await.is_none());

    let h2 = tauri::async_runtime::spawn(async {
        tokio::time::sleep(Duration::from_secs(60)).await;
    });
    *state.heartbeat_handle.lock().await = Some(h2);
    assert!(state.heartbeat_handle.lock().await.is_some());

    // Cleanup.
    let mut guard = state.heartbeat_handle.lock().await;
    if let Some(h) = guard.take() {
        h.abort();
    }
}

/// `shutdown_sidecar_for_exit` must abort any in-flight
/// heartbeat task stored on `state.heartbeat_handle`.
///
/// The handle is aborted + cleared EARLY in the function (before the
/// graceful-exit wait). In dev-mode (`child_exit_rx = None`) that
/// wait sleeps `EXIT_SHUTDOWN_ACK_TIMEOUT_MS` (30s) before the
/// force-kill backstop, so this test runs the shutdown on the async
/// runtime and polls for the early handle-clear instead of awaiting
/// full completion.
#[tokio::test]
async fn test_gt8_shutdown_sidecar_for_exit_aborts_heartbeat_handle() {
    use crate::state::shutdown_sidecar_for_exit;
    let state = Arc::new(crate::state::SidecarState::new());
    let h = tauri::async_runtime::spawn(async {
        tokio::time::sleep(Duration::from_secs(60)).await;
    });
    *state.heartbeat_handle.lock().await = Some(h);
    assert!(state.heartbeat_handle.lock().await.is_some());

    let state_clone = state.clone();
    let shutdown_task = tauri::async_runtime::spawn(async move {
        shutdown_sidecar_for_exit(&state_clone).await;
    });

    // Poll for the early handle-clear with a bounded deadline. The
    // 20ms interval is intentional (event-based poll, not a fixed
    // sleep: matches the XS-53 bounded-polling pattern elsewhere).
    tokio::time::timeout(Duration::from_millis(3000), async {
        loop {
            if state.heartbeat_handle.lock().await.is_none() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("GT-8: shutdown_sidecar_for_exit must abort + clear the heartbeat handle within 3s");

    // Stop the spawned shutdown task, its remaining 30s dev-mode
    // sleep is irrelevant to the assertion above.
    shutdown_task.abort();
}

// abort_heartbeat helper ────────────────────────────

/// `abort_heartbeat` must clear the `heartbeat_handle`
/// slot and abort the in-flight task. Verifies the helper is
/// callable and idempotent: the two shutdown paths
/// (`shutdown_sidecar_for_exit` in state.rs, `shutdown_sidecar` in
/// sidecar_cmds.rs) both need to call it safely even if the other
/// path already ran.
#[tokio::test]
async fn test_ue8_f10_abort_heartbeat_clears_handle_and_aborts_task() {
    let state = Arc::new(crate::state::SidecarState::new());
    // Spawn a long-running task (sleep 60s, well beyond the test
    // timeout). The heartbeat task in production runs an infinite
    // loop; a 60s sleep simulates "in-flight" for the test window.
    let h = tauri::async_runtime::spawn(async {
        tokio::time::sleep(Duration::from_secs(60)).await;
    });
    *state.heartbeat_handle.lock().await = Some(h);
    assert!(
        state.heartbeat_handle.lock().await.is_some(),
        "precondition: heartbeat_handle must be Some before abort_heartbeat"
    );

    // Call the abort_heartbeat helper.
    abort_heartbeat(&state).await;

    // The handle must be cleared.
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "UE-8-F10: abort_heartbeat must clear the heartbeat handle"
    );

    // Calling abort_heartbeat again must be a no-op (idempotent) —
    // a second shutdown path arriving after the first must not panic.
    abort_heartbeat(&state).await;
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "UE-8-F10: abort_heartbeat must be idempotent on a None handle"
    );
}

/// `abort_heartbeat` on a fresh state (handle is None)
/// must be a no-op without panicking. Pins the "idempotent on empty"
/// contract for the cold-start path where no heartbeat has been
/// spawned yet but a shutdown is initiated.
#[tokio::test]
async fn test_ue8_f10_abort_heartbeat_on_fresh_state_is_noop() {
    let state = Arc::new(crate::state::SidecarState::new());
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "precondition: fresh state must have heartbeat_handle = None"
    );
    // Calling on a fresh state must not panic.
    abort_heartbeat(&state).await;
    assert!(
        state.heartbeat_handle.lock().await.is_none(),
        "UE-8-F10: abort_heartbeat on fresh state must leave handle as None"
    );
}

// dispatch cancellation cleans up its pending entry ────────────────────

/// Cancelling an in-flight `dispatch_inner` future, exactly what the
/// heartbeat task's liveness-probe wrapper does at its response
/// deadline (`tokio::time::timeout` drops the inner future), must NOT
/// leak the dispatch's pending-map entry.
///
/// The dispatch's own timeout arm (the pending-map removal in
/// `dispatch_frame`) runs at the SAME 15s deadline as the heartbeat's
/// outer wrapper for `cmd="heartbeat"` (both route to the short
/// timeout), and a dropped future's timeout branch never fires at all:
/// without a Drop guard on the pending entry, the entry lingered until
/// a late response, the reader's exit drain, or the miss-#3 supervisor
/// respawn cleared it. This test pins the self-cleaning contract: the
/// dropped future removes its own bookkeeping immediately.
///
/// Cancellation is driven with `tokio::select!`: the cancel branch
/// completes only AFTER the pending entry is observable, so the drop
/// is guaranteed to land while the dispatch is parked on its
/// unfulfilled response oneshot (never before the insert, the test
/// cannot pass vacuously). Nothing fulfills the oneshot (the WS-writer
/// receiver is held but never polled), mirroring a hung sidecar.
#[tokio::test]
async fn test_cancelled_dispatch_removes_its_pending_entry() {
    use crate::commands::sidecar_cmds::{dispatch_inner, DispatchArgs};
    use tokio_tungstenite::tungstenite::Message;

    let state = Arc::new(crate::state::SidecarState::new());
    // Wire a live WS-writer channel so `dispatch_frame` passes its
    // `ws_tx` Some-check, inserts its pending entry, and parks on the
    // response await. The receiver stays alive (never polled) so
    // `try_send` succeeds.
    let (ws_tx, _ws_rx_keepalive) = tokio::sync::mpsc::channel::<Message>(8);
    *crate::state::lock(&state.ws_tx) = Some(ws_tx);

    // Mirrors the heartbeat wrapper's cancellation: when this branch
    // wins, `select!` drops the in-flight dispatch future mid-await.
    let cancel_after_pending_entry_exists = async {
        loop {
            if state.pending.lock().await.len() >= 1 {
                break;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
    };

    let dispatch = dispatch_inner(
        DispatchArgs {
            cmd: "heartbeat".to_string(),
            data: None,
        },
        state.clone(),
    );

    tokio::select! {
        _ = cancel_after_pending_entry_exists => {
            // Cancellation fired mid-flight; `select!` drops the
            // dispatch future at this point.
        }
        result = dispatch => {
            panic!(
                "dispatch must park on its unfulfilled response oneshot; \
                 unexpected early completion: {:?}",
                result
            );
        }
    }

    // The dropped dispatch future must clean up its own pending entry
    // (Drop guard) instead of leaking it until a late response / the
    // reader drain / a supervisor respawn. Bounded poll (20ms
    // interval, matching the polling pattern of the other tests in
    // this file): the removal runs as a detached task on the global
    // Tauri runtime, not inline in the drop.
    tokio::time::timeout(Duration::from_secs(3), async {
        loop {
            if state.pending.lock().await.is_empty() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(20)).await;
        }
    })
    .await
    .expect("cancelled dispatch future must remove its pending-map entry");
}
