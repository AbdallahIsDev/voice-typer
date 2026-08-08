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

/// ADR-0020 §10: supervisor retry cap. After `SUPERVISOR_MAX_RETRIES`
/// failed respawns, the supervisor emits `supervisor_relaunching` and
/// calls `app.restart()` (full-app relaunch). No runtime code path
/// reads this constant directly — the supervisor loop iterates over
/// `SUPERVISOR_BACKOFF_MS` (whose length == `SUPERVISOR_MAX_RETRIES`),
/// and the post-loop exhaustion path handles the relaunch.
///
/// Kept at module scope (not inside the test module) so the Python
/// source-inspection regex `pub\(crate\)\s+const\s+SUPERVISOR_MAX_RETRIES`
/// (test_shutdown_windows.py + 7 sibling mig* test files) keeps matching.
/// `#[allow(dead_code)]` suppresses the lint for the no-runtime-reader
/// contract; the constant is pinned equal to
/// `SUPERVISOR_BACKOFF_MS.len()` by `util_tests::test_supervisor_backoff_constants`.
#[allow(dead_code)]
pub(crate) const SUPERVISOR_MAX_RETRIES: u32 = 5;

/// ADR-0020 §10: cooperative shutdown hard timeout. The sidecar must
/// ack `{"type":"shutdown"}` and exit within this window; if it
/// doesn't, the host force-kills the process tree.
///
/// Used by the renderer-invoked `shutdown_sidecar` Tauri command (the
/// UI-active path). There a tight budget is appropriate because the UI
/// is still alive and a long block freezes it. The exit-path teardown
/// (`shutdown_sidecar_for_exit` → `on_host_exit`) uses the longer
/// [`EXIT_SHUTDOWN_ACK_TIMEOUT_MS`] instead — see its doc comment.
pub(crate) const SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000;

/// Cooperative shutdown timeout for the EXIT path only
/// (`RunEvent::Exit` / `ExitRequested` → `on_host_exit` →
/// `shutdown_sidecar_for_exit`). This is the LAST-RESORT teardown
/// path — the host is going away, the UI is already gone, and the
/// sidecar's audited worst-case cooperative cleanup (history_db.flush,
/// crash_recovery.flush, native hotkey binary teardown, WAL
/// checkpoint) can legitimately take ~30s on a cold disk.
///
/// The prior 2s budget (the same `SHUTDOWN_ACK_TIMEOUT_MS` used by the
/// UI-active command) force-killed the sidecar mid-cleanup, which
/// interrupted `history_db.flush()` (WAL not checkpointed → potential
/// corruption), `crash_recovery.flush()` (partial snapshot), and the
/// native hotkey binary teardown (left the binary running with the
/// mic/input device held). 30s gives the sidecar the full cleanup
/// window before the host's force-kill backstop fires.
///
/// The renderer-invoked `shutdown_sidecar` command keeps the tighter
/// 2s budget — there a long block would freeze the UI. The two paths
/// are intentionally separate constants so the exit path can be
/// lengthened without regressing the UI-freeze protection.
pub(crate) const EXIT_SHUTDOWN_ACK_TIMEOUT_MS: u64 = 30_000;

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
//this is now the LONG-RUNNING timeout — used only for model
/// lifecycle commands (download/import/delete/cancel/pause/resume) that
/// can legitimately take >15s (tens-of-MB-to-GB download + LFS clone,
/// file copy + validation, filesystem rmtree). All other commands use
/// [`DISPATCH_SHORT_TIMEOUT_SECS`] (15s). See `dispatch_timeout_for` in
/// `commands/sidecar_cmds.rs` for the per-command routing.
pub(crate) const DISPATCH_TIMEOUT_SECS: u64 = 120;

//short per-dispatch response timeout (15s). Used for every
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

/// ADR-0020 §11: max bytes per log file. Single-file policy:
/// when the log exceeds this cap it is truncated IN PLACE (emptied) and
/// writing continues — numbered backups (`.log.1`, ...) are NEVER created.
pub(crate) const LOG_MAX_BYTES: u64 = 5 * 1024 * 1024; // 5 MB

//heartbeat / kill-tree / paste / poll / flush constants ──────
//
// Previously these were inline `Duration::from_secs(N)` / `from_millis(N)`
// literals scattered across `sidecar/ws.rs`, `state.rs`, `commands/paste.rs`,
// `sidecar/spawn.rs`, and `main.rs`. Each had to be tuned by reading the
// surrounding docstring; `spawn.rs`'s 500ms poll was duplicated at two
// sites so a fix to one path wouldn't propagate. Named here so a single
// grep lands on the canonical value, and so the unit tests in
// `util.rs::tests` can pin the values.

//(session 1) + ADR-0020 §10: Tauri-side heartbeat dispatches a
/// `heartbeat` command every 10s to detect application-level sidecar
/// hangs (GIL contention, infinite loop, blocking C call) that keep the
/// WS socket open but don't respond to dispatches.
pub(crate) const HEARTBEAT_INTERVAL_SECS: u64 = 10;

