//! Log timestamp formatting (ADR-0020 §11 + the C-LOG-1 canonical
//! log-line template).
//!
//! Split out of the former catch-all `util.rs` so the timestamp
//! helpers (and Howard Hinnant's `civil_from_days` day→date math they
//! embed) live in one focused module. Re-exported from `crate::util`
//! so every existing `crate::util::now_time_only` /
//! `crate::util::now_timestamps` path keeps resolving.
//!
//! The output format is pinned by `time_tests.rs`
//! (`test_now_timestamp_format` / `test_now_time_only_format` /
//! `test_now_timestamps_pair_consistent`) and by the Python log-format
//! tests: DO NOT change the rendered shape, only (if ever) its
//! location.

/// Format the current time as a clean space-separated timestamp
/// (LOCAL wall clock): `YYYY-MM-DD  HH:MM:SS`, TWO spaces between the
/// date and the time, seconds-only precision (no millisecond
/// fraction), no `T` separator, no timezone offset, matching the
/// Python side's `_iso_timestamp` in
/// `voice_typer/server/log/formatters.py` so the sidecar lines and
/// Python lines in the log folder use the same timestamp column width
/// (the level column uses `{:5}` padding, so INFO/WARN/ERROR align
/// consistently across both files).
///
/// BOTH clocks must agree (fix): the Python side logs
/// `time.localtime` (`formatters.py`), so a UTC host stamp made every
/// cross-file correlation (`voice-typer-rust.log` ↔ `voice-typer.log`)
/// off by the user's UTC offset. The host therefore renders LOCAL
/// civil time via the OS API:
///   - Windows: `GetLocalTime()` (Win32), which already applies the
///     machine's timezone + DST rules.
///   - POSIX: `localtime_r()` on the current `time_t` (`libc`, already
///     a dependency for the POSIX reaper's `setsid`).
///   - Any other target: the UTC `civil_from_days` fallback below, so
///     the module still compiles + renders the pinned shape.
///
/// The UTC path uses Howard Hinnant's `civil_from_days` algorithm to
/// convert days-since-Unix-epoch to a (y, m, d) triple without pulling
/// in `chrono` or `time` (keeping the dep tree minimal per ADR-0020
/// §11's "prefer minimal deps" guidance). UTC remains the clock for
/// the opt-in JSON formatter only (`VOICE_TYPER_LOG_JSON=1`, which
/// stamps `Z` + millis), never for the text sinks.
///
/// `#[cfg(test)]`: production logging now calls [`now_timestamps`]
/// (single clock read for both sinks), so this standalone file-format
/// helper is referenced only by `time_tests::test_now_timestamp_format`
/// (and `test_now_timestamp_increases`). Keeping it test-scoped avoids
/// a `dead_code` warning in release builds while preserving the
/// format-pin test.
#[cfg(test)]
pub(crate) fn now_timestamp() -> String {
    let (y, m, d, hour, min, sec) = now_civil_parts();
    format!(
        "{:04}-{:02}-{:02}  {:02}:{:02}:{:02}",
        y, m, d, hour, min, sec
    )
}

/// Format the current time as TIME ONLY (LOCAL wall clock):
/// `HH:MM:SS`, no date.
///
/// Used by the stderr/terminal sinks: the date lives only in the log
/// file (``now_timestamp``), and console output shows just the clock
/// time, matching the Python `_ColorFormatter` (which passes
/// ``include_date=False`` to `_iso_timestamp`). Same local clock as
/// the file sink (see `now_timestamp`'s doc for the alignment
/// rationale).
pub(crate) fn now_time_only() -> String {
    let (_, _, _, hour, min, sec) = now_civil_parts();
    format!("{:02}:{:02}:{:02}", hour, min, sec)
}

/// Return BOTH the file timestamp and the terminal time-only string
/// from a SINGLE clock read, so a log record's file line and terminal
/// line can never straddle a second boundary (``now_timestamp`` and
/// ``now_time_only`` called separately could disagree by one second if
/// a record is emitted exactly at a second tick).
pub(crate) fn now_timestamps() -> (String, String) {
    let (y, m, d, hour, min, sec) = now_civil_parts();
    let file_ts = format!(
        "{:04}-{:02}-{:02}  {:02}:{:02}:{:02}",
        y, m, d, hour, min, sec
    );
    let term_ts = format!("{:02}:{:02}:{:02}", hour, min, sec);
    (file_ts, term_ts)
}

