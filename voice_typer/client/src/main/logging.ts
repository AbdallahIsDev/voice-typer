/**
 * Console logging helpers shared across the main-process modules.
 *
 * Extracted from `index.ts` (REF-2). The ANSI color constants match the
 * Python backend's `_ColorFormatter` so Electron and Python log lines
 * look identical in the terminal.
 */
import fs from "node:fs";

// ANSI color constants — match the Python backend's _ColorFormatter so
// Electron and Python log lines look identical in the terminal.
export const DIM = "\x1b[38;5;242m"; // dim grey for timestamps
export const RESET = "\x1b[0m";
export const BUBBLE_CLR = "\x1b[38;5;39m"; // bright cyan for [BUBBLE] tags
export const RENDERER_CLR = "\x1b[38;5;227m"; // bright yellow for [MAIN renderer] tags

/**
 * Default maximum size for a crash/rejection log file before it is rotated.
 *
 * 1 MiB. The Python backend uses 5 MiB × 5 files via `RotatingFileHandler`;
 * the Rust host uses 5 MB × 5 via `tracing-appender::rolling::Rotation`.
 * The Electron crash log is much lower-volume (one line per process-level
 * error, not one line per IPC frame), so 1 MiB is plenty for hundreds of
 * crash entries while still bounding the worst-case disk usage at ~2 MiB
 * (active file + rotated .1 file).
 */
export const DEFAULT_CRASH_LOG_MAX_BYTES = 1_048_576;

/**
 * CR-9: rotate a log file before appending to it, so the file cannot grow
 * unbounded across crash-loop scenarios.
 *
 * Strategy (option (a) from the review — no new dependency, simple, robust):
 *   1. `stat` the file. If it does not exist yet (ENOENT) or is smaller
 *      than `maxSize`, do nothing.
 *   2. If it exceeds `maxSize`, rename `filePath` → `filePath + ".1"`,
 *      overwriting any prior `.1` file. On Windows where `rename` refuses
 *      to overwrite, unlink the destination first.
 *
 * This is a single-generation rotation (only one `.1` backup kept), which
 * bounds total disk usage for each log at `2 * maxSize` worst case. The
 * Python/Rust hosts keep 5 generations because they write orders of
 * magnitude more log volume; the Electron crash log is low-volume and a
 * single backup is sufficient.
 *
 * Best-effort: any I/O error is swallowed. The caller (`setupErrorHandlers`)
 * will still attempt the append, which is the more important operation.
 * Returning silently on ENOENT is critical because the very first crash
 * has no file yet — we must not block the append in that case.
 *
 * Exported so unit tests can exercise it directly without going through
 * the Electron-coupled `setupErrorHandlers`.
 *
 * @param filePath Absolute path to the active log file.
 * @param maxSize  Rotate once the file exceeds this many bytes. Defaults
 *                 to {@link DEFAULT_CRASH_LOG_MAX_BYTES} (1 MiB).
 */
export function rotateIfNeeded(
	filePath: string,
	maxSize: number = DEFAULT_CRASH_LOG_MAX_BYTES,
): void {
	let size: number;
	try {
		size = fs.statSync(filePath).size;
	} catch {
		// File does not exist yet (ENOENT, the expected case on the
		// first crash) or is unreadable (EACCES, EBUSY). Either way
		// there is nothing to rotate — let the caller try the append.
		return;
	}
	if (size <= maxSize) return;
	const backup = `${filePath}.1`;
	try {
		// POSIX `rename` overwrites the destination; Windows `rename`
		// throws EEXIST. Unlink first for cross-platform safety. The
		// unlink-then-rename window is racy on Windows if another
		// process holds the file open, but for our crash log (only
		// ever touched by this same Electron main process) that is
		// not a concern.
		try {
			fs.unlinkSync(backup);
		} catch (e) {
			const code = (e as NodeJS.ErrnoException).code;
			if (code !== "ENOENT") throw e;
		}
		fs.renameSync(filePath, backup);
	} catch {
		// Best-effort: rotation failed (disk full, permission, race).
		// Continue — the caller will still attempt the append. The
		// file may grow past `maxSize` in this rare case, but we
		// prefer "log the crash" over "rotate or nothing".
	}
}

// Clean Electron console-message format strings for terminal output.
// Strips printf-style format specifiers (%c, %o, %s, %d, %i, %f) that
// Electron's console-message event doesn't interpolate — it only
// captures the first argument (the format string).  React error
// boundaries commonly log with console.error('%o\n\n%s\n%s', obj, ...)
// which would otherwise leave raw "%o\n\n%s\n%s" artifacts in the log.
// Also collapses runs of whitespace/newlines into a single space.
export const cleanConsoleMsg = (msg: string): string =>
	msg
		.replace(/^%c[^;]+;\s*/, "")
		.replace(/%[csoidf]/g, "")
		.replace(/\n{3,}/g, "\n\n")
		.replace(/[ \t]+/g, " ")
		.trim();

/**
 * Format current time as H:MM:SS (12h, no leading zero), wrapped in ANSI
 * dim-grey, matching the Python backend's timestamp format/color so the
 * terminal output is visually consistent across both processes.
 */
export function ts(): string {
	const d = new Date();
	const h = d.getHours() % 12 || 12;
	const m = String(d.getMinutes()).padStart(2, "0");
	const s = String(d.getSeconds()).padStart(2, "0");
	return `${DIM}${h}:${m}:${s}${RESET}`;
}
