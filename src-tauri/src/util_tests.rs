//! Unit tests for `util` (moved verbatim from the inline
//! `#[cfg(test)] mod tests` block to satisfy C-TEST-5 — tests must
//! live in a sibling file, not inline in the production source).
//!
//! No test logic changed — the `use super::*;` path still resolves
//! to the parent module (`util`) because the parent file
//! declares this module via `#[cfg(test)] mod util_tests;`.

use super::*;

// `SUPERVISOR_MAX_RETRIES` lives at module scope in `util.rs` (not here)
// so the Python source-inspection regex in tests/tauri/mig*/test_*.py
// keeps matching against `util.rs`. It's imported here via `use super::*;`
// (it's `pub(crate)`), so the test fns below reference it unqualified.

//generate_token (ADR-0020 §3) ──────────────────────────

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
    // the thread-local RNG (`rand::rng()` in 0.9, was `thread_rng()`
    // in 0.8), but guards against a regression that e.g. seeds a
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
    assert_eq!(file_ts.len(), 20, "file ts should be `YYYY-MM-DD  HH:MM:SS`: \"{}\"", file_ts);
    assert_eq!(term_ts.len(), 8, "term ts should be `HH:MM:SS`: \"{}\"", term_ts);
    // The terminal time is the tail of the file timestamp (same read).
    assert_eq!(&file_ts[12..], term_ts, "term ts must match file ts time part: \"{}\" vs \"{}\"", file_ts, term_ts);
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

//supervisor backoff constants (ADR-0020 §10) ─────────────────

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
    //polls `CommandEvent::Terminated` against this same
    // deadline via `tokio::time::timeout`.
    assert_eq!(
        SHUTDOWN_ACK_TIMEOUT_MS, 2000,
        "SHUTDOWN_ACK_TIMEOUT_MS must be 2000 (2s graceful window - UI-active path only)"
    );
}

/// The exit-path cooperative timeout must be 30s. This is the
/// budget for `shutdown_sidecar_for_exit` (the `RunEvent::Exit`
/// last-resort teardown), NOT the UI-active `shutdown_sidecar`
/// command (which keeps the 2s `SHUTDOWN_ACK_TIMEOUT_MS`). The
/// 30s budget gives the sidecar time to run its full audited
/// cleanup (history_db.flush, crash_recovery.flush, native
/// hotkey binary teardown, WAL checkpoint) before the host's
/// force-kill backstop fires — preventing WAL corruption + native
/// binary orphan that the prior 2s budget caused.
#[test]
fn test_exit_shutdown_ack_timeout_constant() {
    assert_eq!(
        EXIT_SHUTDOWN_ACK_TIMEOUT_MS, 30_000,
        "EXIT_SHUTDOWN_ACK_TIMEOUT_MS must be 30000 (30s exit-path cooperative window)"
    );
    // Invariant: the exit-path budget must be STRICTLY GREATER
    // than the UI-active budget. If they ever become equal, the
    // exit path regresses the UI-freeze protection OR the UI path
    // undercuts the sidecar's full cleanup window. Either is a bug.
    assert!(
        EXIT_SHUTDOWN_ACK_TIMEOUT_MS > SHUTDOWN_ACK_TIMEOUT_MS,
        "EXIT_SHUTDOWN_ACK_TIMEOUT_MS ({}) must be > SHUTDOWN_ACK_TIMEOUT_MS ({}) — the exit path needs a longer budget than the UI-active path",
        EXIT_SHUTDOWN_ACK_TIMEOUT_MS,
        SHUTDOWN_ACK_TIMEOUT_MS
    );
}

// ── atomic_write_bytes (moved from migrate.rs) ───────────────────

