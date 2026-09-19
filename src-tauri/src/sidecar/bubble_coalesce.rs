//! Bubble-level coalesce predicate (ADR-0020 §9): downsample the sidecar's
//! ~60 Hz `bubble_level` stream to ≤30 Hz before emitting to the bubble UI.
//! Interval uses `from_nanos` so high `hz` never integer-divides to 0
//! (see docs/code-notes/tauri-host.md#bubble-coalesce-interval).

use std::time::{Duration, Instant};

/// Returns `true` if the current event should be emitted given the
/// last-emitted timestamp and the target Hz rate (WS reader task only).
pub(crate) fn bubble_coalesce_should_emit(
    last_emitted: Option<Instant>,
    now: Instant,
    hz: u64,
) -> bool {
    // from_nanos(1e9/hz): non-zero interval for any plausible UI rate.
    // Default hz=30 → 33.333 ms (60 Hz in emits every other event).
    last_emitted.map_or(true, |t| {
        now.duration_since(t) >= Duration::from_nanos(1_000_000_000 / hz)
    })
}

// C-TEST-5: sibling test file.
#[cfg(test)]
#[path = "bubble_coalesce_tests.rs"]
mod bubble_coalesce_tests;
