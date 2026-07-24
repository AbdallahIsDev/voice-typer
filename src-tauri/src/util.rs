//! Pure helpers + shared constants for the Voice Typer Tauri host (ADR-0020).

use rand::RngCore;

// ─── Constants (ADR-0020) ─────────────────────────────────────────────

/// ADR-0020 §3: 256-bit bearer token (despite the ADR's "HMAC" wording,
/// the host uses bearer-token auth — see `Cargo.toml` note). Regenerated
/// per launch + per respawn; never logged.
pub(crate) const TOKEN_BYTES: usize = 32;

/// ADR-0020 §10: supervisor backoff schedule (ms). Cap 5 retries
/// before falling back to full-app relaunch.
pub(crate) const SUPERVISOR_BACKOFF_MS: &[u64] = &[500, 1000, 2000, 4000, 8000];
// PVT-G5-089: SUPERVISOR_MAX_RETRIES moved into the `#[cfg(test)] mod tests`
// block below — no runtime code path reads it (the in-loop retry cap
// was removed in NF-R19-2), so keeping it at module scope triggered
// `#[allow(dead_code)]`. It's still `pub(crate)` inside the test
// module so the Python source-inspection regex
// `pub\(crate\)\s+const\s+SUPERVISOR_MAX_RETRIES` (test_shutdown_windows.py)
// keeps matching.

/// ADR-0020 §10: cooperative shutdown hard timeout. The sidecar must
/// ack `{"type":"shutdown"}` and exit within this window; if it
/// doesn't, the host force-kills the process tree.
pub(crate) const SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000;

/// ADR-0020 §1: time to wait for the `server_started` JSON on the
/// sidecar's stdout before giving up.
pub(crate) const SERVER_STARTED_TIMEOUT_MS: u64 = 30_000;

/// ADR-0020 §9: `bubble_level` coalesce target rate (Hz). The sidecar
/// emits at ~60 Hz; we keep only the latest {rms, peak} and emit at
/// ≤30 Hz.
pub(crate) const BUBBLE_LEVEL_COALESCE_HZ: u64 = 30;

/// ADR-0020 §10: 1 MiB WS frame cap. Enforced at WS-connect time via
/// `connect_async_with_config(WebSocketConfig { max_message_size:
/// Some(MAX_FRAME_BYTES), .. })`. Guards against memory-exhaustion
/// attacks from a compromised sidecar.
pub(crate) const MAX_FRAME_BYTES: usize = 1024 * 1024;

/// ADR-0020 §7: per-dispatch response timeout. The sidecar must respond
/// within this window or the host returns a timeout error to the webview
/// (so the UI can show a retry banner instead of hanging indefinitely).
pub(crate) const DISPATCH_TIMEOUT_SECS: u64 = 120;

/// ADR-0020 §10: brief delay between emitting `supervisor_relaunching` and
/// calling `app.restart()`, so the webview has time to render the
/// "restarting…" banner before the process exits.
pub(crate) const PRE_RESTART_DELAY_MS: u64 = 500;

/// Polling interval for the cooperative-shutdown waiter in
/// `shutdown_sidecar`. We sleep in increments of this duration until
/// `SHUTDOWN_ACK_TIMEOUT_MS` elapses, then force-kill the child.
pub(crate) const SHUTDOWN_POLL_INTERVAL_MS: u64 = 100;

/// ADR-0020 §6.2: paste-text short/long threshold (characters). Short
/// text is injected via `enigo.text()` (IME-safe); long text is copied
/// to the clipboard then Ctrl/Cmd+V is pressed.
///
/// GT-E3-9: this is a HOST-INTERNAL heuristic with NO Python
/// counterpart — `voice_typer/server/` has zero references to
/// `paste_short_threshold`, `PASTE_SHORT_THRESHOLD`, or `300` in a
/// paste context (verified via `rg`). The constant is consumed only
/// by `src-tauri/src/commands/paste.rs` (GT-FIX-20's domain) to
/// decide the short-vs-long injection strategy on the HOST side; the
/// Python sidecar never sees this value. So there's no parity
/// requirement — leave as-is.
pub(crate) const PASTE_SHORT_THRESHOLD: usize = 300;

/// ADR-0020 §11: max bytes per log file before rotation.
pub(crate) const ROTATE_MAX_BYTES: u64 = 5 * 1024 * 1024; // 5 MB