//per-heartbeat-dispatch response timeout. The sidecar must
/// respond within 15s or this heartbeat counts as a miss. Generous
/// enough to ride out a brief GIL stall; tight enough that 3 misses
/// (45s total) reliably indicate a hang rather than transient load.
pub(crate) const HEARTBEAT_RESPONSE_TIMEOUT_SECS: u64 = 15;

//consecutive heartbeat misses before triggering supervisor
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

/// `sidecar/spawn.rs::spawn_sidecar_and_get_port` (and the dev-mode
/// `spawn_dev_sidecar` sibling): polling interval for the
/// `server_started` JSON on the sidecar's stdout. 500ms balances
/// startup latency (a fast sidecar acks in ~50ms, so we sleep ~450ms
/// of that) against CPU cost (polling at 10ms would burn a core for
//the entire 30s startup window). : previously duplicated at
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
    //write each byte directly into the pre-allocated String
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
            //(: was `.unwrap()` with a `SAFETY:` comment —
            // switched to `.expect` with a `Rationale:` prefix since
            // this is not an `unsafe` block and `SAFETY:` is reserved
            // for `unsafe` rationale.)
            write!(s, "{:02x}", b).expect("fmt::Write for String is infallible");
        }
        s
    }
}

// ─── now_timestamp (ADR-0020 §11) ─────────────────────────────────────

/// Format the current time as a clean space-separated timestamp
/// (UTC): `YYYY-MM-DD  HH:MM:SS` — TWO spaces between the date and
/// the time, seconds-only precision (no millisecond fraction), no
/// `T` separator, no timezone offset — matching the Python side's
/// `_iso_timestamp` in `voice_typer/server/log/formatters.py` so the
/// sidecar lines and Python lines in the log folder use the same
/// timestamp column width (the level column uses `{:5}` padding, so
/// INFO/WARN/ERROR align consistently across both files).
///
/// Uses Howard Hinnant's `civil_from_days` algorithm to convert days-
/// since-Unix-epoch to a (y, m, d) triple without pulling in `chrono`
/// or `time` (keeping the dep tree minimal per ADR-0020 §11's "prefer
/// minimal deps" guidance). UTC is fine for log timestamps — the
/// Python side also logs in UTC (`log.py` uses `gmtime()`).
///
/// `#[cfg(test)]`: production logging now calls [`now_timestamps`]
/// (single clock read for both sinks), so this standalone file-format
/// helper is referenced only by `util_tests::test_now_timestamp_format`.
/// Keeping it test-scoped avoids a `dead_code` warning in release
/// builds while preserving the format-pin test.
#[cfg(test)]
pub(crate) fn now_timestamp() -> String {
    let (y, m, d, hour, min, sec) = now_civil_parts();
    format!(
        "{:04}-{:02}-{:02}  {:02}:{:02}:{:02}",
        y, m, d, hour, min, sec
    )
}

/// Format the current time as TIME ONLY (UTC): `HH:MM:SS` — no date.
///
/// Used by the stderr/terminal sinks: the date lives only in the log
/// file (``now_timestamp``), and console output shows just the clock
/// time, matching the Python `_ColorFormatter` (which passes
/// ``include_date=False`` to `_iso_timestamp`).
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

