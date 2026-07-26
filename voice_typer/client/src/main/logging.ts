/**
 * Console logging helpers shared across the main-process modules.
 *
 * Extracted from `index.ts` (REF-2). The ANSI color constants match the
 * Python backend's `_ColorFormatter` so Electron and Python log lines
 * look identical in the terminal.
 *
 * Two structured loggers are exported from this module:
 *
 *   1. `logger` — message-first API
 *      `logger.info("TCP connected", { port: 7001 })`. Writes to
 *      `<userData>/electron-main.log` with 5 MiB rotation. DEBUG is
 *      dev-only (gated by `!app.isPackaged`). INFO is dev-only in file
 *      output by default (production writes only WARN/ERROR to file).
 *      Set `VOICE_TYPER_ELECTRON_INFO_LOG=1` to opt in to production INFO
 *      persistence (routes to `electron-lifecycle.log`). Also
 *      exposes `rendererErrorsLogPath()` and the reusable `appendLogLine()`
 *      helper (used by the main-window `console-message` handler for
 *      renderer-error persistence).
 *
 *   2. `log` — printf-style API
 *      `log.info("[BUBBLE] creating window at", x, y)`. Routes through
 *      colored stdout (ANSI `[INFO]`/`[WARN]`/`[ERROR]` prefixes) and
 *      tees WARN/ERROR to `<userData>/electron-runtime.log` with 5 MiB
 *      rotation. Uses the top-level `import { app } from "electron"`
 *      (AC-117 — the previous lazy-`require` was dead code; the ESM
 *      import already forces the module to resolve at load time, so
 *      tests must mock `electron` via `vi.mock`).
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

// Opt-in INFO persistence for support/enterprise deployments.
// Set VOICE_TYPER_ELECTRON_INFO_LOG=1 to route INFO logs to
// `electron-lifecycle.log` (1 MiB × 1 backup). When unset, INFO
// persistence is unchanged (dev-only file write, production no-op).
//
// Production Electron builds have no terminal attached, so `console.info`
// is a no-op. Without this opt-in, lifecycle events (TCP connect, Python
// sidecar spawned, bubble shown, window created) leave ZERO durable trace
// in packaged builds — making support triage impossible. The opt-in
// default-off preserves the original disk-space-conservative behavior
// for the 99% case while letting enterprise/support deployments flip a
// single env var to get the lifecycle trail.
const PERSIST_INFO = process.env.VOICE_TYPER_ELECTRON_INFO_LOG === "1";

/**
 * Resolve the path to `electron-lifecycle.log` under the Electron
 * userData dir. Kept separate from {@link mainLogPath} so the opt-in
 * INFO stream never competes with the WARN/ERROR stream for the 5 MiB
 * `electron-main.log` rotation window.
 */
function lifecycleLogPath(): string {
	return path.join(app.getPath("userData"), "electron-lifecycle.log");
}

/**
 * Append a single INFO line to `electron-lifecycle.log` with a
 * 1 MiB rotation (single `.1` backup). Best-effort — any I/O error is
 * swallowed so logging can never crash the caller's code path.
 *
 * Mirrors the rotate-then-append pattern of {@link appendLogLine} but
 * uses a tighter 1 MiB cap (vs the 5 MiB cap on `electron-main.log`)
 * because the INFO stream is higher-volume and would otherwise push
 * WARN/ERROR context out of the smaller log too quickly. Total disk
 * usage is bounded at ~2 MiB worst case (active file + .1 backup).
 */
