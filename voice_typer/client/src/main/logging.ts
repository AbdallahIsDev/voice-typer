/**
 * Console logging helpers shared across the main-process modules.
 *
 * Extracted from `index.ts` (REF-2). The ANSI color constants match the
 * Python backend's `_ColorFormatter` so Electron and Python log lines
 * look identical in the terminal.
 *
 * Two structured loggers are exported from this module:
 *
 *   1. `logger` (G4-H-37, session-4) — message-first API
 *      `logger.info("TCP connected", { port: 7001 })`. Writes to
 *      `<userData>/electron-main.log` with 5 MiB rotation. DEBUG is
 *      dev-only (gated by `!app.isPackaged`). INFO is also dev-only
 *      in file output (production writes only WARN/ERROR to file).
 *      Also exposes `mainLogPath()`, `rendererErrorsLogPath()`, and
 *      the reusable `appendLogLine()` helper (used by the main-window
 *      `console-message` handler for G4-M-67 renderer-error persistence).
 *
 *   2. `log` (PVT-G5-080 / PVT-G5-082, session-5) — printf-style API
 *      `log.info("[BUBBLE] creating window at", x, y)`. Routes through
 *      colored stdout (ANSI `[INFO]`/`[WARN]`/`[ERROR]` prefixes) and
 *      tees WARN/ERROR to `<userData>/electron-runtime.log` with 5 MiB
 *      rotation. Lazy-`require`s `electron` so the module is importable
 *      from non-Electron test contexts without mocking.
 *
 * DUPLICATION NOTE: the two loggers overlap in functionality (both
 * write WARN/ERROR lines to a 5 MiB-rotated file under userData). They
 * are kept side-by-side because (a) their consumer files use disjoint
 * APIs (message-first vs printf), (b) their file targets are different
 * (`electron-main.log` vs `electron-runtime.log`), and (c) merging them
 * into one would require touching every call site in main-window.ts,
 * bubble-window.ts, python-call-handler.ts, and window-handlers.ts —
 * those files are owned by other merge sub-agents. The primary agent
 * may consolidate later (P3 cleanup).
 */
import fs from "node:fs";
import path from "node:path";
import { app } from "electron";

// ANSI color constants — match the Python backend's _ColorFormatter so
// Electron and Python log lines look identical in the terminal.
export const DIM = "\x1b[38;5;242m"; // dim grey for timestamps
export const RESET = "\x1b[0m";
export const BUBBLE_CLR = "\x1b[38;5;39m"; // bright cyan for [BUBBLE] tags
export const RENDERER_CLR = "\x1b[38;5;227m"; // bright yellow for [MAIN renderer] tags

// PVT-G5-080: ANSI color constants for the structured `log` logger's
// level prefix. Bright cyan matches the BUBBLE_CLR (intentional — INFO
// is the "happy" level and visually parallels the [BUBBLE] tag color).
// Orange for WARN, bright red for ERROR — same palette as the Python
// backend's _ColorFormatter so multi-process log output is visually
// consistent.
export const INFO_CLR = "\x1b[38;5;39m"; // bright cyan
export const WARN_CLR = "\x1b[38;5;214m"; // orange
export const ERROR_CLR = "\x1b[38;5;196m"; // bright red

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
 * G4-H-37: rotation cap for the structured `electron-main.log`.
 *
 * 5 MiB matches the Python backend's `RotatingFileHandler` cap and the
 * Rust host's `tracing-appender` cap so all three processes retain
 * roughly the same wall-clock history window. Single-generation
 * rotation (`.1` backup) bounds total disk usage at ~10 MiB worst case.
 */
export const DEFAULT_MAIN_LOG_MAX_BYTES = 5 * 1024 * 1024;

/**
 * PVT-G5-082: maximum size for the persistent runtime log file
 * (`electron-runtime.log`) before rotation. 5 MiB matches the Python
 * backend's `RotatingFileHandler` threshold and the Rust host's
 * `tracing-appender` rotation. The existing {@link rotateIfNeeded}
 * helper keeps a single `.1` backup, so total disk usage is bounded at
 * ~10 MiB worst case (active file + .1 backup) — plenty for hundreds
 * of WARN/ERROR lines across a long-running session without
 * unbounded growth in crash-loop scenarios.
 */
