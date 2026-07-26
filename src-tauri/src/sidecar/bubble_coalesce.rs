//! Bubble-level coalesce predicate (ADR-0020 §9) — pure UI-rate-limiting
//! helper used by the WebSocket reader task to downsample the sidecar's
//! ~60 Hz `bubble_level` event stream to ≤30 Hz before emitting to the
//! bubble renderer.
//!
//! ## DT-53 — extraction rationale
//!
//! Previously this helper lived in `sidecar/supervisor.rs:474-482`. The
//! supervisor module accreted 3 unrelated responsibilities: respawn /
//! backoff, restart-counter disk I/O, and bubble-level coalesce. The
//! coalesce predicate has nothing to do with sidecar supervision — it's
//! a pure UI-rate-limiting decision called ONLY from `sidecar/ws.rs:599`
//! (never from supervisor.rs itself). Coupling the WS reader to the
//! supervisor module for a function the supervisor doesn't use made the
//! import graph misleading + forced a recompile of supervisor.rs whenever
//! the coalesce logic changed.
//!
//! DT-53 moves the predicate here (its own focused module) so
//! `supervisor.rs` owns ONLY respawn/backoff logic. The signature +
//! behavior is preserved EXACTLY — only the module path changes.
//!
//! The min interval is `Duration::from_millis(1000 / hz)` — for the
//! default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33ms (integer
//! division), so a 60 Hz input stream emits every other event = 30 Hz.

use std::time::{Duration, Instant};

/// Pure form of the bubble_level coalesce decision used by the WS
/// reader task (ADR-0020 §9). Returns `true` if the current event
/// should be emitted given the last-emitted timestamp and the target
/// Hz rate. Extracted from `reconnect_ws`'s inline coalesce logic so
/// unit tests can verify the 30 Hz cap without spinning up a Tauri
/// runtime + mock WS server.
///
/// The min interval is `Duration::from_millis(1000 / hz)` — for the
/// default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33ms (integer
/// division), so a 60 Hz input stream emits every other event = 30 Hz.
pub(crate) fn bubble_coalesce_should_emit(
    last_emitted: Option<Instant>,
    now: Instant,
    hz: u64,
) -> bool {
    last_emitted.map_or(true, |t| {
        now.duration_since(t) >= Duration::from_millis(1000 / hz)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::util::BUBBLE_LEVEL_COALESCE_HZ;
    use std::time::{Duration, Instant};

    #[test]
    fn test_bubble_coalesce_should_emit_first_event() {
        // First event (no prior emit) → always emit.
        let now = Instant::now();
        assert!(bubble_coalesce_should_emit(None, now, BUBBLE_LEVEL_COALESCE_HZ));
    }

    #[test]
    fn test_bubble_coalesce_should_emit_respects_min_interval() {
        // With hz=30, min_interval = 33ms. An event 32ms after the last
        // emit should be suppressed; an event 33ms after should pass.
        let start = Instant::now();
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        // 32ms gap → suppressed.
        let too_soon = start + Duration::from_millis(32);
        assert!(
            !bubble_coalesce_should_emit(Some(start), too_soon, hz),
            "event 32ms after last emit should be suppressed (min_interval=33ms)"
        );
        // 33ms gap → emitted (>= comparison).
        let just_enough = start + Duration::from_millis(33);
        assert!(
            bubble_coalesce_should_emit(Some(start), just_enough, hz),
            "event 33ms after last emit should pass (min_interval=33ms, >= comparison)"
        );
        // 100ms gap → emitted.
        let well_after = start + Duration::from_millis(100);
        assert!(
            bubble_coalesce_should_emit(Some(start), well_after, hz),
            "event 100ms after last emit should pass"
        );
    }

    #[test]
    fn test_bubble_level_coalesce_respects_30hz_cap() {
        // Simulate a 60 Hz event stream for ~1 second (60 events, ~16.67ms
        // apart). With BUBBLE_LEVEL_COALESCE_HZ=30 (min interval 33ms),
        // every other event passes the filter → exactly 30 emits per
        // simulated second, hitting the cap without exceeding it.
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        let start = Instant::now();
        let step_60hz = Duration::from_micros(16_667); // ~16.67ms = 1/60 s
        let mut last_emitted: Option<Instant> = None;
        let mut emitted = 0usize;
        for i in 0..60u32 {
            let now = start + step_60hz * i;
            if bubble_coalesce_should_emit(last_emitted, now, hz) {
                last_emitted = Some(now);
                emitted += 1;
            }
        }
        assert!(
            emitted <= 30,
            "emitted {} events in 1s, expected ≤30 (30 Hz cap)",
            emitted
        );
        // The 60 Hz stream downsampled to a 30 Hz cap should emit ~30
        // events per second (exactly 30 with 16.667ms spacing — every
        // other event). Allow a small ±2 tolerance in case integer
        // division edges shift the boundary by one.
        assert!(
            emitted >= 28,
            "emitted {} events in 1s, expected ~30 — coalesce is too aggressive",
            emitted
        );
    }
}
