/**
 * Printf-style structured logger for the Electron main process.
 *
 * Extracted from the original `main/logging.ts` (spaghetti
 * split). Owns:
 *
 *   - `log` — printf-style API:
 *     `log.info("[BUBBLE] creating window at", x, y)`. Routes through
 *     colored stdout (ANSI `[INFO]`/`[WARN]`/`[ERROR]` prefixes) and
 *     tees WARN/ERROR to `<config-dir>/logs/electron-runtime.log` with 5 MiB
 *     rotation. Uses the dependency-free `computeConfigDir()` leaf
 *     (extracted from `single_instance.ts` so the logging package
 *     stays cycle-free).
 *   - `LogShape` — the public type of the `log` object (consumed by
 *     tests + type-only importers).
 *   - `getRuntimeLogPath()` — memoized resolver for
 *     `electron-runtime.log` (cached for the process lifetime;
 *     `_resetRuntimeLogPathForTest` clears the cache for tests).
 *   - `_getRuntimeLogPathForTest()` / `_resetRuntimeLogPathForTest()`
 *     — test-only exports for asserting memoization behavior.
 *
 * Local (non-exported) helpers:
 *   - `_runtimeLogPath` — the memoization slot.
 *   - `writeStdout()` — writes one line to stdout with the standard
 *     timestamp + level prefix. Accepts a pre-formatted args string
 *     (the output of `structuredLogger.ts::redactArgsForFile` — the
 *     single shared formatting primitive for every file/stdout tee)
 *     rather than the raw args.
 *   - `mainRuntimeLogger` — the persistent runtime log file writer
 *     (appends WARN/ERROR to `electron-runtime.log` via
 *     `appendLogLine`). Accepts a pre-formatted args string.
 *
 * Imports: `path`, `computeConfigDir` from `../config-dir`, the color
 * constants (`INFO_CLR` / `WARN_CLR` / `ERROR_CLR` / `RESET`), the
 * `RUNTIME_LOG_MAX_BYTES` cap, `ts` + `appendLogLine` from
 * `./rotation`, and `PERSIST_INFO` + `appendLifecycleLine` +
 * `redactArgsForFile` from `./structuredLogger` (so the printf-style
 * `log.*` formats through the same single primitive as the structured
 * logger's sinks, and its `log.info` can mirror the opt-in INFO
 * persistence wired up on `logger.info`).
 */
import path from "node:path";

import { computeConfigDir } from "../config-dir";
import { DIM, ERROR_CLR, INFO_CLR, RESET, WARN_CLR } from "./colors";
import { RUNTIME_LOG_MAX_BYTES } from "./constants";
import { appendLogLine, fileTimestamp, ts } from "./rotation";
import {
	appendLifecycleLine,
	PERSIST_INFO,
	redactArgsForFile,
} from "./structuredLogger";

/**
 * The shape of the structured `log` logger exported below. Exported as a
 * type so consumers (and tests) can reference it without depending on
 * the concrete object.
 */
export type LogShape = {
	info(...args: unknown[]): void;
	debug(...args: unknown[]): void;
	warn(...args: unknown[]): void;
	error(...args: unknown[]): void;
};

/**
 * Resolve the path to `electron-runtime.log` under `<config-dir>/logs`.
 *
 * Memoized. The function is called on every `log.warn` / `log.error`
 * invocation, and the underlying `computeConfigDir` resolution is
 * non-trivial (platform-specific dir computation + legacy
 * `~/.voice-typer` probe). On a hot crash-loop path this added a few
 * microseconds per line — pure overhead since the path is stable for
 * the process lifetime (the config dir never moves after
 * `setupUserData()` in `bootstrap.ts` runs once at startup).
 *
 * The cache uses `undefined` as the "not yet computed" sentinel so the
 * cached value can be either a real path string or `null` (resolution
 * failure). Subsequent calls return the cached value without touching
 * `computeConfigDir` again. `_resetRuntimeLogPathForTest()` clears the
 * cache for unit tests that need to re-resolve after swapping the
 * config-dir mock.
 *
 * The previous `_runtimeLogPathOverride` + `_setRuntimeLogPathForTest`
 * test-override pair was removed — no test imported it. Tests that need to
 * assert against the file-tee path now mock `computeConfigDir` (as
 * the log-path tests already do).
 */
