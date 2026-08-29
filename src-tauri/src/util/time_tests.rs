#![allow(
    clippy::unwrap_used,
    clippy::expect_used,
    clippy::panic,
    clippy::unreachable,
    clippy::todo,
    clippy::unimplemented,
    clippy::cast_possible_truncation,
    clippy::assertions_on_constants
)] // const invariant pins with descriptive runtime messages

//! Unit tests for `util::time` (log timestamp formatting).
//!
//! Moved verbatim from `util_tests.rs` when the timestamp concern was
//! split into its own submodule — tests move with their code. No test
//! logic changed; `use super::*;` now resolves to the `util::time`
//! module because this file is declared via
//! `#[cfg(test)] #[path = "time_tests.rs"] mod time_tests;` inside
//! `util/time.rs`.

use super::*;

// ── now_timestamp ─────────────────────────────────────────────────

#[test]
fn test_now_timestamp_format() {
    let ts = now_timestamp();
    // Clean space-separated format `YYYY-MM-DD  HH:MM:SS` → 20 chars:
    // TWO spaces between the date and the time (so the time column
    // aligns in the log file), seconds-only precision (no millisecond
    // fraction), no `T` separator, no `Z` suffix — reads naturally and
    // matches the Python side's clean `_iso_timestamp` format.
    assert_eq!(ts.len(), 20, "unexpected timestamp length: \"{}\"", ts);
    assert_eq!(ts.chars().nth(4), Some('-'), "year-month sep: {}", ts);
    assert_eq!(ts.chars().nth(7), Some('-'), "month-day sep: {}", ts);
    // TWO space separators between date and time (no `T`).
    assert_eq!(ts.chars().nth(10), Some(' '), "date-time sep 1: {}", ts);
    assert_eq!(ts.chars().nth(11), Some(' '), "date-time sep 2: {}", ts);
    assert_eq!(ts.chars().nth(14), Some(':'), "hour-min sep: {}", ts);
    assert_eq!(ts.chars().nth(17), Some(':'), "min-sec sep: {}", ts);
    // No millisecond fraction, no tz suffix.
    assert!(!ts.contains('.'), "no millis expected: {}", ts);
    assert!(!ts.contains('Z'), "no Z suffix expected: {}", ts);
}

#[test]
fn test_now_time_only_format() {
    let ts = now_time_only();
    // Time-only format `HH:MM:SS` → 8 chars (no date — the date lives
    // only in the log file; terminal output shows just the clock).
    assert_eq!(ts.len(), 8, "unexpected time-only length: \"{}\"", ts);
    assert_eq!(ts.chars().nth(2), Some(':'), "hour-min sep: {}", ts);
    assert_eq!(ts.chars().nth(5), Some(':'), "min-sec sep: {}", ts);
    // No date prefix, no millis, no tz suffix.
    assert!(!ts.contains('-'), "no date expected: {}", ts);
    assert!(!ts.contains('.'), "no millis expected: {}", ts);
    assert!(!ts.contains('Z'), "no Z suffix expected: {}", ts);
}

#[test]
fn test_now_timestamps_pair_consistent() {
    let (file_ts, term_ts) = now_timestamps();
    // Both come from a SINGLE clock read (no second-boundary straddle).
    assert_eq!(
        file_ts.len(),
        20,
        "file ts should be `YYYY-MM-DD  HH:MM:SS`: \"{}\"",
        file_ts
    );
    assert_eq!(
        term_ts.len(),
        8,
        "term ts should be `HH:MM:SS`: \"{}\"",
        term_ts
    );
    // The terminal time is the tail of the file timestamp (same read).
    assert_eq!(
        &file_ts[12..],
        term_ts,
        "term ts must match file ts time part: \"{}\" vs \"{}\"",
        file_ts,
        term_ts
    );
    // No millis / tz in either.
    assert!(!file_ts.contains('.'), "no millis in file ts: {}", file_ts);
    assert!(!term_ts.contains('Z'), "no Z in term ts: {}", term_ts);
}

#[test]
fn test_now_timestamp_increases() {
    let t1 = now_timestamp();
    std::thread::sleep(std::time::Duration::from_millis(10));
    let t2 = now_timestamp();
    // The timestamp should not decrease (compare lexicographically
    // since the format is fixed-width sortable).
    assert!(t2 >= t1, "timestamp went backwards: t1={} t2={}", t1, t2);
}
