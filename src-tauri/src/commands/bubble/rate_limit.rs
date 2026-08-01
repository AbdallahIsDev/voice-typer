//! : bubble_toggle_dictation rate limiter (ADR-0020 §9 + ).
//!
//! Process-wide last-toggle timestamp (nanoseconds since UNIX epoch).
//! `AtomicU64` because:
//!   - The Tauri command handler can be invoked concurrently from
//!     multiple webview windows (e.g. if a future code path opens a
//!     second bubble), so we need atomic access.
//!   - `Instant` doesn't have a stable u64 representation (it's an
//!     opaque monotonic clock with no public conversion to integer
//!     types), so we use `SystemTime::now().duration_since(UNIX_EPOCH)`
//!     and store the nanoseconds. This is monotonic enough for rate
//!     limiting (NTP adjustments could shift it but not by enough to
//!     matter for a 500ms window).
//! 0 = "never toggled" (epoch start) — the first toggle always passes.

use std::sync::atomic::AtomicU64;

/// Process-wide last-toggle timestamp (nanoseconds since UNIX epoch).
/// See the module-level doc comment for the rationale.
pub(super) static LAST_TOGGLE_NANOS: AtomicU64 = AtomicU64::new(0);

//minimum interval between consecutive toggle_dictation
/// invocations (500ms = 500_000_000 ns). Matches the bubble renderer's
/// UI animation frame budget (~16ms) — a 500ms window allows ~30
/// clicks/sec before throttling, which is well above any legitimate
/// user click rate (~5 clicks/sec max) but well below the rate that
/// would DoS the sidecar's recording state machine.
pub(super) const TOGGLE_RATE_LIMIT_NS: u64 = 500_000_000;

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
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        //`d.as_nanos()` returns `u128`; the raw `as u64` cast
        // silently truncates after ~584 years (u64::MAX ns ≈ 584 years
        // from the UNIX epoch). `u64::try_from(...).unwrap_or(u64::MAX)`
        // saturates instead of truncating. In practice the value is
        // always <u64::MAX for any plausible timestamp, but the
        //saturating cast is the project-adopted pattern ( /
        //). The `.unwrap_or(0)` on the outer `map` handles the
        // clock-before-epoch case (returns 0 — "never toggled").
        .map(|d| u64::try_from(d.as_nanos()).unwrap_or(u64::MAX))
        .unwrap_or(0);
    loop {
        let last = LAST_TOGGLE_NANOS.load(Ordering::SeqCst);
        // If `now < last` (NTP skew or clock went backwards), allow
        // the toggle (don't penalize the user for a clock glitch).
        // If the elapsed time is < TOGGLE_RATE_LIMIT_NS, deny.
        if now >= last && now.saturating_sub(last) < TOGGLE_RATE_LIMIT_NS {
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
