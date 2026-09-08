//! `bubble_toggle_dictation` rate limiter (ADR-0020 §9).
//!
//! Process-wide last-toggle state: `None` = "never toggled", `Some(n)`
//! = nanoseconds elapsed since the process-lifetime anchor instant.
//! The state is a `Mutex<Option<u64>>` because:
//!   - The Tauri command handler can be invoked concurrently from
//!     multiple webview windows (e.g. if a future code path opens a
//!     second bubble), so access must be synchronized. The critical
//!     section is two integer operations, so the mutex is held for
//!     nanoseconds — no contention concern.
//!   - `Instant` has no public conversion to a stable `u64` (it's an
//!     opaque monotonic clock), so we anchor it once via a
//!     `OnceLock<Instant>` and store the elapsed nanoseconds since
//!     that anchor. `Instant` is monotonic (immune to NTP skew /
//!     wall-clock jumps), which closes the `SystemTime::now()`
//!     rate-limiter bypass vector where a malicious or misconfigured
//!     NTP step could disable the limiter by jumping the wall clock
//!     backwards.
//!   - "Never toggled" is `None` — NOT a sentinel numeric value. An
//!     earlier encoding used `AtomicU64` with `0` as the "never
//!     toggled" sentinel, which collided with a genuinely-anchored-
//!     at-zero timestamp: on Windows the first call anchors
//!     `Instant::now()` and immediately reads `Instant::now()` again,
//!     and QueryPerformanceCounter granularity can make that elapsed
//!     value exactly `0` — so the stored timestamp was
//!     indistinguishable from "never toggled" and the SECOND rapid
//!     toggle bypassed the limiter once. The `Option` encoding makes
//!     `Some(0)` (a real anchored-at-zero timestamp) and `None`
//!     (never toggled) structurally distinct, so that bypass is
//!     impossible by construction.

use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use crate::state::lock;

/// Process-wide anchor instant. Initialized on the first call to
/// [`toggle_rate_limiter_allows`]; subsequent calls compute the elapsed
/// duration since this anchor and store it (as nanoseconds) in
/// [`LAST_TOGGLE`]. Using a process-lifetime anchor (rather than
/// re-deriving from `SystemTime::now()` each call) keeps the stored
/// timestamps monotonic and immune to NTP adjustments.
static ANCHOR: OnceLock<Instant> = OnceLock::new();

/// Process-wide last-toggle state (nanoseconds since the
/// process-lifetime [`ANCHOR`] instant; `None` = never toggled).
/// See the module-level doc comment for the rationale.
pub(super) static LAST_TOGGLE: Mutex<Option<u64>> = Mutex::new(None);

/// Minimum interval between consecutive toggle_dictation
/// invocations (500ms = 500_000_000 ns). A 500ms floor allows at
/// most 2 toggles/sec (1 / 0.5s) — the first click in any 500ms
/// window passes, and clicks inside the remaining window are
/// dropped. 2/sec is far below the rate that would DoS the sidecar's
/// recording state machine (one start/stop pair per rapid click)
/// while still leaving room for a deliberate start/stop pair per
/// second.
pub(super) const TOGGLE_RATE_LIMIT_NS: u64 = 500_000_000;

/// Returns a monotonic "now" timestamp in nanoseconds since the
/// process-lifetime [`ANCHOR`] instant. The anchor is initialized
/// lazily on first call so this function never panics and never reads
/// `Instant` before it's available. `Instant` is monotonic by contract
/// (immune to NTP skew), so the returned value never decreases between
/// successive calls — the rate limiter can rely on `now >= last` for
/// any stored `last`.
fn monotonic_now_nanos() -> u64 {
    let anchor = ANCHOR.get_or_init(Instant::now);
    // `duration_since(*anchor)` returns `Duration`; `as_nanos()` is
    // `u128`. The `u64::try_from(...).unwrap_or(u64::MAX)` saturating
    // cast matches the project-adopted pattern (a process running for
    // ~584 years would saturate, which is not a real concern).
    u64::try_from(Instant::now().duration_since(*anchor).as_nanos()).unwrap_or(u64::MAX)
}

/// Pure decision core for [`toggle_rate_limiter_allows`]:
/// given the last-toggle timestamp (`None` = never toggled) and the
/// current monotonic nanos, return `Some(now)` when the toggle is
/// ALLOWED (the caller stores this as the new last-toggle value), or
/// `None` when the toggle is rate-limited (denied; the stored
/// last-toggle stays unchanged).
///
/// Decision rules:
/// - `last == None` → always allow (the first toggle after process
///   start passes — `Some(0)`, a genuinely anchored-at-zero
///   timestamp, is a REAL last-toggle value here, never a "never"
///   marker).
/// - `now >= last` and `now - last < TOGGLE_RATE_LIMIT_NS` → deny.
/// - `now < last` (defensive — `Instant` is monotonic so this should
///   never happen, but the check protects against a future refactor
///   that swaps the clock source) → allow (don't penalize the user
///   for a clock glitch) and store the new (lower) value.
/// - `now - last >= TOGGLE_RATE_LIMIT_NS` → allow.
///
/// Extracted as a pure function so unit tests can pin every branch —
/// including the anchored-at-zero regression case — without touching
/// the shared process-wide state.
pub(super) fn toggle_decision(last: Option<u64>, now: u64) -> Option<u64> {
    if let Some(last) = last {
        if now >= last && now.saturating_sub(last) < TOGGLE_RATE_LIMIT_NS {
            return None;
        }
    }
    Some(now)
}

/// Rate-limiter predicate. Returns `true` if the toggle is
/// allowed (>= 500ms since the last toggle), `false` if rate-limited.
/// Updates `LAST_TOGGLE` atomically on success.
///
/// The `Mutex` short critical section serializes concurrent callers
/// (multiple windows invoking the command at once): the first caller
/// to acquire the lock stores its timestamp; every caller that
/// arrives within 500ms afterwards observes the stored value and is
/// denied — the same observable semantics the previous
/// compare-exchange loop provided, with no sentinel encoding.
pub(super) fn toggle_rate_limiter_allows() -> bool {
    let now = monotonic_now_nanos();
    // Poison-safe acquisition via the canonical `crate::state::lock`
    // helper: the critical section below cannot panic (arithmetic +
    // one assignment), so a poisoned lock means a panic happened
    // elsewhere while the guard was held — recovering the data is
    // strictly better than propagating a spurious failure.
    let mut last = lock(&LAST_TOGGLE);
    match toggle_decision(*last, now) {
        Some(new_last) => {
            *last = Some(new_last);
            true
        }
        None => false,
    }
}

// Unit tests for `monotonic_now_nanos`, `toggle_decision`, `LAST_TOGGLE`,
// and `TOGGLE_RATE_LIMIT_NS` live in the sibling `rate_limit_tests.rs`
// file (C-TEST-5 — keeps production source free of inline test code,
// matching the `commands/bubble/tests.rs` pattern). The module is wired
// as a child of `rate_limit` so the test file can use
// `use super::{...}` to access the private `monotonic_now_nanos` helper
// + the `pub(super)` items (`LAST_TOGGLE`, `TOGGLE_RATE_LIMIT_NS`,
// `toggle_decision`) without visibility changes.
#[cfg(test)]
#[path = "rate_limit_tests.rs"]
mod rate_limit_tests;
