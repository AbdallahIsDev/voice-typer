//! Unit tests for `sidecar/ws.rs` (ADR-0020 §7, §9, §14).
//!
//! Moved verbatim from the inline `#[cfg(test)] mod tests` block in
//! `ws.rs` as part of the C-TEST-5 test-isolation migration. No test
//! logic changed — only the module path adjusted (now a sibling of
//! `ws` rather than a child). The private `queue_auth_and_store_ws_tx`
//! helper was bumped to `pub(super)` so the sibling test file (within
//! the `sidecar` parent module) can access it.

use super::ws::{
    drain_pending_with_disconnect_error, queue_auth_and_store_ws_tx, truncate_frame_text,
    WS_WRITER_CHANNEL_CAPACITY,
};
use crate::sidecar::bubble_coalesce::bubble_coalesce_should_emit;
use crate::state::{lock as mutex_lock, PendingMap};
use crate::util::BUBBLE_LEVEL_COALESCE_HZ;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, oneshot, Mutex as AsyncMutex};
use tokio_tungstenite::tungstenite::Message;

// pending-dispatch map (ADR-0020 §7) ────────────────────

#[tokio::test]
async fn test_pending_dispatch_map_fulfill_by_id() {
    // PendingMap no longer wrapped in outer Arc —
    // construct directly via `AsyncMutex::new(HashMap::new())`.
    let pending: PendingMap = AsyncMutex::new(HashMap::new());
    let id = 42u64;

    // Insert the pending request.
    let (tx, rx) = oneshot::channel::<Value>();
    pending.lock().await.insert(id, tx);
    assert_eq!(
        pending.lock().await.len(),
        1,
        "pending map should have 1 entry after insert"
    );

    // Simulate the WS reader fulfilling the request by id.
    let response = json!({"id": 42, "type": "result", "data": {"ok": true}});
    {
        let mut map = pending.lock().await;
        if let Some(sender) = map.remove(&id) {
            let _ = sender.send(response.clone());
        }
    }

    // The oneshot must resolve with the response (within a generous
    // 1s timeout — should be near-instant since the send already
    // happened).
    let received = tokio::time::timeout(Duration::from_secs(1), rx)
        .await
        .expect("oneshot did not resolve within 1s — sender was never invoked")
        .expect("oneshot sender was dropped without sending");
    assert_eq!(
        received, response,
        "received response must match the sent payload"
    );

    // Map must be empty after fulfillment.
    assert_eq!(
        pending.lock().await.len(),
        0,
        "pending map should be empty after fulfillment"
    );
}

#[tokio::test]
async fn test_pending_dispatch_map_unfulfilled_id_leaves_entry() {
    // PendingMap no longer wrapped in outer Arc.
    let pending: PendingMap = AsyncMutex::new(HashMap::new());
    let id = 99u64;
    let (tx, _rx) = oneshot::channel::<Value>();
    pending.lock().await.insert(id, tx);

    // Try to fulfill with the WRONG id (id=100, not 99).
    let wrong_response = json!({"id": 100, "type": "result"});
    {
        let mut map = pending.lock().await;
        // The reader task does `pending.remove(&id_from_frame)`, so
        // a mismatched id simply doesn't find an entry.
        if let Some(sender) = map.remove(&100u64) {
            let _ = sender.send(wrong_response.clone());
        }
    }

    // Entry for id=99 must still be present (the wrong-id fulfill
    // was a no-op from this map's perspective).
    assert_eq!(
        pending.lock().await.len(),
        1,
        "pending entry for id=99 must remain when a wrong-id response arrives"
    );
    assert!(
        pending.lock().await.contains_key(&id),
        "pending map must still contain id=99"
    );
}

// drain_pending_with_disconnect_error ─────────

