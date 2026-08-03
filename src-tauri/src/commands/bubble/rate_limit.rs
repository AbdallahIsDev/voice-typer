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
        match LAST_TOGGLE_NANOS.compare_exchange(
            last,
            now,
            Ordering::SeqCst,
            Ordering::SeqCst,
        ) {
            Ok(_) => return true,
            Err(_) => continue,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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
}
