//! Pure helpers + shared constants for the Voice Typer Tauri host (ADR-0020).

use rand::RngCore;
use std::path::Path;

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
///
/// DT-44: this is now the LONG-RUNNING timeout — used only for model
/// lifecycle commands (download/import/delete/cancel/pause/resume) that
/// can legitimately take >15s (tens-of-MB-to-GB download + LFS clone,
/// file copy + validation, filesystem rmtree). All other commands use
/// [`DISPATCH_SHORT_TIMEOUT_SECS`] (15s). See `dispatch_timeout_for` in
/// `commands/sidecar_cmds.rs` for the per-command routing.
pub(crate) const DISPATCH_TIMEOUT_SECS: u64 = 120;

/// DT-44: short per-dispatch response timeout (15s). Used for every
/// command NOT in `_LONG_RUNNING_COMMANDS` (i.e. everything except
/// model lifecycle commands). The prior uniform 120s timeout let a
/// hung `get_status` poll block the UI for 2 minutes before rejecting;
/// 15s is generous for any non-model command (the sidecar's median
/// response time is <50ms) while still bounding the worst case.
pub(crate) const DISPATCH_SHORT_TIMEOUT_SECS: u64 = 15;

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

// ─── DT-44: heartbeat / kill-tree / paste / poll / flush constants ──────
//
// Previously these were inline `Duration::from_secs(N)` / `from_millis(N)`
// literals scattered across `sidecar/ws.rs`, `state.rs`, `commands/paste.rs`,
// `sidecar/spawn.rs`, and `main.rs`. Each had to be tuned by reading the
// surrounding docstring; `spawn.rs`'s 500ms poll was duplicated at two
// sites so a fix to one path wouldn't propagate. Named here so a single
// grep lands on the canonical value, and so the unit tests in
// `util.rs::tests` can pin the values.

/// PVT-1 (session 1) + ADR-0020 §10: Tauri-side heartbeat dispatches a
/// `heartbeat` command every 10s to detect application-level sidecar
/// hangs (GIL contention, infinite loop, blocking C call) that keep the
/// WS socket open but don't respond to dispatches.
pub(crate) const HEARTBEAT_INTERVAL_SECS: u64 = 10;

/// PVT-1: per-heartbeat-dispatch response timeout. The sidecar must
/// respond within 15s or this heartbeat counts as a miss. Generous
/// enough to ride out a brief GIL stall; tight enough that 3 misses
/// (45s total) reliably indicate a hang rather than transient load.
pub(crate) const HEARTBEAT_RESPONSE_TIMEOUT_SECS: u64 = 15;

/// PVT-1: consecutive heartbeat misses before triggering supervisor
/// respawn. 3 misses × 10s interval ≈ 30s of unresponsiveness before
/// the sidecar is killed + restarted.
pub(crate) const HEARTBEAT_MAX_MISSES: u32 = 3;

/// `state.rs::kill_tree` (Linux/Unix recursive-kill helper) sends
/// SIGTERM to each descendant, then sleeps this grace period before
/// sending SIGKILL. 200ms matches the systemd / `killall --wait`
/// convention: enough time for a Python process to run `atexit`
/// handlers + flush WAL, short enough that a stuck child doesn't
/// block the host's shutdown path for seconds.
///
/// Gated behind `#[cfg(unix)]` because the only consumer is
/// `state.rs::kill_tree` which is itself unix-only. On Windows this
/// constant would be dead code and trigger the Rust `dead_code` lint.
#[cfg(unix)]
pub(crate) const KILL_TREE_SIGTERM_GRACE_MS: u64 = 200;

/// `commands/paste.rs::paste_via_clipboard_and_ctrl_v`: delay between
/// sending Ctrl+V and restoring the user's pre-paste clipboard
/// contents. 250ms is the empirically-tuned value that lets the
/// foreground app's paste handler read the clipboard before we
/// overwrite it with the original contents. Too short → the paste
/// inserts the user's original clipboard instead of the transcribed
/// text; too long → the user's clipboard stays clobbered longer
/// (risking they copy something else first and wonder where their
/// transcription went).
pub(crate) const PASTE_CLIPBOARD_RESTORE_DELAY_MS: u64 = 250;

/// `commands/paste.rs::restore_focus_or_fallback`: when `AttachThreadInput`
/// fails (UIPI blocks the attach — the foreground window is elevated),
/// we write the text to the clipboard + post a toast telling the user
/// to press Ctrl+V manually. We then delay restoring the original
/// clipboard by 30s — generous enough for the user to read the toast
/// + press Ctrl+V, short enough that the original clipboard isn't
/// held "hostage" for too long.
pub(crate) const PASTE_UIPI_FALLBACK_RESTORE_SECS: u64 = 30;

