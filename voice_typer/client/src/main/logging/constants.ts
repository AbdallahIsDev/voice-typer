/**
 * Rotation-cap size constants for the Electron main-process log files.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). All three were already `export`ed from
 * `main/logging.ts` and are imported by `bootstrap.ts` (for
 * `DEFAULT_CRASH_LOG_MAX_BYTES`) and by `rotation.ts` /
 * `structuredLogger.ts` / `printfLogger.ts` internally.
 *
 * Leaf module — no imports.
 */

/**
 * Default maximum size for a crash/rejection log file before it is rotated.
 *
 * 1 MiB. The Python backend uses 5 MiB × 5 files via `RotatingFileHandler`;
 * the Rust host uses 5 MB × 5 via `tracing-appender::rolling::Rotation`.
 * The Electron crash log is much lower-volume (one line per process-level
 * error, not one line per IPC frame), so 1 MiB is plenty for hundreds of
 * crash entries while still bounding the worst-case disk usage at ~2 MiB
 * (active file + rotated `.1` file).
 */
export const DEFAULT_CRASH_LOG_MAX_BYTES = 1_048_576;

/**
 * Rotation cap for the structured `electron-main.log`.
 *
 * 5 MiB matches the Python backend's `RotatingFileHandler` cap and the
 * Rust host's `tracing-appender` cap so all three processes retain
 * roughly the same wall-clock history window. Single-generation
 * rotation (`.1` backup) bounds total disk usage at ~10 MiB worst case.
 */
export const DEFAULT_MAIN_LOG_MAX_BYTES = 5 * 1024 * 1024;

/**
 * Maximum size for the persistent runtime log file
 * (`electron-runtime.log`) before rotation. 5 MiB matches the Python
 * backend's `RotatingFileHandler` threshold and the Rust host's
 * `tracing-appender` rotation. The existing `rotateIfNeeded` helper
 * keeps a single `.1` backup, so total disk usage is bounded at
 * ~10 MiB worst case (active file + `.1` backup) — plenty for hundreds
 * of WARN/ERROR lines across a long-running session without
 * unbounded growth in crash-loop scenarios.
 */
export const RUNTIME_LOG_MAX_BYTES = 5 * 1024 * 1024;
