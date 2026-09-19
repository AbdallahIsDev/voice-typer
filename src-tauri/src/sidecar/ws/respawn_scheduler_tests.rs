#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation
)]

//! Unit tests for `sidecar::ws::respawn_scheduler` auth-cleanup gating.
//!
//! Wired via `#[cfg(test)] #[path = "respawn_scheduler_tests.rs"]
//! mod respawn_scheduler_tests;` in `respawn_scheduler.rs` (same
//! convention as `heartbeat.rs` → `heartbeat_tests.rs`, C-TEST-5).
//!
//! `cleanup_and_trigger_respawn` itself needs a `tauri::AppHandle`
//! (emit + supervisor trigger), which is infeasible in a unit test,
//! so these tests pin the gating predicate `auth_cleanup_is_stale`
//! plus the guarded clear + drain outcome on real `SidecarState`
//! transitions built with the real `queue_auth_and_store_ws_tx`.

use super::super::{drain_pending_with_disconnect_error, queue_auth_and_store_ws_tx};
use super::auth_cleanup_is_stale;
use crate::state::{lock as mutex_lock, SidecarState};
use serde_json::Value;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use tokio::sync::oneshot;

// Retry-during-startup race: an old connection's auth wait (gen=1)
// gives up AFTER a newer reconnect (gen=2) stored its own sender.
// The stale cleanup must skip the clear + drain so the live
// connection keeps working; only the None-generation respawn
// trigger still fires (C-WS-3, not exercised here: needs AppHandle).
#[tokio::test]
async fn test_auth_cleanup_stale_generation_skips_clear_and_drain() {
    let state = Arc::new(SidecarState::new());

    let (_old_ws_rx, gen1) = queue_auth_and_store_ws_tx(&state, "old-token")
        .await
        .expect("old queue_auth must succeed");
    let (new_ws_rx, gen2) = queue_auth_and_store_ws_tx(&state, "new-token")
        .await
        .expect("new queue_auth must succeed");
    assert_eq!((gen1, gen2), (1, 2));
    let _new_ws_rx_guard = new_ws_rx;

    let (pending_tx, mut pending_rx) = oneshot::channel::<Value>();
    state.pending.lock().await.insert(99u64, pending_tx);

    // The old connection's cleanup runs with its captured generation.
    assert!(
        auth_cleanup_is_stale(&state, gen1),
        "old generation must read as stale once a newer reconnect took over"
    );
    let current = state.ws_generation.load(Ordering::SeqCst);
    if current != gen1 {
        // Skip branch: log only, mirror of cleanup_and_trigger_respawn.
    } else {
        panic!("stale path must not reach the clear + drain branch");
    }

    assert!(
        mutex_lock(&state.ws_tx).is_some(),
        "new reconnect's sender must survive a stale auth cleanup"
    );
    assert_eq!(
        state.pending.lock().await.len(),
        1,
        "new connection's in-flight dispatch must not be drained"
    );
    assert!(
        matches!(
            pending_rx.try_recv(),
            Err(oneshot::error::TryRecvError::Empty)
        ),
        "pending oneshot must still be open (no disconnect error sent)"
    );
}

// Normal auth failure (no newer reconnect): the cleanup must clear
// the dead sender and drain pending so dispatches fail fast instead
// of hanging the full dispatch timeout.
#[tokio::test]
async fn test_auth_cleanup_current_generation_clears_and_drains() {
    let state = Arc::new(SidecarState::new());

    let (_ws_rx, gen1) = queue_auth_and_store_ws_tx(&state, "token")
        .await
        .expect("queue_auth must succeed");
    let _ws_rx_guard = _ws_rx;

    let (pending_tx, mut pending_rx) = oneshot::channel::<Value>();
    state.pending.lock().await.insert(7u64, pending_tx);

    assert!(
        !auth_cleanup_is_stale(&state, gen1),
        "matching generation must read as current"
    );
    let current = state.ws_generation.load(Ordering::SeqCst);
    if current != gen1 {
        panic!("current path must reach the clear + drain branch");
    } else {
        {
            let mut ws_tx_guard = mutex_lock(&state.ws_tx);
            *ws_tx_guard = None;
        }
        let drained = drain_pending_with_disconnect_error(&state).await;
        assert_eq!(drained, 1, "drain must reject the 1 in-flight dispatch");
    }

    assert!(
        mutex_lock(&state.ws_tx).is_none(),
        "dead sender must be cleared on current-generation cleanup"
    );
    assert_eq!(
        state.pending.lock().await.len(),
        0,
        "pending map must be empty after current-generation cleanup"
    );
    assert!(
        pending_rx.try_recv().is_ok(),
        "pending oneshot must be closed (drain sent disconnect error)"
    );
}