export const RUNTIME_LOG_MAX_BYTES = 5 * 1024 * 1024;

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

// ────────────────────────────────────────────────────────────────────
// G4-H-37 (session-4): structured `logger` + `appendLogLine` helper
// ────────────────────────────────────────────────────────────────────
//
// A minimal rotating-file logger that mirrors Python's `RotatingFileHandler`
// semantics (single `.1` backup, size-based rotation). Writes are
// synchronous (`fs.appendFileSync`) so crash-path logs reach disk before
// the process exits — the volume is low (one line per IPC frame at most,
// usually far less) so the synchronous I/O cost is negligible.
//
// Levels:
//   - debug: dev-mode only (gated by `!app.isPackaged`); skipped in
//     production file output to avoid 5 MB of DEBUG noise per session.
//   - info: routine lifecycle (TCP connect, window created, Python
//     started). Persists in production so support staff can correlate
//     startup milestones with crash logs.
//   - warn: recoverable degradation (model folder picker failed,
//     openPath returned an error string, nativeTheme listener leak
//     recovered). Persists in production.
//   - error: unrecoverable (TCP buffer overflow, render-process gone).
//     Persists in production. Also goes through `console.error` so the
//     terminal shows it red.
//
// Every line is prefixed with an ISO-8601 timestamp + level tag so the
// file is greppable without color codes. Extra args are JSON-stringified
// so structured context (cmd, error, retry count) survives the line format.

type Level = "debug" | "info" | "warn" | "error";

/**
 * Format a log line for the file. Always ends with `\n` so `tail -f`
 * shows lines as they're written.
 */
function formatLine(level: Level, msg: string, args: unknown[]): string {
	const tsStr = new Date().toISOString();
	const formatted =
		args.length > 0
			? `${msg} ${args
					.map((a) => {
						try {
							return JSON.stringify(a);
						} catch {
							return String(a);
						}
					})
					.join(" ")}`
			: msg;
	return `${tsStr} [${level.toUpperCase()}] ${formatted}\n`;
}

/**
 * Resolve the path to `electron-main.log` under the Electron userData dir.
 * Exposed for tests + the G4-M-67 renderer-error persistence path.
 */
export function mainLogPath(): string {
	return path.join(app.getPath("userData"), "electron-main.log");
}

/**
 * Resolve the path to `electron-renderer-errors.log` under the Electron
 * userData dir. G4-M-67: the main-window `console-message` handler
 * appends level>=3 (ERROR) renderer messages to this file so support
 * staff can see renderer crashes without fishing through DevTools.
 */
export function rendererErrorsLogPath(): string {
	return path.join(app.getPath("userData"), "electron-renderer-errors.log");
}

/**
 * Append a single line to `filePath`, rotating first if the file has
 * grown past `maxBytes`. Best-effort: any I/O error is swallowed —
 * logging must never break the caller's code path.
 *
 * Exported so the bootstrap crash handlers + the main-window
 * `console-message` handler (G4-M-67) can share the same
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
	} catch {
		// Best-effort: disk full, permission denied, parent dir
		// missing, etc. Swallow — the caller's code path is more
		// important than the log line.
	}
}

/**
 * The structured main-process logger (G4-H-37). Mirrors its calls to the
 * matching `console.*` method (so dev-mode terminal output is unchanged)
 * AND appends a structured line to `electron-main.log`.
 *
 * In production, `debug` and `info` are NOT written to the file (only
 * to the terminal, which is closed in packaged builds) to avoid 5 MB
 * of DEBUG noise per session. `warn` and `error` always go to the file.
 *
 * Usage:
 *   logger.info("TCP connected", { port: 7001 });
 *   logger.warn("model:import-dialog failed", { error: err.message });
 *   logger.error("render-process-gone", details);
 */
