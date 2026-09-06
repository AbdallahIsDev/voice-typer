//! Tests for `persisted_position.rs` (pure-function style — no Tauri
//! runtime needed; C-TEST-5 keeps them in this sibling module).

use super::*;

// The parse/cache tests below share the process-global PERSISTED_POS
// static, so they run inside one serialized guard to avoid cross-test
// interference under the default parallel test harness.
static CACHE_GUARD: Mutex<()> = Mutex::new(());

#[test]
fn parses_valid_pair_including_negatives() {
    let data = serde_json::json!({"bubble_x": -1920, "bubble_y": 1040});
    assert_eq!(parse_persisted_pos(&data), Some((-1920, 1040)));

    let zero = serde_json::json!({"bubble_x": 0, "bubble_y": 0});
    assert_eq!(parse_persisted_pos(&zero), Some((0, 0)));
}

#[test]
fn rejects_null_partial_and_junk_payloads() {
    // Nulls (edge-toggle reset).
    assert_eq!(
        parse_persisted_pos(&serde_json::json!({"bubble_x": null, "bubble_y": null})),
        None
    );
    // One-sided pair.
    assert_eq!(
        parse_persisted_pos(&serde_json::json!({"bubble_x": 5})),
        None
    );
    // Non-numeric junk.
    assert_eq!(
        parse_persisted_pos(&serde_json::json!({"bubble_x": "junk", "bubble_y": 4})),
        None
    );
    // Non-object payload.
    assert_eq!(parse_persisted_pos(&serde_json::json!("nope")), None);
    assert_eq!(parse_persisted_pos(&serde_json::json!(null)), None);
}

#[test]
fn rejects_out_of_range_coordinates() {
    // Beyond the server allowlist bound (±100000) and beyond i32.
    assert_eq!(
        parse_persisted_pos(&serde_json::json!({"bubble_x": 3_000_000_000.0_f64, "bubble_y": 0})),
        None
    );
    // NaN / infinite.
    assert_eq!(
        parse_persisted_pos(&serde_json::json!({"bubble_x": f64::NAN, "bubble_y": 0})),
        None
    );
}

#[test]
fn cache_roundtrip_and_clear() {
    let _guard = CACHE_GUARD.lock().unwrap_or_else(|e| e.into_inner());

    update_persisted_pos_from_config(&serde_json::json!({"bubble_x": -100, "bubble_y": -40}));
    assert_eq!(persisted_pos(), Some((-100, -40)));

    // A reset payload clears the cache.
    update_persisted_pos_from_config(&serde_json::json!({"bubble_x": null}));
    assert_eq!(persisted_pos(), None);

    // Missing payload keys clear too (idempotent).
    update_persisted_pos_from_config(&serde_json::json!({}));
    assert_eq!(persisted_pos(), None);
}

#[test]
fn suppression_window_blocks_then_expires() {
    let _guard = CACHE_GUARD.lock().unwrap_or_else(|e| e.into_inner());

    // No suppression initially (fresh process state in test runs may
    // have residue from other tests — force-clear first).
    if let Ok(mut slot) = SUPPRESS_UNTIL.lock() {
        *slot = None;
    }
    assert!(!currently_suppressed());

    suppress_persist_for_window();
    assert!(currently_suppressed());

    // Expired window reads as unsuppressed again.
    if let Ok(mut slot) = SUPPRESS_UNTIL.lock() {
        *slot = Some(Instant::now() - Duration::from_millis(1));
    }
    assert!(!currently_suppressed());
}

#[test]
fn schedule_stores_latest_move_last_write_wins() {
    let _guard = CACHE_GUARD.lock().unwrap_or_else(|e| e.into_inner());

    store_pending_move(10, 20);
    store_pending_move(-30, -40);
    let queued = PENDING_MOVE
        .lock()
        .unwrap_or_else(|e| e.into_inner())
        .as_ref()
        .map(|p| p.pos);
    assert_eq!(queued, Some((-30, -40)));

    // Clear so the async tests below start from an empty queue.
    *PENDING_MOVE.lock().unwrap_or_else(|e| e.into_inner()) = None;
}

#[tokio::test]
async fn quiesced_wake_fires_after_full_debounce_window() {
    let _guard = CACHE_GUARD.lock().unwrap_or_else(|e| e.into_inner());
    *PENDING_MOVE.lock().unwrap_or_else(|e| e.into_inner()) = None;
    // Drain any leftover wakeup permit from a previous test — notify
    // permits persist in the shared static between tests and would
    // otherwise make the waiter below return instantly with None.
    let _ = tokio::time::timeout(Duration::from_millis(5), wait_for_quiesced_move()).await;

    let waiter = tokio::spawn(wait_for_quiesced_move());
    tokio::time::sleep(Duration::from_millis(50)).await;
    let started = Instant::now();
    store_pending_move(11, 22);
    PERSIST_NOTIFY.get_or_init(Notify::new).notify_one();

    let fired = waiter.await.expect("debounce task panicked");
    assert_eq!(fired, Some((11, 22)));
    // The wake must fire no earlier than one full debounce window after
    // the move was stored (trailing-edge debounce; small tolerance for
    // scheduler jitter).
    assert!(
        started.elapsed() >= Duration::from_millis(PERSIST_DEBOUNCE_MS - 50),
        "fired {:?} after store — earlier than the {}ms debounce window",
        started.elapsed(),
        PERSIST_DEBOUNCE_MS
    );

    // The fired move is consumed (a surplus wakeup permit can't re-fire it).
    assert!(PENDING_MOVE.lock().unwrap_or_else(|e| e.into_inner()).is_none());
}

#[tokio::test]
async fn quiesced_wake_extends_window_and_keeps_latest_move() {
    let _guard = CACHE_GUARD.lock().unwrap_or_else(|e| e.into_inner());
    *PENDING_MOVE.lock().unwrap_or_else(|e| e.into_inner()) = None;
    // Drain any leftover wakeup permit from a previous test.
    let _ = tokio::time::timeout(Duration::from_millis(5), wait_for_quiesced_move()).await;

    let waiter = tokio::spawn(wait_for_quiesced_move());
    tokio::time::sleep(Duration::from_millis(50)).await;
    store_pending_move(1, 2);
    PERSIST_NOTIFY.get_or_init(Notify::new).notify_one();
    // A second move INSIDE the window must re-arm the debounce (the
    // fire waits for the FULL window after the LAST move) and win as
    // the persisted pair.
    tokio::time::sleep(Duration::from_millis(100)).await;
    let started = Instant::now();
    store_pending_move(3, 4);
    PERSIST_NOTIFY.get_or_init(Notify::new).notify_one();

    let fired = waiter.await.expect("debounce task panicked");
    assert_eq!(fired, Some((3, 4)));
    assert!(
        started.elapsed() >= Duration::from_millis(PERSIST_DEBOUNCE_MS - 50),
        "fired {:?} after the last move — the debounce window was not re-armed",
        started.elapsed()
    );

    *PENDING_MOVE.lock().unwrap_or_else(|e| e.into_inner()) = None;
}
