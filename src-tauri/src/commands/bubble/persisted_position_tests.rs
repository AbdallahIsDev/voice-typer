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
        parse_persisted_pos(
            &serde_json::json!({"bubble_x": f64::NAN, "bubble_y": 0})
        ),
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

    // Generation bumped by the suppress call.
    let before = SCHEDULE_GENERATION.load(Ordering::SeqCst);
    assert!(before >= 1);

    // Expired window reads as unsuppressed again.
    if let Ok(mut slot) = SUPPRESS_UNTIL.lock() {
        *slot = Some(Instant::now() - Duration::from_millis(1));
    }
    assert!(!currently_suppressed());
}