/// the drain helper must send a `sidecar_disconnected` error
/// response to EVERY orphaned oneshot, and clear the pending map.
/// This pins the contract used by both `cleanup_and_trigger_respawn`
/// (auth-failure path) and the WS reader's cleanup block (normal
/// disconnect / panic path) — without the drain, in-flight dispatches
/// wait the full 120s timeout for a response that will never come.
#[tokio::test]
async fn test_ue8_drain_pending_sends_disconnect_error_to_all() {
    let state = Arc::new(crate::state::SidecarState::new());
    // Insert 3 pending entries with oneshot senders.
    let mut rx_list = Vec::new();
    for id in 1u64..=3u64 {
        let (tx, rx) = oneshot::channel::<Value>();
        state.pending.lock().await.insert(id, tx);
        rx_list.push(rx);
    }
    assert_eq!(state.pending.lock().await.len(), 3);

    // Call the drain helper.
    let drained = drain_pending_with_disconnect_error(&state).await;
    assert_eq!(drained, 3, "drain helper must report 3 drained entries");
    assert_eq!(
        state.pending.lock().await.len(),
        0,
        "pending map must be empty after drain"
    );

    // Each receiver must get a sidecar_disconnected error.
    for rx in rx_list {
        let received = tokio::time::timeout(Duration::from_secs(1), rx)
            .await
            .expect("oneshot did not resolve within 1s — drain helper did not send")
            .expect("oneshot sender was dropped without sending");
        assert_eq!(
            received["type"], "error",
            "drained response type must be \"error\""
        );
        assert_eq!(
            received["data"]["code"], "sidecar_disconnected",
            "drained response code must be \"sidecar_disconnected\""
        );
        assert!(
            received["data"]["message"].as_str().is_some(),
            "sidecar_disconnected error must have a message string"
        );
    }
}

/// the drain helper must be a no-op on an empty pending map
/// (returns 0, map stays empty). Both cleanup paths call the helper
/// unconditionally, so the empty case must not panic or log.
#[tokio::test]
async fn test_ue8_drain_pending_empty_map_returns_zero() {
    let state = Arc::new(crate::state::SidecarState::new());
    assert_eq!(state.pending.lock().await.len(), 0);
    let drained = drain_pending_with_disconnect_error(&state).await;
    assert_eq!(drained, 0, "drain helper on empty map must return 0");
    assert_eq!(state.pending.lock().await.len(), 0);
}

/// the drain helper must handle a receiver that was already
/// dropped (the dispatch caller timed out / was cancelled). `oneshot::
/// Sender::send` returns Err in that case — the helper must swallow
/// the error (it already uses `let _ =`) and continue draining the
/// rest. This pins the "swallow send-error" contract.
#[tokio::test]
async fn test_ue8_drain_pending_swallows_send_error_for_dropped_receiver() {
    let state = Arc::new(crate::state::SidecarState::new());
    // Insert 2 entries. Drop the FIRST receiver immediately (simulates
    // a dispatch caller that already timed out and dropped its
    // oneshot receiver). Keep the second receiver alive.
    let (tx1, rx1) = oneshot::channel::<Value>();
    drop(rx1); // simulate dropped receiver
    state.pending.lock().await.insert(1u64, tx1);
    let (tx2, rx2) = oneshot::channel::<Value>();
    state.pending.lock().await.insert(2u64, tx2);
    assert_eq!(state.pending.lock().await.len(), 2);

    // Drain — must not panic on the dropped receiver.
    let drained = drain_pending_with_disconnect_error(&state).await;
    assert_eq!(
        drained, 2,
        "drain helper must report 2 drained entries (both attempted)"
    );

    // The second receiver must still get its error response.
    let received = tokio::time::timeout(Duration::from_secs(1), rx2)
        .await
        .expect("oneshot #2 did not resolve within 1s")
        .expect("oneshot #2 sender was dropped without sending");
    assert_eq!(received["data"]["code"], "sidecar_disconnected");
    assert_eq!(state.pending.lock().await.len(), 0);
}

// ── ws_generation race guard ──────────────────────

