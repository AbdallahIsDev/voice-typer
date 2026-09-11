#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented
)]

//! Unit tests for `bubble::rate_limit` (extracted per C-TEST-5).
//!
//! Originally inline in `bubble/rate_limit.rs` as
//! `#[cfg(test)] mod tests { ... }`; moved to this sibling file to keep
//! production source files free of test code (C-TEST-5, matches the
//! pattern established by `commands/bubble/tests.rs`).
//!
//! These tests pin the toggle-rate-limiter invariants:
//!
//! - `toggle_decision` (the pure decision core): the first toggle
//!   always passes (`None` = never toggled), a rapid second toggle is
//!   denied, a toggle at/after the 500ms window boundary passes, and
//!   the defensive clock-glitch branch allows;
//! - **the anchored-at-zero regression**: `Some(0)` is a REAL
//!   last-toggle timestamp (Windows QPC granularity can anchor the
//!   process at exactly 0ns elapsed) and must never be mistaken for
//!   "never toggled": the second rapid toggle at 0ns is denied;
//! - `monotonic_now_nanos` monotonicity contract (closes the
//!   NTP-skew rate-limiter bypass) and the `TOGGLE_RATE_LIMIT_NS`
//!   constant (500ms);
//! - the real `toggle_rate_limiter_allows` integration behavior: two
//!   rapid successive invocations yield (allowed, denied).
//!
//! The initial `None` state of the shared `LAST_TOGGLE` is pinned
//! INSIDE the integration test (not in a standalone test) so no other
//! test in this binary can observe a transient `Some` value while
//! tests run in parallel: the integration test is the single owner
//! of the shared static and resets it to `None` before returning.

use super::{monotonic_now_nanos, toggle_decision, LAST_TOGGLE, TOGGLE_RATE_LIMIT_NS};
use crate::state::lock;

// ── toggle_decision (pure decision core) ─────────────────────────

#[test]
fn test_toggle_decision_first_toggle_allows() {
    // `None` = never toggled: the first toggle always passes and
    // stores the current timestamp.
    assert_eq!(toggle_decision(None, 123), Some(123));
    assert_eq!(toggle_decision(None, 0), Some(0));
    assert_eq!(
        toggle_decision(None, TOGGLE_RATE_LIMIT_NS),
        Some(TOGGLE_RATE_LIMIT_NS)
    );
}

#[test]
fn test_toggle_decision_rapid_second_toggle_is_denied() {
    // 1ns after the last toggle → well inside the 500ms window → deny
    // (returns None: no new timestamp, the stored one stays).
    assert_eq!(toggle_decision(Some(1_000_000), 1_000_001), None);
    // 499.999ms after → still inside → deny.
    assert_eq!(
        toggle_decision(Some(1_000_000), 1_000_000 + TOGGLE_RATE_LIMIT_NS - 1),
        None
    );
}

#[test]
fn test_toggle_decision_zero_anchored_timestamp_is_not_the_never_state() {
    // THE regression: on Windows, QPC granularity can make the first
    // toggle's elapsed-since-anchor value EXACTLY 0. Under the old
    // AtomicU64-with-0-sentinel encoding, that stored timestamp was
    // indistinguishable from "never toggled", so the second rapid
    // toggle re-took the first-toggle path and bypassed the limiter
    // once. With the Option encoding, `Some(0)` is a real timestamp:
    // a second toggle arriving at 0ns (or any point inside the 500ms
    // window) MUST be denied.
    // First toggle anchored at exactly 0ns: allowed, stores Some(0).
    assert_eq!(toggle_decision(None, 0), Some(0));
    // Second rapid toggle, still at 0ns elapsed: DENIED.
    assert_eq!(toggle_decision(Some(0), 0), None);
    // Second rapid toggle a few ns later: DENIED.
    assert_eq!(toggle_decision(Some(0), 42), None);
}

#[test]
fn test_toggle_decision_at_and_beyond_window_boundary_allows() {
    // Exactly 500ms after the last toggle → at the boundary → allow.
    let last = 1_000_000u64;
    assert_eq!(
        toggle_decision(Some(last), last + TOGGLE_RATE_LIMIT_NS),
        Some(last + TOGGLE_RATE_LIMIT_NS)
    );
    // Beyond the window → allow.
    assert_eq!(
        toggle_decision(Some(last), last + TOGGLE_RATE_LIMIT_NS + 1),
        Some(last + TOGGLE_RATE_LIMIT_NS + 1)
    );
}

#[test]
fn test_toggle_decision_clock_backwards_allows_defensively() {
    // `now < last` should be impossible with a monotonic `Instant`,
    // but the branch exists to protect against a future clock-source
    // swap. Contract: allow (don't penalize the user) and store the
    // new value: the same behavior the previous compare-exchange
    // encoding had.
    assert_eq!(toggle_decision(Some(10_000), 9_999), Some(9_999));
}

// ── monotonic_now_nanos + constant ──────────────────────────────

/// `monotonic_now_nanos` must return a non-decreasing value across
/// successive calls (the whole point of switching from
/// `SystemTime::now()`, closes the NTP-skew rate-limiter bypass).
#[test]
fn test_monotonic_now_nanos_is_non_decreasing() {
    let a = monotonic_now_nanos();
    // Spin briefly to ensure the clock advances (Instant's
    // resolution is platform-dependent: on Linux it's typically
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

/// The rate-limit threshold constant must remain 500ms, matches
/// the renderer's UI animation frame budget and the documented
/// contract. A regression here would either over-throttle (e.g.
/// 500µs) or under-throttle (e.g. 5s) legitimate user clicks.
#[test]
fn test_toggle_rate_limit_ns_is_500ms() {
    assert_eq!(
        TOGGLE_RATE_LIMIT_NS, 500_000_000,
        "TOGGLE_RATE_LIMIT_NS must be 500ms (500_000_000 ns): matches the bubble \
         renderer's UI animation budget"
    );
}

// ── integration: the real limiter, twice in rapid succession ────
//
// This is the only test in the binary that mutates the shared
// `LAST_TOGGLE` (the pure `toggle_decision` tests above cover the
// branch matrix without touching it). It asserts the shared state's
// initial `None` ("never toggled") value at its start, drives the
// REAL predicate twice in rapid succession, the exact user-facing
// bypass scenario: toggle, then immediately toggle again, and
// resets the shared state to `None` before returning so the
// process-wide limiter is left as if the test never ran.

#[test]
fn test_toggle_rate_limiter_allows_rapid_double_toggle_is_denied() {
    // Initial shared state: never toggled (the Option encoding —
    // there is no numeric sentinel to pin anymore).
    assert!(
        lock(&LAST_TOGGLE).is_none(),
        "LAST_TOGGLE must start as None (never toggled)"
    );
    // First toggle: allowed (no stored last-toggle).
    let first = super::toggle_rate_limiter_allows();
    // Second toggle immediately after (microseconds later, well
    // inside the 500ms window): the limiter must HOLD.
    let second = super::toggle_rate_limiter_allows();
    assert!(
        first,
        "the first toggle after process start must be allowed"
    );
    assert!(
        !second,
        "a second toggle in rapid succession (<< 500ms apart) must be rate-limited: \
         the limiter must not be bypassable by a rapid double toggle"
    );
    // Reset the shared state so this test leaves no trace for any
    // other test (or a re-run) in the same binary.
    *lock(&LAST_TOGGLE) = None;
}
