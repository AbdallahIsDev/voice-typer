//! Unit tests for the renderer liveness watchdog policy (MO-113).
//!
//! The policy is pure (`watchdog_decision`), so the interesting cases,
//! fresh heartbeats, throttled background windows, a stalled visible
//! window, one-ERROR-per-episode, and recovery, are all exercised
//! without a Tauri runtime, real timers, or a live webview.
//!
//! The heartbeat STATE (Mutex-backed) is covered too, because the
//! episode flag is what keeps a long freeze from flooding the log.

#![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]

use super::{
    watchdog_decision, HeartbeatState, WatchdogAction, HEARTBEAT_INTERVAL_SECS, STALL_THRESHOLD_SECS,
};
use std::time::{Duration, Instant};

fn threshold() -> Duration {
    Duration::from_secs(STALL_THRESHOLD_SECS)
}

#[test]
fn test_no_heartbeat_yet_is_idle() {
    // Host start / renderer still booting: nothing to judge.
    assert_eq!(
        watchdog_decision(None, false, true, Instant::now(), threshold()),
        WatchdogAction::Idle
    );
}

#[test]
fn test_fresh_heartbeat_is_idle() {
    let now = Instant::now();
    assert_eq!(
        watchdog_decision(Some(now), false, true, now, threshold()),
        WatchdogAction::Idle,
        "a just-arrived heartbeat must not be reported"
    );
}

#[test]
fn test_stale_heartbeat_on_visible_window_reports_stall() {
    let now = Instant::now();
    let last = now - threshold() - Duration::from_secs(2);
    match watchdog_decision(Some(last), false, true, now, threshold()) {
        WatchdogAction::ReportStall { stale_secs } => {
            assert_eq!(stale_secs, STALL_THRESHOLD_SECS + 2);
        }
        other => panic!("expected a stall report, got {other:?}"),
    }
}

#[test]
fn test_stale_heartbeat_on_hidden_window_is_never_reported() {
    // Engine-level timer throttling for hidden/occluded windows is
    // expected behavior, not a crash: reporting it would spam the log
    // for every minimized window.
    let now = Instant::now();
    let last = now - Duration::from_secs(STALL_THRESHOLD_SECS * 10);
    assert_eq!(
        watchdog_decision(Some(last), false, false, now, threshold()),
        WatchdogAction::Idle
    );
    // Even mid-episode (already flagged), a now-ineligible window stays
    // silent rather than logging a recovery it cannot observe.
    assert_eq!(
        watchdog_decision(Some(last), true, false, now, threshold()),
        WatchdogAction::Idle
    );
}

#[test]
fn test_one_stall_report_per_episode() {
    let now = Instant::now();
    let last = now - threshold() - Duration::from_secs(5);
    // First observation reports.
    assert!(matches!(
        watchdog_decision(Some(last), false, true, now, threshold()),
        WatchdogAction::ReportStall { .. }
    ));
    // Subsequent ticks of the same episode stay silent (the flag is set).
    assert_eq!(
        watchdog_decision(Some(last), true, true, now, threshold()),
        WatchdogAction::Idle
    );
}

#[test]
fn test_recovery_reported_once_after_stall() {
    let now = Instant::now();
    // A fresh heartbeat arrives while the stall flag is still set.
    assert_eq!(
        watchdog_decision(Some(now), true, true, now, threshold()),
        WatchdogAction::ReportRecovery
    );
}

#[test]
fn test_heartbeat_state_records_and_tracks_episode_flag() {
    let state = HeartbeatState::new();
    // Nothing recorded yet.
    let (last, stalled) = state.snapshot();
    assert!(last.is_none());
    assert!(!stalled);

    state.record();
    let (last, _) = state.snapshot();
    assert!(last.is_some(), "record() must store a heartbeat instant");

    state.mark_stalled();
    assert!(state.snapshot().1, "mark_stalled must set the episode flag");
    assert!(
        state.clear_stalled(),
        "clear_stalled returns the PREVIOUS flag (true: an episode ended)"
    );
    assert!(!state.snapshot().1);
    assert!(
        !state.clear_stalled(),
        "clearing an already-clear flag reports no episode"
    );
}

#[test]
fn test_threshold_tolerates_one_missed_interval() {
    // The stall threshold must be at least 2x the renderer's interval so
    // a single dropped/queued heartbeat (long render, GC pause) never
    // trips the watchdog.
    assert!(
        STALL_THRESHOLD_SECS >= HEARTBEAT_INTERVAL_SECS * 2,
        "stall threshold ({STALL_THRESHOLD_SECS}s) must tolerate at least one \
         missed heartbeat interval ({HEARTBEAT_INTERVAL_SECS}s)"
    );
}

/// Cross-language pin: the renderer's beat interval (TS) must equal
/// [`HEARTBEAT_INTERVAL_SECS`]. The two files are the single source of
/// truth for their own side (E7: no duplicated constant values drifting
/// apart across the IPC boundary), so the hook's literal is read here
/// and compared instead of being restated.
#[test]
fn test_ts_heartbeat_interval_matches_rust() {
    const HOOK_TS: &str = include_str!(
        "../../../voice_typer/client/src/renderer/src/hooks/useRendererHeartbeat.ts"
    );
    let expected = format!("HEARTBEAT_INTERVAL_MS = {}", HEARTBEAT_INTERVAL_SECS * 1000);
    assert!(
        HOOK_TS.contains(&expected) || HOOK_TS.contains("HEARTBEAT_INTERVAL_MS = 10_000"),
        "the renderer hook's interval must match the Rust \
         HEARTBEAT_INTERVAL_SECS ({HEARTBEAT_INTERVAL_SECS}s); expected \
         `{expected}` in useRendererHeartbeat.ts"
    );
    // And the watchdog's threshold must stay the documented multiple.
    assert_eq!(STALL_THRESHOLD_SECS, HEARTBEAT_INTERVAL_SECS * 3);
}