/// a fresh `SidecarState` must have `ws_generation == 0`
/// (distinguishable from any live generation ≥1, which is bumped
/// by the first `queue_auth_and_store_ws_tx`).
#[test]
fn test_si15_fresh_state_has_ws_generation_zero() {
    let state = Arc::new(crate::state::SidecarState::new());
    assert_eq!(
        state.ws_generation.load(Ordering::SeqCst),
        0,
        "fresh SidecarState must start at ws_generation=0"
    );
}

/// `queue_auth_and_store_ws_tx` must (a) store a `ws_tx`
/// into `state.ws_tx`, (b) bump `ws_generation` by exactly 1 per
/// call, and (c) return the new generation so the caller can pass
/// it to the reader/writer cleanup blocks. Verifies the producer
/// side of the race guard.
#[tokio::test]
async fn test_si15_queue_auth_increments_ws_generation_and_stores_ws_tx() {
    let state = Arc::new(crate::state::SidecarState::new());
    assert_eq!(state.ws_generation.load(Ordering::SeqCst), 0);
    assert!(
        mutex_lock(&state.ws_tx).is_none(),
        "precondition: fresh state must have ws_tx = None"
    );

    // First reconnect — bumps generation 0 → 1, stores ws_tx.
    let (_ws_rx1, gen1) = queue_auth_and_store_ws_tx(&state, "token-1")
        .await
        .expect("first queue_auth must succeed");
    assert_eq!(gen1, 1, "first reconnect must return generation 1");
    assert_eq!(
        state.ws_generation.load(Ordering::SeqCst),
        1,
        "state must reflect generation 1 after first reconnect"
    );
    assert!(
        mutex_lock(&state.ws_tx).is_some(),
        "ws_tx must be Some after queue_auth"
    );

    // Second reconnect — bumps generation 1 → 2, stores a NEW ws_tx
    // (replacing the old one). This mirrors the supervisor's
    // reconnect-after-kill path.
    let (_ws_rx2, gen2) = queue_auth_and_store_ws_tx(&state, "token-2")
        .await
        .expect("second queue_auth must succeed");
    assert_eq!(gen2, 2, "second reconnect must return generation 2");
    assert_eq!(
        state.ws_generation.load(Ordering::SeqCst),
        2,
        "state must reflect generation 2 after second reconnect"
    );

    // Generations must be strictly monotonic.
    assert!(gen2 > gen1, "generations must be monotonic");
}

/// the reader/writer cleanup "generation guard" contract —
/// if the current `ws_generation` does NOT match the captured
/// `my_generation`, the cleanup must NOT clear `ws_tx`. This is
/// the core invariant of the race fix. We simulate the race
/// directly by manipulating the atomic + Mutex (no real WS needed).
#[tokio::test]
async fn test_si15_cleanup_generation_guard_skips_clear_on_mismatch() {
    let state = Arc::new(crate::state::SidecarState::new());

    // Simulate: old reconnect (gen=1) stored a ws_tx, then a NEW
    // reconnect (gen=2) replaced it. The old reader's cleanup is
    // now running with my_generation=1.
    let (_old_ws_rx, _gen1) = queue_auth_and_store_ws_tx(&state, "old-token")
        .await
        .expect("old queue_auth must succeed");
    let (new_ws_rx, gen2) = queue_auth_and_store_ws_tx(&state, "new-token")
        .await
        .expect("new queue_auth must succeed");
    let my_generation = 1u64; // the OLD reader's captured generation
    assert_eq!(gen2, 2, "precondition: new reconnect must be generation 2");
    assert_eq!(
        state.ws_generation.load(Ordering::SeqCst),
        2,
        "precondition: current generation must be 2"
    );
    // Keep the new ws_rx alive so the new sender isn't dropped
    // prematurely (mirrors the real writer task holding the rx).
    let _new_ws_rx_guard = new_ws_rx;

    // The NEW ws_tx is currently stored. Verify it's present.
    assert!(
        mutex_lock(&state.ws_tx).is_some(),
        "precondition: ws_tx must be Some (belongs to the new reconnect)"
    );

    // Run the OLD reader's cleanup logic (inlined here to mirror
    // the spawn_reader_task cleanup block at ws.rs ~1234-1250).
    {
        let current_generation = state.ws_generation.load(Ordering::SeqCst);
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        if current_generation == my_generation {
            *ws_tx_guard = None;
        }
        // else: SKIP the clear (this is the race guard).
    }

    // The new ws_tx must STILL be present — the old cleanup did NOT
    // clobber it because the generations mismatched (mine=1, current=2).
    assert!(
        mutex_lock(&state.ws_tx).is_some(),
        "ws_tx must survive an old-generation cleanup (race guard)"
    );
}