// undefined = "not yet computed"; string = cached path;
// null = computed but config-dir resolution failed.
let _runtimeLogPath: string | null | undefined;

export function getRuntimeLogPath(): string | null {
	// Cache hit — return the previously resolved path (or null
	// if a prior call found config-dir resolution failed). Avoids
	// the `computeConfigDir` round-trip on every `log.warn` /
	// `log.error` invocation.
	if (_runtimeLogPath !== undefined) {
		return _runtimeLogPath;
	}
	try {
		// O1: the Electron logs live under `<config-dir>/logs`,
		// resolved via the dependency-free `computeConfigDir()` leaf
		// (extracted from `single_instance.ts` so the logging package
		// stays cycle-free).
		const logsDir = path.join(computeConfigDir(), "logs");
		_runtimeLogPath = path.join(logsDir, "electron-runtime.log");
	} catch {
		// Edge case: config-dir resolution can throw (e.g. very
		// early in test setup where the config-dir resolver isn't
		// available). Return null so `mainRuntimeLogger.write` silently
		// no-ops — the stdout tee already captured the message.
		_runtimeLogPath = null;
	}
	return _runtimeLogPath;
}

/**
 * Test-only export of the memoized path resolver. Exposed so
 * unit tests can call `getRuntimeLogPath()` directly and assert that
 * `app.getPath` is invoked exactly once across N calls — verifying
 * the memoization. Production callers go through `mainRuntimeLogger.write`
 * which calls `getRuntimeLogPath()` internally.
 *
 * Underscore-prefixed to signal "internal/test-only" — matching the
 * existing `_resetFileSizeCacheForTest` / `_resetRuntimeLogPathForTest`
 * / `_crashLogPaths` convention in this module.
 */
export function _getRuntimeLogPathForTest(): string | null {
	return getRuntimeLogPath();
}

/**
 * Clear the memoized runtime log path. Exported for unit tests
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
 * Write a single line to stdout with the standard timestamp + level
 * prefix. Routes to `console.error` / `console.warn` / `console.log`
 * so Node's stderr/stdout split is preserved (Electron's crash log
 * captures stderr; INFO goes to stdout).
 *
 * Accepts a PRE-FORMATTED args string (the output of the shared
 * `structuredLogger.ts::redactArgsForFile` primitive) rather than the
 * raw `unknown[]` args. This lets `log.warn` / `log.error` compute the
 * formatted string ONCE and pass it to both `writeStdout` (stdout tee)
 * and `mainRuntimeLogger.write` (file tee) — eliminating any double-
 * format penalty where every WARN/ERROR line would run the per-arg
 * `redactPii` passes twice on identical input.
 */
