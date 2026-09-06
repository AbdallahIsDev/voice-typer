//! Pure helpers + shared constants for the Voice Typer Tauri host (ADR-0020).
//!
//! Concern layout (each helper concern lives in a focused submodule and
//! is re-exported from here so every existing `crate::util::X` path
//! keeps resolving):
//!
//! - **THIS file** — the shared constants block (token, supervisor,
//!   shutdown, heartbeat, kill-tree, dispatch, restart, log-rotation).
//! - [`crypto`] — bearer-token + session-id generation (+ `hex`).
//! - [`time`] — log timestamp formatting (the canonical log template).
//! - [`atomic_fs`] — atomic file write/copy primitives.
//!
//! The constants deliberately stay in THIS file rather than a
//! `consts` submodule: the cross-language source-inspection tests in
//! `tests/tauri/mig15|16|17` regex these exact `pub(crate) const`
//! declarations against this file's raw source (and assert that dead
//! constants removed in past refactors never reappear here), so
//! relocating them would regress the suite without a coordinated test
//! update. The supervisor retry cap is `SUPERVISOR_BACKOFF_MS.len()`
//! (5) — the now-deleted standalone `SUPERVISOR_MAX_RETRIES` constant
//! was production-dead and is pinned as a schedule-length invariant in
//! `util_tests` + the mig* gate tests instead.

pub(crate) mod atomic_fs;
pub(crate) mod crypto;
pub(crate) mod time;

// Re-exports — keep every pre-split `crate::util::<name>` call site
// (and the `use crate::util;`-then-`util::<name>` pattern inside
// `migrate/`) resolving unchanged.
pub(crate) use atomic_fs::{atomic_copy, atomic_copy_file, atomic_write_bytes};
pub(crate) use crypto::{generate_token, session_id};
pub(crate) use time::{now_time_only, now_timestamps};

// ─── Constants (ADR-0020) ─────────────────────────────────────────────

/// ADR-0020 §3: 256-bit bearer token (despite the ADR's "HMAC" wording,
/// the host uses bearer-token auth — see `Cargo.toml` note). Regenerated
/// per launch + per respawn; never logged.
pub(crate) const TOKEN_BYTES: usize = 32;

/// ADR-0020 §10: supervisor backoff schedule (ms). The schedule LENGTH
/// is the supervisor retry cap: `respawn_inner` iterates these steps
/// and falls back to full-app relaunch after the last one (5 retries).
pub(crate) const SUPERVISOR_BACKOFF_MS: &[u64] = &[500, 1000, 2000, 4000, 8000];

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

/// Download-scale dispatch timeout (1h). `download_model` / `import_model`
/// stream multi-GB model files and legitimately run for many minutes on
/// normal connections — the previous uniform 120s long-running cap
/// aborted the host-side dispatch (and therefore the renderer's promise)
/// while the Python sidecar kept downloading, producing a false-failure
/// UI over a download that was still progressing.
pub(crate) const DISPATCH_DOWNLOAD_TIMEOUT_SECS: u64 = 3600;

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

/// ADR-0020 §11: three-tier log-cleanup design (mirrors
/// `voice_typer/server/_log_constants.py`).
///
/// Tier 1 — age retention (session-start delete): the startup sweep
/// deletes any log file in `logs/` older than
/// [`LOG_AGE_RETENTION_SECS`] (7 days).
///
/// Tier 2 — size fallback (session-start delete): the startup sweep
/// deletes any log file larger than [`LOG_SIZE_FALLBACK_BYTES`]
/// (25 MB) even if freshly written — covers a marathon session that
/// pushed a log past the fallback between startups. Checked ONLY at
/// session start, never mid-session.
///
/// Tier 3 — mid-session hard ceiling: [`LOG_MAX_BYTES`] (40 MB).
/// Single-file policy: when the log exceeds this cap it is truncated
/// IN PLACE (emptied) and writing continues — numbered backups
/// (`.log.1`, ...) are NEVER created. Deliberately far above the
/// Tier-2 fallback so normal multi-day usage never truncates
/// mid-session.
pub(crate) const LOG_AGE_RETENTION_SECS: u64 = 7 * 24 * 60 * 60; // 7 days
pub(crate) const LOG_SIZE_FALLBACK_BYTES: u64 = 25 * 1024 * 1024; // 25 MB
pub(crate) const LOG_MAX_BYTES: u64 = 40 * 1024 * 1024; // 40 MB

//heartbeat / kill-tree / paste / poll / flush constants ──────
//
// Previously these were inline `Duration::from_secs(N)` / `from_millis(N)`
// literals scattered across `sidecar/ws.rs`, `state.rs`, `commands/paste.rs`,
// `sidecar/spawn.rs`, and `main.rs`. Each had to be tuned by reading the
// surrounding docstring; `spawn.rs`'s 500ms poll was duplicated at two
// sites so a fix to one path wouldn't propagate. Named here so a single
// grep lands on the canonical value, and so the unit tests in
// `util_tests.rs` can pin the values.

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

/// `sidecar/spawn.rs::spawn_sidecar_and_get_port_with_shutdown` (and
/// the dev-mode
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

// Sibling test module — tests live in `util_tests.rs` (per C-TEST-5:
// no inline `#[cfg(test)] mod tests` blocks in production source). It
// pins the constants that remain in THIS file; the token/session-id,
// timestamp, and atomic-fs tests moved with their code into
// `crypto_tests.rs` / `time_tests.rs` / `atomic_fs_tests.rs` under
// `util/`.
#[cfg(test)]
#[path = "util_tests.rs"]
mod util_tests;