export const logger: {
	debug: (msg: string, ...args: unknown[]) => void;
	info: (msg: string, ...args: unknown[]) => void;
	warn: (msg: string, ...args: unknown[]) => void;
	error: (msg: string, ...args: unknown[]) => void;
} = {
	debug(msg: string, ...args: unknown[]): void {
		// Debug is dev-only — both terminal and file are gated by
		// `!app.isPackaged` so production never writes DEBUG noise.
		if (!app.isPackaged) {
			console.debug(msg, ...args);
			appendLogLine(mainLogPath(), formatLine("debug", msg, args));
		}
	},
	info(msg: string, ...args: unknown[]): void {
		console.info(msg, ...args);
		if (!app.isPackaged) {
			// Dev: persist INFO so the dev can grep the file.
			appendLogLine(mainLogPath(), formatLine("info", msg, args));
		}
		// Production: INFO is too chatty for the rotating file —
		// it would push WARN/ERROR out of the 5 MB window too
		// fast. Skip the file write; the console call above is a
		// no-op in packaged builds (no terminal attached).
	},
	warn(msg: string, ...args: unknown[]): void {
		console.warn(msg, ...args);
		appendLogLine(mainLogPath(), formatLine("warn", msg, args));
	},
	error(msg: string, ...args: unknown[]): void {
		console.error(msg, ...args);
		appendLogLine(mainLogPath(), formatLine("error", msg, args));
	},
};

// ────────────────────────────────────────────────────────────────────
// PVT-G5-080 / PVT-G5-082 (session-5): structured `log` + persistent runtime log
// ────────────────────────────────────────────────────────────────────

/**
 * The shape of the structured `log` logger exported below. Exported as a
 * type so consumers (and tests) can reference it without depending on
 * the concrete object.
 */
export type LogShape = {
	info(...args: unknown[]): void;
	warn(...args: unknown[]): void;
	error(...args: unknown[]): void;
};

/**
 * Test-only override for the runtime log file path. When set (even to
 * `null`), {@link getRuntimeLogPath} returns this value verbatim
 * instead of resolving `<userData>/electron-runtime.log` via Electron's
 * `app.getPath`. Pass `null` to disable file logging for the duration
 * of a test (file writes silently no-op).
 *
 * Not part of the public API; exported only for test isolation.
 */
let _runtimeLogPathOverride: string | null | undefined;

export function _setRuntimeLogPathForTest(p: string | null): void {
	_runtimeLogPathOverride = p;
}

/**
 * Resolve the path to `electron-runtime.log`. Lazy-`require`s
 * `electron` so this module can be imported in non-Electron contexts
 * (vitest unit tests that exercise `rotateIfNeeded` directly) without
 * crashing — `require` is wrapped in `try/catch` and the function
 * returns `null` if Electron is unavailable, in which case
 * {@link mainRuntimeLogger.write} silently no-ops.
 *
 * Returns the override set by {@link _setRuntimeLogPathForTest} when
 * one is in effect.
 */
function getRuntimeLogPath(): string | null {
	if (_runtimeLogPathOverride !== undefined) return _runtimeLogPathOverride;
	try {
		// Lazy require so unit tests that import this module
		// (e.g. bootstrap.test.ts) don't need to mock Electron
		// unless they exercise the file-tee path. Top-level
		// `import { app } from "electron"` would force every
		// test that transitively imports logging.ts to mock
		// the entire Electron module.
		//
		// eslint-disable-next-line @typescript-eslint/no-var-requires
		const electron = require("electron") as {
			app?: { getPath?: (name: string) => string };
		};
		const userDataDir = electron?.app?.getPath?.("userData") ?? process.cwd();
		return path.join(userDataDir, "electron-runtime.log");
	} catch {
		return null;
	}
}

/**
 * Coerce a list of console-style args to a single space-joined string
 * for file output. Mirrors `console.*`'s space-joined behavior. Errors
 * are stringified with their stack (when available) so the file log
 * preserves the same detail as stdout. Non-stringifiable values fall
 * back to `String(value)` to never throw.
 */
function formatArgsForFile(args: unknown[]): string {
	return args
		.map((a) => {
			if (a instanceof Error) {
				return a.stack ?? `${a.name}: ${a.message}`;
			}
			if (typeof a === "string") return a;
			try {
				return JSON.stringify(a);
			} catch {
				return String(a);
			}
		})
		.join(" ");
}