function appendLifecycleLine(
	level: string,
	msg: string,
	args: unknown[],
): void {
	try {
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
		const line = `${tsStr} [${level.toUpperCase()}] ${formatted}\n`;
		const p = lifecycleLogPath();
		// 1 MiB rotation × 1 backup (same strategy as rotateIfNeeded
		// but inlined to keep this helper self-contained and avoid the
		// XV-154 file-size cache — the INFO stream is lower priority
		// than WARN/ERROR and the extra stat on each write is
		// acceptable for an opt-in diagnostic path).
		try {
			const stat = fs.statSync(p);
			if (stat.size > 1024 * 1024) {
				try {
					fs.renameSync(p, `${p}.1`);
				} catch {
					/* ignore — best-effort rotation */
				}
			}
		} catch {
			/* file doesn't exist yet — fine, append will create it */
		}
		fs.appendFileSync(p, line, { flag: "a", mode: 0o600 });
	} catch {
		// Never let logging crash the app.
	}
}

// ANSI color constants — match the Python backend's _ColorFormatter so
// Electron and Python log lines look identical in the terminal.
//
// Internal-constants cleanup: `DIM`, `INFO_CLR`, `WARN_CLR`, `ERROR_CLR` are used ONLY
// inside this module (verified by `rg '\b(DIM|ERROR_CLR|INFO_CLR|WARN_CLR)\b'`);
// the `export` keyword was leftover from earlier refactors and has been
// dropped. `RESET`, `BUBBLE_CLR`, `RENDERER_CLR` remain exported because
// they are imported by `index.ts`, `handle-message.ts`, `bubble-window.ts`,
// and `main-window.ts`.
const DIM = "\x1b[38;5;242m"; // dim grey for timestamps
export const RESET = "\x1b[0m";
export const BUBBLE_CLR = "\x1b[38;5;39m"; // bright cyan for [BUBBLE] tags
export const RENDERER_CLR = "\x1b[38;5;227m"; // bright yellow for [MAIN renderer] tags

// ANSI color constants for the structured `log` logger's
// level prefix. Bright cyan matches the BUBBLE_CLR (intentional — INFO
// is the "happy" level and visually parallels the [BUBBLE] tag color).
// Orange for WARN, bright red for ERROR — same palette as the Python
// backend's _ColorFormatter so multi-process log output is visually
// consistent.
//
// Internal-constants cleanup: dropped `export` — these constants are referenced only
// internally (by `writeStdout` and `ts()` below).
const INFO_CLR = "\x1b[38;5;39m"; // bright cyan
const WARN_CLR = "\x1b[38;5;214m"; // orange
const ERROR_CLR = "\x1b[38;5;196m"; // bright red

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
// XV-154: _fileSizeCache memoizes statSync results so appendLogLine
// doesn't call fs.statSync on every write — only on cache miss. The
// cache is bumped (updated) after every successful append so the
// next call can skip stat. Rotations reset the cache entry to 0.
// ────────────────────────────────────────────────────────────────────

/** @internal module-level cache keyed by absolute log-file path. */
const _fileSizeCache = new Map<string, number>();

/**
 * XV-154: reset the file-size cache. Exported for tests so each
 * test starts with a clean cache state.
 */
export function _resetFileSizeCacheForTest(): void {
	_fileSizeCache.clear();
}

/**
 * Read the cached file size for `filePath`. Returns `null` on cache
 * miss (caller should stat the real file).
 */
function _getCachedFileSize(filePath: string): number | null {
	const cached = _fileSizeCache.get(filePath);
	return cached !== undefined ? cached : null;
}

/**
 * Update the cached file size for `filePath` after a successful append.
 */
function _setCachedFileSize(filePath: string, size: number): void {
	_fileSizeCache.set(filePath, size);
}

/**
 * Remove an entry from the cache (used by rotateIfNeeded after rotation).
 */
function _clearCachedFileSize(filePath: string): void {
	_fileSizeCache.delete(filePath);
}

// ────────────────────────────────────────────────────────────────────
// Structured `logger` + `appendLogLine` helper
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
 *
 * Internal-constants cleanup: dropped `export` — verified zero external importers (only used
 * internally by `logger.warn` / `logger.error` / `logger.debug` /
 * `logger.info` below). The docstring's previous "Exposed for tests"
 * note was stale: no test imports `mainLogPath` directly.
 */