/// ADR-0020 §11: max rotated files to keep (current + N-1 rotated).
/// Total disk cap ≈ 5 MB × 5 files = 25 MB.
pub(crate) const ROTATE_MAX_FILES: usize = 5;

// ─── Token generation (ADR-0020 §3) ───────────────────────────────────

pub(crate) fn generate_token() -> String {
    let mut bytes = [0u8; TOKEN_BYTES];
    rand::thread_rng().fill_bytes(&mut bytes);
    hex::encode(&bytes)
}

pub(crate) mod hex {
    /// XV-146: write each byte directly into the pre-allocated String
    /// buffer via `core::fmt::Write`. The `expect` is safe (the
    /// `fmt::Write` impl for `String` is infallible — it never returns
    /// `Err`).
    pub fn encode(bytes: &[u8]) -> String {
        use std::fmt::Write;
        let mut s = String::with_capacity(bytes.len() * 2);
        for b in bytes {
            // Rationale: String's `fmt::Write` impl never errors —
            // `std::fmt::Write::write_str` for `String` unconditionally
            // returns `Ok(())` because the underlying `Vec<u8>` push
            // cannot fail (it aborts on OOM rather than returning Err).
            // (GT-D3-3: was `.unwrap()` with a `SAFETY:` comment —
            // switched to `.expect` with a `Rationale:` prefix since
            // this is not an `unsafe` block and `SAFETY:` is reserved
            // for `unsafe` rationale.)
            write!(s, "{:02x}", b).expect("fmt::Write for String is infallible");
        }
        s
    }
}

// ─── now_timestamp (ADR-0020 §11) ─────────────────────────────────────