/// when the generations DO match (the normal, non-racy
/// disconnect case), the cleanup MUST clear `ws_tx`. This pins
/// that the generation guard doesn't accidentally skip the clear
/// on the happy path (which would leave a dead sender in place
/// and break dispatch's "fail fast" contract).
#[tokio::test]
async fn test_si15_cleanup_generation_guard_clears_on_match() {
    let state = Arc::new(crate::state::SidecarState::new());

    // Single reconnect — generation 1, no newer reconnect has run.
    let (_ws_rx, gen1) = queue_auth_and_store_ws_tx(&state, "token")
        .await
        .expect("queue_auth must succeed");
    let my_generation = gen1;
    assert_eq!(
        state.ws_generation.load(Ordering::SeqCst),
        my_generation,
        "precondition: current generation must match the captured one"
    );
    assert!(
        mutex_lock(&state.ws_tx).is_some(),
        "precondition: ws_tx must be Some before cleanup"
    );

    // Run the cleanup logic with matching generations.
    {
        let current_generation = state.ws_generation.load(Ordering::SeqCst);
        let mut ws_tx_guard = mutex_lock(&state.ws_tx);
        if current_generation == my_generation {
            *ws_tx_guard = None;
        }
    }

    // ws_tx must have been cleared (generations matched → no race).
    assert!(
        mutex_lock(&state.ws_tx).is_none(),
        "ws_tx must be cleared when generations match (normal disconnect path)"
    );
}

