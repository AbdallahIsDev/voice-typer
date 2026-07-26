/**
 * File-rotation primitive + low-level log-line helpers for the
 * Electron main-process loggers.
 *
 * Extracted from the original `main/logging.ts` (DT-35 Phase 4.5
 * spaghetti split). Owns four exported helpers:
 *
 *   - `rotateIfNeeded(filePath, maxSize)` — single-generation
 *     `.1`-backup rotation, used by `appendLogLine` and exported for
 *     the bootstrap crash handlers + tests.
 *   - `appendLogLine(filePath, line, maxBytes)` — rotate-then-append
 *     writer with XV-154 cache bumping; consumed by both
 *     `structuredLogger.ts` (`logger.*`) and `printfLogger.ts`
 *     (`mainRuntimeLogger.write`).
 *   - `cleanConsoleMsg(msg)` — strips printf format specifiers from
 *     Electron's `console-message` event payload.
 *   - `ts()` — current time formatted as `H:MM:SS` wrapped in ANSI
 *     dim-grey (matches the Python backend's timestamp format/color).
 *
 * Imports: `fs`, the color constants (`DIM`, `RESET` — used by `ts`),
 * the max-bytes constants (default rotation caps), and the XV-154
 * file-size cache helpers.
 */
import fs from "node:fs";

import { DIM, RESET } from "./colors";
import {
	DEFAULT_CRASH_LOG_MAX_BYTES,
	DEFAULT_MAIN_LOG_MAX_BYTES,
} from "./constants";
import {
	_clearCachedFileSize,
	_getCachedFileSize,
	_setCachedFileSize,
} from "./fileSizeCache";

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
 *                 to `DEFAULT_CRASH_LOG_MAX_BYTES` (1 MiB).
 */
export function rotateIfNeeded(
	filePath: string,
	maxSize: number = DEFAULT_CRASH_LOG_MAX_BYTES,
): void {
	// XV-154: check the cache first — only stat the real file on
	// cache miss.
	const cachedSize = _getCachedFileSize(filePath);
	let size: number;
	if (cachedSize !== null) {
		size = cachedSize;
	} else {
		try {
			size = fs.statSync(filePath).size;
		} catch {
			// File does not exist yet (ENOENT, the expected case on the
			// first crash) or is unreadable (EACCES, EBUSY). Either way
			// there is nothing to rotate — let the caller try the append.
			return;
		}
	}
	if (size <= maxSize) {
		// Cache the size for next time (avoids re-stat on the next append).
		_setCachedFileSize(filePath, size);
		return;
	}
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
		// XV-154: after rotation, reset the cache so the next call
		// stats the active (new) file.
		_clearCachedFileSize(filePath);
	} catch (e) {
		// Best-effort: rotation failed (disk full, permission, race).
		// Continue — the caller will still attempt the append. The
		// file may grow past `maxSize` in this rare case, but we
		// prefer "log the crash" over "rotate or nothing".
		console.warn("[logging] rotateIfNeeded failed:", e);
	}
}

/**
 * Append a single line to `filePath`, rotating first if the file has
 * grown past `maxBytes`. Best-effort: any I/O error is swallowed —
 * logging must never break the caller's code path.
 *
 * Exported so the bootstrap crash handlers + the main-window
 * `console-message` handler can share the same
 * rotate-then-append semantics without re-implementing them.
 */
export function appendLogLine(
	filePath: string,
	line: string,
	maxBytes: number = DEFAULT_MAIN_LOG_MAX_BYTES,
): void {
	try {
		rotateIfNeeded(filePath, maxBytes);
		fs.appendFileSync(filePath, line, { encoding: "utf-8" });
		// XV-154: bump the cache after a successful append so the
		// next call doesn't need to stat. Cache the NEW file size
		// (previous cached size + line bytes).
		const prevSize = _getCachedFileSize(filePath);
		if (prevSize !== null) {
			_setCachedFileSize(filePath, prevSize + Buffer.byteLength(line, "utf-8"));
		}
	} catch (e) {
		// Best-effort: disk full, permission denied, parent dir
		// missing, etc. Swallow — the caller's code path is more
		// important than the log line.
		console.warn(`[logging] appendLogLine failed for ${filePath}:`, e);
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
 * Format current time as `H:MM:SS` (12h, no leading zero), wrapped in
 * ANSI dim-grey, matching the Python backend's timestamp format/color
 * so the terminal output is visually consistent across both processes.
 */
export function ts(): string {
	const d = new Date();
	const h = d.getHours() % 12 || 12;
	const m = String(d.getMinutes()).padStart(2, "0");
	const s = String(d.getSeconds()).padStart(2, "0");
	return `${DIM}${h}:${m}:${s}${RESET}`;
}
