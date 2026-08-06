//! Unit tests for `bubble::rate_limit` (extracted per C-TEST-5).
//!
//! Originally inline in `bubble/rate_limit.rs` as
//! `#[cfg(test)] mod tests { ... }`; moved to this sibling file to keep
//! production source files free of test code (C-TEST-5 — matches the
//! pattern established by `commands/bubble/tests.rs`).
//!
//! These tests pin the toggle-rate-limiter invariants: the
//! `LAST_TOGGLE_NANOS` initial sentinel (0 = "never toggled"), the
//! `monotonic_now_nanos` monotonicity contract (closes the
//! NTP-skew rate-limiter bypass), and the `TOGGLE_RATE_LIMIT_NS`
//! constant (500ms).

use super::{monotonic_now_nanos, LAST_TOGGLE_NANOS, TOGGLE_RATE_LIMIT_NS};
use std::sync::atomic::Ordering;

/// The initial value of `LAST_TOGGLE_NANOS` MUST be 0 (the "never
/// toggled" sentinel). If a future refactor changes this, the
/// predicate's first-call-passes contract breaks. We don't call
/// `toggle_rate_limiter_allows()` directly here because that would
/// mutate the shared `LAST_TOGGLE_NANOS` and race with other
/// tests; instead we verify the predicate logic by reading the
/// initial state and confirming the documented invariant.
#[test]
fn test_last_toggle_nanos_initial_value_is_never_sentinel() {
    let initial = LAST_TOGGLE_NANOS.load(Ordering::SeqCst);
    assert_eq!(
        initial, 0,
        "LAST_TOGGLE_NANOS must start at 0 (the 'never toggled' sentinel); got {initial}"
    );
}

/// `monotonic_now_nanos` must return a non-decreasing value across
/// successive calls (the whole point of switching from
/// `SystemTime::now()` — closes the NTP-skew rate-limiter bypass).
#[test]
fn test_monotonic_now_nanos_is_non_decreasing() {
    let a = monotonic_now_nanos();
    // Spin briefly to ensure the clock advances (Instant's
    // resolution is platform-dependent — on Linux it's typically
    // 1ns, on Windows ~15ms).
    for _ in 0..1000 {
        std::hint::spin_loop();
    }
    let b = monotonic_now_nanos();
    assert!(
        b >= a,
        "monotonic_now_nanos() went backwards: {a} -> {b} (Instant is monotonic; this is a bug)"
    );
}

/// `monotonic_now_nanos` must return a small value (nanoseconds
/// since process anchor), NOT a wall-clock nanos-since-epoch value
/// (which would be ~10^18). This pins the "anchored" behavior: if
/// a future refactor re-introduces `SystemTime::now()` (which
/// would return a huge value), this test fails.
#[test]
fn test_monotonic_now_nanos_is_anchored_not_wall_clock() {
    let now = monotonic_now_nanos();
    // One hour in nanoseconds = 3_600_000_000_000. A wall-clock
    // nanos-since-epoch value would be ~10^18 (year 2024+). If we
    // ever see a value larger than 1 hour of nanoseconds in a
    // unit test, the anchor was bypassed.
    const ONE_HOUR_NS: u64 = 3_600_000_000_000;
    assert!(
        now < ONE_HOUR_NS,
        "monotonic_now_nanos() returned {now}, which looks like a wall-clock value \
         (nanos since epoch) rather than an anchored monotonic value. \
         Did the implementation revert to SystemTime::now()?"
    );
}

/// The rate-limit threshold constant must remain 500ms — matches
/// the renderer's UI animation frame budget and the documented
/// contract. A regression here would either over-throttle (e.g.
/// 500µs) or under-throttle (e.g. 5s) legitimate user clicks.
#[test]
fn test_toggle_rate_limit_ns_is_500ms() {
    assert_eq!(
        TOGGLE_RATE_LIMIT_NS, 500_000_000,
        "TOGGLE_RATE_LIMIT_NS must be 500ms (500_000_000 ns) — matches the bubble \
         renderer's UI animation budget"
    );
}
