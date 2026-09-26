//! Sibling tests for `sidecar::worker_supervisor` (per C-TEST-5):
//! pure policy surface only (backoff pin, C-WS-3 predicate, §7.3
//! hook). The async engine needs a live child, so the host run covers
//! it, not here.

use super::worker_supervisor::{
    respawn_worker, should_keep_worker_running, spawn_worker_exit_watcher,
    worker_backoff_delay_ms, worker_generation_is_stale,
};
use crate::util::SUPERVISOR_BACKOFF_MS;

#[test]
fn test_worker_backoff_pins_shared_doubling_schedule() {
    assert_eq!(
        SUPERVISOR_BACKOFF_MS,
        &[500, 1000, 2000, 4000, 8000],
        "worker respawn shares the sidecar supervisor backoff schedule"
    );
}

#[test]
fn test_worker_backoff_first_attempt_is_500ms() {
    assert_eq!(worker_backoff_delay_ms(0), Some(500));
}

#[test]
fn test_worker_backoff_doubles_each_attempt() {
    for i in 1..SUPERVISOR_BACKOFF_MS.len() {
        assert_eq!(
            worker_backoff_delay_ms(i),
            Some(worker_backoff_delay_ms(i - 1).unwrap_or(0) * 2),
            "backoff must double at attempt {}",
            i
        );
    }
}

#[test]
fn test_worker_backoff_past_end_is_none() {
    assert_eq!(worker_backoff_delay_ms(SUPERVISOR_BACKOFF_MS.len()), None);
    assert_eq!(worker_backoff_delay_ms(usize::MAX), None);
}

#[test]
fn test_worker_generation_stale_on_mismatch() {
    assert!(worker_generation_is_stale(Some(2), 3));
    assert!(worker_generation_is_stale(Some(0), 1));
}

#[test]
fn test_worker_generation_fresh_on_match() {
    assert!(!worker_generation_is_stale(Some(3), 3));
}

#[test]
fn test_worker_generation_none_never_stale() {
    assert!(!worker_generation_is_stale(None, 0));
    assert!(!worker_generation_is_stale(None, 99));
}

#[test]
fn test_should_keep_worker_running_is_passthrough() {
    assert!(should_keep_worker_running(true));
    assert!(!should_keep_worker_running(false));
}

/// Compile-time contract: the supervisor entry points exist under
/// their documented names (mirrors `test_worker_spawn_stubs_exist`).
#[test]
fn test_worker_supervisor_entry_points_exist() {
    let _respawn_fn = respawn_worker;
    let _watch_fn = spawn_worker_exit_watcher;
}
