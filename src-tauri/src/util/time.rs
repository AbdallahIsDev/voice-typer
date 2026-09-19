//! Log timestamp formatting (C-LOG-1). Format pinned by `time_tests.rs`
//! + Python log-format tests — DO NOT change the rendered shape.
//! see docs/code-notes/tauri-host.md#logging-format-c-log-1

#[cfg(test)]
pub(crate) fn now_timestamp() -> String {
    let (y, m, d, hour, min, sec) = now_civil_parts();
    format!(
        "{:04}-{:02}-{:02}  {:02}:{:02}:{:02}",
        y, m, d, hour, min, sec
    )
}

pub(crate) fn now_time_only() -> String {
    let (_, _, _, hour, min, sec) = now_civil_parts();
    format!("{:02}:{:02}:{:02}", hour, min, sec)
}

pub(crate) fn now_timestamps() -> (String, String) {
    let (y, m, d, hour, min, sec) = now_civil_parts();
    let file_ts = format!(
        "{:04}-{:02}-{:02}  {:02}:{:02}:{:02}",
        y, m, d, hour, min, sec
    );
    let term_ts = format!("{:02}:{:02}:{:02}", hour, min, sec);
    (file_ts, term_ts)
}

fn now_civil_parts() -> (i64, u64, u64, u64, u64, u64) {
    #[cfg(target_os = "windows")]
    {
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
        let ts = i64::try_from(now.as_secs()).unwrap_or(i64::MAX);
        let mut tm: libc::tm = unsafe { std::mem::zeroed() };
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
    // Primary target arms already returned; utc fallback is for other cfgs.
    #[allow(unreachable_code)]
    utc_civil_parts()
}

fn utc_civil_parts() -> (i64, u64, u64, u64, u64, u64) {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
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