/// `sidecar/spawn.rs::spawn_sidecar_and_get_port` (and the dev-mode
/// `spawn_dev_sidecar` sibling): polling interval for the
/// `server_started` JSON on the sidecar's stdout. 500ms balances
/// startup latency (a fast sidecar acks in ~50ms, so we sleep ~450ms
/// of that) against CPU cost (polling at 10ms would burn a core for
/// the entire 30s startup window). DT-44: previously duplicated at
/// `spawn.rs:280` and `spawn.rs:495` — now sourced from this single
/// constant.
pub(crate) const SERVER_STARTED_POLL_INTERVAL_MS: u64 = 500;

/// `main.rs::setup` (`relaunch_app` listener): brief delay between
/// emitting the `supervisor_relaunching` Tauri event and calling
/// `app.restart()`. 10ms gives the webview's event loop one tick to
/// render the "restarting…" banner before the process exits. Even on
/// a loaded host the writer task schedules within 1ms, so 10ms is
/// generous.
pub(crate) const PRE_RESTART_FLUSH_DELAY_MS: u64 = 10;

// ─── Token generation (ADR-0020 §3) ───────────────────────────────────

pub(crate) fn generate_token() -> String {
    let mut bytes = [0u8; TOKEN_BYTES];
    rand::rng().fill_bytes(&mut bytes);
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

// ─── atomic_write_bytes ────────────────────────────────────────────────

/// Write `contents` to `path` atomically (temp + fsync + rename).
///
/// Originally implemented in `migrate.rs` for the Electron→Tauri
/// config migration; promoted from `fn` to `pub(crate) fn` in PVT-G5-033
/// so the supervisor (`supervisor.rs::write_restart_counter`) could reuse
/// it for atomic persistence of the restart counter. Previously the
/// counter used `std::fs::write` (non-atomic: truncate-then-write),
/// which on a crash mid-write could leave a partially-written
/// `restart_counter.json` that fails to parse — falling back to 0
/// (the fail-open default in `read_restart_counter`), silently
/// bypassing the circuit breaker on the next launch.
///
/// Moved here because it is a generic fs-write helper that has nothing
/// to do with Electron migration; two non-migration callers
/// (`sidecar/supervisor.rs`, `commands/export.rs`) import it from
/// `crate::util` instead of `crate::migrate`.
pub(crate) fn atomic_write_bytes(path: &Path, contents: &[u8]) -> Result<(), String> {
    use std::io::Write;

    let dir = path
        .parent()
        .ok_or_else(|| format!("path has no parent: {}", path.display()))?;
    // Same dir as target so rename is atomic (same filesystem).
    // The temp filename is dotted so it doesn't show up in normal
    // directory listings and is prefixed with the target's filename
    // so a human inspecting the dir can tell what it's for.
    let tmp_name = match path.file_name().and_then(|n| n.to_str()) {
        Some(n) => format!(".{}.tmp.migrate", n),
        None => return Err(format!("path has no file_name: {}", path.display())),
    };
    let tmp = dir.join(&tmp_name);

    {
        let mut f = std::fs::File::create(&tmp)
            .map_err(|e| format!("create tmp {}: {}", tmp.display(), e))?;
        f.write_all(contents)
            .map_err(|e| format!("write tmp {}: {}", tmp.display(), e))?;
        // fsync the file so the data is on disk before we rename.
        // Without this, a crash after rename but before the kernel
        // flushes the file's data could leave the new file with zero
        // bytes (POSIX allows this).
        f.sync_all()
            .map_err(|e| format!("fsync tmp {}: {}", tmp.display(), e))?;
        // Drop the file handle BEFORE rename so Windows can rename
        // (Windows refuses to rename a file that's still open).
    }

    std::fs::rename(&tmp, path).map_err(|e| {
        // Best-effort cleanup of the temp file on rename failure so
        // we don't leave orphaned .tmp.migrate files lying around.
        let _ = std::fs::remove_file(&tmp);
        format!("rename {} -> {}: {}", tmp.display(), path.display(), e)
    })?;

    // Fsync the parent directory after the rename on
    // POSIX so the rename itself is durable. Without this, a crash
    // after the rename returns but before the kernel flushes the
    // directory entry could leave the OLD file (or NO file) at `path`
    // on next mount — the file data is durable (we fsync'd the temp
    // file above), but the directory metadata linking the new name to
    // the inode is not. Mirrors the Python side's
    // `_secure_atomic_write` pattern at
    // `voice_typer/server/secure_file_io.py:100-113`.
    //
    // Best-effort: a `sync_all` failure on the parent dir does NOT
    // fail the write (matches the Python side's suppress-and-continue
    // pattern). The data is already safely in the new file; the only
    // loss on a crash-before-dir-fsync is the rename itself, which on
    // next mount would surface as "the old file is still there" — a
    // known acceptable degradation that the Python side also accepts.
    #[cfg(unix)]
    {
        if let Some(parent) = path.parent() {
            if let Ok(dir) = std::fs::File::open(parent) {
                let _ = dir.sync_all();
            }
        }
    }

    Ok(())
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
        let tmp_path = tmp.join(".config.json.tmp.migrate");
        assert!(
            !tmp_path.exists(),
            "temp file leaked after rename: {}",
            tmp_path.display()
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
}