function mainLogPath(): string {
	return path.join(app.getPath("userData"), "electron-main.log");
}

/**
 * Resolve the path to `electron-renderer-errors.log` under the Electron
 * userData dir. The main-window `console-message` handler
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

/**
 * The structured main-process logger. Mirrors its calls to the
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
		//
		// Opt-in INFO persistence. When
		// `VOICE_TYPER_ELECTRON_INFO_LOG=1` is set (support /
		// enterprise deployments), route INFO through a dedicated
		// `electron-lifecycle.log` so support staff can correlate
		// startup milestones (TCP connect, Python spawned, window
		// created, bubble shown) with crash logs. Default-off
		// preserves the disk-space-conservative behavior for the
		// 99% case.
		if (PERSIST_INFO) {
			appendLifecycleLine("info", msg, args);
		}
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
// Structured `log` + persistent runtime log
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
 * Resolve the path to `electron-runtime.log`. Uses the top-level
 * `import { app } from "electron"` (line 38) directly.
 *
 * AC-117: the previous implementation lazily `require("electron")`
 * inside a `try/catch` here, claiming it let unit tests import the
 * module without mocking Electron. That was dead code — the top-level
 * ESM `import { app }` already forces the Electron module to resolve
 * at module-load time, so if Electron is unavailable the module never
 * loads and this function is never reached. The lazy `require` was
 * contradictory with the top-level import strategy and is removed.
 *
 * ER-63: memoized. The function is called on every `log.warn` / `log.error`
 * invocation, and the underlying `app.getPath` resolution is non-trivial
 * (Electron lazy-loads its `app` module, and `getPath("userData")` does a
 * platform-specific dir computation). On a hot crash-loop path this added
 * a few microseconds per line — pure overhead since the path is stable for
 * the process lifetime (it changes only if `app.setPath("userData", …)` is
 * called between two log calls, which never happens — `setupUserData()` in
 * `bootstrap.ts` runs exactly once at startup BEFORE any `log.warn` call
 * could fire).
 *
 * The cache uses `undefined` as the "not yet computed" sentinel so the
 * cached value can be either a real path string or `null` (Electron
 * unavailable). Subsequent calls return the cached value without touching
 * `app.getPath` again. `_resetRuntimeLogPathForTest()` clears the cache
 * for unit tests that need to re-resolve after swapping the Electron
 * mock.
 *
 * XS-66: the previous `_runtimeLogPathOverride` + `_setRuntimeLogPathForTest`
 * test-override pair was removed — no test imported it. Tests that need to
 * assert against the file-tee path now mock `electron`'s `app.getPath` (as
 * `bootstrap.test.ts` already does).
 */
// ER-63: undefined = "not yet computed"; string = cached path;
// null = computed but Electron was unavailable.
let _runtimeLogPath: string | null | undefined;

function getRuntimeLogPath(): string | null {
	// ER-63: cache hit — return the previously resolved path (or null
	// if a prior call found Electron unavailable). Avoids the
	// `app.getPath` round-trip on every `log.warn` / `log.error`
	// invocation.
	if (_runtimeLogPath !== undefined) {
		return _runtimeLogPath;
	}
	try {
		// The top-level `import { app } from "electron"` on line 38
		// is the canonical binding (vitest 4 intercepts static imports
		// but NOT dynamic `require("electron")` in ESM-transpiled
		// modules — so the previous lazy-require pattern was opaque
		// to the mock system and untestable). The `?? process.cwd()`
		// fallback preserves the original behaviour for non-Electron
		// contexts.
		const userDataDir = app?.getPath?.("userData") ?? process.cwd();
		_runtimeLogPath = path.join(userDataDir, "electron-runtime.log");
	} catch {
		// Edge case: `app.getPath` can throw if the userData dir
		// is unset/unavailable (e.g. very early in test setup
		// where the Electron mock doesn't yet implement getPath).
		// Return null so {@link mainRuntimeLogger.write} silently
		// no-ops — the stdout tee already captured the message.
		_runtimeLogPath = null;
	}
	return _runtimeLogPath;
}

