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
//! The min interval is `Duration::from_nanos(1_000_000_000 / hz)` — for the
//! default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33.333ms (nanosecond
//! precision), so a 60 Hz input stream emits every other event = 30 Hz.
//! UE-3-F12: previously used `Duration::from_millis(1000 / hz)`, which for
//! `hz > 1000` integer-divided to 0 → coalescing was silently disabled.
//! `from_nanos` keeps the interval non-zero for any `hz` up to `u64::MAX`
//! (sub-nanosecond precision is not representable in `Duration`, so the
//! arithmetic saturates at 0ns only for `hz >= 1_000_000_000` — far above
//! any plausible UI frame rate).

use std::time::{Duration, Instant};

/// Pure form of the bubble_level coalesce decision used by the WS
/// reader task (ADR-0020 §9). Returns `true` if the current event
/// should be emitted given the last-emitted timestamp and the target
/// Hz rate. Extracted from `reconnect_ws`'s inline coalesce logic so
/// unit tests can verify the 30 Hz cap without spinning up a Tauri
/// runtime + mock WS server.
///
/// The min interval is `Duration::from_nanos(1_000_000_000 / hz)` — for the
/// default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33.333ms (nanosecond
/// precision), so a 60 Hz input stream emits every other event = 30 Hz.
///
/// UE-3-F12: previously used `Duration::from_millis(1000 / hz)`, which
/// for `hz > 1000` integer-divided to 0 (`1000 / 2000 == 0` in Rust's
/// integer division), silently disabling coalescing for any rate above
/// 1 kHz. The `from_nanos` form keeps a non-zero interval for any
/// plausible UI rate (`hz < 1_000_000_000` — a billion Hz is not a
/// plausible UI rate).
pub(crate) fn bubble_coalesce_should_emit(
    last_emitted: Option<Instant>,
    now: Instant,
    hz: u64,
) -> bool {
    // UE-3-F12: use `from_nanos(1_000_000_000 / hz)` instead of
    // `from_millis(1000 / hz)`. The millis form integer-divided to 0 for
    // any `hz > 1000`, silently disabling coalescing. The nanos form
    // keeps a non-zero interval up to `hz == 999_999_999` (well above any
    // plausible UI frame rate). For the default `hz=30` the interval is
    // 33_333_333 ns ≈ 33.333 ms (the millis form produced 33 ms exactly;
    // the extra 333 µs of precision does not change the 30 Hz cap behavior).
    last_emitted.map_or(true, |t| {
        now.duration_since(t) >= Duration::from_nanos(1_000_000_000 / hz)
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
        // With hz=30, min_interval = 1_000_000_000 / 30 = 33_333_333 ns ≈ 33.333 ms
        // (UE-3-F12 changed the unit from `from_millis(33)` to `from_nanos(33_333_333)`,
        // adding 333 µs of precision). An event 33 ms after the last emit is
        // BELOW the 33.333 ms threshold → suppressed; an event 34 ms after is
        // ABOVE → emitted. The 100 ms "well_after" case is unaffected.
        let start = Instant::now();
        let hz = BUBBLE_LEVEL_COALESCE_HZ;
        // 32 ms gap → suppressed (32 ms < 33.333 ms).
        let too_soon = start + Duration::from_millis(32);
        assert!(
            !bubble_coalesce_should_emit(Some(start), too_soon, hz),
            "event 32 ms after last emit should be suppressed (min_interval≈33.333 ms)"
        );
        // 33 ms gap → still suppressed under the new from_nanos form
        // (33 ms < 33.333 ms). Previously this passed under the
        // `from_millis(33)` form; the assertion was updated to reflect the
        // tighter boundary.
        let still_too_soon = start + Duration::from_millis(33);
        assert!(
            !bubble_coalesce_should_emit(Some(start), still_too_soon, hz),
            "event 33 ms after last emit should be suppressed under from_nanos(33_333_333) (33 ms < 33.333 ms)"
        );
        // 34 ms gap → emitted (34 ms > 33.333 ms).
        let just_enough = start + Duration::from_millis(34);
        assert!(
            bubble_coalesce_should_emit(Some(start), just_enough, hz),
            "event 34 ms after last emit should pass (min_interval≈33.333 ms, >= comparison)"
        );
        // 100 ms gap → emitted.
        let well_after = start + Duration::from_millis(100);
        assert!(
            bubble_coalesce_should_emit(Some(start), well_after, hz),
            "event 100 ms after last emit should pass"
        );
    }

    #[test]
    fn test_bubble_level_coalesce_respects_30hz_cap() {
        // Simulate a 60 Hz event stream for ~1 second (60 events, ~16.67ms
        // apart). With BUBBLE_LEVEL_COALESCE_HZ=30 (min interval ≈ 33.333 ms
        // under UE-3-F12's from_nanos form), every other event passes the
        // filter → exactly 30 emits per simulated second, hitting the cap
        // without exceeding it.
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
        // other event). Allow a small ±2 tolerance in case the
        // sub-millisecond precision shift (33 ms → 33.333 ms) moves the
        // boundary by one emit.
        assert!(
            emitted >= 28,
            "emitted {} events in 1s, expected ~30 — coalesce is too aggressive",
            emitted
        );
    }

    // ── UE-3-F12: hz > 1000 no longer silently disables coalescing ──

    #[test]
    fn test_ue3_f12_hz_above_1000_does_not_silently_disable_coalescing() {
        // UE-3-F12: with the old `Duration::from_millis(1000 / hz)` form,
        // hz=2000 produced `1000 / 2000 == 0` (Rust integer division) →
        // `from_millis(0)` → coalescing silently disabled (every event
        // passes). The fix uses `Duration::from_nanos(1_000_000_000 / hz)`
        // which for hz=2000 yields 500_000 ns = 0.5 ms — a small but
        // non-zero interval that still rate-limits bursts.
        let hz: u64 = 2000;
        let start = Instant::now();

        // First event: always emit (no prior timestamp).
        assert!(
            bubble_coalesce_should_emit(None, start, hz),
            "first event must always emit regardless of hz"
        );

        // Event 100 µs after the last emit — well below the 500 µs min
        // interval. Under the old form this would WRONGLY pass (min
        // interval was 0); under the fixed form it must be suppressed.
        let too_soon = start + Duration::from_micros(100);
        assert!(
            !bubble_coalesce_should_emit(Some(start), too_soon, hz),
            "UE-3-F12: hz=2000 must produce a 500 µs min interval — 100 µs gap should be suppressed (old form silently disabled coalescing here)"
        );

        // Event 600 µs after the last emit — above the 500 µs min
        // interval. Should pass under both old and new forms.
        let just_enough = start + Duration::from_micros(600);
        assert!(
            bubble_coalesce_should_emit(Some(start), just_enough, hz),
            "UE-3-F12: event 600 µs after last emit (hz=2000, min_interval=500 µs) should pass"
        );
    }
}