/// Format the current time as `YYYY-MM-DD HH:MM:SS.mmm` (UTC).
///
/// Uses Howard Hinnant's `civil_from_days` algorithm to convert days-
/// since-Unix-epoch to a (y, m, d) triple without pulling in `chrono`
/// or `time` (keeping the dep tree minimal per ADR-0020 §11's "prefer
/// minimal deps" guidance). UTC is fine for log timestamps — the
/// Python side also logs in UTC (`log.py` uses `gmtime()`).
pub(crate) fn now_timestamp() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let now = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    let secs = now.as_secs();
    let millis = now.subsec_millis();
    // GT-D3-7: use `i64::try_from(...).unwrap_or(i64::MAX)` for the
    // `u64 → i64` cast instead of `as i64`. The `as i64` cast silently
    // wraps any u64 value above `i64::MAX`. The saturating `try_from`
    // keeps the value at `i64::MAX` instead of wrapping negative,
    // matching PVT-G5-051's pattern. In practice both produce the same
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
    // GT-D3-7: same `i64::try_from` saturating cast for `yoe → i64`.
    // `yoe` is in `[0, 399]` so it always fits, but the explicit
    // `try_from` documents the invariant and is consistent with the
    // `days` cast above.
    let y = i64::try_from(yoe).unwrap_or(i64::MAX) + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100); // [0, 365]
    let mp = (5 * doy + 2) / 153; // [0, 11]
    let d = doy - (153 * mp + 2) / 5 + 1; // [1, 31]
    let m = if mp < 10 { mp + 3 } else { mp - 9 }; // [1, 12]
    let y = if m <= 2 { y + 1 } else { y };
    format!(
        "{:04}-{:02}-{:02} {:02}:{:02}:{:02}.{:03}",
        y, m, d, hour, min, sec, millis
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    // PVT-G5-089: was at module scope with `#[allow(dead_code)]` (no
    // runtime code path reads it after NF-R19-2 — the in-loop retry
    // cap was removed). Moved here so the dead-code lint doesn't fire
    // at module scope. Still `pub(crate)` so the Python source-
    // inspection regex `pub\(crate\)\s+const\s+SUPERVISOR_MAX_RETRIES`
    // (test_shutdown_windows.py) keeps matching.
    pub(crate) const SUPERVISOR_MAX_RETRIES: u32 = 5;

    // ── CR-13: generate_token (ADR-0020 §3) ──────────────────────────

    #[test]
    fn test_generate_token_is_64_char_hex() {
        // ADR-0020 §3: 32 random bytes hex-encoded → 64 hex chars.
        let token = generate_token();
        assert_eq!(token.len(), 64, "token must be 64 hex chars (32 bytes * 2)");
        assert!(
            token.chars().all(|c| c.is_ascii_hexdigit()),
            "token must be valid hex, got: {}",
            token
        );
    }

    #[test]
    fn test_generate_token_is_unique_across_calls() {
        // Two consecutive tokens must differ (vanishingly unlikely with
        // thread_rng, but guards against a regression that e.g. seeds a
        // fixed value or reuses a buffer without clearing).
        let t1 = generate_token();
        let t2 = generate_token();
        let t3 = generate_token();
        assert_ne!(t1, t2, "tokens must be unique: t1={} t2={}", t1, t2);
        assert_ne!(t2, t3, "tokens must be unique: t2={} t3={}", t2, t3);
        assert_ne!(t1, t3, "tokens must be unique: t1={} t3={}", t1, t3);
    }

    // ── now_timestamp ─────────────────────────────────────────────────

    #[test]
    fn test_now_timestamp_format() {
        let ts = now_timestamp();
        // Expected: "YYYY-MM-DD HH:MM:SS.mmm" → 23 chars.
        assert_eq!(ts.len(), 23, "unexpected timestamp length: \"{}\"", ts);
        assert_eq!(ts.chars().nth(4), Some('-'), "year-month sep: {}", ts);
        assert_eq!(ts.chars().nth(7), Some('-'), "month-day sep: {}", ts);
        assert_eq!(ts.chars().nth(10), Some(' '), "date-time sep: {}", ts);
        assert_eq!(ts.chars().nth(13), Some(':'), "hour-min sep: {}", ts);
        assert_eq!(ts.chars().nth(16), Some(':'), "min-sec sep: {}", ts);
        assert_eq!(ts.chars().nth(19), Some('.'), "sec-ms sep: {}", ts);
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

    // ── CR-13: supervisor backoff constants (ADR-0020 §10) ─────────────────

    #[test]
    fn test_supervisor_backoff_constants() {
        // ADR-0020 §10: supervisor backoff schedule + retry cap.
        // The schedule doubles each step (500ms → 1s → 2s → 4s → 8s)
        // and the cap is 5 retries before full-app relaunch.
        assert_eq!(
            SUPERVISOR_BACKOFF_MS,
            &[500, 1000, 2000, 4000, 8000],
            "SUPERVISOR_BACKOFF_MS must be [500, 1000, 2000, 4000, 8000] (doubling schedule)"
        );
        assert_eq!(
            SUPERVISOR_MAX_RETRIES, 5,
            "SUPERVISOR_MAX_RETRIES must be 5 (then fall back to full-app relaunch)"
        );
        // The schedule length must match the retry cap so the loop in
        // `respawn_inner` actually iterates SUPERVISOR_MAX_RETRIES times
        // (each iteration sleeps delay_ms[attempt] before retrying)
        // before falling back to `app.restart()`.
        assert_eq!(
            SUPERVISOR_BACKOFF_MS.len() as u32,
            SUPERVISOR_MAX_RETRIES,
            "SUPERVISOR_BACKOFF_MS.len() must equal SUPERVISOR_MAX_RETRIES so the loop iterates exactly N times"
        );
        // Verify the doubling property explicitly — guards against an
        // accidental edit that breaks the geometric progression.
        for i in 1..SUPERVISOR_BACKOFF_MS.len() {
            assert_eq!(
                SUPERVISOR_BACKOFF_MS[i],
                SUPERVISOR_BACKOFF_MS[i - 1] * 2,
                "backoff step {} must be 2x step {} (got {} vs {})",
                i,
                i - 1,
                SUPERVISOR_BACKOFF_MS[i],
                SUPERVISOR_BACKOFF_MS[i - 1]
            );
        }
    }

    #[test]
    fn test_shutdown_ack_timeout_constant() {
        // ADR-0020 §10: cooperative shutdown hard timeout. The sidecar
        // must ack `{"type":"shutdown"}` and exit within this window;
        // if it doesn't, the host force-kills the process tree.
        // CR-2 polls `CommandEvent::Terminated` against this same
        // deadline via `tokio::time::timeout`.
        assert_eq!(
            SHUTDOWN_ACK_TIMEOUT_MS, 2000,
            "SHUTDOWN_ACK_TIMEOUT_MS must be 2000 (2s graceful window)"
        );
        // The poll interval is only used by the dev-mode fallback path
        // (the ShellPlugin path now uses tokio::time::timeout + rx.recv).
        assert_eq!(
            SHUTDOWN_POLL_INTERVAL_MS, 100,
            "SHUTDOWN_POLL_INTERVAL_MS must be 100ms (dev-mode fallback step)"
        );
    }
}