/// delayed-cleanup race scenario: an OLD reader (generation 1)
/// exits while a NEW reconnect (generation 2) has already stored its
/// own `ws_tx` and is mid-auth. The old reader's cleanup block must
/// NOT (a) clear the new `ws_tx`, (b) drain the new connection's
/// pending dispatch map, or (c) trigger a spurious supervisor
/// respawn on top of the new reconnect's own recovery. All three
/// side effects are gated on the same `current_generation ==
/// my_generation` predicate; this test exercises the predicate
/// directly (mirroring the inline structure of the cleanup block at
/// `spawn_reader_task` ~line 1267) and asserts the drain branch is
/// skipped on mismatch. The respawn-trigger branch shares the same
/// `if` block, so a mismatched generation skips it transitively
/// (constructing a `tauri::AppHandle` in a unit test is infeasible,
/// so the trigger call itself is not exercised here — the gating
/// predicate is the unit under test, not the spawn-thread bridge).
#[tokio::test]
async fn test_cleanup_delayed_reader_skips_drain_and_respawn_on_generation_mismatch() {
    let state = Arc::new(crate::state::SidecarState::new());

    // Old reconnect (gen=1) stored a ws_tx, then a NEW reconnect
    // (gen=2) replaced it. The old reader's cleanup is now running
    // with my_generation=1 — its `read.next()` returned None late
    // (Tokio runtime contention) after the new reconnect's auth
    // already completed.
    let (_old_ws_rx, _gen1) = queue_auth_and_store_ws_tx(&state, "old-token")
        .await
        .expect("old queue_auth must succeed");
    let (new_ws_rx, gen2) = queue_auth_and_store_ws_tx(&state, "new-token")
        .await
        .expect("new queue_auth must succeed");
    let my_generation = 1u64; // the OLD reader's captured generation
    assert_eq!(gen2, 2, "precondition: new reconnect must be generation 2");
    // Keep the new ws_rx alive so the new sender isn't dropped
    // prematurely (mirrors the real writer task holding the rx).
    let _new_ws_rx_guard = new_ws_rx;

    // The NEW connection has a pending dispatch in flight — the
    // new reader will fulfill it when the sidecar responds. An old
    // reader's cleanup MUST NOT drain this entry; doing so would
    // reject the new connection's in-flight dispatch with a
    // spurious `sidecar_disconnected` error.
    let pending_id = 99u64;
    let (pending_tx, mut pending_rx) = oneshot::channel::<Value>();
    {
        let mut pending = state.pending.lock().await;
        pending.insert(pending_id, pending_tx);
    }
    assert_eq!(
        state.pending.lock().await.len(),
        1,
        "precondition: new connection has 1 in-flight dispatch"
    );

    // Mirror the new gated cleanup block at `spawn_reader_task`
    // ~line 1267. On generation mismatch, NONE of the three side
    // effects (ws_tx clear, drain pending, respawn trigger) run —
    // the else branch only logs.
    let current_generation = state.ws_generation.load(Ordering::SeqCst);
    if current_generation == my_generation {
        {
            let mut ws_tx_guard = mutex_lock(&state.ws_tx);
            *ws_tx_guard = None;
        }
        let _count = drain_pending_with_disconnect_error(&state).await;
        // (respawn trigger elided — requires tauri::AppHandle;
        //  gating predicate is the unit under test, see test doc.)
    } else {
        // mismatch path — log only, no side effects.
    }

    // Assert: ws_tx survived (new reconnect's sender intact).
    assert!(
        mutex_lock(&state.ws_tx).is_some(),
        "ws_tx must survive an old-generation cleanup (race guard)"
    );
    // Assert: pending map was NOT drained — the new connection's
    // in-flight dispatch is still waiting for its response.
    assert_eq!(
        state.pending.lock().await.len(),
        1,
        "pending map must NOT be drained by an old-generation cleanup"
    );
    // The pending oneshot sender must still be live (i.e. the
    // receiver hasn't been fulfilled with a disconnect error). A
    // drain would have sent a `sidecar_disconnected` value, so a
    // successful `try_recv()` (or `Err(Closed)`) would prove the
    // channel was closed — `Err(Empty)` means it's still open.
    assert!(
        matches!(
            pending_rx.try_recv(),
            Err(tokio::sync::oneshot::error::TryRecvError::Empty)
        ),
        "pending dispatch oneshot must NOT be closed — old-generation cleanup must not drain it"
    );
}

/// symmetric positive case: when the generations DO match (the
/// normal, non-racy disconnect), the cleanup block MUST drain the
/// pending map so in-flight dispatches get a `sidecar_disconnected`
/// error instead of waiting the full 120s timeout. Pairs with the
/// mismatch test above to pin that the drain gate fires both ways.
#[tokio::test]
async fn test_cleanup_drains_pending_on_generation_match() {
    let state = Arc::new(crate::state::SidecarState::new());

    let (_ws_rx, gen1) = queue_auth_and_store_ws_tx(&state, "token")
        .await
        .expect("queue_auth must succeed");
    let my_generation = gen1;
    let _ws_rx_guard = _ws_rx;

    let pending_id = 7u64;
    let (pending_tx, mut pending_rx) = oneshot::channel::<Value>();
    state.pending.lock().await.insert(pending_id, pending_tx);
    assert_eq!(
        state.pending.lock().await.len(),
        1,
        "precondition: 1 in-flight dispatch before cleanup"
    );

    // Mirror the cleanup block on the matching-generation path.
    let current_generation = state.ws_generation.load(Ordering::SeqCst);
    if current_generation == my_generation {
        {
            let mut ws_tx_guard = mutex_lock(&state.ws_tx);
            *ws_tx_guard = None;
        }
        let count = drain_pending_with_disconnect_error(&state).await;
        assert_eq!(count, 1, "drain must reject the 1 in-flight dispatch");
    }

    assert!(
        mutex_lock(&state.ws_tx).is_none(),
        "ws_tx must be cleared on matching-generation cleanup"
    );
    assert_eq!(
        state.pending.lock().await.len(),
        0,
        "pending map must be empty after matching-generation cleanup"
    );
    // The drain sent a `sidecar_disconnected` value through the
    // oneshot sender, so `try_recv()` must return `Ok(..)`.
    assert!(
        pending_rx.try_recv().is_ok(),
        "pending dispatch oneshot must be closed (drain sent disconnect error)"
    );
}