/// Compute the current UTC civil date + clock time once, so both
/// timestamp formatters share a single clock read.
fn now_civil_parts() -> (i64, u64, u64, u64, u64, u64) {
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

// ─── atomic_write_bytes ────────────────────────────────────────────────

/// Write `contents` to `path` atomically (temp + fsync + rename).
///
/// Originally implemented in `migrate.rs` for the Electron→Tauri
//config migration; promoted from `fn` to `pub(crate) fn` in
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
    //
    //include a unique suffix (PID + 4 random bytes hex) so
    // concurrent invocations on the same target path don't race on
    // the same temp filename. Pre-fix the deterministic name
    // `.NAME.tmp.migrate` meant two concurrent `atomic_write_bytes`
    // calls to the same `path` would: (1) both open the SAME temp
    // file with `File::create` (which truncates), (2) interleave
    // writes, (3) race the rename — corrupted content + lost writes.
    // The PID disambiguates across processes; the 4-byte random
    // suffix disambiguates within a process (multiple threads, or
    // rapid sequential calls). `rand::rng()` is the thread-local RNG
    // (rand 0.9 API) — same as `generate_token` uses.
    let tmp_name = match path.file_name().and_then(|n| n.to_str()) {
        Some(n) => {
            let mut rng_bytes = [0u8; 4];
            rand::rng().fill_bytes(&mut rng_bytes);
            let suffix = u32::from_le_bytes(rng_bytes);
            format!(".{}.tmp.{}.{:08x}", n, std::process::id(), suffix)
        }
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

// ─── atomic_copy / atomic_copy_file ( moved from migrate.rs) ──
//
// these two helpers previously lived in `migrate.rs` next to
//the (now-removed) `atomic_write_bytes` impl.  already moved
// `atomic_write_bytes` here because it's a generic fs-write helper
// with no coupling to Electron-migration logic; the same reasoning
// applies to `atomic_copy` / `atomic_copy_file` — they're generic
// fs-copy helpers. Co-locating all three atomic-fs helpers in
// `util.rs` lets the `migrate.rs` callers reach them via a single
// `util::` qualification and drops the bridge
// `use crate::util::atomic_write_bytes;` import that lived in
// `migrate.rs` solely to let `atomic_copy` call `atomic_write_bytes`
// unqualified. Pure refactor — no behavior change.

/// M-65: atomically copy `src` to `dst` by reading src into memory
/// then writing via `atomic_write_bytes`. Suitable for small-to-
/// medium files (config.json, history.db, WAL sidecars). For very
/// large files (model weights) use `atomic_copy_file` instead — it
/// streams via `std::fs::copy` to a sibling temp file then renames,
/// avoiding the memory doubling that `atomic_copy`'s read-into-memory
/// would impose on multi-GB model weights.
pub(crate) fn atomic_copy(src: &Path, dst: &Path) -> Result<(), String> {
    let bytes = std::fs::read(src).map_err(|e| format!("read src {}: {}", src.display(), e))?;
    atomic_write_bytes(dst, &bytes)
}

//atomically copy a (potentially large) file from `src` to
/// `dst` by streaming to a sibling temp file then renaming. Unlike
/// `atomic_copy` (which reads the entire source into memory), this
/// streams the bytes via `std::fs::copy` so it's suitable for multi-GB
/// model weight files. The temp file lives in the SAME directory as
/// `dst` (so `rename` is an atomic same-filesystem op on POSIX), is
/// fsync'd before the rename (so the data is durable), and is
/// best-effort cleaned up on failure (so we don't leak temp files).
///
/// On a crash mid-copy, the temp file is left behind (best-effort
/// cleanup only runs on the Err path of THIS function); a future
/// launch's `atomic_copy_file` call to the same `dst` will simply
/// create a NEW temp file (unique suffix via PID + random) and the
/// stale temp file will be orphaned. The orphan is harmless (it's a
/// dotfile in the user's config dir) and the destination is never
/// left in a partial state.
pub(crate) fn atomic_copy_file(src: &Path, dst: &Path) -> Result<(), String> {
    use rand::RngCore;

    let dir = dst
        .parent()
        .ok_or_else(|| format!("dst has no parent: {}", dst.display()))?;
    // Same uniqueness scheme as `atomic_write_bytes` in util.rs —
    // PID + 4 random bytes hex so concurrent invocations on the same
    // dst don't race on the same temp filename.
    let tmp_name = match dst.file_name().and_then(|n| n.to_str()) {
        Some(n) => {
            let mut rng_bytes = [0u8; 4];
            rand::rng().fill_bytes(&mut rng_bytes);
            let suffix = u32::from_le_bytes(rng_bytes);
            format!("{}.tmp.copy.{}.{:08x}", n, std::process::id(), suffix)
        }
        None => return Err(format!("dst has no file_name: {}", dst.display())),
    };
    let tmp = dir.join(&tmp_name);

    // Stream-copy src → tmp via std::fs::copy (kernel-level splice on
    // Linux, no userspace buffering — efficient for large files).
    if let Err(e) = std::fs::copy(src, &tmp) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!("copy {} → {}: {}", src.display(), tmp.display(), e));
    }

    // fsync the temp file so the data is durable before the rename.
    // Without this, a crash after rename but before the kernel flushes
    // the temp file's data could leave the renamed file with zero
    // bytes (ext4's auto-no-csum mode) — corrupting the destination.
    {
        let f = std::fs::File::open(&tmp)
            .map_err(|e| format!("open tmp for fsync {}: {}", tmp.display(), e))?;
        // Best-effort fsync — not all filesystems support it (tmpfs,
        // network FS), and a failure here doesn't invalidate the copy
        // (the data is still in the page cache and will be flushed
        // eventually). Log and continue.
        if let Err(e) = f.sync_all() {
            // kept the historical `[MIGRATE]` log prefix
            // (this helper was originally in `migrate.rs`) so log
            // aggregators / test fixtures that match on the prefix
            // keep working — pure refactor, no log-output change.
            log::warn!(
                "[MIGRATE] fsync of tmp {} failed (non-fatal): {}",
                tmp.display(),
                e
            );
        }
    }

    // Atomic rename (same-filesystem on POSIX; on Windows the dst is
    // absent so rename succeeds).
    if let Err(e) = std::fs::rename(&tmp, dst) {
        let _ = std::fs::remove_file(&tmp);
        return Err(format!(
            "rename {} → {}: {}",
            tmp.display(),
            dst.display(),
            e
        ));
    }
    Ok(())
}

#[cfg(test)]
#[path = "util_tests.rs"]
mod util_tests;
