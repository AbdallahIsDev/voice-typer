/**
 * Log-retention constants for the Electron main-process log files
 * (three-tier cleanup design — mirrors
 * `voice_typer/server/_log_constants.py` and `src-tauri/src/util.rs`).
 *
 * Extracted from the original `main/logging.ts` (spaghetti split).
 *
 * Leaf module — no imports.
 */

/**
 * Default maximum size for a crash/rejection log file before it is rotated.
 *
 * 1 MiB. The crash log is much lower-volume (one line per process-level
 * error, not one line per IPC frame), so 1 MiB is plenty for hundreds of
 * crash entries while still bounding the worst-case disk usage at ~2 MiB
 * (active file + rotated `.1` file). Crash logs are append-on-crash only,
 * so this cap never fires during normal operation.
 */
export const DEFAULT_CRASH_LOG_MAX_BYTES = 1_048_576;

/**
 * Tier 1 — age retention (session-start delete).
 *
 * Any log file in `logs/` whose last write is older than 7 days is
 * deleted by the startup sweep (`sweepStaleLogs`). Bounds storage for
 * low-traffic installs whose logs would otherwise sit forever.
 */
export const LOG_AGE_RETENTION_MS = 7 * 24 * 60 * 60 * 1000;

/**
 * Tier 2 — size fallback (session-start delete).
 *
 * Any log file larger than 25 MB is deleted by the startup sweep even
 * if freshly written — covers a marathon session that pushed a log past
 * the fallback between startups. Checked ONLY at session start, never
 * mid-session.
 */
export const LOG_SIZE_FALLBACK_BYTES = 25 * 1024 * 1024;

/**
 * Tier 3 — mid-session hard ceiling for the structured
 * `electron-main.log`.
 *
 * When the file exceeds 40 MB mid-session it is truncated IN PLACE
 * (emptied) and writing continues — the emergency brake so a single
 * never-ending session cannot grow a log without bound. Deliberately
 * far above the Tier-2 fallback (25 MB) so normal multi-day usage never
 * truncates mid-session (a file the ceiling truncates would have been
 * deleted at the previous startup had one occurred).
 */
export const DEFAULT_MAIN_LOG_MAX_BYTES = 40 * 1024 * 1024;

/**
 * Tier 3 — mid-session hard ceiling for the persistent runtime log
 * (`electron-runtime.log`). Same rationale as
 * {@link DEFAULT_MAIN_LOG_MAX_BYTES}: 40 MB emergency brake, far above
 * the 25 MB session-start fallback so normal multi-day usage never
 * truncates mid-session.
 */
export const RUNTIME_LOG_MAX_BYTES = 40 * 1024 * 1024;