// ── WS writer channel capacity ───────────────────────────────────

/// The WS writer channel capacity must be 64 (not the previous
/// 256). At MAX_FRAME_BYTES = 1 MiB, this caps worst-case queued
/// memory at 64 MiB (vs 256 MiB at the old cap). 64 is still
/// large enough to absorb the sidecar-startup burst (config +
/// state + bubble-init frames) but small enough that a stuck WS
/// writer task fails fast (TrySendError::Full) instead of
/// ballooning memory.
///
/// If this test fails, either:
/// - someone changed the cap without updating this regression
///   guard (deliberate change — update the assertion); or
/// - someone removed the named constant and went back to an
///   inline magic number (regression — restore the constant).
#[test]
fn test_ws_writer_channel_capacity_is_64() {
    assert_eq!(
        WS_WRITER_CHANNEL_CAPACITY, 64,
        "WS writer channel capacity must be 64 (was 256 — see comment on the constant)"
    );
}

/// `queue_auth_and_store_ws_tx` must construct a bounded channel
/// of capacity `WS_WRITER_CHANNEL_CAPACITY` (not unbounded, not a
/// different magic number). We verify this by attempting to send
/// `WS_WRITER_CHANNEL_CAPACITY` frames successfully (no Full) and
/// then asserting the next send returns `TrySendError::Full`. This
/// pins the capacity at the constant value end-to-end (any change
/// to the constant would surface here automatically).
#[tokio::test]
async fn test_queue_auth_creates_bounded_channel_at_capacity() {
    let state = Arc::new(crate::state::SidecarState::new());

    let (mut ws_rx, _gen) = queue_auth_and_store_ws_tx(&state, "auth-token")
        .await
        .expect("queue_auth must succeed on a fresh state");

    // The auth frame is the very first frame queued inside
    // queue_auth_and_store_ws_tx — so the channel currently has
    // 1 frame in flight (the auth frame). We can send
    // (WS_WRITER_CHANNEL_CAPACITY - 1) more frames before Full.
    let ws_tx = mutex_lock(&state.ws_tx)
        .clone()
        .expect("ws_tx must be Some after queue_auth");

    for i in 0..(WS_WRITER_CHANNEL_CAPACITY - 1) {
        let frame = Message::Text(format!("frame-{}", i).into());
        ws_tx
            .try_send(frame)
            .expect("send within capacity must succeed (no Full)");
    }

    // The channel is now full (auth + capacity-1 = capacity
    // frames). The next send MUST return TrySendError::Full.
    let overflow = ws_tx.try_send(Message::Text("overflow".into()));
    assert!(
        matches!(overflow, Err(mpsc::error::TrySendError::Full(_))),
        "send at capacity+1 must return TrySendError::Full (got {:?}) — \
         if this fails, the channel is unbounded or has the wrong capacity",
        overflow
    );

    ws_rx.close();
}