/// Compute the current LOCAL civil date + clock time once, so both
/// timestamp formatters share a single clock read (`now_timestamps`)
/// and the file + terminal lines of one record can never straddle a
/// second boundary.
fn now_civil_parts() -> (i64, u64, u64, u64, u64, u64) {
    #[cfg(target_os = "windows")]
    {
        // `GetLocalTime` returns the already-tz-adjusted local
        // wall clock (no manual bias math, DST handled by the OS).
        // SAFETY: the function reads the system clock and writes into
        // the caller-supplied `SYSTEMTIME`; no preconditions beyond the
        // struct being addressable.
        let st = unsafe { windows::Win32::System::SystemInformation::GetLocalTime() };
        return (
            i64::from(st.wYear),
            u64::from(st.wMonth),
            u64::from(st.wDay),
            u64::from(st.wHour),
            u64::from(st.wMinute),
            u64::from(st.wSecond),
        );
    }
    #[allow(unreachable_code)] // the fallback below is reachable on
    // non-Windows/non-Unix targets only; the compiler flags it on the
    // two primary targets, where the cfg-arm above already returned.
    #[cfg(unix)]
    {
        use std::time::{SystemTime, UNIX_EPOCH};
        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default();
        // `libc::time_t` is `i64` on every 64-bit target this app
        // ships; the saturating `try_from` documents the invariant
        // (same pattern as the `days` cast below) instead of letting
        // a bare `as` cast wrap.
        let ts = i64::try_from(now.as_secs()).unwrap_or(i64::MAX);
        let mut tm: libc::tm = unsafe { std::mem::zeroed() };
        // SAFETY: `localtime_r` writes into the caller-owned `tm` and
        // takes a pointer to our stack-local `time_t`; both outlive
        // the call. It does NOT return a shared static (that is the
        // non-reentrant `localtime`), so no data race is possible.
        let ok = unsafe { !libc::localtime_r(&ts, &mut tm).is_null() };
        if ok {
            return (
                i64::from(tm.tm_year) + 1900,
                u64::try_from(tm.tm_mon + 1).unwrap_or(1),
                u64::try_from(tm.tm_mday).unwrap_or(1),
                u64::try_from(tm.tm_hour).unwrap_or(0),
                u64::try_from(tm.tm_min).unwrap_or(0),
                u64::try_from(tm.tm_sec).unwrap_or(0),
            );
        }
        // `localtime_r` failed (out-of-range timestamp): fall through
        // to the UTC fallback below rather than dropping the line.
    }
    // UNREACHABLE on Windows (the cfg-arm above always returns) and
    // effectively unreachable on POSIX (localtime_r only fails for
    // out-of-range timestamps, which as_secs cannot produce for a real
    // clock). Kept as the compile-time fallback for OTHER targets.
    #[allow(unreachable_code)]
    utc_civil_parts()
}

/// UTC fallback: used on any target without a local-time API above,
/// and when `localtime_r` fails. Identical algorithm to the historical
/// implementation (Howard Hinnant's `civil_from_days`).
fn utc_civil_parts() -> (i64, u64, u64, u64, u64, u64) {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    //use `i64::try_from(...).unwrap_or(i64::MAX)` for the
    // `u64 → i64` cast instead of `as i64`. The `as i64` cast silently
    // wraps any u64 value above `i64::MAX`. The saturating `try_from`
    // keeps the value at `i64::MAX` instead of wrapping negative,
    //matching 's pattern. In practice both produce the same
    // output for any real timestamp.
    let days = i64::try_from(secs / 86_400).unwrap_or(i64::MAX);
    let rem = secs % 86_400;
    let hour = rem / 3600;
    let min = (rem % 3600) / 60;
    let sec = rem % 60;
    // Howard Hinnant's civil_from_days (http://howardhinnant.github.io/date_algorithms.html).
    let z = days + 719468;
    let era = if z >= 0 { z } else { z - 146096 } / 146097;
    let doe = (z - era * 146097) as u64; // [0, 146096]
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365; // [0, 399]
                                                                     //same `i64::try_from` saturating cast for `yoe → i64`.
                                                                     // `yoe` is in `[0, 399]` so it always fits, but the explicit
                                                                     // `try_from` documents the invariant and is consistent with the
                                                                     // `days` cast above.
    let y = i64::try_from(yoe).unwrap_or(i64::MAX) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let y = if m <= 2 { y + 1 } else { y };
    (y, m, d, hour, min, sec)
}

// Sibling test module: tests live in `time_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source).
#[cfg(test)]
#[path = "time_tests.rs"]
mod time_tests;
