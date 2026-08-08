//! : bubble_toggle_dictation rate limiter (ADR-0020 §9 + ).
//!
//! Process-wide last-toggle timestamp (nanoseconds since process start).
//! `AtomicU64` because:
//!   - The Tauri command handler can be invoked concurrently from
//!     multiple webview windows (e.g. if a future code path opens a
//!     second bubble), so we need atomic access.
//!   - We store a monotonic timestamp derived from `Instant::now()` —
//!     `Instant` has no public conversion to a stable `u64` (it's an
//!     opaque monotonic clock), so we anchor it once via a
//!     `OnceLock<Instant>` and store the elapsed nanoseconds since
//!     that anchor in the atomic. `Instant` is monotonic (immune to
//!     NTP skew / wall-clock jumps), which closes the
//!     `SystemTime::now()` rate-limiter bypass vector where a
//!     malicious or misconfigured NTP step could disable the limiter
//!     by jumping the wall clock backwards.
//! 0 = "never toggled" (sentinel) — the first toggle always passes.

use std::sync::atomic::AtomicU64;
use std::sync::OnceLock;
use std::time::Instant;

/// Process-wide anchor instant. Initialized on the first call to
/// [`toggle_rate_limiter_allows`]; subsequent calls compute the elapsed
/// duration since this anchor and store it (as nanoseconds) in
/// [`LAST_TOGGLE_NANOS`]. Using a process-lifetime anchor (rather than
/// re-deriving from `SystemTime::now()` each call) keeps the stored
/// timestamps monotonic and immune to NTP adjustments.
static ANCHOR: OnceLock<Instant> = OnceLock::new();

/// Process-wide last-toggle timestamp (nanoseconds since the
/// process-lifetime [`ANCHOR`] instant).
/// See the module-level doc comment for the rationale.
pub(super) static LAST_TOGGLE_NANOS: AtomicU64 = AtomicU64::new(0);

//minimum interval between consecutive toggle_dictation
/// invocations (500ms = 500_000_000 ns). Matches the bubble renderer's
/// UI animation frame budget (~16ms) — a 500ms window allows ~30
/// clicks/sec before throttling, which is well above any legitimate
/// user click rate (~5 clicks/sec max) but well below the rate that
/// would DoS the sidecar's recording state machine.
pub(super) const TOGGLE_RATE_LIMIT_NS: u64 = 500_000_000;

/// Returns a monotonic "now" timestamp in nanoseconds since the
/// process-lifetime [`ANCHOR`] instant. The anchor is initialized
/// lazily on first call so this function never panics and never reads
/// `Instant` before it's available. `Instant` is monotonic by contract
/// (immune to NTP skew), so the returned value never decreases between
/// successive calls — the rate limiter's compare-exchange loop can
/// rely on `now >= last` once `last != 0`.
fn monotonic_now_nanos() -> u64 {
    let anchor = ANCHOR.get_or_init(Instant::now);
    // `duration_since(*anchor)` returns `Duration`; `as_nanos()` is
    // `u128`. The `u64::try_from(...).unwrap_or(u64::MAX)` saturating
    // cast matches the project-adopted pattern (a process running for
    // ~584 years would saturate, which is not a real concern).
    u64::try_from(Instant::now().duration_since(*anchor).as_nanos()).unwrap_or(u64::MAX)
}

//rate-limiter predicate. Returns `true` if the toggle is
/// allowed (>= 500ms since the last toggle), `false` if rate-limited.
/// Updates `LAST_TOGGLE_NANOS` atomically on success.
///
/// Uses `compare_exchange` in a loop to handle the rare race where two
/// concurrent calls both read the same `last`. The loop terminates
/// quickly: on `compare_exchange` failure (another caller updated the
/// timestamp), we re-read the new timestamp and re-check the rate
/// limit (almost always returns `false` on the second iteration
/// because the winning caller just updated it <500ms ago).
pub(super) fn toggle_rate_limiter_allows() -> bool {
    use std::sync::atomic::Ordering;
    let now = monotonic_now_nanos();
    loop {
        let last = LAST_TOGGLE_NANOS.load(Ordering::SeqCst);
        // `last == 0` is the "never toggled" sentinel — the first
        // toggle always passes. Otherwise, if `now < last` (defensive
        // — `Instant` is monotonic so this should never happen, but
        // the check protects against a future refactor that swaps the
        // clock source), allow the toggle (don't penalize the user
        // for a clock glitch). If the elapsed time is <
        // TOGGLE_RATE_LIMIT_NS, deny.
        if last != 0 && now >= last && now.saturating_sub(last) < TOGGLE_RATE_LIMIT_NS {
            return false;
        }
        // Try to claim this toggle by updating LAST_TOGGLE_NANOS. If
        // another caller beat us, retry the loop with the new value.
        match LAST_TOGGLE_NANOS.compare_exchange(last, now, Ordering::SeqCst, Ordering::SeqCst) {
            Ok(_) => return true,
            Err(_) => continue,
        }
    }
}

// Unit tests for `monotonic_now_nanos`, `LAST_TOGGLE_NANOS`, and
// `TOGGLE_RATE_LIMIT_NS` live in the sibling `rate_limit_tests.rs` file
// (C-TEST-5 — keeps production source free of inline test code, matching
// the `commands/bubble/tests.rs` pattern). The module is wired as a child
// of `rate_limit` so the test file can use `use super::{...}` to access
// the private `monotonic_now_nanos` helper + the `pub(super)` statics
// (`LAST_TOGGLE_NANOS`, `TOGGLE_RATE_LIMIT_NS`) without visibility
// changes.
#[cfg(test)]
#[path = "rate_limit_tests.rs"]
mod rate_limit_tests;