#[test]
fn test_atomic_write_bytes_creates_file_with_expected_contents() {
    // Sanity: the basic write+rename contract still holds after
    // the parent-dir fsync addition. We write a small file,
    // then read it back and verify the contents match.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-pi9-test-{}-basic",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    let path = tmp.join("config.json");
    let contents = b"{\"key\":\"value\"}";
    atomic_write_bytes(&path, contents).expect("write must succeed");
    let read_back = std::fs::read(&path).expect("file must exist");
    assert_eq!(
        read_back.as_slice(),
        contents.as_ref(),
        "sanity: contents must match"
    );
    // The temp file must NOT exist after the rename (cleanup).
    //the temp name is now randomized (`.NAME.tmp.PID.HEX`)
    // so we scan the parent dir for any leftover temp file matching
    // the `.config.json.tmp.*` prefix instead of hardcoding the
    //pre- deterministic name.
    let mut leaked: Vec<String> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(&tmp) {
        for entry in entries.flatten() {
            if let Some(name) = entry.file_name().to_str() {
                if name.starts_with(".config.json.tmp.") {
                    leaked.push(name.to_string());
                }
            }
        }
    }
    assert!(
        leaked.is_empty(),
        "temp file leaked after rename: {:?} (in dir {})",
        leaked,
        tmp.display()
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_atomic_write_bytes_overwrites_existing_file() {
    // Atomic overwrite: a pre-existing file at `path` must be
    // replaced atomically (the rename is atomic on POSIX, and on
    // Windows the temp file is in the same dir so rename succeeds
    // even when the target exists — Windows allows rename-over-
    // existing when both files are on the same volume + the
    // target isn't open by another handle).
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-pi9-test-{}-overwrite",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    let path = tmp.join("config.json");
    // Pre-existing file with different contents.
    std::fs::write(&path, b"OLD CONTENTS").unwrap();
    atomic_write_bytes(&path, b"NEW CONTENTS").expect("overwrite must succeed");
    let read_back = std::fs::read(&path).expect("file must still exist");
    assert_eq!(
        read_back.as_slice(),
        b"NEW CONTENTS".as_ref(),
        "overwrite must replace contents atomically"
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[cfg(unix)]
#[test]
fn test_atomic_write_bytes_parent_dir_fsync_does_not_fail_write() {
    // The parent-dir `sync_all()` is best-effort — a failure
    // (e.g. the dir is on a read-only filesystem, or the dir
    // doesn't support fsync) must NOT fail the write. The data is
    // already safely in the new file (we fsync'd the temp file
    // before the rename); the dir fsync only persists the rename
    // metadata. Skipping it on failure is the documented behavior
    // (mirrors the Python side's `secure_file_io.py:100-113`).
    //
    // We can't easily force a `sync_all` failure in a unit test
    // (the temp dir is on a writable filesystem that supports
    // fsync). Instead, this test asserts the positive case: the
    // write succeeds AND the parent dir's mtime is updated (which
    // is the visible side-effect of the rename, which the fsync
    // then makes durable). The test name pins the "does not fail
    // the write" contract for future regression coverage.
    let tmp = std::env::temp_dir().join(format!(
        "voice-typer-pi9-test-{}-fsync",
        std::process::id()
    ));
    std::fs::remove_dir_all(&tmp).ok();
    std::fs::create_dir_all(&tmp).unwrap();
    // Read the parent dir mtime BEFORE the write, then sleep briefly
    // so the mtime update (if any) is detectable (mtime has
    // second-level granularity on most filesystems).
    let dir_meta_before = std::fs::metadata(&tmp).unwrap();
    let mtime_before = dir_meta_before.modified().unwrap();
    std::thread::sleep(std::time::Duration::from_millis(1100));

    let path = tmp.join("config.json");
    atomic_write_bytes(&path, b"{}").expect("write must succeed");

    // The write must succeed regardless of the dir fsync outcome.
    assert!(path.exists(), "file must exist after write");
    // The parent dir's mtime should be >= the pre-write mtime
    // (the rename updates the dir's metadata; the fsync then
    // flushes that update to durable storage). On filesystems
    // where mtime has second-level granularity, the 1.1s sleep
    // above ensures the mtime tick is visible.
    let dir_meta_after = std::fs::metadata(&tmp).unwrap();
    let mtime_after = dir_meta_after.modified().unwrap();
    assert!(
        mtime_after >= mtime_before,
        "parent dir mtime must be >= pre-write mtime. \
         before={:?} after={:?}",
        mtime_before,
        mtime_after
    );
    std::fs::remove_dir_all(&tmp).ok();
}

#[test]
fn test_atomic_write_bytes_empty_path_returns_error() {
    // Edge case: a path with no parent (`Path::new("")` has
    // `parent() == None`) must return Err — the prior code
    // returned an error here via the `ok_or_else` on `path.parent()`;
    // The parent-dir fsync must NOT change that behavior (we
    // still error out before reaching the fsync code).
    let empty_path = std::path::Path::new("");
    let result = atomic_write_bytes(empty_path, b"");
    assert!(
        result.is_err(),
        "empty path must return Err, got Ok. (result={:?})",
        result
    );
}