/**
 * Write a single line to stdout with the standard timestamp + level
 * prefix. Routes to `console.error` / `console.warn` / `console.log`
 * so Node's stderr/stdout split is preserved (Electron's crash log
 * captures stderr; INFO goes to stdout).
 */
function writeStdout(
	level: "INFO" | "WARN" | "ERROR",
	color: string,
	args: unknown[],
): void {
	const prefix = `${ts()}  ${color}[${level}]${RESET}`;
	const out = `${prefix} ${formatArgsForFile(args)}`;
	if (level === "ERROR") {
		console.error(out);
	} else if (level === "WARN") {
		console.warn(out);
	} else {
		console.log(out);
	}
}

/**
 * PVT-G5-082: persistent runtime log file writer. Appends WARN/ERROR
 * lines to `<userData>/electron-runtime.log` with 5 MiB rotation via
 * the existing {@link rotateIfNeeded} helper. INFO lines are NOT
 * written to file (avoid bloat per PVT-G5-082 sub-finding — routine
 * lifecycle events would drown the signal in a long-running session).
 *
 * Best-effort: if the file path cannot be resolved (e.g. Electron is
 * not available in a test environment) or the write fails (disk full,
 * permission, etc.), file writes silently no-op. The stdout tee
 * already captured the message, so we lose durability but not
 * visibility.
 *
 * Exposed as a separate object (rather than inlined in `log.warn` /
 * `log.error`) so unit tests can exercise the file-tee path directly
 * via `_setRuntimeLogPathForTest` without going through the stdout
 * path.
 */
export const mainRuntimeLogger = {
	write(level: "WARN" | "ERROR", args: unknown[]): void {
		const logPath = getRuntimeLogPath();
		if (!logPath) return;
		try {
			rotateIfNeeded(logPath, RUNTIME_LOG_MAX_BYTES);
			const iso = new Date().toISOString();
			const line = `${iso} [${level}] ${formatArgsForFile(args)}\n`;
			fs.appendFileSync(logPath, line, { encoding: "utf-8" });
		} catch {
			// Best-effort: file write failed. The stdout
			// tee already captured the message — we lose
			// durability but not visibility. Swallowing
			// here is correct: a logging failure must not
			// cascade into a runtime failure of the
			// calling code.
		}
	},
};

/**
 * PVT-G5-080: tiny structured logger for the Electron main process.
 *
 * Routes lifecycle events through three semantic levels instead of the
 * previous "everything is `console.warn`" pattern, which made real
 * warnings indistinguishable from routine startup noise.
 *
 *   - `log.info(...)`  — routine lifecycle (connected, spawned, exited
 *                         normally). Stdout only — NOT written to file
 *                         to avoid bloat per PVT-G5-082.
 *   - `log.warn(...)`  — unexpected but non-fatal. Stdout + file.
 *   - `log.error(...)` — failures. Stdout + file.
 *
 * The stdout output uses the existing {@link ts} timestamp helper and
 * ANSI color constants so Electron and Python log lines look identical
 * in the terminal. WARN/ERROR lines are teed to
 * `electron-runtime.log` via {@link mainRuntimeLogger} for post-mortem
 * analysis without needing to reproduce the issue.
 *
 * Usage:
 *   log.info("[BUBBLE] creating window at", x, y);
 *   log.warn("[BUBBLE] screen-saver failed, trying floating:", err);
 *   log.error("[BUBBLE] did-fail-load code=", code, "desc=", desc);
 *
 * NOTE: callers that previously formatted their own `${ts()}  ${CLR}[TAG]
 * ...${RESET}` prefix should drop the manual prefix and pass the tag
 * (e.g. `[BUBBLE]`) as the first arg — the logger adds the timestamp
 * and level prefix automatically.
 */
export const log: LogShape = {
	info(...args: unknown[]): void {
		writeStdout("INFO", INFO_CLR, args);
		// PVT-G5-082: INFO not written to file (avoid bloat).
	},
	warn(...args: unknown[]): void {
		writeStdout("WARN", WARN_CLR, args);
		mainRuntimeLogger.write("WARN", args);
	},
	error(...args: unknown[]): void {
		writeStdout("ERROR", ERROR_CLR, args);
		mainRuntimeLogger.write("ERROR", args);
	},
};