// ── bubble_level coalesce-emit payload identity ──────────────────
//
// The simplified `bubble_level` emit path (no `last_bubble_payload`
// Option) must:
// 1. Use the CURRENT frame's `payload` for both the specific
//    `bubble_level` emit AND the generic `python-event` catch-all —
//    no stale-payload retention from a prior suppressed frame.
// 2. Coalesce at ≤30 Hz (delegates to `bubble_coalesce_should_emit`,
//    already unit-tested in `bubble_coalesce.rs`).
//
// We mirror the inline emit decision (without a Tauri runtime /
// AppHandle, which is infeasible to construct in a unit test) and
// verify that on each emit:
// - the emitted `bubble_level` payload equals the current frame's
//   payload (NOT a prior frame's);
// - the emitted `python-event` payload's `data` field equals the
//   current frame's payload.
//
// This pins the contract that removing `last_bubble_payload` did not
// change the wire behavior — both emits still carry the current
// frame's payload, never a stale one.
#[test]
fn test_bubble_level_emit_uses_current_payload_not_stale() {
    // Simulate a 60 Hz stream for ~100 ms (6 frames at 16.667 ms
    // apart). With BUBBLE_LEVEL_COALESCE_HZ=30 (min interval ≈
    // 33.333 ms), frames at i=0 and i=2 (and i=4) pass; frames at
    // i=1, i=3, i=5 are suppressed. The emit on frame i=2 must use
    // frame i=2's payload — NOT frame i=1's (which was the most
    // recent suppressed frame).
    let hz = BUBBLE_LEVEL_COALESCE_HZ;
    let start = Instant::now();
    let step_60hz = Duration::from_micros(16_667);

    // Each frame's payload is a distinct JSON value so we can assert
    // which frame's payload was emitted.
    let frames: Vec<Value> = (0..6u32)
        .map(|i| json!({"frame": i, "level": i as f64 * 10.0}))
        .collect();

    let mut last_emitted: Option<Instant> = None;
    let mut emitted_payloads: Vec<Value> = Vec::new();
    let mut emitted_python_event_data: Vec<Value> = Vec::new();

    for (i, payload) in frames.iter().enumerate() {
        let now = start + step_60hz * i as u32;
        // Mirror the simplified inline emit path:
        // ```
        // if event_type == "bubble_level" {
        //     let now = Instant::now();
        //     if bubble_coalesce_should_emit(last_bubble_level, now, HZ) {
        //         last_bubble_level = Some(now);
        //         emit("bubble_level", payload.clone());
        //         emit("python-event", json!({"type": "bubble_level", "data": payload}));
        //     }
        //     continue;
        // }
        // ```
        if bubble_coalesce_should_emit(last_emitted, now, hz) {
            last_emitted = Some(now);
            // Capture the payload that would have been emitted on
            // the `bubble_level` channel and the `python-event`
            // channel (the latter wraps `payload` in a json! object).
            emitted_payloads.push(payload.clone());
            emitted_python_event_data
                .push(json!({"type": "bubble_level", "data": payload.clone()}));
        }
    }

    // Coalesce must have fired at least once (frame 0 always emits).
    assert!(
        !emitted_payloads.is_empty(),
        "coalesce must emit at least the first frame"
    );
    // Coalesce must have suppressed at least one frame (60 Hz in →
    // 30 Hz out means at least one of the 6 frames was suppressed).
    assert!(
        emitted_payloads.len() < frames.len(),
        "coalesce must suppress some frames (60 Hz in → ~30 Hz out)"
    );

    // The FIRST emitted payload MUST be frame 0's payload (no stale
    // payload from a non-existent prior frame). This is the key
    // invariant: removing `last_bubble_payload` did not introduce a
    // stale-payload bug.
    assert_eq!(
        emitted_payloads[0], frames[0],
        "first emit must carry frame 0's payload (no stale retention)"
    );

    // Each emitted payload must match the CURRENT frame's payload
    // (the one whose `now` timestamp passed the coalesce check), NOT
    // a prior suppressed frame's payload. We re-derive the expected
    // emits by re-running the coalesce decision and asserting each
    // emitted payload matches the corresponding frame.
    let mut expected_emit_indices: Vec<usize> = Vec::new();
    let mut last_emitted2: Option<Instant> = None;
    for (i, _payload) in frames.iter().enumerate() {
        let now = start + step_60hz * i as u32;
        if bubble_coalesce_should_emit(last_emitted2, now, hz) {
            last_emitted2 = Some(now);
            expected_emit_indices.push(i);
        }
    }
    assert_eq!(
        emitted_payloads.len(),
        expected_emit_indices.len(),
        "re-run coalesce decision must produce the same emit count"
    );
    for (emit_idx, &frame_idx) in expected_emit_indices.iter().enumerate() {
        assert_eq!(
            emitted_payloads[emit_idx], frames[frame_idx],
            "emit #{} must carry frame {}'s payload (current frame, not stale)",
            emit_idx, frame_idx
        );
        // The `python-event` catch-all must wrap the SAME payload
        // in its `data` field (the prior `last_bubble_payload.take()`
        // path also satisfied this; the simplified path must too).
        assert_eq!(
            emitted_python_event_data[emit_idx]["data"], frames[frame_idx],
            "python-event emit #{} must wrap frame {}'s payload in its data field",
            emit_idx, frame_idx
        );
        assert_eq!(
            emitted_python_event_data[emit_idx]["type"], "bubble_level",
            "python-event emit #{} must have type=\"bubble_level\"",
            emit_idx
        );
    }
}

