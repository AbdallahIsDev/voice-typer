//! `bubble_toggle_dictation` rate limiter (ADR-0020 §9): process-wide
//! monotonic last-toggle state. `Option` encoding (not AtomicU64+0) so
//! "never toggled" and "anchored at zero" are distinct.
//! see docs/code-notes/tauri-host.md#toggle-rate-limiter-encoding

use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use crate::state::lock;

/// Process-lifetime monotonic anchor; first call initializes it.
static ANCHOR: OnceLock<Instant> = OnceLock::new();

/// Last-toggle nanoseconds since [`ANCHOR`]; `None` = never toggled.
pub(super) static LAST_TOGGLE: Mutex<Option<u64>> = Mutex::new(None);

/// Minimum interval between toggles (500 ms → at most 2 toggles/sec).
pub(super) const TOGGLE_RATE_LIMIT_NS: u64 = 500_000_000;

/// Monotonic now (ns since anchor). Instant is immune to NTP skew.
fn monotonic_now_nanos() -> u64 {
    let anchor = ANCHOR.get_or_init(Instant::now);
    u64::try_from(Instant::now().duration_since(*anchor).as_nanos()).unwrap_or(u64::MAX)
}

/// Pure decision: `Some(now)` if allowed (caller stores it), `None` if
/// rate-limited. `last == None` always allows; clock-regress also allows.
pub(super) fn toggle_decision(last: Option<u64>, now: u64) -> Option<u64> {
    if let Some(last) = last {
        if now >= last && now.saturating_sub(last) < TOGGLE_RATE_LIMIT_NS {
            return None;
        }
    }
    Some(now)
}

/// Returns true if the toggle is allowed (>= 500ms since last); updates
/// `LAST_TOGGLE` on success. Mutex serializes concurrent webview windows.
pub(super) fn toggle_rate_limiter_allows() -> bool {
    let now = monotonic_now_nanos();
    // Poison-safe: critical section cannot panic; recover is better than
    // propagating a spurious failure.
    let mut last = lock(&LAST_TOGGLE);
    match toggle_decision(*last, now) {
        Some(new_last) => {
            *last = Some(new_last);
            true
        }
        None => false,
    }
}

// C-TEST-5: sibling test file (child module so `use super::*` works).
#[cfg(test)]
#[path = "rate_limit_tests.rs"]
mod rate_limit_tests;