function writeStdout(
	level: "INFO" | "DEBUG" | "WARN" | "ERROR",
	color: string,
	formattedArgs: string,
): void {
	// Canonical terminal line (C-LOG-1): `HH:MM:SS  LEVEL  msg` — TWO
	// spaces between fields, bare level label (no brackets).
	const prefix = `${ts()}  ${color}${level}${RESET}`;
	const out = `${prefix}  ${formattedArgs}`;
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
 * the existing `rotateIfNeeded` helper. INFO lines are NOT
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
 * via `_getRuntimeLogPathForTest` / `_resetRuntimeLogPathForTest`
 * without going through the stdout path.
 *
 * Originally module-private in `main/logging.ts` ("dropped `export` —
 * verified zero external importers; only `log.warn` / `log.error`
 * below use it; tests exercise the file-tee path via
 * `_getRuntimeLogPathForTest` / `_resetRuntimeLogPathForTest`").
 */
const mainRuntimeLogger = {
	/**
	 * Accepts a PRE-FORMATTED args string (the output of the shared
	 * `structuredLogger.ts::redactArgsForFile` primitive) rather than
	 * the raw `unknown[]` args. This lets `log.warn` / `log.error`
	 * compute the formatted string ONCE and pass it to both
	 * `writeStdout` (stdout tee) and this writer (file tee) — the
	 * per-arg redaction passes run exactly once per line.
	 */
	write(level: "WARN" | "ERROR", formattedArgs: string): void {
		const logPath = getRuntimeLogPath();
		if (!logPath) return;
		const fileTs = fileTimestamp();
		// Canonical file line (C-LOG-1): two-space field separators,
		// bare level label (no brackets).
		const line = `${fileTs}  ${level}  ${formattedArgs}\n`;
		// Route through `appendLogLine` so the file-size
		// cache is populated after each successful append. Previously
		// this site called `rotateIfNeeded` + `fs.appendFileSync` directly,
		// which bypassed the cache and forced a synchronous `fs.statSync`
		// on every `log.warn`/`log.error` call (the exact perf bug
		// cache is populated after each successful append. Previously
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
 * The stdout output uses the existing `ts` timestamp helper and
 * ANSI color constants so Electron and Python log lines look identical
 * in the terminal. WARN/ERROR lines are teed to
 * `electron-runtime.log` via `mainRuntimeLogger` for post-mortem
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
		// Compute the formatted args ONCE through the shared
		// `redactArgsForFile` primitive (single-format discipline):
		// the result feeds both tees that need a formatted string
		// (stdout here; the runtime-log file tee on warn/error below)
		// so the per-arg redaction passes never run twice.
		const formatted = redactArgsForFile(args);
		writeStdout("INFO", INFO_CLR, formatted);
		// INFO not written to file (avoid bloat).
		//
		// Opt-in INFO persistence — mirror `logger.info`'s
		// `PERSIST_INFO` branch. The printf-style `log.info` is the
		// primary logger used by `bubble-window.ts`, `relaunch-app.ts`,
		// and the bootstrap crash path, so supporting the opt-in here
		// is just as important as on `logger.info`. Coerces args to
		// strings via `String(...)` (matching `redactArgsForFile`'s
		// non-Error fallback) — rich object formatting would change
		// the existing stdout behavior, so we keep it lossy here.
		//
		// NOTE: `appendLifecycleLine` uses `String(a)` (not
		// `JSON.stringify`) for the lossy printf-style formatting, so
		// we can't reuse `formatted` here — pass the raw `args` and
		// let `appendLifecycleLine` re-stringify.
		if (PERSIST_INFO) {
			appendLifecycleLine("info", args.map((a) => String(a)).join(" "), []);
		}
	},
	debug(...args: unknown[]): void {
		// Single-format discipline, same as `info` above. Stdout only —
		// DEBUG not written to file (avoid bloat).
		const formatted = redactArgsForFile(args);
		writeStdout("DEBUG", DIM, formatted);
	},
	warn(...args: unknown[]): void {
		// Compute the formatted args ONCE through the shared
		// `redactArgsForFile` primitive so both tees (stdout +
		// `electron-runtime.log`) share the same pre-formatted string
		// and the per-arg PII-redaction passes run exactly once.
		const formatted = redactArgsForFile(args);
		writeStdout("WARN", WARN_CLR, formatted);
		mainRuntimeLogger.write("WARN", formatted);
	},
	error(...args: unknown[]): void {
		// Compute the formatted args ONCE — see `warn` above for the
		// single-format discipline rationale.
		const formatted = redactArgsForFile(args);
		writeStdout("ERROR", ERROR_CLR, formatted);
		mainRuntimeLogger.write("ERROR", formatted);
	},
};