/**
 * ER-63: test-only export of the memoized path resolver. Exposed so
 * unit tests can call `getRuntimeLogPath()` directly and assert that
 * `app.getPath` is invoked exactly once across N calls — verifying
 * the memoization. Production callers go through
 * {@link mainRuntimeLogger.write} which calls `getRuntimeLogPath()`
 * internally.
 *
 * Underscore-prefixed to signal "internal/test-only" — matching the
 * existing `_resetFileSizeCacheForTest` / `_resetRuntimeLogPathForTest`
 * / `_crashLogPaths` convention in this module.
 */
export function _getRuntimeLogPathForTest(): string | null {
	return getRuntimeLogPath();
}

/**
 * ER-63: clear the memoized runtime log path. Exported for unit tests
 * so each test case starts with a fresh cache and can assert against
 * the call count of `app.getPath`.
 *
 * Production code should NOT call this — `getRuntimeLogPath` is intended
 * to memoize for the process lifetime, and the userData dir does not
 * move after `bootstrapRuntime()`'s `setupUserData()` step.
 */
export function _resetRuntimeLogPathForTest(): void {
	_runtimeLogPath = undefined;
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
 * Persistent runtime log file writer. Appends WARN/ERROR
 * lines to `<userData>/electron-runtime.log` with 5 MiB rotation via
 * the existing {@link rotateIfNeeded} helper. INFO lines are NOT
 * written to file (avoid bloat — routine
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
 *
 * Internal-constants cleanup: dropped `export` — verified zero external importers (only
 * `log.warn` / `log.error` below use it; tests exercise the file-tee
 * path via `_getRuntimeLogPathForTest` / `_resetRuntimeLogPathForTest`).
 */
const mainRuntimeLogger = {
	write(level: "WARN" | "ERROR", args: unknown[]): void {
		const logPath = getRuntimeLogPath();
		if (!logPath) return;
		const iso = new Date().toISOString();
		const line = `${iso} [${level}] ${formatArgsForFile(args)}\n`;
		// AC-12: route through `appendLogLine` so the XV-154 file-size
		// cache is populated after each successful append. Previously
		// this site called `rotateIfNeeded` + `fs.appendFileSync` directly,
		// which bypassed the cache and forced a synchronous `fs.statSync`
		// on every `log.warn`/`log.error` call (the exact perf bug
		// XV-154 described). `appendLogLine` swallows I/O errors
		// internally (best-effort), so no surrounding try/catch is
		// needed — a logging failure must not cascade into a runtime
		// failure of the calling code.
		appendLogLine(logPath, line, RUNTIME_LOG_MAX_BYTES);
	},
};

/**
 * Tiny structured logger for the Electron main process.
 *
 * Routes lifecycle events through three semantic levels instead of the
 * previous "everything is `console.warn`" pattern, which made real
 * warnings indistinguishable from routine startup noise.
 *
 *   - `log.info(...)`  — routine lifecycle (connected, spawned, exited
 *                         normally). Stdout only — NOT written to file
 *                         to avoid bloat.
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
		// INFO not written to file (avoid bloat).
		//
		// Opt-in INFO persistence — mirror `logger.info`'s
		// `PERSIST_INFO` branch. The printf-style `log.info` is the
		// primary logger used by `bubble-window.ts`, `relaunch-app.ts`,
		// and the bootstrap crash path, so supporting the opt-in here
		// is just as important as on `logger.info`. Coerces args to
		// strings via `String(...)` (matching `formatArgsForFile`'s
		// non-Error fallback) — rich object formatting would change
		// the existing stdout behavior, so we keep it lossy here.
		if (PERSIST_INFO) {
			appendLifecycleLine("info", args.map((a) => String(a)).join(" "), []);
		}
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
