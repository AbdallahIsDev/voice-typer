//! Pure helpers + shared constants for the Tauri host (ADR-0020).
//! Constants stay in THIS file (not a `consts` submodule): mig15|16|17
//! source-inspection tests regex these `pub(crate) const` declarations.
//! Deep notes: docs/code-notes/tauri-host.md

pub(crate) mod atomic_fs;
pub(crate) mod crypto;
pub(crate) mod time;

// Re-exports keep every pre-split `crate::util::<name>` call site resolving.
pub(crate) use atomic_fs::{atomic_copy, atomic_copy_file, atomic_write_bytes};
pub(crate) use crypto::{generate_token, session_id};
pub(crate) use time::{now_time_only, now_timestamps};

// ─── Constants (ADR-0020) ─────────────────────────────────────────────

/// 256-bit bearer token (host uses bearer auth, not HMAC). Regenerated
/// per launch + per respawn; never logged.
pub(crate) const TOKEN_BYTES: usize = 32;

/// Supervisor backoff schedule (ms). LENGTH is the retry cap: after the
/// last step the host falls back to full-app relaunch (5 retries).
pub(crate) const SUPERVISOR_BACKOFF_MS: &[u64] = &[500, 1000, 2000, 4000, 8000];

/// Cooperative shutdown hard timeout for the UI-active
/// `shutdown_sidecar` command (tight budget: long block freezes the UI).
/// Exit path uses [`EXIT_SHUTDOWN_ACK_TIMEOUT_MS`] instead.
pub(crate) const SHUTDOWN_ACK_TIMEOUT_MS: u64 = 2000;

/// Exit-path shutdown ack only (`RunEvent::Exit` → `on_host_exit`).
/// 30s covers sidecar history/crash-recovery/WAL flush + native hotkey
/// teardown; 2s force-killed mid-cleanup. see docs/code-notes/tauri-host.md
pub(crate) const EXIT_SHUTDOWN_ACK_TIMEOUT_MS: u64 = 30_000;

/// Time to wait for the `server_started` JSON on sidecar stdout.
pub(crate) const SERVER_STARTED_TIMEOUT_MS: u64 = 30_000;

/// `bubble_level` coalesce target rate (Hz). Sidecar emits ~60 Hz.
pub(crate) const BUBBLE_LEVEL_COALESCE_HZ: u64 = 30;

/// 1 MiB WS frame cap (memory-exhaustion guard vs compromised sidecar).
pub(crate) const MAX_FRAME_BYTES: usize = 1024 * 1024;

/// Long-running dispatch timeout (model lifecycle commands).
/// see docs/code-notes/tauri-host.md#dispatch-timeouts
pub(crate) const DISPATCH_TIMEOUT_SECS: u64 = 120;

/// Download-scale dispatch timeout (1h) for multi-GB model streams.
pub(crate) const DISPATCH_DOWNLOAD_TIMEOUT_SECS: u64 = 3600;

/// Short dispatch timeout (15s) for every non-model command.
pub(crate) const DISPATCH_SHORT_TIMEOUT_SECS: u64 = 15;

/// Brief delay after `supervisor_relaunching` before `app.restart()` so
/// the webview can render the restarting banner.
pub(crate) const PRE_RESTART_DELAY_MS: u64 = 500;

/// Log cleanup tiers (mirrors Python `_log_constants.py`): 7-day age
/// retention, 25 MB session-start fallback, 40 MB mid-session truncate
/// in place (no numbered backups). see docs/code-notes/tauri-host.md
pub(crate) const LOG_AGE_RETENTION_SECS: u64 = 7 * 24 * 60 * 60; // 7 days
pub(crate) const LOG_SIZE_FALLBACK_BYTES: u64 = 25 * 1024 * 1024; // 25 MB
pub(crate) const LOG_MAX_BYTES: u64 = 40 * 1024 * 1024; // 40 MB

//heartbeat / kill-tree / poll / flush ──────────────────────────

/// Heartbeat dispatch interval: detects app-level sidecar hangs that keep
/// the WS open but stop responding to dispatches.
pub(crate) const HEARTBEAT_INTERVAL_SECS: u64 = 10;

/// Per-heartbeat response timeout. 3 misses × 10s ≈ 30s unresponsive
/// before supervisor respawn.
pub(crate) const HEARTBEAT_RESPONSE_TIMEOUT_SECS: u64 = 15;

/// Consecutive heartbeat misses before supervisor respawn.
pub(crate) const HEARTBEAT_MAX_MISSES: u32 = 3;

/// Unix `kill_tree` SIGTERM grace before SIGKILL (systemd convention).
#[cfg(unix)]
pub(crate) const KILL_TREE_SIGTERM_GRACE_MS: u64 = 200;

/// Poll interval for the sidecar's `server_started` stdout JSON.
pub(crate) const SERVER_STARTED_POLL_INTERVAL_MS: u64 = 500;

/// Brief delay after emitting `supervisor_relaunching` before
/// `app.restart()` (one event-loop tick for the banner).
pub(crate) const PRE_RESTART_FLUSH_DELAY_MS: u64 = 10;

// C-TEST-5: sibling test file pins the constants that remain here.
#[cfg(test)]
#[path = "util_tests.rs"]
mod util_tests;
