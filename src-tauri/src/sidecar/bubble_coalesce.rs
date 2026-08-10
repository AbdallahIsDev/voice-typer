//! Bubble-level coalesce predicate (ADR-0020 §9) — pure UI-rate-limiting
//! helper used by the WebSocket reader task to downsample the sidecar's
//! ~60 Hz `bubble_level` event stream to ≤30 Hz before emitting to the
//! bubble renderer.
//!
//! ## extraction rationale
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
//! moves the predicate here (its own focused module) so
//! `supervisor.rs` owns ONLY respawn/backoff logic. The signature +
//! behavior is preserved EXACTLY — only the module path changes.
//!
//! The min interval is `Duration::from_nanos(1_000_000_000 / hz)` — for the
//! default `BUBBLE_LEVEL_COALESCE_HZ = 30`, that's 33.333ms (nanosecond
//! precision), so a 60 Hz input stream emits every other event = 30 Hz.
//! previously used `Duration::from_millis(1000 / hz)`, which for
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
/// previously used `Duration::from_millis(1000 / hz)`, which
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
    // use `from_nanos(1_000_000_000 / hz)` instead of
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

// Sibling test module — tests live in `bubble_coalesce_tests.rs` (per
// C-TEST-5: no inline `#[cfg(test)] mod tests` blocks in production
// source).
#[cfg(test)]
#[path = "bubble_coalesce_tests.rs"]
mod bubble_coalesce_tests;