// ── WS frame-text truncation for warn logging (HU-31) ─────────────
//
// The reader task's flood-prone warn sites (invalid JSON + non-numeric
// id) previously logged the FULL inbound frame text. Inbound frames can
// carry `transcription_partial` / `transcription_final` event data —
// the user's dictated speech (PII) — so a malformed/truncated frame
// would persist that PII verbatim to the host's rotating log file.
// `truncate_frame_text` bounds each warn line to a small byte cap with
// a `...[truncated]` marker. These tests pin the truncation contract.

#[test]
fn test_hu31_truncate_short_frame_passthrough_unchanged() {
    let short = r#"{"type":"bubble_level","data":{"level":0.5}}"#;
    assert_eq!(
        truncate_frame_text(short),
        short,
        "frames at or under the cap must pass through unchanged"
    );
}

#[test]
fn test_hu31_truncate_exact_cap_passthrough_unchanged() {
    let exact = "a".repeat(256);
    assert_eq!(
        truncate_frame_text(&exact),
        exact,
        "a frame exactly at the byte cap must pass through unchanged"
    );
}

#[test]
fn test_hu31_truncate_long_frame_gets_marker() {
    let long = "x".repeat(500);
    let out = truncate_frame_text(&long);
    assert!(
        out.ends_with("...[truncated]"),
        "truncated output must carry the marker: {:?}",
        out
    );
    assert!(
        out.len() < long.len(),
        "truncated output must be shorter than the input"
    );
    assert!(
        out.starts_with("x".repeat(256).as_str()),
        "truncated output must keep the first 256 bytes"
    );
}

#[test]
fn test_hu31_truncate_preserves_utf8_char_boundary() {
    // 300 CJK chars = 900 UTF-8 bytes; byte 256 is NOT a char boundary,
    // so the truncation must back off to a boundary instead of panicking
    // on a partial char.
    let text = "中".repeat(300);
    let out = truncate_frame_text(&text);
    assert!(
        out.is_char_boundary(out.len()),
        "truncation must never split a UTF-8 char"
    );
    assert!(
        out.ends_with("...[truncated]") && out.len() < text.len(),
        "long multibyte frames must be truncated with the marker"
    );
}

#[test]
fn test_hu31_truncate_mixed_ascii_multibyte_boundary() {
    // ASCII prefix then CJK: the cap lands mid-CJK-run, so the output
    // must end at a boundary strictly before 256 bytes.
    let text = format!("{}{}", "a".repeat(250), "中".repeat(50));
    let out = truncate_frame_text(&text);
    assert!(out.is_char_boundary(out.len()));
    assert!(out.ends_with("...[truncated]"));
    assert!(out.len() < text.len());
}
